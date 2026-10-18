"""Tests for pest scan outcomes: multi-frame best-of, confidence band,
explicit failure reasons, and scan_busy refusals."""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from automation.controller import AutomationController, PestTask
from database.database import Database
from hardware.relay_controller import RelayController
from state import SystemState


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _make_config(tmp_path):
    return {
        "system": {"name": "Test", "simulate_hardware": True, "timezone": "UTC", "tick_seconds": 0.05},
        "database": {"path": str(tmp_path / "test.db")},
        "npk": {"enabled": False, "read_interval_seconds": 0.1, "log_interval_seconds": 0.1,
                "thresholds": {"nitrogen": 20, "phosphorus": 20, "potassium": 20}},
        "soil_moisture": {"enabled": False, "read_interval_seconds": 0.1, "log_interval_seconds": 0.1,
                          "threshold": 30, "relay_id": 2, "watering_cooldown_seconds": 30,
                          "relay_activation_duration_seconds": 1},
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
            "enabled": True,
            "relay_id": 3,
            "detector": "mock",
            "confidence_threshold": 0.70,
            "low_confidence_threshold": 0.45,
            "manual_scan_frames": 5,
            "manual_scan_delay_seconds": 0.0,
            "capture_interval_seconds": 600,
            "max_activations_per_month": 2,
            "relay_activation_duration_seconds": 2,
            "mock": {"mode": "none", "sequence": []},
        },
        "camera": {"enabled": False, "capture_interval_seconds": 1},
        "tanks": {
            "pesticide": {
                "enabled": True,
                "auto_enabled": True,
                "pesticide_type": "test",
                "total_ml": 2000,
                "ml_dispensed_since_refill": 0.0,
                "ml_remaining_est": 2000,
                "status": "full",
                "tank_capacity_ml": 2000,
                "pump_lph": 800,
                "mode": "separate",
                "ml_per_activation": 250,
                "min_dose_seconds": 1.0,
                "soak_days": 0,
            },
        },
    }


def _make_controller(tmp_path, pest_cfg=None):
    cfg = _make_config(tmp_path)
    if pest_cfg is not None:
        cfg["pest_detection"].update(pest_cfg)
    db = Database(cfg["database"]["path"])
    relays = RelayController(cfg["relays"], simulate=True)
    state = SystemState(cfg)
    ctrl = AutomationController(
        config=cfg, db=db, relays=relays, state=state,
        sensors={"npk": MagicMock(), "moisture": MagicMock()},
    )
    return ctrl, state, db, relays, cfg["pest_detection"]


class _Camera:
    """Fake camera: returns paths from a queue, records calls."""

    name = "fake-camera"

    def __init__(self, paths):
        self.paths = list(paths)
        self.calls = 0

    def capture(self, save_path=None):
        self.calls += 1
        if not self.paths:
            return None
        return self.paths.pop(0)


class _StaticDetector:
    """Returns the same DetectionResult for every frame."""

    name = "static"

    def __init__(self, confidence, pest_class="aphid"):
        self.confidence = confidence
        self.pest_class = pest_class
        self.calls = 0

    def detect(self, image_path=None, roi=None):
        from pest_detection.detector import DetectionResult

        self.calls += 1
        return DetectionResult(
            detected=self.confidence is not None and self.confidence >= 0.70,
            pest_class=self.pest_class,
            confidence=self.confidence,
            image_path=image_path,
            model="static",
        )


class _SequenceDetector:
    """Returns confidences in order (last value repeats)."""

    name = "sequence"

    def __init__(self, confidences, pest_class="aphid"):
        self._confidences = list(confidences)
        self.pest_class = pest_class
        self.calls = 0

    def detect(self, image_path=None, roi=None):
        from pest_detection.detector import DetectionResult

        idx = min(self.calls, len(self._confidences) - 1)
        conf = self._confidences[idx]
        self.calls += 1
        return DetectionResult(
            detected=conf is not None and conf >= 0.70,
            pest_class=self.pest_class if conf is not None else None,
            confidence=conf,
            image_path=image_path,
            model="sequence",
        )


class _UnconfiguredStatusDetector(_StaticDetector):
    name = "unconfigured"

    def status(self):
        return {"configured": False, "model": self.name}


class _FakeStepper:
    name = "fake-stepper"
    simulate = True

    def __init__(self):
        self.forward = 0
        self.backward = 0
        self.off_calls = 0

    def step(self, steps, delay=0.002, clockwise=True):
        if clockwise:
            self.forward += steps
        else:
            self.backward += steps

    def off(self):
        self.off_calls += 1


