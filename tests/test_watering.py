"""Closed-loop watering policy tests."""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from automation.controller import AutomationController
from database.database import Database
from hardware.relay_controller import RelayController
from sensors.base import Reading
from state import SystemState


def _make_config(tmp_path):
    db_path = str(tmp_path / "test.db")
    return {
        "system": {"name": "Test", "simulate_hardware": True, "timezone": "UTC", "tick_seconds": 0.05},
        "database": {"path": db_path},
        "npk": {
            "enabled": False,
            "read_interval_seconds": 0.1,
            "log_interval_seconds": 0.1,
            "thresholds": {"nitrogen": 20, "phosphorus": 20, "potassium": 20},
        },
        "soil_moisture": {
            "enabled": True,
            "read_interval_seconds": 0.1,
            "log_interval_seconds": 0.1,
            "threshold": 30,
            "relay_id": 2,
            "watering_cooldown_seconds": 10,
            "hysteresis": 5,
            "response_rise_percent": 2.0,
            "max_consecutive_non_responses": 3,
            "max_watering_seconds_per_day": 1000,
            "relay_activation_duration_seconds": 2,
        },
        "relays": {
            "active_high": True,
            "auto_off_watchdog_seconds": 10,
            "watchdog_check_seconds": 0.05,
            "default_duration_seconds": 5,
            "items": [
                {"id": 1, "name": "fertilizer", "label": "Fertilizer", "pin": 17, "activation_duration_seconds": 2},
                {"id": 2, "name": "watering", "label": "Watering", "pin": 27, "activation_duration_seconds": 2},
                {"id": 3, "name": "pest_response", "label": "Pest", "pin": 22, "activation_duration_seconds": 2},
            ],
        },
        "pest_detection": {"enabled": False},
        "camera": {"enabled": False},
    }


def _make_stack(tmp_path, **moisture_overrides):
    cfg = _make_config(tmp_path)
    cfg["soil_moisture"].update(moisture_overrides)
    db = Database(cfg["database"]["path"])
    relays = RelayController(cfg["relays"], simulate=True)
    state = SystemState(cfg)
    sensors = {"npk": None, "moisture": MagicMock()}
    ctrl = AutomationController(config=cfg, db=db, relays=relays, state=state, sensors=sensors)
    # prime moisture sensor read
    sensors["moisture"].read = MagicMock(return_value=Reading(
        timestamp=datetime.now(), values={"moisture": 45},
    ))
    return cfg, ctrl, state, relays, db, sensors


def _cleanup(ctrl, relays, db):
    try:
        ctrl.stop()
    except Exception:
        pass
    try:
        relays.shutdown()
    except Exception:
        pass
    try:
        db.close()
    except Exception:
        pass


@pytest.fixture
def stack(tmp_path):
    cfg, ctrl, state, relays, db, sensors = _make_stack(tmp_path)
    yield cfg, ctrl, state, relays, db, sensors
    _cleanup(ctrl, relays, db)


def test_hysteresis_keeps_and_clears(stack):
    cfg, ctrl, state, relays, db, sensors = stack
    moist_cfg = cfg["soil_moisture"]
    now = time.monotonic()

    # Start latched False; value 32 (above threshold 30 but within hysteresis)
    # should NOT latch below True.
    state.update_moisture(value=40, below=False, timestamp=datetime.now().isoformat(timespec="seconds"))
    sensors["moisture"].read.return_value = Reading(timestamp=datetime.now(), values={"moisture": 32})
    ctrl._read_moisture(now, moist_cfg)
    assert state.moisture["below"] is False

    # Now latched True; same value 32 should STAY below True (32 < 35).
    state.update_moisture(value=20, below=True, timestamp=datetime.now().isoformat(timespec="seconds"))
    sensors["moisture"].read.return_value = Reading(timestamp=datetime.now(), values={"moisture": 32})
    ctrl._read_moisture(now, moist_cfg)
    assert state.moisture["below"] is True


