"""Thread-safe shared state for VeggieCare.

The automation controller thread writes live sensor/relay/alert state into a
:class:`SystemState` instance; the Flask dashboard thread reads it through
:meth:`SystemState.snapshot`. Neither side ever blocks on a hardware read,
because all hardware I/O happens inside the controller thread.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime
from typing import Any


class SystemState:
    def __init__(self, config: dict):
        self._lock = threading.RLock()
        self.uptime_start = time.monotonic()
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.system_name = str(config["system"].get("name", "VeggieCare"))
        self.simulate_hardware = bool(config["system"].get("simulate_hardware", False))

        # Latest NPK reading + threshold status
        self.npk: dict[str, Any] = {
            "nitrogen": None,
            "phosphorus": None,
            "potassium": None,
            "below": {"nitrogen": False, "phosphorus": False, "potassium": False},
            "timestamp": None,
            "last_success": None,
            "error": None,
        }

        # Latest soil moisture reading
        self.moisture: dict[str, Any] = {
            "value": None,
            "below": False,
            "timestamp": None,
            "last_success": None,
            "error": None,
        }

        # Relay state, seeded from the (validated) config
        self.relays: dict[int, dict[str, Any]] = {}
        for item in config["relays"]["items"]:
            self.relays[int(item["id"])] = {
                "id": int(item["id"]),
                "name": item["name"],
                "label": item["label"],
                "pin": int(item["pin"]),
                "activation_duration_seconds": float(item.get("activation_duration_seconds", 60)),
                "state": "off",
                "activated_at": None,
                "duration": None,
                "last_activation": None,
                "available": True,
                "error": None,
            }

        # Pest detection state (stays "not configured" until enabled)
        self.pest: dict[str, Any] = {
            "enabled": bool(config["pest_detection"].get("enabled", False)),
            "configured": False,
            "detected": False,
            "pest_class": None,
            "confidence": None,
            "timestamp": None,
            "image_path": None,
            "model": None,
            "error": None,
        }

        # Camera state
        self.camera: dict[str, Any] = {
            "configured": False,
            "error": None,
            "last_capture": None,
        }

        # Pest response relay (Relay 3) monthly activation usage
        self.usage: dict[str, Any] = {
            "month": None,
            "used": 0,
            "remaining": 0,
            "max": 0,
            "limit_reached": False,
        }

        self.db: dict[str, Any] = {"ok": True, "error": None, "last_write": None}
        self.automation: dict[str, Any] = {"running": False, "paused": False}
        self._alerts: deque = deque(maxlen=30)

    # ------------------------------------------------------------------
    # writers
    # ------------------------------------------------------------------

    def update_npk(self, values: dict[str, float], below: dict[str, bool],
                   timestamp: str, error: str | None = None) -> None:
        with self._lock:
            self.npk.update({
                "nitrogen": values.get("nitrogen"),
                "phosphorus": values.get("phosphorus"),
                "potassium": values.get("potassium"),
                "below": dict(below),
                "timestamp": timestamp,
                "error": error,
            })
            if error is None:
                self.npk["last_success"] = timestamp

    def update_moisture(self, value: float, below: bool, timestamp: str,
                        error: str | None = None) -> None:
        with self._lock:
            self.moisture.update({
                "value": value,
                "below": bool(below),
                "timestamp": timestamp,
                "error": error,
            })
            if error is None:
                self.moisture["last_success"] = timestamp

    def set_relay(self, relay_id: int, state: str, activated_at: str | None = None,
                  duration: float | None = None, available: bool | None = None,
                  error: str | None = None) -> None:
        with self._lock:
            relay = self.relays.get(relay_id)
            if relay is None:
                return
            relay["state"] = state
            if activated_at is not None:
                relay["activated_at"] = activated_at
            if duration is not None:
                relay["duration"] = duration
            if state == "off":
                relay["activated_at"] = None
                relay["duration"] = None
            if state == "on":
                relay["last_activation"] = activated_at or datetime.now().isoformat(timespec="seconds")
            if available is not None:
                relay["available"] = available
            if error is not None:
                relay["error"] = error

    def update_usage(self, month: str, used: int, max_activations: int) -> None:
        with self._lock:
            self.usage = {
                "month": month,
                "used": used,
                "remaining": max(0, max_activations - used),
                "max": max_activations,
                "limit_reached": used >= max_activations,
            }

    def update_pest(self, **fields: Any) -> None:
        with self._lock:
            self.pest.update(fields)

    def update_camera(self, **fields: Any) -> None:
        with self._lock:
            self.camera.update(fields)

    def update_db(self, ok: bool, error: str | None = None, last_write: str | None = None) -> None:
        with self._lock:
            self.db["ok"] = ok
            self.db["error"] = error
            if last_write is not None:
                self.db["last_write"] = last_write

    def set_automation(self, running: bool | None = None, paused: bool | None = None) -> None:
        with self._lock:
            if running is not None:
                self.automation["running"] = running
            if paused is not None:
                self.automation["paused"] = paused

    def add_alert(self, level: str, message: str, source: str | None = None) -> None:
        with self._lock:
            self._alerts.appendleft({
                "level": level,
                "message": message,
                "source": source,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            })

    # ------------------------------------------------------------------
    # reader
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Deep-enough copy of all live state for API/dashboard reads."""
        with self._lock:
            relays = sorted(
                ({k: v for k, v in r.items()}
                 for r in self.relays.values()),
                key=lambda r: r["id"],
            )
            return {
                "system": {
                    "name": self.system_name,
                    "simulate_hardware": self.simulate_hardware,
                    "uptime_seconds": int(time.monotonic() - self.uptime_start),
                    "started_at": self.started_at,
                },
                "npk": dict(self.npk),
                "moisture": dict(self.moisture),
                "relays": relays,
                "usage": dict(self.usage),
                "pest": dict(self.pest),
                "camera": dict(self.camera),
                "db": dict(self.db),
                "automation": dict(self.automation),
                "alerts": list(self._alerts),
            }