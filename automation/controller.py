"""Automation controller — the brain of VeggieCare.

Runs a background loop that periodically reads sensors, evaluates rules,
activates relays and records everything in the database. Every hardware
operation is wrapped in a try/except so sensor or database failures never
crash the loop.

Rule summary
~~~~~~~~~~~~
* **NPK** — alert only (no automatic relay activation).
* **Soil moisture** — auto-water (Relay 2) when moisture drops below
  threshold, subject to the monthly activation limit and a configurable
  cooldown after each watering.
* **Pest detection** — when the detector is enabled, run the detection
  cycle and activate Relay 3 on a positive result.

Safety
~~~~~~
* Relays are never activated if ``state.automation.paused`` is True.
* The relay controller watchdog enforces a hard upper bound on every
  activation regardless of what this controller requests.
"""

from __future__ import annotations

import logging
import math
import queue
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from database.database import Database, DatabaseError
from hardware.relay_controller import RelayController
from sensors.base import SensorError
from state import SystemState

logger = logging.getLogger(__name__)

# Hard ceiling on one sweep leg. Guards against a mis-typed geometry config
# (e.g. extension_distance_in: 3600) commanding a runaway motion.
_MAX_SWEEP_STEPS = 50000


@dataclass(slots=True)
class PestTask:
    """One queued pest operation for the background pest worker."""

    mode: str           # "sweep" | "camera"
    source: str = "automatic"