def test_cooldown_blocks_second_watering(stack):
    cfg, ctrl, state, relays, db, sensors = stack
    moist_cfg = cfg["soil_moisture"]
    state.update_moisture(value=20, below=True, timestamp=datetime.now().isoformat(timespec="seconds"))
    now = time.monotonic()
    ctrl._watering_cooldown_until = 0.0
    ctrl._water_baseline_value = None
    ctrl._evaluate_watering(now, moist_cfg)
    assert relays.is_active(2) is True

    # capture activation timestamp/count before second attempt
    rows_before = len(db._query("SELECT * FROM relay_activations WHERE relay_id = 2"))
    activated_at_before = relays.get(2).get("activated_at")

    # force cooldown window into the future
    ctrl._watering_cooldown_until = time.monotonic() + 100
    state.update_moisture(value=20, below=True, timestamp=datetime.now().isoformat(timespec="seconds"))
    ctrl._water_baseline_value = None
    ctrl._evaluate_watering(time.monotonic(), moist_cfg)

    rows_after = len(db._query("SELECT * FROM relay_activations WHERE relay_id = 2"))
    # no second watering triggered while cooldown active
    assert rows_after == rows_before
    # activated_at unchanged (relay not re-activated)
    assert relays.get(2).get("activated_at") == activated_at_before


def test_response_confirmed_resets_counter(stack):
    cfg, ctrl, state, relays, db, sensors = stack
    moist_cfg = cfg["soil_moisture"]
    ctrl._watering_non_responses = 2
    ctrl._water_baseline_value = 20.0
    ctrl._watering_cooldown_until = 0.0
    state.update_moisture(value=25, below=False, timestamp=datetime.now().isoformat(timespec="seconds"), error=None)
    ctrl._evaluate_watering(time.monotonic(), moist_cfg)
    assert ctrl._watering_non_responses == 0
    assert ctrl._water_baseline_value is None
    assert state.watering["non_responses"] == 0


def test_response_absent_escalates_to_fault(stack):
    cfg, ctrl, state, relays, db, sensors = stack
    moist_cfg = cfg["soil_moisture"]
    # max_consecutive_non_responses = 3
    for _ in range(3):
        ctrl._water_baseline_value = 20.0
        ctrl._watering_cooldown_until = 0.0
        ctrl._evaluate_watering_response(20.0, None, moist_cfg)
    assert ctrl._watering_fault is True
    snap = state.snapshot()
    assert snap["watering"]["fault"] is True
    assert snap["watering"]["fault_reason"]  # non-empty
    alerts = snap["alerts"]
    assert any(a["level"] == "CRITICAL" for a in alerts)


def test_faulted_no_watering(stack):
    cfg, ctrl, state, relays, db, sensors = stack
    moist_cfg = cfg["soil_moisture"]
    ctrl._watering_fault = True
    state.update_moisture(value=20, below=True, timestamp=datetime.now().isoformat(timespec="seconds"))
    state.update_watering(fault=True, fault_reason="x", non_responses=3)
    ctrl._watering_cooldown_until = 0.0
    ctrl._evaluate_watering(time.monotonic(), moist_cfg)
    assert relays.is_active(2) is False


def test_fault_auto_clears_on_recovery(stack):
    cfg, ctrl, state, relays, db, sensors = stack
    moist_cfg = cfg["soil_moisture"]
    ctrl._watering_fault = True
    state.update_moisture(value=50, below=False, timestamp=datetime.now().isoformat(timespec="seconds"))
    state.update_watering(fault=True, fault_reason="x", non_responses=3)
    ctrl._evaluate_watering(time.monotonic(), moist_cfg)
    assert ctrl._watering_fault is False
    assert ctrl._watering_non_responses == 0
    snap = state.snapshot()
    assert snap["watering"]["fault"] is False
    assert any(a["level"] == "INFO" for a in snap["alerts"])


def test_sensor_error_not_counted(stack):
    cfg, ctrl, state, relays, db, sensors = stack
    moist_cfg = cfg["soil_moisture"]
    ctrl._watering_non_responses = 0
    ctrl._water_baseline_value = 20.0
    ctrl._watering_cooldown_until = 0.0
    state.update_moisture(value=20, below=False, timestamp=datetime.now().isoformat(timespec="seconds"), error="boom")
    ctrl._evaluate_watering(time.monotonic(), moist_cfg)
    assert ctrl._watering_non_responses == 0
    assert ctrl._water_baseline_value is None