# ----------------------------------------------------------------------
# Multi-frame best-of and early stop
# ----------------------------------------------------------------------

def test_pest_cycle_stops_early_on_confirmed_frame(tmp_path):
    """A frame at/above the threshold ends the scan immediately."""
    ctrl, state, db, relays, pest_cfg = _make_controller(tmp_path, {
        "manual_scan_frames": 5,
        "manual_scan_delay_seconds": 0.0,
    })
    detector = _SequenceDetector([0.3, 0.5, 0.8, 0.9], pest_class="aphid")
    camera = _Camera(["/tmp/f1.jpg", "/tmp/f2.jpg", "/tmp/f3.jpg", "/tmp/f4.jpg", "/tmp/f5.jpg"])
    ctrl.attach_detector(detector)
    ctrl.attach_camera(camera)
    try:
        ctrl._pest_cycle(time.monotonic(), pest_cfg, source="test")

        # Frames 1 (0.3), 2 (0.5), 3 (0.8 >= 0.70 -> confirmed, stop early)
        assert detector.calls == 3
        assert camera.calls == 3

        pest = state.snapshot()["pest"]
        assert pest["status"] == "detected"
        assert pest["reason"] == "ok"
        assert pest["detected"] is True
        assert pest["confidence"] == pytest.approx(0.8)
        assert pest["pest_class"] == "aphid"
        assert pest["frames"] == 3
        assert relays.is_active(3) is True

        rows = db.recent_pest_detections(limit=1)
        assert len(rows) == 1
        assert rows[0]["detected"] == 1
        assert rows[0]["pest_class"] == "aphid"
        assert rows[0]["confidence"] == pytest.approx(0.8)
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


def test_pest_cycle_possible_band_no_relay(tmp_path):
    """Best frame inside the low-confidence band: possible, no relay, no suggestion."""
    ctrl, state, db, relays, pest_cfg = _make_controller(tmp_path, {
        "manual_scan_frames": 2,
        "manual_scan_delay_seconds": 0.0,
        "confidence_threshold": 0.80,
        "low_confidence_threshold": 0.45,
    })
    detector = _StaticDetector(0.5, pest_class="aphid")
    ctrl.attach_detector(detector)
    ctrl.attach_camera(_Camera(["/tmp/f1.jpg", "/tmp/f2.jpg"]))
    try:
        ctrl._pest_cycle(time.monotonic(), pest_cfg, source="test")

        assert detector.calls == 2  # no early stop, 0.5 < 0.80
        pest = state.snapshot()["pest"]
        assert pest["status"] == "possible"
        assert pest["reason"] == "low_confidence"
        assert pest["detected"] is False
        assert pest["pest_class"] == "aphid"
        assert pest["confidence"] == pytest.approx(0.5)
        assert pest["frames"] == 2
        assert pest["suggestion"] is None
        assert relays.is_active(3) is False

        rows = db.recent_pest_detections(limit=1)
        assert len(rows) == 1
        assert rows[0]["detected"] == 0
        assert rows[0]["pest_class"] == "aphid"
        assert rows[0]["confidence"] == pytest.approx(0.5)
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


def test_pest_cycle_clear_band(tmp_path):
    """Best frame below the low band: clear, class hidden, row inserted."""
    ctrl, state, db, relays, pest_cfg = _make_controller(tmp_path, {
        "manual_scan_frames": 2,
        "manual_scan_delay_seconds": 0.0,
    })
    detector = _StaticDetector(0.3, pest_class="aphid")
    ctrl.attach_detector(detector)
    ctrl.attach_camera(_Camera(["/tmp/f1.jpg", "/tmp/f2.jpg"]))
    try:
        ctrl._pest_cycle(time.monotonic(), pest_cfg, source="test")

        pest = state.snapshot()["pest"]
        assert pest["status"] == "clear"
        assert pest["reason"] == "below_threshold"
        assert pest["detected"] is False
        assert pest["pest_class"] is None
        assert pest["confidence"] == pytest.approx(0.3)
        assert pest["frames"] == 2
        assert relays.is_active(3) is False

        rows = db.recent_pest_detections(limit=1)
        assert len(rows) == 1
        assert rows[0]["detected"] == 0
        assert rows[0]["pest_class"] is None
        assert rows[0]["confidence"] == pytest.approx(0.3)
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


