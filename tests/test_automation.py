"""Tests for automation rules and controller logic."""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from automation.controller import AutomationController
from database.database import Database
from hardware.relay_controller import RelayController
from state import SystemState


def _wait_until(predicate, timeout=5.0, interval=0.02):
    """Block until predicate() is truthy; return False if timeout elapses.

    The automation controller runs on a background thread, so a fixed
    `time.sleep(0.3)` followed by an assertion is a race: the thread must be
    scheduled AND the sensor's read interval must elapse inside that window.
    Poll the condition instead, keeping a generous ceiling so a real bug still
    fails loudly rather than hanging.
    """
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _make_config(tmp_path=None):
    db_path = str(tmp_path / "test.db") if tmp_path else "test.db"
    return {
        "system": {"name": "Test", "simulate_hardware": True, "timezone": "UTC", "tick_seconds": 0.05},
        "database": {"path": db_path},
        "npk": {
            "enabled": True,
            "read_interval_seconds": 0.1,
            "log_interval_seconds": 0.1,
            "thresholds": {"nitrogen": 20, "phosphorus": 20, "potassium": 20},
            "scale": 1.0,
        },
        "soil_moisture": {
            "enabled": True,
            "read_interval_seconds": 0.1,
            "log_interval_seconds": 0.1,
            "threshold": 30,
            "relay_id": 2,
            "watering_cooldown_seconds": 30,
            "relay_activation_duration_seconds": 1,
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
        "pest_detection": {
            "enabled": False,
            "relay_id": 3,
            "detector": "mock",
            "confidence_threshold": 0.70,
            "capture_interval_seconds": 1,
            "max_activations_per_month": 2,
            "relay_activation_duration_seconds": 2,
            "mock": {"mode": "none", "sequence": [], "random": {"detection_probability": 0.25, "confidence_min": 0.50, "confidence_max": 0.98}},
        },
        "camera": {"enabled": False, "capture_interval_seconds": 1},
    }


def _make_sensors():
    """Create mock sensors that return configurable readings."""
    npk_sensor = MagicMock()
    moisture_sensor = MagicMock()

    def make_npk_reading(n, p, k):
        from sensors.base import Reading
        from datetime import datetime
        return Reading(
            timestamp=datetime.now(),
            values={"nitrogen": n, "phosphorus": p, "potassium": k},
        )

    def make_moisture_reading(val):
        from sensors.base import Reading
        from datetime import datetime
        return Reading(
            timestamp=datetime.now(),
            values={"moisture": val},
        )

    npk_sensor.read = MagicMock(return_value=make_npk_reading(25, 25, 25))
    moisture_sensor.read = MagicMock(return_value=make_moisture_reading(45))

    return {"npk": npk_sensor, "moisture": moisture_sensor}


@pytest.fixture
def automation_setup(tmp_path):
    """Create a full automation stack with mocked sensors."""
    cfg = _make_config(tmp_path)

    db = Database(cfg["database"]["path"])
    relays = RelayController(cfg["relays"], simulate=True)
    state = SystemState(cfg)
    sensors = _make_sensors()

    ctrl = AutomationController(
        config=cfg, db=db, relays=relays, state=state, sensors=sensors,
    )
    # Attach mock detector
    from pest_detection.mock_detector import MockDetector
    pest_cfg = cfg["pest_detection"]
    pest_cfg["mock"]["mode"] = "none"
    detector = MockDetector(pest_cfg)
    ctrl.attach_detector(detector)

    # Attach mock camera
    from camera.camera import NotConfiguredCamera
    ctrl.attach_camera(NotConfiguredCamera())

    ctrl.start()
    time.sleep(0.2)  # let first tick run

    yield ctrl, state, sensors, relays, db

    ctrl.stop()
    relays.shutdown()
    db.close()


def test_npk_alert_only_no_auto_relay(automation_setup):
    """NPK below threshold should alert but NOT activate Relay 1."""
    ctrl, state, sensors, relays, db = automation_setup
    # Make NPK below threshold
    sensors["npk"].read.return_value = sensors["npk"].read.return_value.__class__(
        timestamp=datetime.now(),
        values={"nitrogen": 10, "phosphorus": 25, "potassium": 25},
    )
    # Wait for the controller's next NPK read to fire the transition alert.
    assert _wait_until(
        lambda: [a for a in state.snapshot()["alerts"] if a["source"] == "npk"]
    ), "no NPK alert within timeout"

    # Check alert fired
    snap = state.snapshot()
    alerts = snap["alerts"]
    npk_alerts = [a for a in alerts if a["source"] == "npk"]
    assert len(npk_alerts) >= 1
    assert "nitrogen" in npk_alerts[0]["message"].lower()

    # Relay 1 should NOT be activated
    assert relays.is_active(1) is False


def test_soil_moisture_auto_waters_when_below_threshold(automation_setup):
    """Moisture below threshold should auto-activate Relay 2."""
    ctrl, state, sensors, relays, db = automation_setup
    # Make moisture low
    sensors["moisture"].read.return_value = sensors["moisture"].read.return_value.__class__(
        timestamp=datetime.now(),
        values={"moisture": 20},  # below 30
    )

    # Relay 2 should be activated
    assert _wait_until(lambda: relays.is_active(2)), "watering never activated relay 2"
    entry = relays.get(2)
    assert entry["trigger"] == "automatic"


def test_pest_monthly_limit_blocks_after_max(automation_setup, tmp_path):
    """After max pest responses, further detections are blocked and logged."""
    ctrl, state, sensors, relays, db = automation_setup

    # Use up the monthly limit (2) for relay 3 directly in the DB
    now = datetime.now()
    for _ in range(2):
        ctrl._db.insert_relay_activation(
            relay_id=3, relay_name="pest_response", trigger_type="automatic",
            duration_seconds=2, source="pest_detection",
            timestamp=now.isoformat(timespec="seconds"),
        )

    # Enable pest detection with a detector that always detects
    from pest_detection.mock_detector import MockDetector
    pest_cfg = _make_config(tmp_path)["pest_detection"]
    pest_cfg["enabled"] = True
    pest_cfg["mock"]["mode"] = "sequential"
    pest_cfg["mock"]["sequence"] = [("aphid", 0.91, True)]
    pest_cfg["confidence_threshold"] = 0.5
    detector = MockDetector(pest_cfg)
    ctrl.attach_detector(detector)

    from camera.camera import NotConfiguredCamera
    class MockCam(NotConfiguredCamera):
        def capture(self, save_path=None):
            return "/tmp/test.jpg"
    ctrl.attach_camera(MockCam())

    state.update_pest(configured=True, model="mock", enabled=True)

    ctrl._pest_cycle(time.monotonic(), pest_cfg)
    time.sleep(0.1)

    assert relays.is_active(3) is False

    rows = ctrl._db._query("SELECT * FROM blocked_activations WHERE relay_id = 3")
    assert len(rows) >= 1
    assert "Monthly pest response limit reached" in rows[0]["reason"]

    snap = state.snapshot()
    assert snap["usage"]["limit_reached"] is True
    assert snap["usage"]["remaining"] == 0


def test_watering_cooldown_prevents_retrigger(automation_setup):
    """After watering, cooldown should prevent immediate re-trigger."""
    ctrl, state, sensors, relays, db = automation_setup

    # Make moisture low → watering starts
    sensors["moisture"].read.return_value = sensors["moisture"].read.return_value.__class__(
        timestamp=datetime.now(),
        values={"moisture": 20},
    )
    assert _wait_until(lambda: relays.is_active(2)), "watering never activated relay 2"

    # Wait for watering to finish (relay 2 runs 2s, then the watchdog clears it)
    assert _wait_until(lambda: not relays.is_active(2)), "watchdog never turned relay 2 off"

    # Immediately make moisture low again — should NOT re-trigger due to cooldown.
    # Wait for a *fresh* read: a negative assertion here would otherwise pass
    # merely because no tick landed, proving nothing about the cooldown.
    last_read = ctrl._last_moisture_read
    sensors["moisture"].read.return_value = sensors["moisture"].read.return_value.__class__(
        timestamp=datetime.now(),
        values={"moisture": 20},
    )
    assert _wait_until(lambda: ctrl._last_moisture_read > last_read), "no fresh moisture read"
    # Still off because of cooldown
    assert relays.is_active(2) is False


def test_manual_relay_activation_via_controller(automation_setup):
    """activate_relay() should work for manual dashboard actions."""
    ctrl, state, sensors, relays, db = automation_setup
    ok, msg = ctrl.activate_relay(1, duration=1.0, trigger="manual", source="dashboard")
    assert ok is True
    assert relays.is_active(1) is True
    entry = relays.get(1)
    assert entry["trigger"] == "manual"

    # DB should have manual activation
    rows = ctrl._db._query(
        "SELECT * FROM relay_activations WHERE relay_id = 1 AND trigger_type = 'manual'"
    )
    assert len(rows) == 1


def test_emergency_stop_forces_all_off(automation_setup):
    """emergency_stop() turns off all relays immediately."""
    ctrl, state, sensors, relays, db = automation_setup

    # Turn on all three
    for rid in (1, 2, 3):
        relays.activate(rid, duration=10, trigger="test")
    time.sleep(0.1)
    assert all(relays.is_active(rid) for rid in (1, 2, 3))

    ctrl.emergency_stop(source="test")
    time.sleep(0.1)
    assert not any(relays.is_active(rid) for rid in (1, 2, 3))

    # Alert should be CRITICAL
    snap = state.snapshot()
    critical = [a for a in snap["alerts"] if a["level"] == "CRITICAL"]
    assert len(critical) >= 1
    assert "Emergency stop" in critical[0]["message"]


def test_pause_resume_automation(automation_setup):
    """Pausing automation should halt sensor reads and relay actions."""
    ctrl, state, sensors, relays, db = automation_setup

    # Make moisture low
    sensors["moisture"].read.return_value = sensors["moisture"].read.return_value.__class__(
        timestamp=datetime.now(),
        values={"moisture": 20},
    )
    assert _wait_until(lambda: relays.is_active(2)), "watering never activated relay 2"

    # Pause
    ctrl.pause()
    assert state.snapshot()["automation"]["paused"] is True

    # Wait for relay watchdog to turn it off
    assert _wait_until(lambda: not relays.is_active(2)), "watchdog never turned relay 2 off"
    # No new watering should start while paused
    assert relays.is_active(2) is False

    # Resume
    ctrl.resume()
    assert state.snapshot()["automation"]["paused"] is False


def test_pest_detection_activates_relay_3(automation_setup, tmp_path):
    """When pest detection is enabled and detects, Relay 3 activates."""
    ctrl, state, sensors, relays, db = automation_setup

    # Enable pest detection with a detector that always detects
    from pest_detection.mock_detector import MockDetector
    pest_cfg = _make_config(tmp_path)["pest_detection"]
    pest_cfg["enabled"] = True
    pest_cfg["mock"]["mode"] = "sequential"
    pest_cfg["mock"]["sequence"] = [("aphid", 0.91, True)]
    pest_cfg["confidence_threshold"] = 0.5
    detector = MockDetector(pest_cfg)
    ctrl.attach_detector(detector)

    # Also need to attach a mock camera that returns a path
    from camera.camera import NotConfiguredCamera
    class MockCam(NotConfiguredCamera):
        def capture(self, save_path=None):
            return "/tmp/test.jpg"
    ctrl.attach_camera(MockCam())

    state.update_pest(configured=True, model="mock", enabled=True)

    # Trigger pest cycle manually
    ctrl._pest_cycle(time.monotonic(), pest_cfg)
    time.sleep(0.1)

    assert relays.is_active(3) is True
    entry = relays.get(3)
    assert entry["trigger"] == "automatic"

    # DB should have pest detection + relay activation
    pest_rows = ctrl._db.recent_pest_detections(limit=1)
    assert len(pest_rows) == 1
    assert pest_rows[0]["detected"] == 1
    assert pest_rows[0]["pest_class"] == "aphid"