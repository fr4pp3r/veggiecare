"""Full system integration test for relays, sensors, camera, and stepper modules.

This test validates all hardware subsystems working together in simulated mode,
following the patterns established in existing tests.
"""

from __future__ import annotations

import time
from datetime import datetime

from automation.controller import AutomationController
from camera.camera import NotConfiguredCamera
from camera import usb_camera as usb_mod
from database.database import Database
from hardware.relay_controller import RelayController
from hardware.stepper import StepperMotor
from state import SystemState
from sensors.npk import NpkSensor
from sensors.soil_moisture import SoilMoistureSensor


def _make_config(tmp_path=None):
    db_path = str(tmp_path / "system_test.db") if tmp_path else "system_test.db"
    img_dir = str(tmp_path / "images") if tmp_path else "images"
    return {
        "system": {"name": "SystemTest", "simulate_hardware": True, "timezone": "UTC", "tick_seconds": 0.05},
        "database": {"path": db_path},
        "npk": {
            "enabled": True,
            "port": "/dev/ttyUSB0",
            "slave_id": 1,
            "baudrate": 4800,
            "timeout": 1.0,
            "register_start": 0,
            "register_count": 3,
            "scale": 1.0,
            "read_interval_seconds": 0.1,
            "log_interval_seconds": 0.1,
            "thresholds": {"nitrogen": 20, "phosphorus": 20, "potassium": 20},
            "simulated": {"nitrogen": 25, "phosphorus": 25, "potassium": 25},
        },
        "soil_moisture": {
            "enabled": True,
            "spi_bus": 0,
            "spi_device": 0,
            "channel": 0,
            "adc_max": 1023,
            "vref": 3.3,
            "dry_adc": 500,
            "wet_adc": 50,
            "samples": 2,
            "read_interval_seconds": 0.1,
            "log_interval_seconds": 0.1,
            "threshold": 30,
            "relay_id": 2,
            "watering_cooldown_seconds": 30,
            "relay_activation_duration_seconds": 1,
            "simulated": {"moisture": 45},
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
            "max_activations_per_month": 10,
            "relay_activation_duration_seconds": 2,
            "pest_classes": ["aphid", "caterpillar", "fungus"],
            "mock": {"mode": "none", "sequence": [], "random": {"detection_probability": 0.25, "confidence_min": 0.50, "confidence_max": 0.98}},
        },
        "camera": {"enabled": True, "type": "usb", "device_path": "auto", "device_index": 0, "width": 640, "height": 480, "fps": 15, "capture_interval_seconds": 1, "image_dir": img_dir},
        "stepper": {"enabled": True, "pins": [23, 24, 25, 26], "steps_per_plant": 512, "delay": 0.003, "simulate": True},
    }


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def test_system_relay_controller_basics(tmp_path):
    """Test relay controller initialization, activation, and watchdog."""
    cfg = _make_config(tmp_path)
    relays = RelayController(cfg["relays"], simulate=True)
    
    try:
        # Verify all relays start off
        for rid in (1, 2, 3):
            assert relays.is_active(rid) is False
            assert relays.available(rid) is True
            entry = relays.get(rid)
            assert entry["active"] is False
            assert entry["deadline"] is None
        
        # Test activation
        ok = relays.activate(1, duration=1.0, trigger="test")
        assert ok is True
        assert relays.is_active(1) is True
        entry = relays.get(1)
        assert entry["trigger"] == "test"
        assert entry["deadline"] is not None
        
        # Test manual deactivate
        relays.deactivate(1)
        assert relays.is_active(1) is False
        entry = relays.get(1)
        assert entry["deadline"] is None
        
        # Test all_off emergency
        relays.activate(1, duration=10, trigger="test")
        relays.activate(2, duration=10, trigger="test")
        relays.activate(3, duration=10, trigger="test")
        assert all(relays.is_active(rid) for rid in (1, 2, 3))
        relays.all_off(reason="test")
        assert not any(relays.is_active(rid) for rid in (1, 2, 3))
    finally:
        relays.shutdown()


def test_system_sensors_read_simulated(tmp_path):
    """Test NPK and soil moisture sensors in simulated mode."""
    cfg = _make_config(tmp_path)
    
    # Test NPK sensor
    npk = NpkSensor(cfg["npk"], simulate=True)
    reading = npk.read()
    assert reading.values["nitrogen"] == 25
    assert reading.values["phosphorus"] == 25
    assert reading.values["potassium"] == 25
    assert reading.timestamp is not None
    npk.close()
    
    # Test soil moisture sensor
    moisture = SoilMoistureSensor(cfg["soil_moisture"], simulate=True)
    reading = moisture.read()
    assert reading.values["moisture"] == 45
    assert reading.timestamp is not None
    moisture.close()


def test_system_stepper_simulated(tmp_path):
    """Test stepper motor in simulated mode."""
    cfg = _make_config(tmp_path)
    stepper = StepperMotor(
        pins=cfg["stepper"]["pins"],
        simulate=True,
    )
    
    # Test step operation (should not error in simulate mode)
    stepper.step(steps=10, delay=0.001, clockwise=True)
    stepper.step(steps=10, delay=0.001, clockwise=False)
    
    # Test cleanup
    stepper.off()