# ----------------------------------------------------------------------
# Failure reasons
# ----------------------------------------------------------------------

def test_pest_cycle_capture_none_records_error_no_db_row(tmp_path):
    """capture() returning None -> error/capture_failed and no DB row."""
    ctrl, state, db, relays, pest_cfg = _make_controller(tmp_path)
    detector = _StaticDetector(0.3)
    ctrl.attach_detector(detector)
    ctrl.attach_camera(_Camera([None]))
    try:
        ctrl._pest_cycle(time.monotonic(), pest_cfg, source="test")

        assert detector.calls == 0  # never invoked with a missing frame

        pest = state.snapshot()["pest"]
        assert pest["status"] == "error"
        assert pest["reason"] == "capture_failed"
        assert pest["detected"] is False
        assert pest["error"]
        assert relays.is_active(3) is False

        rows = db.recent_pest_detections(limit=5)
        assert len(rows) == 0  # failed scan writes no row
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


def test_pest_cycle_capture_exception_records_error(tmp_path):
    """capture() raising -> error/capture_failed, no relay."""
    class _ThrowingCamera:
        name = "throwing-camera"

        def capture(self, save_path=None):
            raise RuntimeError("camera unplugged")

    ctrl, state, db, relays, pest_cfg = _make_controller(tmp_path)
    ctrl.attach_detector(_StaticDetector(0.3))
    ctrl.attach_camera(_ThrowingCamera())
    try:
        ctrl._pest_cycle(time.monotonic(), pest_cfg, source="test")

        pest = state.snapshot()["pest"]
        assert pest["status"] == "error"
        assert pest["reason"] == "capture_failed"
        assert "camera unplugged" in pest["error"]
        assert relays.is_active(3) is False
        assert len(db.recent_pest_detections(limit=5)) == 0
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


def test_pest_cycle_unconfigured_model_records_error(tmp_path):
    """Detector status configured=false -> error/model_unavailable, no detect."""
    ctrl, state, db, relays, pest_cfg = _make_controller(tmp_path)
    detector = _UnconfiguredStatusDetector(0.9)
    ctrl.attach_detector(detector)
    ctrl.attach_camera(_Camera(["/tmp/f1.jpg"]))
    try:
        ctrl._pest_cycle(time.monotonic(), pest_cfg, source="test")

        assert detector.calls == 0
        pest = state.snapshot()["pest"]
        assert pest["status"] == "error"
        assert pest["reason"] == "model_unavailable"
        assert relays.is_active(3) is False
        assert len(db.recent_pest_detections(limit=5)) == 0
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


def test_pest_cycle_detector_exception_records_error(tmp_path):
    """detect() raising -> error/exception, suggestion cleared, previous result gone."""
    from pest_detection.detector import DetectionResult

    class _ThrowingDetector:
        name = "throwing"
        configured = True

        def detect(self, image_path=None, roi=None):
            raise RuntimeError("detector boom")

        def status(self):
            return {"configured": True}

    ctrl, state, db, relays, pest_cfg = _make_controller(tmp_path)
    ctrl.attach_detector(_ThrowingDetector())
    ctrl.attach_camera(_Camera(["/tmp/f1.jpg"]))
    try:
        # Establish a prior good scan first.
        state.update_pest(
            detected=True, pest_class="aphid", confidence=0.9, suggestion={"title": "x"},
        )
        ctrl._pest_cycle(time.monotonic(), pest_cfg, source="test")

        pest = state.snapshot()["pest"]
        assert pest["status"] == "error"
        assert pest["reason"] == "exception"
        assert "detector boom" in pest["error"]
        assert pest["suggestion"] is None
        assert pest["detected"] is False
        assert relays.is_active(3) is False
        assert len(db.recent_pest_detections(limit=5)) == 0
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


# ----------------------------------------------------------------------
# Busy refusal
# ----------------------------------------------------------------------

