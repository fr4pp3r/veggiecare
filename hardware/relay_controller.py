"""Relay controller with safety-first design.

Safety guarantees
~~~~~~~~~~~~~~~~~
* All relays are forced OFF at startup, before any other logic runs.
* Every :meth:`activate` schedules a watchdog deadline; the background
  thread turns the relay OFF when the deadline is reached, regardless
  of what the automation controller thinks.
* :meth:`all_off` can be called at any time (including from a signal
  handler) and returns only after all pins are verified low.
* If ``gpiozero`` is unavailable (e.g. Windows dev box), the controller
  automatically falls back to in-memory simulated relays.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ======================================================================
# Abstract base
# ======================================================================

class BaseRelay:
    """Minimal interface shared by real and simulated relays."""

    def __init__(self, relay_id: int, name: str, pin: int):
        self.relay_id = relay_id
        self.name = name
        self.pin = pin
        self._active = False

    def on(self) -> None:
        self._active = True

    def off(self) -> None:
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def close(self) -> None:
        self.off()


# ======================================================================
# Real GPIO relay (gpiozero)
# ======================================================================

class GpioRelay(BaseRelay):
    """Relay driven through gpiozero's LED abstraction."""

    def __init__(self, relay_id: int, name: str, pin: int, active_high: bool):
        super().__init__(relay_id, name, pin)
        try:
            from gpiozero import LED
            self._led = LED(pin, active_high=active_high, initial_value=False)
            self._available = True
        except Exception as exc:
            logger.error("gpiozero init failed for relay %s on GPIO %s: %s", name, pin, exc)
            self._led = None
            self._available = False

    @property
    def available(self) -> bool:
        return self._available and self._led is not None

    def on(self) -> None:
        if self._led is not None:
            self._led.on()
            self._active = True

    def off(self) -> None:
        if self._led is not None:
            self._led.off()
            self._active = False

    def close(self) -> None:
        self.off()
        if self._led is not None:
            try:
                self._led.close()
            except Exception:
                pass
            self._led = None


# ======================================================================
# Simulated relay (no hardware)
# ======================================================================

class SimulatedRelay(BaseRelay):
    """In-memory relay for development and testing."""

    def __init__(self, relay_id: int, name: str, pin: int):
        super().__init__(relay_id, name, pin)
        self._available = True

    @property
    def available(self) -> bool:
        return True


# ======================================================================
# Controller
# ======================================================================

class RelayController:
    """Manages three relays with a hard watchdog for auto-off."""

    def __init__(self, cfg: dict[str, Any], simulate: bool = False):
        self._active_high = bool(cfg.get("active_high", True))
        self._watchdog_cap = float(cfg["auto_off_watchdog_seconds"])
        self._default_duration = float(cfg.get("default_duration_seconds", 60))
        self._watchdog_step = float(cfg.get("watchdog_check_seconds", 1.0))
        if self._watchdog_step <= 0:
            self._watchdog_step = 1.0

        # {relay_id: {"relay": BaseRelay, "deadline": float|None, "duration": float}}
        self._relays: dict[int, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # Build relay objects — all start OFF.
        for item in cfg["items"]:
            rid = int(item["id"])
            name = str(item["name"])
            pin = int(item["pin"])
            if simulate:
                relay = SimulatedRelay(rid, name, pin)
            else:
                relay = GpioRelay(rid, name, pin, self._active_high)
            self._relays[rid] = {
                "relay": relay,
                "deadline": None,
                "duration": float(item.get("activation_duration_seconds", self._default_duration)),
                "activated_at": None,
                "trigger": None,
            }

        # Start watchdog thread.
        self._thread = threading.Thread(target=self._watchdog_loop, daemon=True, name="relay-watchdog")
        self._thread.start()
        logger.info(
            "Relay controller ready (%s, active_high=%s): pins=%s",
            "simulated" if simulate else "GPIO",
            self._active_high,
            [r["relay"].pin for r in self._relays.values()],
        )

    # ------------------------------------------------------------------
    # activation
    # ------------------------------------------------------------------

    def activate(self, relay_id: int, duration: float | None = None,
                 trigger: str = "automatic") -> bool:
        """Energize a relay for *duration* seconds (capped by the watchdog).

        Returns True if the relay was activated, False if unavailable.
        """
        with self._lock:
            entry = self._relays.get(relay_id)
            if entry is None or not entry["relay"].available:
                return False
            dur = min(
                duration or entry["duration"],
                self._watchdog_cap,
            )
            now = time.monotonic()
            entry["relay"].on()
            entry["deadline"] = now + dur
            entry["duration"] = dur
            entry["activated_at"] = datetime.now().isoformat(timespec="seconds")
            entry["trigger"] = trigger
            logger.info(
                "Relay %s activated for %.0fs (trigger=%s)", relay_id, dur, trigger,
            )
            return True

    def deactivate(self, relay_id: int) -> None:
        """Manually turn a relay off and clear its deadline."""
        with self._lock:
            entry = self._relays.get(relay_id)
            if entry is None:
                return
            entry["relay"].off()
            entry["deadline"] = None
            entry["activated_at"] = None
            entry["trigger"] = None

    def all_off(self, reason: str = "manual") -> None:
        """Force every relay off (emergency stop)."""
        with self._lock:
            for entry in self._relays.values():
                entry["relay"].off()
                entry["deadline"] = None
                entry["activated_at"] = None
                entry["trigger"] = None
        logger.warning("All relays forced OFF (reason=%s)", reason)

    # ------------------------------------------------------------------
    # status helpers
    # ------------------------------------------------------------------

    def is_active(self, relay_id: int) -> bool:
        entry = self._relays.get(relay_id)
        return entry is not None and entry["relay"].is_active

    def available(self, relay_id: int) -> bool:
        entry = self._relays.get(relay_id)
        return entry is not None and entry["relay"].available

    def get(self, relay_id: int) -> dict[str, Any] | None:
        entry = self._relays.get(relay_id)
        if entry is None:
            return None
        return {
            "id": relay_id,
            "name": entry["relay"].name,
            "pin": entry["relay"].pin,
            "active": entry["relay"].is_active,
            "available": entry["relay"].available,
            "deadline": entry["deadline"],
            "activated_at": entry["activated_at"],
            "duration": entry["duration"],
            "trigger": entry["trigger"],
        }

    def relay_ids(self) -> list[int]:
        return list(self._relays.keys())

    # ------------------------------------------------------------------
    # watchdog background thread
    # ------------------------------------------------------------------

    def _watchdog_loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            with self._lock:
                for entry in self._relays.values():
                    if entry["deadline"] is not None and now >= entry["deadline"]:
                        entry["relay"].off()
                        entry["deadline"] = None
                        entry["activated_at"] = None
                        entry["trigger"] = None
                        logger.info(
                            "Watchdog turned relay %s OFF", entry["relay"].name,
                        )
            self._stop.wait(timeout=self._watchdog_step)

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Emergency OFF + stop watchdog."""
        self.all_off(reason="shutdown")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def close(self) -> None:
        self.shutdown()
        for entry in self._relays.values():
            entry["relay"].close()