class AutomationController:
    """Event-driven loop that ties sensors → rules → relays → database."""

    # Tick frequency — the loop sleeps this long between iterations.
    _TICK_SECONDS = 1.0

    def __init__(
        self,
        *,
        config: dict[str, Any],
        db: Database,
        relays: RelayController,
        state: SystemState,
        sensors: dict[str, Any] | None = None,
    ):
        self._cfg = config
        self._db = db
        self._relays = relays
        self._state = state

        # Injected by app.py after construction (avoids circular import).
        self._npk_sensor = sensors.get("npk") if sensors else None
        self._moisture_sensor = sensors.get("moisture") if sensors else None
        self._detector = None       # set via attach_detector()
        self._camera = None         # set via attach_camera()
        self._stepper = None        # set via attach_stepper()
        self._recommender = None    # set via attach_recommender()
        self._treatment_advisor: Any | None = None  # set via attach_treatment_advisor()

        # Internal bookkeeping
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # Timestamps (monotonic) of the last successful sensor read.
        self._last_npk_read = 0.0
        self._last_moisture_read = 0.0
        self._last_pest_cycle = 0.0

        # Pest work runs on a dedicated worker thread: a stepper sweep blocks
        # for minutes and must not stall the sensor tick. _pest_active is
        # reserved at enqueue time and cleared once the queue drains.
        self._pest_queue: queue.Queue[PestTask] = queue.Queue()
        self._pest_worker: threading.Thread | None = None
        self._pest_active = False
        self._pest_lock = threading.Lock()
        self._pest_abort = threading.Event()
        # Monotonic timestamp of the last automated pest monitor run.
        self._last_pest_monitor = 0.0

        # Timestamps (monotonic) of the last database log for each sensor.
        self._last_npk_log = 0.0
        self._last_moisture_log = 0.0

        # Cooldown: do not re-trigger watering until this time has passed.
        self._watering_cooldown_until = 0.0

        # Watering closed-loop bookkeeping.
        self._water_baseline_value: float | None = None
        self._watering_non_responses: int = 0
        self._watering_fault: bool = False
        self._watering_blocked_alert_day: str | None = None
        self._last_watering_budget_read: float = 0.0
        self._watering_budget_used: float = 0.0
        # Loop cadence — tests can shorten this so ticks run fast.
        self._tick_seconds = float(config.get("system", {}).get("tick_seconds", 1.0))
        if self._tick_seconds <= 0:
            self._tick_seconds = 1.0

        # Transition tracking — alerts fire only on state *change*, not every tick.
        self._npk_was_below = False
        self._moisture_was_below = False
        # Fertilizer id of the last alert sent, so the notification fires once
        # per recommendation rather than on every NPK read.
        self._last_fert_alert_id: str | None = None
        # Month in which the pest-response monthly-limit alert last fired, so
        # the blocked alert is emitted once per month, not every cycle.
        self._pest_blocked_alert_month: str | None = None

    # ------------------------------------------------------------------
    # external wiring
    # ------------------------------------------------------------------

    def attach_detector(self, detector: Any) -> None:
        self._detector = detector

    def attach_camera(self, camera: Any) -> None:
        self._camera = camera

    def attach_stepper(self, stepper: Any) -> None:
        self._stepper = stepper

    def attach_recommender(self, recommender: Any) -> None:
        self._recommender = recommender

    def attach_treatment_advisor(self, advisor: Any) -> None:
        self._treatment_advisor = advisor

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._seed_watering_state()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="automation",
        )
        self._thread.start()
        self._state.set_automation(running=True)
        logger.info("Automation controller started")

    def stop(self) -> None:
        self._stop.set()
        # Ask any in-flight sweep to abort and retract.
        self._pest_abort.set()
        # Drain queued pest work so the worker can exit promptly.
        while True:
            try:
                self._pest_queue.get_nowait()
                self._pest_queue.task_done()
            except queue.Empty:
                break
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self._pest_worker is not None:
            self._pest_worker.join(timeout=30.0)
        if self._stepper is not None:
            try:
                self._stepper.off()
            except Exception:
                pass
        self._state.set_automation(running=False)
        logger.info("Automation controller stopped")

    def pause(self) -> None:
        self._state.set_automation(paused=True)
        logger.info("Automation paused")

    def resume(self) -> None:
        self._state.set_automation(paused=False)
        logger.info("Automation resumed")

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Unhandled exception in automation tick")
            self._stop.wait(timeout=self._tick_seconds)

    def _tick(self) -> None:
        if self._state.automation.get("paused"):
            return

        now = time.monotonic()

        # 1. NPK sensor
        npk_cfg = self._cfg.get("npk", {})
        if npk_cfg.get("enabled") and self._npk_sensor is not None:
            interval = float(npk_cfg.get("read_interval_seconds", 300))
            if now - self._last_npk_read >= interval:
                self._read_npk(now, npk_cfg)

        # 2. Soil moisture sensor + watering rule
        moist_cfg = self._cfg.get("soil_moisture", {})
        if moist_cfg.get("enabled") and self._moisture_sensor is not None:
            interval = float(moist_cfg.get("read_interval_seconds", 30))
            if now - self._last_moisture_read >= interval:
                self._read_moisture(now, moist_cfg)
            self._evaluate_watering(now, moist_cfg)

        # 3. Pest monitoring — schedule only; the dedicated worker does the
        #    long-running (blocking) stepper sweep so this tick stays fast.
        pest_cfg = self._cfg.get("pest_detection", {})
        if pest_cfg.get("enabled") and self._detector is not None:
            interval = float(pest_cfg.get("capture_interval_seconds", 600))
            if now - self._last_pest_monitor >= interval:
                self._last_pest_monitor = now
                mode = "sweep" if self._stepper is not None else "camera"
                self._enqueue_pest_task(mode, "automatic")

        # 4. Database health check (infrequent — every 60 ticks ≈ 60 s)
        self._check_db(now)

    # ------------------------------------------------------------------
    # NPK reading
    # ------------------------------------------------------------------

    def _read_npk(self, now: float, cfg: dict) -> None:
        try:
            reading = self._npk_sensor.read()
        except SensorError as exc:
            ts = datetime.now().isoformat(timespec="seconds")
            self._state.update_npk(
                values={}, below={}, timestamp=ts, error=str(exc),
            )
            self._log_event("WARNING", "npk", str(exc))
            return

        values = reading.values
        thresholds = cfg.get("thresholds", {})
        below = {
            "nitrogen": values.get("nitrogen", 0) < thresholds.get("nitrogen", 0),
            "phosphorus": values.get("phosphorus", 0) < thresholds.get("phosphorus", 0),
            "potassium": values.get("potassium", 0) < thresholds.get("potassium", 0),
        }
        ts = reading.timestamp.isoformat(timespec="seconds")
        self._state.update_npk(values=values, below=below, timestamp=ts)
        self._last_npk_read = now

        # Database log — governed by its own (slower) interval.
        log_interval = float(cfg.get("log_interval_seconds", 900))
        if now - self._last_npk_log >= log_interval:
            try:
                self._db.insert_npk_reading(
                    nitrogen=values.get("nitrogen", 0),
                    phosphorus=values.get("phosphorus", 0),
                    potassium=values.get("potassium", 0),
                    below=below,
                    timestamp=ts,
                )
                self._state.update_db(ok=True, last_write=ts)
                self._last_npk_log = now
            except DatabaseError as exc:
                self._state.update_db(ok=False, error=str(exc))
                logger.error("DB write failed (npk): %s", exc)

        # Alert only on transition to below-threshold.
        any_below = any(below.values())
        if any_below and not self._npk_was_below:
            flagged = [k for k, v in below.items() if v]
            self._state.add_alert(
                "WARNING", f"NPK below threshold: {', '.join(flagged)}", "npk",
            )
            self._log_event("WARNING", "npk", f"Below threshold: {', '.join(flagged)}")
        self._npk_was_below = any_below

        # NPK-based fertilizer recommendation
        if self._recommender is not None:
            try:
                result = self._recommender.recommend(values)
                if result.recommended:
                    self._state.update_recommendation(
                        recommended=True,
                        fertilizer_id=result.fertilizer_id,
                        fertilizer_name=result.fertilizer_name,
                        dosage_g_per_10L=result.dosage_g_per_10L,
                        deficits=result.deficits,
                        reasoning=result.reasoning,
                        priority=result.priority,
                        timestamp=result.timestamp.isoformat(timespec="seconds"),
                        model=result.model,
                    )
                    # Persist recommendation
                    self._db.insert_npk_recommendation(
                        nitrogen=values.get("nitrogen", 0),
                        phosphorus=values.get("phosphorus", 0),
                        potassium=values.get("potassium", 0),
                        fertilizer_id=result.fertilizer_id,
                        fertilizer_name=result.fertilizer_name,
                        dosage_g_per_10L=result.dosage_g_per_10L,
                        deficits=result.deficits,
                        reasoning=result.reasoning,
                        priority=result.priority,
                        model=result.model,
                        timestamp=ts,
                    )
                    self._log_event("INFO", "npk_recommendation",
                        f"Recommended {result.fertilizer_name} @ {result.dosage_g_per_10L}g/10L "
                        f"({result.reasoning})")
                    # Notify once per new/changed recommendation.
                    if result.fertilizer_id != self._last_fert_alert_id:
                        self._last_fert_alert_id = result.fertilizer_id
                        self._state.add_alert(
                            "INFO",
                            f"Fertilizer recommended: {result.fertilizer_name} "
                            f"({result.dosage_g_per_10L}g/10L)",
                            "npk_recommendation",
                        )
                else:
                    # Clear recommendation when all nutrients in range
                    self._state.update_recommendation(recommended=False)
                    self._last_fert_alert_id = None
            except Exception as exc:
                logger.error("NPK recommendation failed: %s", exc)
                self._state.update_recommendation(recommended=False, error=str(exc))

    # ------------------------------------------------------------------
    # Moisture reading + watering rule
    # ------------------------------------------------------------------

    def _read_moisture(self, now: float, cfg: dict) -> None:
        try:
            reading = self._moisture_sensor.read()
        except SensorError as exc:
            ts = datetime.now().isoformat(timespec="seconds")
            self._state.update_moisture(value=0, below=False, timestamp=ts, error=str(exc))
            self._log_event("WARNING", "moisture", str(exc))
            return

        value = reading.values.get("moisture", 0)
        threshold = float(cfg.get("threshold", 30))
        prev_below = bool(self._state.moisture.get("below"))
        hysteresis = float(cfg.get("hysteresis", 5))
        if prev_below:
            below = value < (threshold + hysteresis)
        else:
            below = value < threshold
        ts = reading.timestamp.isoformat(timespec="seconds")
        self._state.update_moisture(value=value, below=below, timestamp=ts)
        self._last_moisture_read = now

        # Database log
        log_interval = float(cfg.get("log_interval_seconds", 300))
        if now - self._last_moisture_log >= log_interval:
            try:
                self._db.insert_moisture_reading(moisture=value, below=below, timestamp=ts)
                self._state.update_db(ok=True, last_write=ts)
                self._last_moisture_log = now
            except DatabaseError as exc:
                self._state.update_db(ok=False, error=str(exc))
                logger.error("DB write failed (moisture): %s", exc)

        # Transition alert
        if below and not self._moisture_was_below:
            self._state.add_alert("WARNING", f"Soil moisture low: {value:.1f}% (threshold {threshold}%)", "moisture")
            self._log_event("WARNING", "moisture", f"Below threshold: {value:.1f}%")
        self._moisture_was_below = below

    def _sync_watering_state(self, now: float, cfg: dict) -> None:
        """Keep cooldown countdown and daily budget display live (every tick)."""
        remaining = max(0.0, self._watering_cooldown_until - now)

        relay_id = int(cfg.get("relay_id", 2))
        relay_entry = self._relays.get(relay_id)
        if relay_entry:
            duration = float(relay_entry["duration"])
        else:
            duration = float(cfg.get("relay_activation_duration_seconds", 300))

        if now - self._last_watering_budget_read >= 30.0:
            self._watering_budget_used = self._watering_seconds_today(now)
            self._last_watering_budget_read = now

        budget_max = float(cfg.get("max_watering_seconds_per_day", 1800))
        blocked = (self._watering_budget_used + duration) > budget_max

        current = self._state.watering
        current_budget = current.get("budget") or {}
        changed = (
            round(remaining) != round(current.get("cooldown_remaining_seconds", 0))
            or self._watering_budget_used != current_budget.get("used_seconds")
            or budget_max != current_budget.get("max_seconds")
            or max(0.0, budget_max - self._watering_budget_used) != current_budget.get("remaining_seconds")
            or blocked != current_budget.get("blocked")
        )
        if changed:
            self._state.update_watering(
                cooldown_remaining_seconds=remaining,
                budget={
                    "used_seconds": self._watering_budget_used,
                    "max_seconds": budget_max,
                    "remaining_seconds": max(0.0, budget_max - self._watering_budget_used),
                    "blocked": blocked,
                },
            )

    def _evaluate_watering(self, now: float, cfg: dict) -> None:
        """Decide whether to auto-water based on current moisture state."""
        moist = self._state.moisture
        value = moist.get("value")
        prev_error = moist.get("error")

        relay_id = int(cfg.get("relay_id", 2))

        self._sync_watering_state(now, cfg)

        # (a) FAULT LATCH — while faulted, never water.
        if self._watering_fault:
            if not moist.get("below"):
                self._watering_fault = False
                self._watering_non_responses = 0
                self._water_baseline_value = None
                self._state.update_watering(
                    fault=False, fault_reason=None, non_responses=0,
                )
                self._state.add_alert("INFO", "Watering fault cleared: soil moisture recovered", "moisture")
                self._log_event("INFO", "watering", "Watering fault auto-cleared (soil recovered)")
            return

        # (b) RESPONSE EVALUATION — once per watering, after observation window.
        if self._water_baseline_value is not None and now >= self._watering_cooldown_until:
            self._evaluate_watering_response(value, prev_error, cfg)
            if self._watering_fault:
                return

        # (c) Normal guards
        if not moist.get("below"):
            return
        if now < self._watering_cooldown_until:
            return
        if self._relays.is_active(relay_id):
            return

        # (d) HARD DAILY BUDGET
        relay_entry = self._relays.get(relay_id)
        if relay_entry:
            duration = float(relay_entry["duration"])
        else:
            duration = float(cfg.get("relay_activation_duration_seconds", 300))
        used_today = self._watering_budget_used
        budget_max = float(cfg.get("max_watering_seconds_per_day", 1800))
        if used_today + duration > budget_max:
            today_str = datetime.now().date().isoformat()
            if self._watering_blocked_alert_day != today_str:
                self._watering_blocked_alert_day = today_str
                msg = "Daily watering budget exceeded; watering blocked"
                self._state.add_alert("WARNING", msg, "watering")
                self._log_event("WARNING", "watering", msg)
            return

        # (e) ACTIVATE
        activated = self._relays.activate(
            relay_id, duration=duration, trigger="automatic",
        )
        if activated:
            ts = datetime.now().isoformat(timespec="seconds")
            self._state.set_relay(
                relay_id, state="on", activated_at=ts, duration=duration,
            )
            try:
                self._db.insert_relay_activation(
                    relay_id=relay_id,
                    relay_name="watering",
                    trigger_type="automatic",
                    duration_seconds=int(duration),
                    source="automation",
                    timestamp=ts,
                )
            except DatabaseError as exc:
                logger.error("DB write failed (relay activation): %s", exc)
            self._log_event("INFO", "watering", f"Auto-watering started ({duration:.0f}s)")

            self._water_baseline_value = value
            cooldown = float(cfg.get("watering_cooldown_seconds", 900))
            self._watering_cooldown_until = now + cooldown
            self._watering_budget_used += duration
            self._state.update_watering(
                non_responses=self._watering_non_responses,
                fault=self._watering_fault,
                cooldown_seconds=cooldown,
                cooldown_remaining_seconds=cooldown,
            )

    def _evaluate_watering_response(self, value: Any, error: Any, cfg: dict) -> None:
        """Evaluate whether the last watering had an effect."""
        if error is not None or value is None:
            # Untrustworthy reading — do not blame the pump.
            self._water_baseline_value = None
            self._state.update_watering(
                non_responses=self._watering_non_responses,
                fault=self._watering_fault,
                fault_reason=self._state.watering.get("fault_reason"),
            )
            return

        baseline = self._water_baseline_value
        rise = 0.0 if baseline is None else (value - baseline)
        response_rise = float(cfg.get("response_rise_percent", 2.0))
        max_non = int(cfg.get("max_consecutive_non_responses", 3))

        if rise >= response_rise:
            self._watering_non_responses = 0
        else:
            self._watering_non_responses += 1
            if self._watering_non_responses >= max_non:
                self._watering_fault = True
                reason = (
                    "The soil isn't getting wetter after watering. "
                    "Check the pump, the water line, or the probe."
                )
                self._state.update_watering(
                    fault=True, fault_reason=reason,
                    non_responses=self._watering_non_responses,
                )
                self._state.add_alert("CRITICAL", reason, "moisture")
                self._log_event("CRITICAL", "watering", reason)
                self._water_baseline_value = None
                return

        self._water_baseline_value = None
        self._state.update_watering(
            non_responses=self._watering_non_responses,
            fault=self._watering_fault,
            fault_reason=self._state.watering.get("fault_reason"),
        )

    def _watering_seconds_today(self, now: Any) -> float:
        """Total watering seconds recorded today in the DB."""
        try:
            moist_cfg = self._cfg.get("soil_moisture", {})
            relay_id = int(moist_cfg.get("relay_id", 2))
            since = datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0,
            ).isoformat(sep=" ", timespec="seconds")
            return float(self._db.watering_seconds_since(relay_id, since))
        except DatabaseError as exc:
            logger.error("DB query failed (watering seconds today): %s", exc)
            return 0.0

    def _seed_watering_state(self) -> None:
        """Seed cooldown/budget state from DB so a restart cannot bypass them."""
        moist_cfg = self._cfg.get("soil_moisture", {})
        cooldown = float(moist_cfg.get("watering_cooldown_seconds", 900))
        try:
            self._state.update_watering(cooldown_seconds=cooldown)
            relay_id = int(moist_cfg.get("relay_id", 2))
            last = self._db.last_relay_activation(relay_id)
            if last is not None:
                ts = last.get("timestamp")
                if ts:
                    try:
                        activated_at = datetime.fromisoformat(ts)
                    except (TypeError, ValueError):
                        activated_at = None
                    if activated_at is not None:
                        elapsed = (datetime.now() - activated_at).total_seconds()
                        if elapsed < cooldown:
                            remaining = cooldown - elapsed
                            self._watering_cooldown_until = time.monotonic() + remaining
                            self._state.update_watering(
                                cooldown_remaining_seconds=remaining,
                            )
            budget_max = float(moist_cfg.get("max_watering_seconds_per_day", 1800))
            used_today = self._watering_seconds_today(time.monotonic())
            self._watering_budget_used = used_today
            self._last_watering_budget_read = time.monotonic()
            self._state.update_watering(budget={
                "used_seconds": used_today,
                "max_seconds": budget_max,
                "remaining_seconds": max(0.0, budget_max - used_today),
                "blocked": False,
            })
        except DatabaseError as exc:
            logger.error("Failed to seed watering state: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to seed watering state: %s", exc)

    def reset_watering_fault(self) -> tuple[bool, str]:
        """Clear a latched watering fault. Never raises."""
        try:
            self._watering_fault = False
            self._watering_non_responses = 0
            self._water_baseline_value = None
            self._state.update_watering(
                fault=False, fault_reason=None, non_responses=0,
            )
            self._log_event("INFO", "watering", "Watering fault reset by operator")
            return True, "Watering fault cleared"
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to reset watering fault: %s", exc)
            return False, str(exc)

    # ------------------------------------------------------------------
    # Pest detection cycle
    # ------------------------------------------------------------------

    def request_pest_scan(self, with_stepper: bool = False) -> tuple[bool, str]:
        """Queue an immediate pest operation for the background worker.

        ``with_stepper`` runs a full camera sweep (motor out and back);
        otherwise a single camera capture is analysed. Respects pause, the
        enabled flag, an attached detector, and one-at-a-time execution.
        Returns ``(accepted, human message)``.
        """
        pest_cfg = self._cfg.get("pest_detection", {})
        if not pest_cfg.get("enabled"):
            return False, "Pest control is turned off."
        if self._detector is None:
            return False, "No pest detector is loaded."
        if self._state.automation.get("paused"):
            return False, "Automation is paused."
        if with_stepper and self._stepper is None:
            return False, "Stepper motor is not available."
        if self._pest_active:
            return False, "A pest scan is already running."

        mode = "sweep" if with_stepper else "camera"
        source = "dashboard_sweep" if with_stepper else "dashboard_camera"
        if not self._enqueue_pest_task(mode, source):
            return False, "A pest scan is already running."
        if with_stepper:
            return True, "Sweep requested — the camera is moving; results appear shortly."
        return True, "Camera scan requested — results appear shortly."

    # ------------------------------------------------------------------
    # pest worker (sweeps and camera-only scans run off the tick thread)
    # ------------------------------------------------------------------

    def _ensure_pest_worker(self) -> None:
        if self._pest_worker is not None and self._pest_worker.is_alive():
            return
        self._pest_worker = threading.Thread(
            target=self._pest_worker_loop, daemon=True, name="pest-worker",
        )
        self._pest_worker.start()

    def _enqueue_pest_task(self, mode: str, source: str) -> bool:
        """Reserve the worker (if idle) and queue one task. False if busy."""
        with self._pest_lock:
            if self._pest_active:
                return False
            self._pest_active = True
        self._ensure_pest_worker()
        self._pest_queue.put(PestTask(mode=mode, source=source))
        return True

    def _pest_worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                task = self._pest_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            self._pest_abort.clear()
            try:
                if task.mode == "sweep":
                    self._run_sweep(task)
                else:
                    self._run_camera_only(task)
            except Exception:
                logger.exception("Pest worker task (%s) failed", task.mode)
            finally:
                self._pest_queue.task_done()
                with self._pest_lock:
                    if self._pest_queue.empty():
                        self._pest_active = False

    def _sweep_geometry(self) -> tuple[int, float]:
        """Derive the one-way extension step count and step delay from config."""
        stepper_cfg = self._cfg.get("stepper", {})

        def _clamp(value: float, low: float, high: float) -> float:
            return max(low, min(value, high))

        wheel = _clamp(float(stepper_cfg.get("wheel_diameter_in", 2.125)), 0.5, 10.0)
        dist = _clamp(float(stepper_cfg.get("extension_distance_in", 36.0)), 1.0, 120.0)
        steps_per_rev = int(_clamp(float(stepper_cfg.get("steps_per_rev", 2048)), 200, 8000))
        delay = _clamp(float(stepper_cfg.get("delay", 0.003)), 0.0005, 0.05)

        revolutions = dist / (math.pi * wheel)
        total_steps = int(round(revolutions * steps_per_rev))
        return total_steps, delay

    def _capture_and_detect(self, save_path: Path | None = None) -> Any:
        """One capture + detect. Returns a DetectionResult, or None on error.

        ``save_path`` lets a sweep overwrite a single temp frame instead of
        writing one image per analysis to disk.
        """
        image_path = None
        if self._camera is not None:
            try:
                image_path = self._camera.capture(save_path=save_path)
            except Exception as exc:
                self._state.update_camera(error=str(exc))
                self._log_event("ERROR", "camera", str(exc))
                return None
        try:
            return self._detector.detect(image_path=image_path)
        except Exception as exc:
            self._state.update_pest(error=str(exc), suggestion=None)
            self._log_event("ERROR", "pest_detection", str(exc))
            return None

    def _run_sweep(self, task: PestTask) -> None:
        """Drive the camera out and back, analysing frames as it moves.

        A positive detection aborts the outward leg and retracts immediately;
        the response fires once per sweep (monthly limit still applies). The
        camera is always retracted to home, even on error or abort.
        """
        pest_cfg = self._cfg.get("pest_detection", {})
        total_steps, delay = self._sweep_geometry()
        if total_steps <= 0 or total_steps > _MAX_SWEEP_STEPS:
            msg = f"Stepper sweep skipped: {total_steps} steps is outside the safe range"
            self._state.add_alert("ERROR", msg, "pest_detection")
            self._log_event("ERROR", "pest_detection", msg)
            return

        analysis_interval = max(0.1, float(pest_cfg.get("analysis_interval_seconds", 1.0)))
        # StepperMotor.step() sleeps `delay` after each of 4 phases per step,
        # so this many steps is roughly one analysis interval of motion.
        steps_per_chunk = max(1, int(round(analysis_interval / (4.0 * delay))))
        threshold = float(pest_cfg.get("confidence_threshold", 0.70))

        sweep_path = Path(tempfile.gettempdir()) / "veggiecare_sweep.jpg"
        extended = 0
        chunks = 0
        detection = None
        motion_error = None

        try:
            while (
                extended < total_steps
                and not self._pest_abort.is_set()
                and not self._stop.is_set()
            ):
                chunk = min(steps_per_chunk, total_steps - extended)
                self._stepper.step(steps=chunk, delay=delay, clockwise=True)
                extended += chunk
                chunks += 1
                result = self._capture_and_detect(save_path=sweep_path)
                if result is not None and result.detected and (
                    result.confidence is None or result.confidence >= threshold
                ):
                    detection = result
                    break
        except Exception as exc:
            logger.exception("Pest sweep motion failed")
            motion_error = str(exc)
        finally:
            try:
                if extended > 0 and self._stepper is not None:
                    self._stepper.step(steps=extended, delay=delay, clockwise=False)
            except Exception:
                logger.exception("Pest sweep retract failed")
            finally:
                if self._stepper is not None:
                    try:
                        self._stepper.off()
                    except Exception:
                        pass

        self._log_event(
            "INFO", task.source,
            f"Pest sweep finished ({chunks} analyses, {extended} steps out)",
        )
        self._handle_sweep_result(detection, task.source, error=motion_error)

    def _run_camera_only(self, task: PestTask) -> None:
        self._pest_cycle(
            time.monotonic(), self._cfg.get("pest_detection", {}), source=task.source,
        )

    def _handle_sweep_result(self, result: Any, source: str, error: str | None = None) -> None:
        """Record one summary row per sweep and respond once when detected."""
        pest_cfg = self._cfg.get("pest_detection", {})
        threshold = float(pest_cfg.get("confidence_threshold", 0.70))
        detected = bool(
            result is not None
            and result.detected
            and (result.confidence is None or result.confidence >= threshold)
        )
        ts = datetime.now().isoformat(timespec="seconds")
        model = result.model if result is not None else getattr(self._detector, "name", "unknown")
        image_path = getattr(result, "image_path", None) if result is not None else None

        suggestion = None
        if self._treatment_advisor is not None and detected:
            try:
                advice = self._treatment_advisor.suggest(result.pest_class if result is not None else None)
                suggestion = advice.to_dict() if advice is not None else None
            except Exception as exc:
                logger.error("Treatment advice failed: %s", exc)
                suggestion = None

        self._state.update_pest(
            detected=detected,
            pest_class=result.pest_class if detected else None,
            confidence=result.confidence if detected else None,
            image_path=image_path,
            timestamp=ts,
            model=model,
            error=error,
            configured=True,
            suggestion=suggestion,
        )

        try:
            self._db.insert_pest_detection(
                detected=detected,
                pest_class=result.pest_class if detected else None,
                confidence=result.confidence if detected else None,
                image_path=image_path,
                model=model,
                camera=getattr(self._camera, "name", None) if self._camera else None,
                timestamp=ts,
                suggestion=suggestion,
            )
            self._state.update_db(ok=True, last_write=ts)
        except DatabaseError as exc:
            self._state.update_db(ok=False, error=str(exc))
            logger.error("DB write failed (pest sweep): %s", exc)

        if not detected:
            return

        alert_msg = f"Pest detected: {result.pest_class} (confidence {result.confidence:.2f})"
        self._state.add_alert("WARNING", alert_msg, source)
        self._log_event("WARNING", source, alert_msg)
        self._notify_pesticide(suggestion, source)

        relay_id = int(pest_cfg.get("relay_id", 3))
        duration = float(pest_cfg.get("relay_activation_duration_seconds", 120))
        for item in self._cfg.get("relays", {}).get("items", []):
            if int(item.get("id", 0)) == relay_id:
                duration = float(item.get("activation_duration_seconds", duration))
                break
        self._activate_pest_response(relay_id, duration, ts, source)

    def _pest_cycle(self, now: float, cfg: dict, source: str = "pest_detection") -> None:
        self._last_pest_cycle = now

        # Capture
        image_path = None
        if self._camera is not None:
            try:
                image_path = self._camera.capture()
            except Exception as exc:
                self._state.update_camera(error=str(exc))
                self._log_event("ERROR", "camera", str(exc))
                return

        # Detect
        try:
            result = self._detector.detect(image_path=image_path)
        except Exception as exc:
            self._state.update_pest(error=str(exc), suggestion=None)
            self._log_event("ERROR", "pest_detection", str(exc))
            return

        ts = result.timestamp.isoformat(timespec="seconds") if result.timestamp else datetime.now().isoformat(timespec="seconds")
        confidence_threshold = float(cfg.get("confidence_threshold", 0.70))

        effective_detected = (
            result.detected
            and (result.confidence is None or result.confidence >= confidence_threshold)
        )

        suggestion = None
        if self._treatment_advisor is not None and effective_detected:
            try:
                advice = self._treatment_advisor.suggest(result.pest_class)
                suggestion = advice.to_dict() if advice is not None else None
            except Exception as exc:
                logger.error("Treatment advice failed: %s", exc)
                suggestion = None

        self._state.update_pest(
            detected=effective_detected,
            pest_class=result.pest_class,
            confidence=result.confidence,
            image_path=result.image_path,
            timestamp=ts,
            model=result.model,
            error=None,
            configured=True,
            suggestion=suggestion,
        )

        # Database record
        try:
            self._db.insert_pest_detection(
                detected=effective_detected,
                pest_class=result.pest_class,
                confidence=result.confidence,
                image_path=result.image_path,
                model=result.model,
                camera=getattr(self._camera, "name", None) if self._camera else None,
                timestamp=ts,
                suggestion=suggestion,
            )
            self._state.update_db(ok=True, last_write=ts)
        except DatabaseError as exc:
            self._state.update_db(ok=False, error=str(exc))
            logger.error("DB write failed (pest detection): %s", exc)

        # Alert + Relay 3
        if effective_detected:
            alert_msg = f"Pest detected: {result.pest_class} (confidence {result.confidence:.2f})"
            self._state.add_alert("WARNING", alert_msg, source)
            self._log_event("WARNING", source, alert_msg)
            self._notify_pesticide(suggestion, source)

            relay_id = int(cfg.get("relay_id", 3))
            # Use the relay's own configured duration
            relay_cfg = self._cfg.get("relays", {}).get("items", [])
            duration = float(cfg.get("relay_activation_duration_seconds", 120))
            for item in relay_cfg:
                if int(item.get("id", 0)) == relay_id:
                    duration = float(item.get("activation_duration_seconds", duration))
                    break

            self._activate_pest_response(relay_id, duration, ts, source)

    # ------------------------------------------------------------------
    # manual / external activation
    # ------------------------------------------------------------------

    def activate_relay(
        self,
        relay_id: int,
        duration: float | None = None,
        trigger: str = "manual",
        source: str = "dashboard",
    ) -> tuple[bool, str]:
        """Manually activate a relay. Returns (success, message)."""
        if not self._relays.available(relay_id):
            return False, f"Relay {relay_id} is not available"

        entry = self._relays.get(relay_id)
        if entry is None:
            return False, f"Relay {relay_id} not found"

        duration = duration or entry.get("duration", float(self._cfg.get("relays", {}).get("default_duration_seconds", 60)))
        activated = self._relays.activate(relay_id, duration=duration, trigger=trigger)
        if not activated:
            return False, "Relay activation failed"

        ts = datetime.now().isoformat(timespec="seconds")
        self._state.set_relay(
            relay_id, state="on", activated_at=ts, duration=duration,
        )

        relay_name = entry.get("name", f"relay_{relay_id}")
        try:
            self._db.insert_relay_activation(
                relay_id=relay_id,
                relay_name=relay_name,
                trigger_type="manual" if trigger == "manual" else "automatic",
                duration_seconds=int(duration),
                source=source,
                timestamp=ts,
            )
            self._state.update_db(ok=True, last_write=ts)
        except DatabaseError as exc:
            logger.error("DB write failed (manual activation): %s", exc)

        msg = f"Relay {relay_id} ({relay_name}) activated for {duration:.0f}s"
        self._state.add_alert("INFO", msg, source)
        self._log_event("INFO", source, msg)
        return True, msg

    def emergency_stop(self, source: str = "dashboard") -> None:
        """Force all relays OFF immediately."""
        self._relays.all_off(reason="emergency_stop")
        # Stop any sweep in progress; the worker retracts when it sees this.
        self._pest_abort.set()
        for rid in self._relays.relay_ids():
            self._state.set_relay(rid, state="off")
        self._state.add_alert("CRITICAL", "Emergency stop — all relays forced OFF", source)
        self._log_event("CRITICAL", "relay", "Emergency stop activated")

    # ------------------------------------------------------------------
    # simulated pest injection (for testing from dashboard)
    # ------------------------------------------------------------------

    def inject_pest_detection(
        self,
        *,
        detected: bool = True,
        pest_class: str = "aphid",
        confidence: float = 0.85,
        source: str = "dashboard_simulate",
    ) -> DetectionResult:
        """Manually inject a pest detection event (for testing only)."""
        from pest_detection.detector import DetectionResult

        result = DetectionResult(
            detected=detected,
            pest_class=pest_class if detected else None,
            confidence=confidence if detected else None,
            model="simulated_injection",
        )
        self._process_pest_result(result, source=source)
        return result

    def suggest_treatment(self, pest_class: str | None) -> dict | None:
        """Return treatment advice for a class, or None if the advisor is unavailable."""
        advisor = self._treatment_advisor
        if advisor is None:
            return None
        try:
            advice = advisor.suggest(pest_class)
            if advice is None:
                return None
            return advice.to_dict()
        except Exception as exc:
            logger.error("Treatment advice failed: %s", exc)
            return None

    def _process_pest_result(self, result: Any, source: str = "pest_detection") -> None:
        """Shared logic for handling a DetectionResult (real or injected)."""
        pest_cfg = self._cfg.get("pest_detection", {})
        ts = result.timestamp.isoformat(timespec="seconds") if hasattr(result, "timestamp") and result.timestamp else datetime.now().isoformat(timespec="seconds")
        confidence_threshold = float(pest_cfg.get("confidence_threshold", 0.70))

        effective_detected = (
            result.detected
            and (result.confidence is None or result.confidence >= confidence_threshold)
        )

        suggestion = None
        if self._treatment_advisor is not None and effective_detected:
            try:
                advice = self._treatment_advisor.suggest(result.pest_class)
                suggestion = advice.to_dict() if advice is not None else None
            except Exception as exc:
                logger.error("Treatment advice failed: %s", exc)
                suggestion = None

        self._state.update_pest(
            detected=effective_detected,
            pest_class=result.pest_class,
            confidence=result.confidence,
            image_path=getattr(result, "image_path", None),
            timestamp=ts,
            model=result.model,
            error=None,
            configured=True,
            suggestion=suggestion,
        )

        try:
            self._db.insert_pest_detection(
                detected=effective_detected,
                pest_class=result.pest_class,
                confidence=result.confidence,
                image_path=getattr(result, "image_path", None),
                model=result.model,
                camera="simulated",
                timestamp=ts,
                suggestion=suggestion,
            )
            self._state.update_db(ok=True, last_write=ts)
        except DatabaseError as exc:
            logger.error("DB write failed (pest detection): %s", exc)

        if effective_detected:
            alert_msg = f"Pest detected: {result.pest_class} (confidence {result.confidence:.2f})"
            self._state.add_alert("WARNING", alert_msg, source)
            self._log_event("WARNING", source, alert_msg)
            self._notify_pesticide(suggestion, source)

            relay_id = int(pest_cfg.get("relay_id", 3))
            relay_items = self._cfg.get("relays", {}).get("items", [])
            duration = float(pest_cfg.get("relay_activation_duration_seconds", 120))
            for item in relay_items:
                if int(item.get("id", 0)) == relay_id:
                    duration = float(item.get("activation_duration_seconds", duration))
                    break

            self._activate_pest_response(relay_id, duration, ts, source)

    @staticmethod
    def _pesticide_label(suggestion: Any) -> str | None:
        """Short product label from a treatment-advice dict, if any."""
        if not isinstance(suggestion, dict):
            return None
        if suggestion.get("matched") is False:
            return None
        for key in ("chemical", "organic"):
            items = suggestion.get(key)
            if isinstance(items, (list, tuple)) and items and isinstance(items[0], str):
                label = items[0].strip()
                if label:
                    return label
        title = suggestion.get("title")
        return title.strip() if isinstance(title, str) and title.strip() else None

    def _notify_pesticide(self, suggestion: Any, source: str) -> None:
        """Raise a notification for the recommended pesticide, if one exists."""
        label = self._pesticide_label(suggestion)
        if label is None:
            return
        self._state.add_alert("INFO", f"Pesticide suggested: {label}", "pest_treatment")

    def _activate_pest_response(self, relay_id: int, duration: float, ts: str, source: str) -> None:
        """Activate the pest response relay, subject to the monthly limit."""
        max_per_month = int(self._cfg.get("pest_detection", {}).get("max_activations_per_month", 2))

        # Monthly activation limit — the ONLY limit in the system. NPK alerts
        # and soil-moisture watering are never limited.
        year_month = datetime.now()
        year, month = year_month.year, year_month.month
        try:
            used = self._db.count_relay_activations(relay_id, year, month)
        except DatabaseError:
            used = 0
        remaining = max(0, max_per_month - used)
        month_str = f"{year:04d}-{month:02d}"
        self._state.update_usage(month=month_str, used=used, max_activations=max_per_month)

        if remaining <= 0:
            if self._pest_blocked_alert_month != month_str:
                self._pest_blocked_alert_month = month_str
                reason = f"Monthly pest response limit reached ({used}/{max_per_month})"
                self._state.add_alert("WARNING", reason, source)
                try:
                    self._db.insert_blocked_activation(relay_id=relay_id, reason=reason)
                except DatabaseError as exc:
                    logger.error("DB write failed (blocked activation): %s", exc)
                self._log_event("WARNING", source, reason)
            return

        activated = self._relays.activate(
            relay_id, duration=duration, trigger="automatic",
        )
        if activated:
            self._state.set_relay(
                relay_id, state="on", activated_at=ts, duration=duration,
            )
            try:
                self._db.insert_relay_activation(
                    relay_id=relay_id,
                    relay_name="pest_response",
                    trigger_type="automatic",
                    duration_seconds=int(duration),
                    source=source,
                    timestamp=ts,
                )
            except DatabaseError as exc:
                logger.error("DB write failed (relay activation): %s", exc)
            try:
                new_used = self._db.count_relay_activations(relay_id, year, month)
            except DatabaseError:
                new_used = used + 1
            self._state.update_usage(month=month_str, used=new_used, max_activations=max_per_month)
            self._log_event("INFO", source, f"Relay {relay_id} activated ({duration:.0f}s)")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _log_event(self, level: str, source: str, message: str) -> None:
        """Write to the system_events table; never let a DB error propagate."""
        try:
            self._db.insert_event(level=level, source=source, message=message)
            self._state.update_db(ok=True)
        except DatabaseError as exc:
            self._state.update_db(ok=False, error=str(exc))
            logger.error("DB write failed (event log): %s", exc)

    def _check_db(self, now: float) -> None:
        """Lightweight DB health check, runs once per ~60 seconds."""
        if not hasattr(self, "_last_db_check"):
            self._last_db_check = 0.0
        if now - self._last_db_check < 60:
            return
        self._last_db_check = now
        ok, err = self._db.health()
        self._state.update_db(ok=ok, error=err)