def test_system_camera_simulated(tmp_path, monkeypatch):
    """Test camera in simulated mode."""
    cfg = _make_config(tmp_path)
    
    # Test simulated camera
    from camera.usb_camera import UsbCamera
    cam = UsbCamera(simulate=True, image_dir=tmp_path / "images")
    
    status = cam.status()
    assert status["simulated"] is True
    # In simulated mode, configured is False (no real device)
    assert status["configured"] is False
    assert "Simulated camera" in status["message"]
    
    # Test capture in simulated mode
    out = cam.capture(save_path=tmp_path / "test_shot.jpg")
    assert out is not None
    
    # Simulated camera stream returns empty
    assert list(cam.stream()) == []
    
    cam.close()


def test_system_camera_not_configured():
    """Test NotConfiguredCamera fallback."""
    cam = NotConfiguredCamera()
    status = cam.status()
    assert status["configured"] is False
    assert status["message"] == "Camera not installed"
    assert cam.capture() is None
    assert list(cam.stream()) == []


def test_system_full_integration_with_automation(tmp_path, monkeypatch):
    """Full system integration test: controller with all subsystems."""
    cfg = _make_config(tmp_path)
    
    db = Database(cfg["database"]["path"])
    relays = RelayController(cfg["relays"], simulate=True)
    state = SystemState(cfg)
    sensors = {
        "npk": NpkSensor(cfg["npk"], simulate=True),
        "moisture": SoilMoistureSensor(cfg["soil_moisture"], simulate=True),
    }
    
    ctrl = AutomationController(
        config=cfg, db=db, relays=relays, state=state, sensors=sensors,
    )
    
    try:
        # Attach detector
        from pest_detection.mock_detector import MockDetector
        pest_cfg = cfg["pest_detection"]
        pest_cfg["mock"]["mode"] = "none"
        detector = MockDetector(pest_cfg)
        ctrl.attach_detector(detector)
        
        # Attach camera
        cam = NotConfiguredCamera()
        ctrl.attach_camera(cam)
        
        # Attach stepper
        stepper = StepperMotor(
            pins=cfg["stepper"]["pins"],
            simulate=True,
        )
        ctrl.attach_stepper(stepper)
        
        # Start controller
        ctrl.start()
        time.sleep(0.2)
        
        # Verify controller is running and state is valid
        snap = state.snapshot()
        assert snap["system"]["name"] == "SystemTest"
        assert snap["automation"]["running"] is True
        
        # Verify sensors are readable
        npk_r = sensors["npk"].read()
        moist_r = sensors["moisture"].read()
        assert npk_r.values["nitrogen"] == 25
        assert moist_r.values["moisture"] == 45
        
        # Verify relays functional
        ok, msg = ctrl.activate_relay(1, duration=0.5, trigger="manual", source="test")
        assert ok is True
        assert relays.is_active(1) is True
        _wait_until(lambda: not relays.is_active(1), timeout=2)
        assert relays.is_active(1) is False
        
        # Verify stepper functional
        stepper.step(5, 0.001, True)
        stepper.off()
        
        # Test emergency stop
        relays.activate(2, duration=10, trigger="test")
        relays.activate(3, duration=10, trigger="test")
        ctrl.emergency_stop(source="test")
        time.sleep(0.1)
        assert not any(relays.is_active(rid) for rid in (1, 2, 3))
        
    finally:
        ctrl.stop()
        relays.shutdown()
        sensors["npk"].close()
        sensors["moisture"].close()
        db.close()


def test_system_watchdog_auto_off(tmp_path):
    """Test relay watchdog enforces auto-off."""
    cfg = _make_config(tmp_path)
    cfg["relays"]["auto_off_watchdog_seconds"] = 0.5
    cfg["relays"]["watchdog_check_seconds"] = 0.05
    relays = RelayController(cfg["relays"], simulate=True)
    
    try:
        relays.activate(1, duration=5.0, trigger="test")
        assert relays.is_active(1) is True
        time.sleep(0.7)
        assert relays.is_active(1) is False
    finally:
        relays.shutdown()


def test_system_multiple_subsystems_independent(tmp_path):
    """Test all subsystems work independently without interference."""
    cfg = _make_config(tmp_path)
    
    # Initialize all subsystems
    relays = RelayController(cfg["relays"], simulate=True)
    npk = NpkSensor(cfg["npk"], simulate=True)
    moisture = SoilMoistureSensor(cfg["soil_moisture"], simulate=True)
    stepper = StepperMotor(cfg["stepper"]["pins"], simulate=True)
    cam = NotConfiguredCamera()
    
    try:
        # Use all subsystems
        relays.activate(1, duration=0.1, trigger="test")
        npk.read()
        moisture.read()
        stepper.step(1, 0.001, True)
        cam.capture()
        cam.status()
        relays.all_off(reason="done")
        
        # All should work without errors
        assert relays.is_active(1) is False
    finally:
        relays.shutdown()
        npk.close()
        moisture.close()
        stepper.off()
        cam.close()
