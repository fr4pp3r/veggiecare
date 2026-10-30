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

import inspect
import logging
import math
import queue
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
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

        # NPK sensor availability tracking: when a read fails, retry sooner
        self._npk_sensor_available = True
        self._npk_retry_interval = 30.0  # seconds between retries after failure
        self._npk_last_failed_read = 0.0
        self._npk_connected = False
        self._npk_connected = False

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
        # Serialises the pest-response gate (limit check -> activate -> record).
        # The pest worker and the dashboard's simulate route are different
        # threads, so without this both could pass the monthly check and
        # double-activate (and double-debit the tank).
        self._pest_response_lock = threading.Lock()

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
        self._seed_tank_state()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="automation",
        )
        self._thread.start()
        self._state.set_automation(running=True)
        logger.info("Automation controller started")
        # Force an immediate NPK read on startup so the sensor is read
        # without waiting for the configured interval.
        if self._npk_sensor is not None:
            npk_cfg = self._cfg.get("npk", {})
            if npk_cfg.get("enabled"):
                try:
                    self._read_npk(time.monotonic(), npk_cfg)
                except Exception:
                    logger.exception("Initial NPK read failed")

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
            # If the sensor was previously unavailable, retry more frequently
            if not self._npk_sensor_available:
                retry_interval = self._npk_retry_interval
                if now - self._npk_last_failed_read >= retry_interval:
                    self._read_npk(now, npk_cfg)
            else:
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
            self._npk_connected = False
            self._log_event("WARNING", "npk", str(exc))
            # Mark sensor as unavailable; next tick will retry sooner.
            if self._npk_sensor_available:
                self._npk_sensor_available = False
                logger.warning("NPK sensor unavailable: %s", exc)
            self._npk_last_failed_read = now
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

        # Sensor came back online after being unavailable.
        if not self._npk_sensor_available:
            self._npk_sensor_available = True
            self._npk_connected = True
            logger.info("NPK sensor is now available")
        else:
            self._npk_connected = True

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

        # Explain why a due fertilization has not been applied yet.
        self._refresh_fertilization_gate_reason()

    @staticmethod
    def _soak_active(ft: dict) -> bool:
        """True while the fertilizer soak cooldown is still in effect."""
        if float(ft.get("soak_days", 0) or 0) <= 0:
            return False
        until = ft.get("cooldown_until_at")
        if not until:
            return False
        try:
            return datetime.fromisoformat(str(until)) > datetime.now()
        except Exception:
            return False

    def _refresh_fertilization_gate_reason(self) -> None:
        """Keep the dashboard's "why is nothing being applied?" answer honest.

        With ``mode: with_watering`` a dose only rides along with an irrigation
        cycle, so a full tank can sit unused while nutrients stay deficient.
        Record the specific blocking reason instead of leaving it to guesswork.
        """
        tanks = getattr(self._state, "tanks", None)
        if not isinstance(tanks, dict):
            return
        ft = tanks.get("fertilizer", {}) or {}
        if not ft.get("enabled") or not ft.get("auto_enabled"):
            return
        if ft.get("mode") != "with_watering":
            return

        if not (self._state.recommendation or {}).get("recommended"):
            # Nothing is due; clear a stale deferral note.
            if ft.get("blocked_reason") == "fertilization_deferred_no_watering":
                self._state.update_tank("fertilizer", blocked_reason=None)
            return

        pump_lph = float(ft.get("pump_lph", 800) or 800)
        required_ml = float(ft.get("fert_ml_per_cycle", 250) or 250)
        min_dose_seconds = float(ft.get("min_dose_seconds", 1.0) or 0)
        dose_seconds = max(self._seconds_for_ml(required_ml, pump_lph), min_dose_seconds)
        dose_ml = self._ml_from_seconds(dose_seconds, pump_lph)

        if float(ft.get("ml_remaining_est", 0) or 0) <= 0:
            reason = "fertilizer_needs_refill"
        elif float(ft.get("ml_remaining_est", 0) or 0) < dose_ml:
            reason = "fertilizer_insufficient_for_cycle"
        elif self._soak_active(ft):
            reason = "fertilizer_soak_cooldown"
        else:
            reason = "fertilization_deferred_no_watering"
        if ft.get("blocked_reason") != reason:
            self._state.update_tank("fertilizer", blocked_reason=reason)

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

        budget_max = self._daily_watering_budget(cfg)
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
        budget_max = self._daily_watering_budget(cfg)

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
            try:
                self._maybe_apply_fertigation(now, duration)
            except Exception:
                pass

    def _maybe_apply_fertigation(self, now: float, watering_duration: float) -> None:
        tanks = getattr(self._state, "tanks", {})
        ft = tanks.get("fertilizer", {}) if isinstance(tanks, dict) else {}
        if not ft.get("enabled") or not ft.get("auto_enabled"):
            return
        if ft.get("mode") != "with_watering":
            # Fertilizer is configured for a standalone dose but only
            # fertigation is implemented; say so rather than failing silently.
            self._block_fertilization(
                "fertilizer_mode_separate_unsupported", self._fert_available(ft),
                record_db=False,
            )
            return
        available_ml = self._fert_available(ft)
        pump_lph = float(ft.get("pump_lph", 800) or 800)
        required_ml = float(ft.get("fert_ml_per_cycle", 250) or 250)
        min_dose_seconds = float(ft.get("min_dose_seconds", 1.0) or 0)
        min_sec = float(ft.get("min_watering_seconds_for_fert", 15) or 15)
        soak_days = float(ft.get("soak_days", 0) or 0)
        # Physical floor: an 800 L/H pump cannot reliably meter below the
        # minimum pulse, so never command less than min_dose_seconds.
        dose_seconds = max(self._seconds_for_ml(required_ml, pump_lph), min_dose_seconds)
        if available_ml <= 0:
            self._block_fertilization("fertilizer_needs_refill", available_ml)
            return
        if self._soak_active(ft):
            # Recurring during the soak window: surface it, but do not fill
            # the audit table with one row per watering cycle.
            self._block_fertilization(
                "fertilizer_soak_cooldown", available_ml, record_db=False,
            )
            return
        if watering_duration < max(min_sec, dose_seconds):
            # The irrigation window cannot fit even one metered dose. Truncating
            # would command a sub-floor pulse, so skip instead.
            self._block_fertilization(
                "fertilizer_watering_too_short", available_ml, record_db=False,
            )
            return
        dose_ml = self._ml_from_seconds(dose_seconds, pump_lph)
        if available_ml < dose_ml:
            self._block_fertilization(
                "fertilizer_insufficient_for_cycle", available_ml, dose_ml,
            )
            return
        # activate fertilizer relay (Relay 1) for dose_seconds
        fert_cfg = self._cfg.get("relays", {}).get("items", [])
        fert_relay_id = None
        for item in fert_cfg:
            if str(item.get("name", "")).lower() in ("fertilizer", "npk/fertilizer"):
                fert_relay_id = int(item.get("id", 1))
                break
        if fert_relay_id is None:
            fert_relay_id = 1
        activated = self._relays.activate(fert_relay_id, duration=dose_seconds, trigger="automatic")
        if not activated:
            self._block_fertilization("fertilizer_relay_unavailable", available_ml)
            return
        ts = datetime.now().isoformat(timespec="seconds")
        # activate() clamps to the watchdog cap — bill what will really pump.
        effective = self._effective_relay_duration(fert_relay_id, dose_seconds)
        ml_disp = self._ml_from_seconds(effective, pump_lph)
        self._state.set_relay(
            fert_relay_id, state="on", activated_at=ts, duration=effective,
        )
        try:
            self._db.insert_relay_activation(
                relay_id=fert_relay_id,
                relay_name="fertilizer",
                trigger_type="automatic",
                duration_seconds=int(round(effective)),
                source="automation_fertigation",
                timestamp=ts,
            )
        except DatabaseError as exc:
            logger.error("DB write failed (fert relay activation): %s", exc)
        ml_remain = available_ml - ml_disp
        if ml_remain < 0:
            ml_remain = 0
        updates = {
            "ml_dispensed_since_refill": float(ft.get("ml_dispensed_since_refill", 0) or 0) + ml_disp,
            "ml_remaining_est": ml_remain,
            "last_fertilized_at": ts,
            "cooldown_until_at": (
                datetime.now() + timedelta(days=soak_days)
            ).isoformat(timespec="seconds"),
            "blocked_reason": None,
            "blocked_until_at": None,
            "status": (
                "empty" if ml_remain <= 0
                else "low" if ml_remain < dose_ml
                else "full"
            ),
        }
        self._state.update_tank("fertilizer", **updates)
        try:
            self._db.insert_tank_auto_dose(
                "fertilizer",
                tank_type="fertilizer",
                ml_dispensed=ml_disp,
                activation_seconds=effective,
                combined_with_watering=True,
                mode="with_watering",
                ml_remaining_est=ml_remain,
            )
        except Exception:
            pass
        self._log_event(
            "INFO", "fertigation",
            f"Fertigation applied {ml_disp:.0f} mL ({effective:.2f}s)",
        )

    @staticmethod
    def _fert_available(ft: dict) -> float:
        """Current fertilizer stock estimate (mL)."""
        try:
            return float(ft.get("ml_remaining_est", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _block_fertilization(self, reason: str, available_ml: float,
                             required_ml: float | None = None,
                             record_db: bool = True) -> None:
        """Record a blocked fertilization without raising."""
        if record_db:
            try:
                self._db.insert_tank_auto_dose_blocked(
                    "fertilizer", reason, ml_remaining_est=available_ml,
                )
            except Exception:
                pass
        self._state.update_tank(
            "fertilizer", blocked_reason=reason, blocked_until_at=None,
        )
        self._log_event(
            "WARNING", "fertigation",
            f"Fertilization blocked: {reason} "
            f"(available {available_ml:.0f} mL, needs {required_ml or 0:.0f} mL)",
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

    def _daily_watering_budget(self, cfg: dict) -> float:
        """Daily watering ceiling in seconds.

        With ``use_area_based_budget`` the budget is derived from the garden:
        area (m²) × target depth (mm) = litres/day, converted to pump seconds
        at 800 L/H. It can only INCREASE (or match) the configured
        ``max_watering_seconds_per_day`` — never reduce it below that floor.
        """
        hardcoded_max = float(cfg.get("max_watering_seconds_per_day", 1800))
        if not cfg.get("use_area_based_budget", False):
            return hardcoded_max
        garden = self._cfg.get("garden", {})
        area_sq_m = float(garden.get("area_sq_m", 10.0))
        target_mm = float(garden.get("water_target_mm_per_day", 5.0))
        # shortcut: fixed 800 L/H pump rate; make it config-driven if a second
        # pump speed is ever supported.
        pump_lph = 800.0
        liters_per_day = area_sq_m * target_mm  # m² × mm == litres
        area_budget = (liters_per_day / pump_lph) * 3600.0
        budget_max = max(area_budget, hardcoded_max)
        logger.info(
            "Area-based watering budget active: %.0f m² × %.1f mm/day = "
            "%.1f L/day -> %.0f s (floor %ds)",
            area_sq_m, target_mm, liters_per_day, area_budget, int(hardcoded_max),
        )
        return budget_max

    def _seed_tank_state(self) -> None:
        """Rebuild tank stock/cooldown from the DB so a restart cannot bypass them.

        The declared refill is the baseline; every auto-dose since then is the
        consumption. Without this the in-memory estimate would silently reset
        to the config default on restart — and, worse, an in-effect soak
        cooldown would be cleared, allowing an immediate re-application.
        """
        for tank in ("fertilizer", "pesticide"):
            try:
                updates = self._seed_one_tank(tank)
            except DatabaseError as exc:
                logger.error("Failed to seed %s tank state: %s", tank, exc)
                continue
            if updates:
                self._state.update_tank(tank, **updates)
                logger.info(
                    "Seeded %s tank from DB: %.0f mL remaining (%.0f mL dispensed)",
                    tank, updates["ml_remaining_est"], updates["ml_dispensed_since_refill"],
                )

    def _seed_one_tank(self, tank: str) -> dict[str, Any] | None:
        refill = self._db.latest_tank_refill(tank)
        if not refill:
            return None
        total_ml = float(refill.get("total_ml") or 0)
        if total_ml <= 0:
            return None

        refill_ts = str(refill.get("timestamp") or "")
        doses = self._db.tank_doses_since(tank, refill_ts) if refill_ts else []
        dispensed = sum(float(d.get("ml_dispensed") or 0) for d in doses)
        remaining = max(0.0, total_ml - dispensed)

        cfg = self._state.tanks.get(tank, {}) or {}
        dose_key = "fert_ml_per_cycle" if tank == "fertilizer" else "ml_per_activation"
        pump_lph = float(refill.get("pump_lph") or 0) or float(cfg.get("pump_lph", 800) or 800)
        dose_size = float(refill.get("ml_per_dose_or_cycle") or 0) or float(cfg.get(dose_key, 250) or 250)
        if remaining <= 0:
            status = "empty"
        elif remaining < dose_size:
            status = "low"
        else:
            status = "full"

        updates: dict[str, Any] = {
            "enabled": True,
            "total_ml": total_ml,
            "ml_dispensed_since_refill": dispensed,
            "ml_remaining_est": remaining,
            "status": status,
            "last_refill_at": refill_ts or None,
            "pump_lph": pump_lph,
        }
        # "Amount each use" is persisted in the refill row (ml_per_dose_or_cycle
        # is the derived value), so a restart restores both it and the cycle
        # seconds it corresponds to — no need to re-derive from elsewhere.
        updates[dose_key] = dose_size
        dose_seconds = self._seconds_for_ml(dose_size, pump_lph)
        if dose_seconds > 0:
            updates["dose_cycle_seconds"] = dose_seconds
        declared_type = str(refill.get("tank_type") or "")
        if declared_type and declared_type != tank:
            updates["fertilizer_type" if tank == "fertilizer" else "pesticide_type"] = declared_type
        if tank == "fertilizer":
            updates["min_ml_for_cycle"] = dose_size
            updates["npk_n"] = float(refill.get("npk_n") or 0)
            updates["npk_p"] = float(refill.get("npk_p") or 0)
            updates["npk_k"] = float(refill.get("npk_k") or 0)
        else:
            # Keep the dashboard's mirror in step with the enforced cap.
            updates["max_activations_per_month"] = self._pest_max_activations_per_month()

        if doses:
            last_at = str(doses[-1].get("timestamp") or "")
            updates["last_fertilized_at" if tank == "fertilizer" else "last_pesticided_at"] = last_at or None
            soak_days = float(cfg.get("soak_days", 0) or 0)
            if soak_days > 0 and last_at:
                try:
                    until = datetime.fromisoformat(last_at) + timedelta(days=soak_days)
                    updates["cooldown_until_at"] = until.isoformat(timespec="seconds")
                except (TypeError, ValueError):
                    logger.warning("Could not parse last dose time %r for %s", last_at, tank)
        return updates

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
            budget_max = self._daily_watering_budget(moist_cfg)
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
            # Merge-only update: the previous detection result must stay
            # intact — this refusal is not a scan outcome.
            self._state.update_pest(reason="scan_busy")
            return False, "A pest scan is already running."

        mode = "sweep" if with_stepper else "camera"
        source = "dashboard_sweep" if with_stepper else "dashboard_camera"
        if not self._enqueue_pest_task(mode, source):
            self._state.update_pest(reason="scan_busy")
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

        # Explicit override: `stepper.max_sweep_steps` sets the one-way step
        # count directly, bypassing the geometry derivation. The _MAX_SWEEP_STEPS
        # guard in _run_sweep() still applies to this value.
        override = stepper_cfg.get("max_sweep_steps")
        if override is not None:
            return int(override), delay

        revolutions = dist / (math.pi * wheel)
        total_steps = int(round(revolutions * steps_per_rev))
        return total_steps, delay

    def _capture_and_detect(self, save_path: Path | None = None) -> Any:
        """One capture + detect. Returns a DetectionResult, or None on error.

        ``save_path`` lets a sweep overwrite a single temp frame instead of
        writing one image per analysis to disk.

        The sweep case passes ``roi="full"`` (the stepper motion already
        covers physical regions) to detectors that expose the parameter;
        older/mock detector signatures without ``roi`` are still supported.
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
            try:
                fn = self._detector.detect
                if "roi" in inspect.signature(fn).parameters:
                    return fn(image_path=image_path, roi="full")
            except (TypeError, ValueError):
                pass
            return fn(image_path=image_path)
        except Exception as exc:
            self._state.update_pest(error=str(exc), suggestion=None)
            self._log_event("ERROR", "pest_detection", str(exc))
            return None

    def _detector_ready(self) -> bool:
        """True when the detector reports a usable (configured) model.

        A detector whose ``status()`` cannot be read or reports
        ``configured: false`` cannot run inference; ``detect()`` would only
        return a null result with ``confidence=None``.
        """
        status_fn = getattr(self._detector, "status", None)
        if status_fn is None:
            return True  # contract-compatible detector without status()
        try:
            return bool(dict(status_fn()).get("configured", False))
        except Exception:
            return False

    def _pest_record_error(self, settings: dict, status: str, reason: str,
                           error: str, source: str, frames: int = 0,
                           model: str | None = None,
                           camera: str | None = None) -> None:
        """Record a failed scan in state (no DB row, no relay)."""
        self._apply_pest_result(
            source=source,
            settings=settings,
            status="error",
            reason=reason,
            confidence=None,
            pest_class=None,
            frames=frames,
            error=error,
            model=model,
            camera=camera,
            insert_row=False,
            alert_on_detect=False,
        )

    def _apply_pest_result(
        self,
        *,
        source: str,
        settings: dict,
        confidence: float | None,
        pest_class: str | None,
        status: str | None = None,
        reason: str | None = None,
        frames: int = 1,
        image_path: str | None = None,
        ts: str | None = None,
        model: str | None = None,
        camera: str | None = None,
        error: str | None = None,
        insert_row: bool = True,
        alert_on_detect: bool = True,
    ) -> None:
        """Classify one completed scan into the confidence band, update the
        shared state, persist one DB row and fire the pest response.

        Band (evaluated on the best frame of the scan):
        * conf >= confidence_threshold  -> status "detected" (relay + suggestion)
        * low <= conf < threshold       -> status "possible" (no relay)
        * conf < low (or None)          -> status "clear"
        Error-classified scans (status="error") never touch relays or DB.
        """
        threshold = float(settings.get("confidence_threshold", 0.70))
        low = float(settings.get("low_confidence_threshold", 0.45))

        if status is None:
            if confidence is not None and confidence >= threshold:
                status = "detected"
            elif confidence is not None and confidence >= low:
                status = "possible"
            else:
                status = "clear"
        if reason is None:
            reason = {
                "detected": "ok",
                "possible": "low_confidence",
                "clear": "below_threshold",
                "error": "exception",
            }.get(status, "exception")

        detected = status == "detected"
        has_class = status in ("detected", "possible")
        ts = ts or datetime.now().isoformat(timespec="seconds")
        model = model or getattr(self._detector, "name", "unknown")

        suggestion = None
        if detected and self._treatment_advisor is not None:
            try:
                advice = self._treatment_advisor.suggest(pest_class)
                suggestion = advice.to_dict() if advice is not None else None
            except Exception as exc:
                logger.error("Treatment advice failed: %s", exc)
                suggestion = None

        self._state.update_pest(
            detected=detected,
            pest_class=pest_class if has_class else None,
            confidence=confidence,
            image_path=image_path,
            timestamp=ts,
            model=model,
            error=error,
            configured=True,
            suggestion=suggestion,
            status=status,
            reason=reason,
            frames=frames,
        )

        if not insert_row:
            return

        try:
            self._db.insert_pest_detection(
                detected=detected,
                pest_class=pest_class if has_class else None,
                confidence=confidence,
                image_path=image_path,
                model=model,
                camera=camera,
                timestamp=ts,
                suggestion=suggestion,
            )
            self._state.update_db(ok=True, last_write=ts)
        except DatabaseError as exc:
            self._state.update_db(ok=False, error=str(exc))
            logger.error("DB write failed (pest detection): %s", exc)

        if not detected or not alert_on_detect:
            return

        alert_msg = (
            f"Pest detected: {pest_class} "
            f"(confidence {(confidence if confidence is not None else 0.0):.2f})"
        )
        self._state.add_alert("WARNING", alert_msg, source)
        self._log_event("WARNING", source, alert_msg)
        self._notify_pesticide(suggestion, source)

        relay_id = int(settings.get("relay_id", 3))
        # Use the relay's own configured duration
        relay_items = self._cfg.get("relays", {}).get("items", [])
        duration = float(settings.get("relay_activation_duration_seconds", 120))
        for item in relay_items:
            if int(item.get("id", 0)) == relay_id:
                duration = float(item.get("activation_duration_seconds", duration))
                break

        self._activate_pest_response(relay_id, duration, ts, source)

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
        analyzed = 0
        detection = None
        best_result = None
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
                if result is not None:
                    analyzed += 1
                    if best_result is None or (
                        (result.confidence or 0.0) > (best_result.confidence or 0.0)
                    ):
                        best_result = result
                    if result.detected and (
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
        self._handle_sweep_result(
            detection, task.source,
            error=motion_error, frames=analyzed, best_result=best_result,
            settings=pest_cfg,
        )

    def _run_camera_only(self, task: PestTask) -> None:
        self._pest_cycle(
            time.monotonic(), self._cfg.get("pest_detection", {}), source=task.source,
        )

    def _handle_sweep_result(
        self,
        result: Any,
        source: str,
        error: str | None = None,
        frames: int = 0,
        best_result: Any = None,
        settings: dict | None = None,
    ) -> None:
        """Record one summary row per sweep and respond once when detected.

        ``result`` is the confirmed DetectionResult (if any). Without one the
        sweep is classified on the best frame collected across it: within the
        low-confidence band the scan is ``possible``, otherwise ``clear``.
        """
        settings = settings if settings is not None else self._cfg.get("pest_detection", {})
        if result is not None:
            self._apply_pest_result(
                source=source,
                settings=settings,
                confidence=result.confidence,
                pest_class=result.pest_class,
                frames=frames,
                image_path=result.image_path,
                ts=result.timestamp.isoformat(timespec="seconds") if result.timestamp else None,
                model=result.model,
                camera=getattr(self._camera, "name", None) if self._camera else None,
                error=error,
            )
            return

        best = best_result
        best_conf = best.confidence if best is not None else None
        best_class = best.pest_class if best is not None else None
        ts = best.timestamp.isoformat(timespec="seconds") if best is not None and best.timestamp else None
        self._apply_pest_result(
            source=source,
            settings=settings,
            confidence=best_conf,
            pest_class=best_class,
            frames=frames,
            image_path=getattr(best, "image_path", None),
            ts=ts,
            model=best.model if best is not None else None,
            camera=getattr(self._camera, "name", None) if self._camera else None,
            error=error,
        )

    def _pest_cycle(self, now: float, cfg: dict, source: str = "pest_detection") -> None:
        """One manual/automatic camera-only pest scan: multi-frame best-of.

        Captures up to ``manual_scan_frames`` frames (``manual_scan_delay_seconds``
        between frames), stops early once a frame reaches the confidence
        threshold, and classifies the best frame. Failures are recorded with an
        explicit outcome so the UI can tell "no pest" from "scan failed".
        """
        self._last_pest_cycle = now

        camera_name = getattr(self._camera, "name", None) if self._camera else None

        # Detector availability gate: detect() on an unloaded model returns
        # confidence=None, which must not be mistaken for a clean scan.
        if not self._detector_ready():
            self._pest_record_error(
                cfg,
                status="error",
                reason="model_unavailable",
                error="Pest scan failed: detector model is not loaded / configured",
                source=source,
                model=getattr(self._detector, "name", "unknown"),
                camera=camera_name,
            )
            return

        threshold = float(cfg.get("confidence_threshold", 0.70))
        max_frames = max(1, int(cfg.get("manual_scan_frames", 5)))
        frame_delay = max(0.0, float(cfg.get("manual_scan_delay_seconds", 0.5)))

        best = None
        analyzed = 0

        for index in range(max_frames):
            # Capture (a failed capture aborts: detect() on no image cannot
            # yield a result, and reporting a clean scan would be a lie).
            image_path = None
            if self._camera is not None:
                try:
                    image_path = self._camera.capture()
                except Exception as exc:
                    self._state.update_camera(error=str(exc))
                    self._log_event("ERROR", "camera", str(exc))
                    self._pest_record_error(
                        cfg,
                        status="error",
                        reason="capture_failed",
                        error=f"Camera capture failed: {exc}",
                        source=source,
                        frames=analyzed,
                        camera=camera_name,
                    )
                    return
                if image_path is None:
                    self._log_event("ERROR", "camera", "Camera capture returned no image")
                    self._pest_record_error(
                        cfg,
                        status="error",
                        reason="capture_failed",
                        error="Camera capture failed: no frame was captured",
                        source=source,
                        frames=analyzed,
                        camera=camera_name,
                    )
                    return

            # Detect
            try:
                result = self._detector.detect(image_path=image_path)
            except Exception as exc:
                self._pest_record_error(
                    cfg,
                    status="error",
                    reason="exception",
                    error=str(exc),
                    source=source,
                    frames=analyzed,
                    camera=camera_name,
                )
                return

            analyzed += 1
            if best is None or (
                (result.confidence or 0.0) > (best.confidence or 0.0)
            ):
                best = result
            # Early stop: a frame is confirmed — no more frames needed.
            if result.confidence is not None and result.confidence >= threshold:
                break
            if index + 1 < max_frames and frame_delay > 0:
                time.sleep(frame_delay)

        ts = best.timestamp.isoformat(timespec="seconds") if best.timestamp else None
        self._apply_pest_result(
            source=source,
            settings=cfg,
            confidence=best.confidence,
            pest_class=best.pest_class,
            frames=analyzed,
            image_path=best.image_path,
            ts=ts,
            model=best.model,
            camera=camera_name,
            error=None,
        )

    # ------------------------------------------------------------------
    # manual / external activation
    # ------------------------------------------------------------------

    def _tank_for_relay(self, relay_id: int) -> str | None:
        """Return the tank name a relay drives, if any."""
        entry = self._relays.get(relay_id)
        if not entry:
            return None
        name = str(entry.get("name", "")).lower()
        if name in ("fertilizer", "npk/fertilizer"):
            return "fertilizer"
        if name in ("pest_response", "pesticide"):
            return "pesticide"
        return None

    def _tank_tracking_on(self, tank_name: str) -> bool:
        tanks = getattr(self._state, "tanks", {})
        if not isinstance(tanks, dict):
            return False
        t = tanks.get(tank_name, {}) or {}
        return bool(t.get("enabled") and t.get("auto_enabled"))

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
        tank_name = self._tank_for_relay(relay_id)
        tracking = tank_name is not None and self._tank_tracking_on(tank_name)

        # Gate on stock when the relay is tracked by a tank.
        if tracking:
            if tank_name == "fertilizer":
                ft = self._state.tanks.get("fertilizer", {}) or {}
                pump_lph = float(ft.get("pump_lph", 800) or 800)
                dose_ml = self._ml_from_seconds(duration, pump_lph)
                available = float(ft.get("ml_remaining_est", 0) or 0)
                if available < dose_ml:
                    return False, (
                        f"Insufficient fertilizer stock "
                        f"({available:.0f} mL < {dose_ml:.0f} mL)"
                    )
            else:
                pt = self._state.tanks.get("pesticide", {}) or {}
                pump_lph = float(pt.get("pump_lph", 800) or 800)
                dose_ml = self._ml_from_seconds(duration, pump_lph)
                available = float(pt.get("ml_remaining_est", 0) or 0)
                if available < dose_ml:
                    return False, (
                        f"Insufficient pesticide stock "
                        f"({available:.0f} mL < {dose_ml:.0f} mL)"
                    )

        activated = self._relays.activate(relay_id, duration=duration, trigger=trigger)
        if not activated:
            return False, "Relay activation failed"

        ts = datetime.now().isoformat(timespec="seconds")
        effective = self._effective_relay_duration(relay_id, duration)
        self._state.set_relay(
            relay_id, state="on", activated_at=ts, duration=effective,
        )

        relay_name = entry.get("name", f"relay_{relay_id}")
        try:
            self._db.insert_relay_activation(
                relay_id=relay_id,
                relay_name=relay_name,
                trigger_type="manual" if trigger == "manual" else "automatic",
                duration_seconds=int(round(effective)),
                source=source,
                timestamp=ts,
            )
            self._state.update_db(ok=True, last_write=ts)
        except DatabaseError as exc:
            logger.error("DB write failed (manual activation): %s", exc)

        msg = f"Relay {relay_id} ({relay_name}) activated for {effective:.0f}s"
        self._state.add_alert("INFO", msg, source)
        self._log_event("INFO", source, msg)

        # Debit tank stock for a tracked, manually-activated relay.
        if tracking:
            if tank_name == "fertilizer":
                ft = self._state.tanks.get("fertilizer", {}) or {}
                pump_lph = float(ft.get("pump_lph", 800) or 800)
                dose_ml = self._ml_from_seconds(effective, pump_lph)
                available = float(ft.get("ml_remaining_est", 0) or 0)
                remaining = max(0.0, available - dose_ml)
                soak_days = float(ft.get("soak_days", 0) or 0)
                updates = {
                    "ml_dispensed_since_refill": float(ft.get("ml_dispensed_since_refill", 0) or 0) + dose_ml,
                    "ml_remaining_est": remaining,
                    "last_fertilized_at": ts,
                    "status": (
                        "empty" if remaining <= 0
                        else "low" if remaining < float(ft.get("fert_ml_per_cycle", 250) or 250)
                        else "full"
                    ),
                    "blocked_reason": None,
                }
                if soak_days > 0:
                    updates["cooldown_until_at"] = (
                        datetime.now() + timedelta(days=soak_days)
                    ).isoformat(timespec="seconds")
                self._state.update_tank("fertilizer", **updates)
                try:
                    self._db.insert_tank_auto_dose(
                        "fertilizer",
                        tank_type=str(ft.get("fertilizer_type") or "fertilizer"),
                        ml_dispensed=dose_ml,
                        activation_seconds=effective,
                        combined_with_watering=False,
                        mode="manual",
                        ml_remaining_est=remaining,
                    )
                except Exception:
                    pass
            else:
                pt = self._state.tanks.get("pesticide", {}) or {}
                pump_lph = float(pt.get("pump_lph", 800) or 800)
                dose_ml = self._ml_from_seconds(effective, pump_lph)
                available = float(pt.get("ml_remaining_est", 0) or 0)
                remaining = max(0.0, available - dose_ml)
                updates = {
                    "ml_dispensed_since_refill": float(pt.get("ml_dispensed_since_refill", 0) or 0) + dose_ml,
                    "ml_remaining_est": remaining,
                    "last_pesticided_at": ts,
                    "status": (
                        "empty" if remaining <= 0
                        else "low" if remaining < float(pt.get("ml_per_activation", 250) or 250)
                        else "full"
                    ),
                    "blocked_reason": None,
                }
                self._state.update_tank("pesticide", **updates)
                try:
                    self._db.insert_tank_auto_dose(
                        "pesticide",
                        tank_type=str(pt.get("pesticide_type") or "pesticide"),
                        ml_dispensed=dose_ml,
                        activation_seconds=effective,
                        combined_with_watering=False,
                        mode="manual",
                        ml_remaining_est=remaining,
                    )
                except Exception:
                    pass

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
        low = float(pest_cfg.get("low_confidence_threshold", 0.45))

        # Injected results are authoritative: their ``detected`` flag decides
        # the outcome even when confidence alone would land in another band.
        if result.detected:
            status, reason, eff_conf = "detected", "ok", result.confidence
        elif result.confidence is not None and result.confidence >= low:
            status, reason, eff_conf = "possible", "low_confidence", result.confidence
        else:
            status, reason, eff_conf = "clear", "below_threshold", result.confidence

        self._apply_pest_result(
            source=source,
            settings=pest_cfg,
            status=status,
            reason=reason,
            confidence=eff_conf,
            pest_class=result.pest_class,
            image_path=getattr(result, "image_path", None),
            ts=ts,
            model=result.model,
            camera="simulated",
            error=None,
        )

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
        with self._pest_response_lock:
            self._activate_pest_response_locked(relay_id, duration, ts, source)

    def _activate_pest_response_locked(self, relay_id: int, duration: float, ts: str,
                                       source: str) -> None:
        # Fail closed: when tank tracking is off, an unmetered pulse would
        # dispense the full configured relay duration (~26.7 L at 800 L/H).
        # Refuse rather than spray an uncontrolled volume.
        if not self._pesticide_tracking_on():
            self._block_pesticide("pesticide_tracking_disabled", 0.0)
            return

        # The monthly cap has a single source of truth (pest_detection); the
        # copy on the tank is a read-only mirror shown on the dashboard.
        max_per_month = self._pest_max_activations_per_month()
        # When tank tracking is on, the dose is volume-driven. At 800 L/H a
        # 120 s relay pulse would dispense ~26 L, so the configured duration
        # becomes an upper safety ceiling rather than the dose itself.
        duration = self._pesticide_effective_duration(duration)
        if duration <= 0:
            # Tracking is on but even the minimum pulse will not fit inside
            # the configured relay ceiling — refuse rather than under-dose.
            self._block_pesticide("pesticide_dose_exceeds_relay_limit", 0.0)
            return

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

        # Tank gate (fail-closed): if pesticide tracking is on and the stock
        # cannot cover the dose, do not activate — and do not burn a monthly
        # allowance.
        if self._pesticide_tracking_on() and not self._pesticide_has_stock(duration):
            self._block_pesticide("pesticide_insufficient_for_cycle", duration)
            return

        activated = self._relays.activate(
            relay_id, duration=duration, trigger="automatic",
        )
        if not activated:
            self._block_pesticide("pesticide_relay_unavailable", duration)
            return

        # activate() clamps to the watchdog cap, so bill the volume that will
        # actually be pumped rather than what we asked for.
        effective = self._effective_relay_duration(relay_id, duration)
        self._state.set_relay(
            relay_id, state="on", activated_at=ts, duration=effective,
        )
        try:
            self._db.insert_relay_activation(
                relay_id=relay_id,
                relay_name="pest_response",
                trigger_type="automatic",
                duration_seconds=int(round(effective)),
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
        self._log_event("INFO", source, f"Relay {relay_id} activated ({effective:.2f}s)")
        # Only now, after a confirmed activation, consume tank stock.
        self._record_pesticide_dose(effective, ts, source)

    def _effective_relay_duration(self, relay_id: int, requested: float) -> float:
        """Duration the relay will actually run for.

        ``RelayController.activate`` clamps the request to
        ``auto_off_watchdog_seconds`` and stores the clamped value on the
        entry; bill against that so stock is not debited for volume that was
        never pumped.
        """
        entry = self._relays.get(relay_id) or {}
        try:
            actual = float(entry.get("duration") or 0)
        except (TypeError, ValueError):
            actual = 0.0
        return actual if actual > 0 else requested

    def _pest_max_activations_per_month(self) -> int:
        """Single source of truth for the monthly pest-response cap."""
        try:
            cap = int(self._cfg.get("pest_detection", {}).get("max_activations_per_month", 2))
        except (TypeError, ValueError):
            cap = 2
        return max(0, cap)

    # ------------------------------------------------------------------
    # pesticide tank helpers
    # ------------------------------------------------------------------

    def _pesticide_tank(self) -> dict:
        tanks = getattr(self._state, "tanks", {})
        if isinstance(tanks, dict):
            return tanks.get("pesticide", {}) or {}
        return {}

    def _pesticide_tracking_on(self) -> bool:
        pt = self._pesticide_tank()
        return bool(pt.get("enabled") and pt.get("auto_enabled"))

    def _pesticide_effective_duration(self, ceiling_seconds: float) -> float:
        """Seconds to run the pesticide pump for one volume-based dose.

        Falls back to the configured ceiling when tracking is off. The
        minimum-pulse floor is never violated: if the ceiling cannot fit even
        the floor, ``0.0`` is returned so the caller refuses the dose instead
        of under-dosing or over-running the ceiling.
        """
        if not self._pesticide_tracking_on():
            return ceiling_seconds
        pt = self._pesticide_tank()
        pump_lph = float(pt.get("pump_lph", 800) or 800)
        target_ml = float(pt.get("ml_per_activation", 250) or 250)
        min_dose = float(pt.get("min_dose_seconds", 1.0) or 0)
        secs = max(self._seconds_for_ml(target_ml, pump_lph), min_dose)
        if ceiling_seconds > 0 and secs > ceiling_seconds:
            return 0.0
        return secs

    def _pesticide_has_stock(self, duration: float) -> bool:
        pt = self._pesticide_tank()
        pump_lph = float(pt.get("pump_lph", 800) or 800)
        dose_ml = self._ml_from_seconds(duration, pump_lph)
        return float(pt.get("ml_remaining_est", 0) or 0) >= dose_ml

    def _block_pesticide(self, reason: str, duration: float) -> None:
        pt = self._pesticide_tank()
        available = float(pt.get("ml_remaining_est", 0) or 0)
        try:
            self._db.insert_tank_auto_dose_blocked(
                "pesticide", reason, ml_remaining_est=available,
            )
        except Exception:
            pass
        self._state.update_tank("pesticide", blocked_reason=reason)
        self._log_event(
            "WARNING", "pest_response",
            f"Pesticide blocked: {reason} (available {available:.0f} mL)",
        )

    def _record_pesticide_dose(self, duration: float, ts: str, source: str) -> None:
        """Consume pesticide stock after a confirmed activation."""
        if not self._pesticide_tracking_on():
            return
        try:
            pt = self._pesticide_tank()
            pump_lph = float(pt.get("pump_lph", 800) or 800)
            ml_per_act = float(pt.get("ml_per_activation", 250) or 250)
            soak_days = float(pt.get("soak_days", 0) or 0)
            ml_disp = self._ml_from_seconds(duration, pump_lph)
            ml_remain = float(pt.get("ml_remaining_est", 0) or 0) - ml_disp
            if ml_remain < 0:
                ml_remain = 0
            self._state.update_tank(
                "pesticide",
                ml_dispensed_since_refill=float(pt.get("ml_dispensed_since_refill", 0) or 0) + ml_disp,
                ml_remaining_est=ml_remain,
                last_pesticided_at=ts,
                cooldown_until_at=(
                    datetime.now() + timedelta(days=soak_days)
                ).isoformat(timespec="seconds"),
                blocked_reason=None,
                blocked_until_at=None,
                status=(
                    "empty" if ml_remain <= 0
                    else "low" if ml_remain < ml_per_act
                    else "full"
                ),
            )
            try:
                self._db.insert_tank_auto_dose(
                    "pesticide",
                    tank_type="pesticide",
                    ml_dispensed=ml_disp,
                    activation_seconds=duration,
                    combined_with_watering=False,
                    mode="separate",
                    ml_remaining_est=ml_remain,
                )
            except Exception:
                pass
            self._log_event(
                "INFO", "pest_response",
                f"Pesticide dispensed {ml_disp:.0f} mL ({duration:.2f}s)",
            )
        except Exception as exc:
            logger.error("Pesticide tank accounting failed: %s", exc)

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

    # ------------------------------------------------------------------
    # tank methods (volume-based, 800 L/H)
    # ------------------------------------------------------------------

    @staticmethod
    def _ml_from_seconds(seconds: float, pump_lph: float) -> float:
        """Volume (mL) dispensed by *pump_lph* over *seconds*.

        ``pump_lph`` is litres/hour, so the conversion carries a x1000 to
        get millilitres. This is the single source of truth for the
        volume<->time relationship used by both tanks.
        """
        if seconds <= 0 or pump_lph <= 0:
            return 0.0
        return (pump_lph * 1000.0 * seconds) / 3600.0

    @classmethod
    def _seconds_for_ml(cls, ml: float, pump_lph: float) -> float:
        """Seconds of pump time needed to dispense *ml* at *pump_lph*."""
        if ml <= 0 or pump_lph <= 0:
            return 0.0
        return (ml * 3600.0) / (pump_lph * 1000.0)

    def _tank_relay_cycle_seconds(self, tank: str) -> float | None:
        """Cycle seconds of the relay that dispenses *tank*.

        The "amount each use" is not a user-entered value: it is derived from
        the pump rate and this relay's configurable ``activation_duration_seconds``.
        The tank is mapped to a relay by name (the same mapping
        :meth:`_tank_for_relay` uses), falling back to the canonical id
        (1 = fertilizer, 3 = pest_response). Returns ``None`` when no matching
        relay setup exists so callers can fall back to configured defaults.
        """
        wanted = ("fertilizer", "npk/fertilizer") if tank == "fertilizer" \
            else ("pest_response", "pesticide")
        fallback_id = 1 if tank == "fertilizer" else 3
        try:
            items = self._cfg.get("relays", {}).get("items", [])
            chosen: dict[str, Any] | None = None
            for item in items:
                if str(item.get("name", "")).lower() in wanted:
                    chosen = item
                    break
            if chosen is None:
                for item in items:
                    if int(item.get("id", 0)) == fallback_id:
                        chosen = item
                        break
            if chosen is not None:
                seconds = float(chosen.get("activation_duration_seconds", 0) or 0)
                if seconds > 0:
                    return seconds
        except Exception:
            pass
        # Last resort: relay state seeded from the same config.
        try:
            relay = self._state.relays.get(fallback_id)
            if relay:
                seconds = float(relay.get("activation_duration_seconds", 0) or 0)
                if seconds > 0:
                    return seconds
        except Exception:
            pass
        return None

    @staticmethod
    def _total_ml_from_kwargs(kwargs: dict) -> float:
        """Refill volume in mL, accepting litres (``total_liters``) first.

        The dashboard form sends litres; the legacy ``total_ml`` key is still
        honoured so older clients keep working.
        """
        liters = kwargs.get("total_liters")
        if liters is not None:
            try:
                value = float(liters)
            except (TypeError, ValueError):
                raise ValueError("total_liters must be a number") from None
            if value <= 0:
                raise ValueError("total_liters must be > 0")
            return value * 1000.0  # litres -> millilitres
        return float(kwargs.get("total_ml", 0) or 0)

    def _derive_dose_for_tank(
        self,
        tank: str,
        kwargs: dict,
        existing: dict,
        pump_lph: float,
        min_dose_seconds: float,
    ) -> tuple[float, float]:
        """Resolve ``(dose_ml, dose_cycle_seconds)`` for a tank refill.

        The dose is derived from ``pump_lph`` and the dose relay's cycle
        seconds — the client no longer sends it. An explicit override (legacy
        clients/migration path) still wins when provided.
        """
        target_key = "fert_ml_per_cycle" if tank == "fertilizer" else "ml_per_activation"
        explicit = kwargs.get(target_key)
        if explicit is not None:
            dose_ml = float(explicit)
            if dose_ml <= 0:
                raise ValueError(f"{target_key} must be > 0")
            return dose_ml, self._seconds_for_ml(dose_ml, pump_lph)

        cycle_seconds = self._tank_relay_cycle_seconds(tank)
        if cycle_seconds is not None:
            # Never derive below the meterability floor: a relay cycle shorter
            # than min_dose_seconds would pump a sub-floor volume.
            effective = max(float(cycle_seconds), float(min_dose_seconds or 0))
            return self._ml_from_seconds(effective, pump_lph), effective

        # No matching relay setup: fall back to the configured tank default.
        dose_ml = float(self._resolve(kwargs, target_key, existing, 250))
        return dose_ml, self._seconds_for_ml(dose_ml, pump_lph)

    @staticmethod
    def _resolve(kwargs: dict, key: str, existing: dict, default: Any) -> Any:
        """Prefer an explicit kwarg, then current tank state, then *default*.

        A refill is a patch: an operator topping up the same fertilizer should
        not silently reset the soak window or the dose size just because the
        caller omitted a field.
        """
        value = kwargs.get(key)
        if value is None:
            value = existing.get(key)
        if value is None:
            value = default
        return value

    def refill_fertilizer_tank(self, **kwargs) -> tuple[bool, str]:
        try:
            existing = dict(self._state.tanks.get("fertilizer", {}))
            fertilizer_type = str(kwargs.get("fertilizer_type", "") or existing.get("fertilizer_type", "")).strip()
            npk_n = float(kwargs.get("npk_n", 0) or 0)
            npk_p = float(kwargs.get("npk_p", 0) or 0)
            npk_k = float(kwargs.get("npk_k", 0) or 0)
            total_ml = self._total_ml_from_kwargs(kwargs)
            tank_capacity_ml = kwargs.get("tank_capacity_ml")
            pump_lph = float(self._resolve(kwargs, "pump_lph", existing, 800) or 800)
            min_dose_seconds = float(self._resolve(kwargs, "min_dose_seconds", existing, 1.0) or 0)
            soak_days = float(self._resolve(kwargs, "soak_days", existing, 0) or 0)
            mode = str(self._resolve(kwargs, "mode", existing, "with_watering")).lower()
            if mode not in ("separate", "with_watering"):
                mode = "with_watering"
            # "Amount each use" is derived, not entered.
            fert_ml_per_cycle, dose_cycle_seconds = self._derive_dose_for_tank(
                "fertilizer", kwargs, existing, pump_lph, min_dose_seconds,
            )

            if total_ml <= 0:
                return False, "total_ml must be > 0"
            if fert_ml_per_cycle <= 0:
                return False, "fert_ml_per_cycle must be > 0"
            if pump_lph <= 0:
                return False, "pump_lph must be > 0"
            # Fail closed on an un-meterable dose: at 800 L/H a 1 s pulse is
            # ~222 mL, so a smaller target cannot be dispensed accurately.
            min_ml = self._ml_from_seconds(min_dose_seconds, pump_lph)
            if fert_ml_per_cycle < min_ml:
                return False, (
                    f"fert_ml_per_cycle must be at least {min_ml:.0f} mL "
                    f"({min_dose_seconds:.1f}s at {pump_lph:.0f} L/H)"
                )

            max_doses = int(total_ml // fert_ml_per_cycle) if fert_ml_per_cycle > 0 else 0
            # tank_capacity_ml is no longer a form input: the tank holds at
            # least what was just poured in. A larger historical capacity (or
            # an explicit legacy value) still wins.
            existing_capacity = float(existing.get("tank_capacity_ml", 0) or 0)
            capacity_ml = max(existing_capacity, total_ml)
            try:
                declared_capacity = float(tank_capacity_ml) if tank_capacity_ml is not None else 0.0
            except (TypeError, ValueError):
                declared_capacity = 0.0
            if declared_capacity > 0:
                capacity_ml = max(capacity_ml, declared_capacity)

            updates = {
                "fertilizer_type": fertilizer_type,
                "npk_n": npk_n,
                "npk_p": npk_p,
                "npk_k": npk_k,
                "total_ml": total_ml,
                "ml_dispensed_since_refill": 0.0,
                "ml_remaining_est": total_ml,
                "pump_lph": pump_lph,
                "fert_ml_per_cycle": fert_ml_per_cycle,
                "dose_cycle_seconds": dose_cycle_seconds,
                "min_ml_for_cycle": fert_ml_per_cycle,
                "min_dose_seconds": min_dose_seconds,
                "soak_days": soak_days,
                "mode": mode,
                "last_refill_at": datetime.now().isoformat(timespec="seconds"),
                "last_fertilized_at": self._state.tanks["fertilizer"].get("last_fertilized_at"),
                "cooldown_until_at": self._state.tanks["fertilizer"].get("cooldown_until_at"),
                "blocked_reason": None,
                "blocked_until_at": None,
                "status": "full" if max_doses >= 1 else "empty",
                "enabled": True,
                "tank_capacity_ml": capacity_ml,
            }

            self._state.update_tank("fertilizer", **updates)
            try:
                self._db.insert_tank_refill(
                    "fertilizer",
                    tank_type=fertilizer_type or "fertilizer",
                    npk_n=npk_n,
                    npk_p=npk_p,
                    npk_k=npk_k,
                    total_ml=total_ml,
                    ml_per_dose_or_cycle=fert_ml_per_cycle,
                    max_doses=max_doses,
                    mode=mode,
                    pump_lph=pump_lph,
                )
            except Exception:
                pass
            return True, (
                f"Fertilizer tank refilled ({total_ml / 1000.0:.2f} L, "
                f"{max_doses} uses of {fert_ml_per_cycle:.0f} mL)"
            )
        except Exception as exc:
            return False, f"Refill failed: {exc}"

    def refill_pesticide_tank(self, **kwargs) -> tuple[bool, str]:
        try:
            existing = dict(self._state.tanks.get("pesticide", {}))
            pesticide_type = str(kwargs.get("pesticide_type", "") or existing.get("pesticide_type", "")).strip()
            total_ml = self._total_ml_from_kwargs(kwargs)
            tank_capacity_ml = kwargs.get("tank_capacity_ml")
            pump_lph = float(self._resolve(kwargs, "pump_lph", existing, 800) or 800)
            min_dose_seconds = float(self._resolve(kwargs, "min_dose_seconds", existing, 1.0) or 0)
            soak_days = float(self._resolve(kwargs, "soak_days", existing, 0) or 0)
            # "Amount each use" is derived, not entered (relay 3 = pest_response).
            ml_per_activation, dose_cycle_seconds = self._derive_dose_for_tank(
                "pesticide", kwargs, existing, pump_lph, min_dose_seconds,
            )
            # Display mirror only. The enforced cap has a single source of truth
            # (pest_detection.max_activations_per_month) so the dashboard can
            # never advertise a limit the controller does not actually apply.
            max_activations_per_month = self._pest_max_activations_per_month()

            if total_ml <= 0:
                return False, "total_ml must be > 0"
            if ml_per_activation <= 0:
                return False, "ml_per_activation must be > 0"
            if pump_lph <= 0:
                return False, "pump_lph must be > 0"
            min_ml = self._ml_from_seconds(min_dose_seconds, pump_lph)
            if ml_per_activation < min_ml:
                return False, (
                    f"ml_per_activation must be at least {min_ml:.0f} mL "
                    f"({min_dose_seconds:.1f}s at {pump_lph:.0f} L/H)"
                )

            max_doses = int(total_ml // ml_per_activation) if ml_per_activation > 0 else 0
            # Same capacity rule as the fertilizer tank: no form input.
            existing_capacity = float(existing.get("tank_capacity_ml", 0) or 0)
            capacity_ml = max(existing_capacity, total_ml)
            try:
                declared_capacity = float(tank_capacity_ml) if tank_capacity_ml is not None else 0.0
            except (TypeError, ValueError):
                declared_capacity = 0.0
            if declared_capacity > 0:
                capacity_ml = max(capacity_ml, declared_capacity)
            updates = {
                "pesticide_type": pesticide_type,
                "total_ml": total_ml,
                "ml_dispensed_since_refill": 0.0,
                "ml_remaining_est": total_ml,
                "pump_lph": pump_lph,
                "ml_per_activation": ml_per_activation,
                "dose_cycle_seconds": dose_cycle_seconds,
                "min_dose_seconds": min_dose_seconds,
                "soak_days": soak_days,
                "max_activations_per_month": max_activations_per_month,
                "last_refill_at": datetime.now().isoformat(timespec="seconds"),
                "last_pesticided_at": self._state.tanks["pesticide"].get("last_pesticided_at"),
                "cooldown_until_at": self._state.tanks["pesticide"].get("cooldown_until_at"),
                "blocked_reason": None,
                "blocked_until_at": None,
                "status": "full" if max_doses >= 1 else "empty",
                "enabled": True,
                "tank_capacity_ml": capacity_ml,
            }

            self._state.update_tank("pesticide", **updates)
            try:
                self._db.insert_tank_refill(
                    "pesticide",
                    tank_type=pesticide_type or "pesticide",
                    total_ml=total_ml,
                    ml_per_dose_or_cycle=ml_per_activation,
                    max_doses=max_doses,
                    mode="separate",
                    pump_lph=pump_lph,
                )
            except Exception:
                pass
            return True, (
                f"Pesticide tank refilled ({total_ml / 1000.0:.2f} L, "
                f"{max_doses} activations of {ml_per_activation:.0f} mL)"
            )
        except Exception as exc:
            return False, f"Refill failed: {exc}"

    def set_tank_auto(self, tank_name: str, enabled: bool) -> tuple[bool, str]:
        tank_name = tank_name.lower()
        if tank_name not in ("fertilizer", "pesticide"):
            return False, "Invalid tank"
        self._state.update_tank(tank_name, auto_enabled=bool(enabled))
        return True, f"{tank_name} auto {'enabled' if enabled else 'disabled'}"

    def set_tank_enabled(self, tank_name: str, enabled: bool) -> tuple[bool, str]:
        tank_name = tank_name.lower()
        if tank_name not in ("fertilizer", "pesticide"):
            return False, "Invalid tank"
        self._state.update_tank(tank_name, enabled=bool(enabled))
        return True, f"{tank_name} {'enabled' if enabled else 'disabled'}"