def test_request_pest_scan_busy_keeps_previous_result(tmp_path):
    """Refusing while a scan runs issues scan_busy and preserves the result."""
    ctrl, state, db, relays, pest_cfg = _make_controller(tmp_path)
    detector = _StaticDetector(0.9)
    ctrl.attach_detector(detector)
    ctrl.attach_camera(_Camera(["/tmp/f1.jpg"]))
    ctrl._pest_active = True  # simulate a scan already in flight
    try:
        # Seed a previous result.
        state.update_pest(
            detected=True, pest_class="aphid", confidence=0.9,
            status="detected", reason="ok", frames=1, timestamp="2026-01-01T00:00:00",
        )
        ok, msg = ctrl.request_pest_scan()
        assert ok is False
        assert "already running" in msg

        pest = state.snapshot()["pest"]
        # scan_busy is a merge-only update: the last result stays intact.
        assert pest["status"] == "detected"
        assert pest["reason"] == "scan_busy"
        assert pest["detected"] is True
        assert pest["pest_class"] == "aphid"
        assert pest["confidence"] == pytest.approx(0.9)
        assert pest["frames"] == 1
        assert pest["timestamp"] == "2026-01-01T00:00:00"
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


# ----------------------------------------------------------------------
# Sweep possible band
# ----------------------------------------------------------------------

class _SweepPossibleDetector:
    """Every frame lands in the possible band (0.45 <= c < threshold)."""

    name = "sweep-possible"

    def __init__(self):
        self.calls = 0

    def detect(self, image_path=None, roi=None):
        from pest_detection.detector import DetectionResult

        self.calls += 1
        return DetectionResult(
            detected=False,
            pest_class="aphid",
            confidence=0.6,
            image_path=image_path,
            model="sweep-possible",
        )


class _FakeCamera:
    name = "fake-camera"

    def capture(self, save_path=None):
        return str(save_path) if save_path else "/tmp/fake.jpg"


def _sweep_config(tmp_path):
    cfg = _make_config(tmp_path)
    cfg["pest_detection"]["enabled"] = True
    cfg["pest_detection"]["confidence_threshold"] = 0.70
    cfg["pest_detection"]["low_confidence_threshold"] = 0.45
    cfg["pest_detection"]["analysis_interval_seconds"] = 1.0
    cfg["stepper"] = {
        "enabled": True,
        "pins": [23, 24, 25, 26],
        "delay": 0.05,
        "simulate": True,
        "wheel_diameter_in": 2.125,
        "extension_distance_in": 1.0,
        "steps_per_rev": 200,
    }
    return cfg


def _make_sweep_controller(cfg, detector):
    db = Database(cfg["database"]["path"])
    relays = RelayController(cfg["relays"], simulate=True)
    state = SystemState(cfg)
    ctrl = AutomationController(
        config=cfg, db=db, relays=relays, state=state,
        sensors={"npk": MagicMock(), "moisture": MagicMock()},
    )
    ctrl.attach_stepper(_FakeStepper())
    ctrl.attach_detector(detector)
    ctrl.attach_camera(_FakeCamera())
    return ctrl, state, relays, db


def test_sweep_possible_band_records_no_relay(tmp_path):
    """A full sweep with only possible-band frames records possible, no relay."""
    cfg = _sweep_config(tmp_path)
    detector = _SweepPossibleDetector()
    ctrl, state, relays, db = _make_sweep_controller(cfg, detector)
    try:
        ctrl._run_sweep(PestTask(mode="sweep", source="test"))

        assert detector.calls == 6  # 30 steps / 5 per chunk, no early stop

        pest = state.snapshot()["pest"]
        assert pest["status"] == "possible"
        assert pest["reason"] == "low_confidence"
        assert pest["detected"] is False
        assert pest["pest_class"] == "aphid"
        assert pest["confidence"] == pytest.approx(0.6)
        assert pest["frames"] == 6
        assert relays.is_active(3) is False

        rows = db.recent_pest_detections(limit=5)
        assert len(rows) == 1
        assert rows[0]["detected"] == 0
        assert rows[0]["pest_class"] == "aphid"
        assert rows[0]["confidence"] == pytest.approx(0.6)
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


def test_sweep_clear_band_records_clear(tmp_path):
    """A sweep with frames below the low band records clear."""
    cfg = _sweep_config(tmp_path)
    detector = _SequenceDetector([0.1], pest_class=None)
    ctrl, state, relays, db = _make_sweep_controller(cfg, detector)
    try:
        ctrl._run_sweep(PestTask(mode="sweep", source="test"))

        pest = state.snapshot()["pest"]
        assert pest["status"] == "clear"
        assert pest["reason"] == "below_threshold"
        assert pest["detected"] is False
        assert pest["pest_class"] is None
        assert relays.is_active(3) is False

        rows = db.recent_pest_detections(limit=5)
        assert len(rows) == 1
        assert rows[0]["detected"] == 0
        assert rows[0]["pest_class"] is None
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()