def test_daily_budget_blocks(stack):
    cfg, ctrl, state, relays, db, sensors = stack
    # override max budget very low
    cfg["soil_moisture"]["max_watering_seconds_per_day"] = 1
    moist_cfg = cfg["soil_moisture"]
    state.update_moisture(value=20, below=True, timestamp=datetime.now().isoformat(timespec="seconds"))
    ctrl._watering_cooldown_until = 0.0
    ctrl._watering_fault = False
    ctrl._evaluate_watering(time.monotonic(), moist_cfg)
    assert relays.is_active(2) is False
    snap = state.snapshot()
    assert snap["watering"]["budget"]["blocked"] is True
    alerts = snap["alerts"]
    assert any(a["level"] == "WARNING" and "budget" in a["message"].lower() for a in alerts) or any(a["level"] == "WARNING" for a in alerts)
    # ensure second call does not duplicate alert? not strictly required


def test_reset_watering_fault(stack):
    cfg, ctrl, state, relays, db, sensors = stack
    ctrl._watering_fault = True
    ctrl._watering_non_responses = 3
    state.update_watering(fault=True, fault_reason="x", non_responses=3)
    ok, msg = ctrl.reset_watering_fault()
    assert ok is True
    assert isinstance(msg, str) and msg
    assert ctrl._watering_fault is False
    assert ctrl._watering_non_responses == 0
    assert state.watering["fault"] is False


def test_restart_safety_seed(tmp_path):
    cfg = _make_config(tmp_path)
    cfg["soil_moisture"]["watering_cooldown_seconds"] = 10
    db = Database(cfg["database"]["path"])
    relays = RelayController(cfg["relays"], simulate=True)
    state = SystemState(cfg)
    sensors = {"npk": None, "moisture": MagicMock()}
    ctrl = AutomationController(config=cfg, db=db, relays=relays, state=state, sensors=sensors)
    # pre-insert activation now
    db.insert_relay_activation(
        relay_id=2, relay_name="watering", trigger_type="automatic",
        duration_seconds=2, source="automation",
        timestamp=datetime.now().isoformat(timespec="seconds"),
    )
    assert ctrl._watering_cooldown_until == 0.0
    ctrl._seed_watering_state()
    assert ctrl._watering_cooldown_until > time.monotonic()
    _cleanup(ctrl, relays, db)


def test_cooldown_countdown_decays(stack):
    cfg, ctrl, state, relays, db, sensors = stack
    moist_cfg = cfg["soil_moisture"]
    state.update_moisture(value=20, below=True, timestamp=datetime.now().isoformat(timespec="seconds"))
    now = time.monotonic()
    ctrl._watering_cooldown_until = 0.0
    ctrl._evaluate_watering(now, moist_cfg)
    # inside window: remaining > 0
    mid = time.monotonic()
    # re-arm a future cooldown manually to test mid-window
    ctrl._watering_cooldown_until = mid + 5
    ctrl._sync_watering_state(mid, moist_cfg)
    assert state.watering["cooldown_remaining_seconds"] > 0

    # well past cooldown: remaining == 0
    later = ctrl._watering_cooldown_until + 10
    ctrl._sync_watering_state(later, moist_cfg)
    assert state.watering["cooldown_remaining_seconds"] == 0
    ctrl._evaluate_watering(later, moist_cfg)
    assert state.watering["cooldown_remaining_seconds"] == 0


def test_budget_blocked_clears_when_usage_drops(stack):
    cfg, ctrl, state, relays, db, sensors = stack
    moist_cfg = cfg["soil_moisture"]
    # blocked: low cap
    cfg["soil_moisture"]["max_watering_seconds_per_day"] = 1
    # force no cached/DB reads affecting scenario; make a current sync reflect blocked
    ctrl._watering_budget_used = 0.0
    ctrl._last_watering_budget_read = time.monotonic()
    state.update_moisture(value=20, below=True, timestamp=datetime.now().isoformat(timespec="seconds"))
    now = time.monotonic()
    ctrl._watering_cooldown_until = 0.0
    ctrl._evaluate_watering(now, moist_cfg)
    assert state.watering["budget"]["blocked"] is True

    # usage/cap back under: raise cap and reset cached usage
    cfg["soil_moisture"]["max_watering_seconds_per_day"] = 1000
    ctrl._watering_budget_used = 0.0
    ctrl._last_watering_budget_read = time.monotonic()
    ctrl._evaluate_watering(now + 1, moist_cfg)
    assert state.watering["budget"]["blocked"] is False