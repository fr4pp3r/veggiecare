"""Tests for dashboard API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from flask import Flask

from dashboard import create_app
from state import SystemState
from automation.controller import AutomationController
from camera.camera import NotConfiguredCamera


#: Smallest config SystemState accepts — every top-level key it indexes.
_MINIMAL_CONFIG = {
    "system": {"name": "Test", "simulate_hardware": True, "timezone": "UTC"},
    "npk": {"thresholds": {"nitrogen": 20, "phosphorus": 20, "potassium": 20}},
    "soil_moisture": {"threshold": 30, "max_activations_per_month": 2},
    "relays": {"items": [
        {"id": 1, "name": "a", "label": "A", "pin": 17, "activation_duration_seconds": 5},
    ]},
    "pest_detection": {"enabled": False, "relay_id": 3, "detector": "mock"},
    "camera": {"enabled": True},
}


@pytest.fixture
def test_app(tmp_path):
    """Create a test Flask app with mocked dependencies."""
    from config import load_config

    cfg = load_config(tmp_path / "test_config.yaml")  # will fail but we don't need it for state

    # Minimal valid config dict for SystemState
    test_cfg = {
        "system": {"name": "Test", "simulate_hardware": True, "timezone": "UTC"},
        "npk": {
            "thresholds": {"nitrogen": 20, "phosphorus": 20, "potassium": 20},
        },
        "soil_moisture": {"threshold": 30, "max_activations_per_month": 2},
        "relays": {
            "items": [
                {"id": 1, "name": "fertilizer", "label": "Fertilizer", "pin": 17, "activation_duration_seconds": 5},
                {"id": 2, "name": "watering", "label": "Watering", "pin": 27, "activation_duration_seconds": 5},
                {"id": 3, "name": "pest_response", "label": "Pest", "pin": 22, "activation_duration_seconds": 5},
            ],
        },
        "pest_detection": {"enabled": False, "relay_id": 3, "detector": "mock"},
        "camera": {"enabled": False},
    }

    state = SystemState(test_cfg)

    # Mock controller
    class MockController:
        def __init__(self):
            self._relays = MockRelays()

        def activate_relay(self, relay_id, duration=None, trigger="manual", source="dashboard"):
            return True, f"Relay {relay_id} activated"

        def emergency_stop(self, source="dashboard"):
            pass

        def pause(self):
            pass

        def resume(self):
            pass

        def inject_pest_detection(self, detected=True, pest_class="aphid", confidence=0.85, source="dashboard_simulate"):
            from pest_detection.detector import DetectionResult
            from datetime import datetime
            return DetectionResult(detected=detected, pest_class=pest_class, confidence=confidence, model="mock")

    class MockRelays:
        def deactivate(self, relay_id):
            pass

    controller = MockController()

    app = create_app(state=state, config=test_cfg, controller=controller,
                     camera=NotConfiguredCamera(), config_path=tmp_path / "test_config.yaml")
    app.config["TESTING"] = True
    return app


@pytest.fixture
def camera_app(tmp_path):
    """Flask app wired to a working fake camera."""

    class WorkingCamera:
        name = "usb_camera"

        def status(self):
            return {
                "configured": True, "name": "usb_camera", "device_path": "/dev/video0",
                "device_index": 0, "width": 640, "height": 480, "fps": 15.0,
                "simulated": False, "error": None, "message": "Connected at 640x480",
            }

        def stream(self, fps=None):
            for _ in range(3):
                yield b"\xff\xd8\xff-jpeg-payload"

        def capture(self, save_path=None):
            path = Path(save_path) if save_path else Path(tmp_path / "snap.jpg")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\xff\xd8\xff-jpeg-snapshot")
            return str(path)

    app = create_app(state=SystemState(_MINIMAL_CONFIG), config=_MINIMAL_CONFIG,
                     camera=WorkingCamera(), config_path=tmp_path / "c.yaml")
    app.config["TESTING"] = True
    return app


def test_api_state_endpoint(test_app):
    client = test_app.test_client()
    resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "system" in data
    assert "sensors" in data
    assert "relays" in data
    assert "usage" in data
    assert "pest" in data
    assert "camera" in data
    assert "database" in data
    assert "automation" in data
    assert "alerts" in data
    assert "thresholds" in data


def test_api_relay_activate(test_app):
    client = test_app.test_client()
    resp = client.post("/api/relays/1/activate", json={"duration": 10})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True


def test_api_relay_off(test_app):
    client = test_app.test_client()
    resp = client.post("/api/relays/2/off")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True


def test_api_emergency_stop(test_app):
    client = test_app.test_client()
    resp = client.post("/api/relays/all-off")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True


def test_api_automation_pause_resume(test_app):
    client = test_app.test_client()
    resp = client.post("/api/automation/pause")
    assert resp.status_code == 200
    resp = client.post("/api/automation/resume")
    assert resp.status_code == 200


def test_api_pest_simulate(test_app):
    client = test_app.test_client()
    resp = client.post("/api/pest/simulate", json={"detected": True, "pest_class": "aphid", "confidence": 0.9})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["detected"] is True
    assert data["pest_class"] == "aphid"


def test_api_detections_empty_without_db(test_app):
    """Without a DB attached, history endpoints return an empty list."""
    client = test_app.test_client()
    resp = client.get("/api/detections")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_api_detections_populated(tmp_path):
    """With a real DB, /api/detections returns pest-detection log rows."""
    from database.database import Database

    from config.config import default_config

    db = Database(tmp_path / "detections.db")
    db.insert_pest_detection(
        detected=True, pest_class="aphid", confidence=0.91,
        image_path=None, model="mock", camera="mock",
    )
    try:
        cfg = default_config()
        cfg["system"]["simulate_hardware"] = True
        state = SystemState(cfg)
        app = create_app(state=state, config=cfg, controller=None, db=db)
        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.get("/api/detections")
        assert resp.status_code == 200
        rows = resp.get_json()
        assert len(rows) == 1
        assert rows[0]["detected"] == 1
        assert rows[0]["pest_class"] == "aphid"
        assert rows[0]["confidence"] == 0.91
        assert rows[0]["model"] == "mock"
    finally:
        db.close()


# ======================================================================
# Settings / config page
# ======================================================================

def test_api_config_schema(test_app):
    client = test_app.test_client()
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "groups" in data
    assert isinstance(data["restart_supported"], bool)
    assert isinstance(data["path"], str)
    assert data["dirty"] is False
    assert data["pending"] is None

    groups = {g["id"]: g for g in data["groups"]}
    assert set(groups) == {"watering", "fertilizer", "pest", "safety", "relays", "system", "npk_recommendations"}
    fields = {f["key"]: f for g in data["groups"] for f in g["fields"]}
    assert fields["soil_moisture.enabled"]["type"] == "toggle"
    # Per-relay pump run times
    assert fields["relays.items.1.activation_duration_seconds"]["type"] == "slider"
    assert fields["relays.items.1.activation_duration_seconds"]["min"] == 5
    assert fields["relays.items.1.activation_duration_seconds"]["max"] == 600
    assert "relays.default_duration_seconds" not in fields
    assert fields["soil_moisture.threshold"]["value"] == 30
    assert fields["soil_moisture.threshold"]["min"] == 0
    assert fields["soil_moisture.threshold"]["max"] == 100
    assert fields["system.timezone"]["type"] == "select"
    assert "Asia/Manila" in fields["system.timezone"]["options"]
    # NPK recommendations fields
    assert fields["npk_recommendations.enabled"]["type"] == "toggle"
    assert fields["npk_recommendations.crop_type"]["type"] == "select"
    assert "tomato" in fields["npk_recommendations.crop_type"]["options"]
    assert fields["npk_recommendations.growth_stage"]["type"] == "select"
    assert "vegetative" in fields["npk_recommendations.growth_stage"]["options"]
    assert fields["npk_recommendations.engine"]["type"] == "select"
    assert "rule_engine" in fields["npk_recommendations.engine"]["options"]


def test_api_settings_save(test_app, tmp_path):
    client = test_app.test_client()
    path = tmp_path / "test_config.yaml"

    resp = client.post("/api/settings", json={"settings": {"soil_moisture.threshold": 45}})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["restart_required"] is True
    assert "Water when soil moisture drops below" in data["changed"]
    assert data["pending"]["fields"] == ["Water when soil moisture drops below"]

    assert path.exists()
    with open(path, "r", encoding="utf-8") as fh:
        saved = yaml.safe_load(fh)
    assert saved["soil_moisture"]["threshold"] == 45

    resp = client.get("/api/config")
    cfg = resp.get_json()
    assert cfg["dirty"] is True
    assert cfg["pending"] is not None


def test_api_settings_save_relay_duration(test_app, tmp_path):
    client = test_app.test_client()
    path = tmp_path / "test_config.yaml"

    resp = client.post(
        "/api/settings",
        json={"settings": {"relays.items.1.activation_duration_seconds": 60}},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "Watering pump (Relay 2)" in data["changed"]

    with open(path, "r", encoding="utf-8") as fh:
        saved = yaml.safe_load(fh)
    assert saved["relays"]["items"][1]["activation_duration_seconds"] == 60
    # Other relays are untouched.
    assert saved["relays"]["items"][0]["activation_duration_seconds"] == 120


def test_api_settings_out_of_range(test_app):
    client = test_app.test_client()
    resp = client.post("/api/settings", json={"settings": {"soil_moisture.threshold": 250}})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["ok"] is False
    assert data["errors"]


def test_api_settings_unknown_key(test_app):
    client = test_app.test_client()
    resp = client.post("/api/settings", json={"settings": {"bogus.key": 1}})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_api_settings_bad_type(test_app):
    client = test_app.test_client()
    resp = client.post("/api/settings", json={"settings": {"soil_moisture.enabled": "yes"}})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_api_settings_preserves_comments(tmp_path):
    from config.config import default_config

    cfg = default_config()
    cfg["system"]["simulate_hardware"] = True
    state = SystemState(cfg)
    yaml_file = tmp_path / "commented.yaml"
    yaml_file.write_text(
        "# keep me\nsoil_moisture:\n  threshold: 30\n",
        encoding="utf-8",
    )
    app = create_app(state=state, config=cfg, controller=None, db=None, config_path=yaml_file)
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.post("/api/settings", json={"settings": {"soil_moisture.threshold": 45}})
    assert resp.status_code == 200
    text = yaml_file.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "threshold: 45" in text


def test_api_system_restart_requires_systemd(test_app, monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    client = test_app.test_client()
    resp = client.post("/api/system/restart")
    assert resp.status_code == 409
    assert "systemd" in resp.get_json()["error"]


# ----------------------------------------------------------------------
# Camera endpoints
# ----------------------------------------------------------------------

def test_camera_page_renders(camera_app):
    resp = camera_app.test_client().get("/camera")
    assert resp.status_code == 200
    assert b"cam-stream" in resp.data


def test_api_camera_status_includes_devices_and_cv2(camera_app, monkeypatch):
    from camera import usb_camera as usb_mod

    monkeypatch.setattr(usb_mod, "list_video_devices", lambda: [
        {"path": "/dev/video0", "index": 0, "name": "usb_camera",
         "capture": True, "resolution": "640x480"},
    ])
    resp = camera_app.test_client().get("/api/camera/status")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["camera"]["configured"] is True
    assert body["camera"]["device_path"] == "/dev/video0"
    assert body["devices"][0]["path"] == "/dev/video0"
    assert "cv2_available" in body


def test_api_camera_stream_emits_mjpeg(camera_app):
    resp = camera_app.test_client().get("/api/camera/stream")

    assert resp.status_code == 200
    # mimetype drops parameters; the boundary lives in the header.
    assert resp.mimetype == "multipart/x-mixed-replace"
    boundary = resp.headers["Content-Type"].split("boundary=")[1]
    assert f"--{boundary}".encode() in resp.data


def test_api_camera_snapshot_returns_jpeg(camera_app):
    resp = camera_app.test_client().get("/api/camera/snapshot")

    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert resp.data.startswith(b"\xff\xd8\xff")


def test_camera_endpoints_report_a_degraded_camera(test_app):
    client = test_app.test_client()

    stream = client.get("/api/camera/stream")
    assert stream.status_code == 503
    assert stream.mimetype == "application/json"
    assert stream.get_json()["ok"] is False

    snap = client.get("/api/camera/snapshot")
    assert snap.status_code == 503
    assert "camera not installed" in snap.get_json()["error"].lower()


def test_api_camera_probe_finds_the_working_node(monkeypatch):
    """A Pi exposes many nodes; only one actually delivers frames."""
    from camera import usb_camera as usb_mod

    # Mirrors a Pi 5: video0 and video19 open but are metadata-only.
    behaviour = {
        "/dev/video0": (True, False),
        "/dev/video1": (True, True),
        "/dev/video19": (True, False),
    }

    class FakeCapture:
        def __init__(self, target):
            self._opens, self._delivers = behaviour[target]
            self.released = False

        def isOpened(self):
            return self._opens

        def get(self, prop):
            return 640 if prop == 3 else 480

        def read(self):
            return (self._delivers, FakeFrame() if self._delivers else None)

        def release(self):
            self.released = True

    class FakeFrame:
        pass

    class FakeCv2:
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4

        def __init__(self):
            self.captures = []

        def VideoCapture(self, target):  # noqa: N802 - mirrors the cv2 API
            capture = FakeCapture(target)
            self.captures.append(capture)
            return capture

    cv2 = FakeCv2()
    monkeypatch.setattr(usb_mod, "load_cv2", lambda: (cv2, None))
    monkeypatch.setattr(usb_mod, "cv2_availability", lambda: (True, None))
    monkeypatch.setattr(usb_mod, "candidate_devices",
                        lambda: ["/dev/video0", "/dev/video1", "/dev/video19"])

    app = create_app(state=SystemState(_MINIMAL_CONFIG), config=_MINIMAL_CONFIG,
                     camera=NotConfiguredCamera(), config_path=None)
    client = app.test_client()

    resp = client.post("/api/camera/probe", json={})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["working"] == ["/dev/video1"]
    assert body["recommended"] == "/dev/video1"
    assert [r["target"] for r in body["results"]] == [
        "/dev/video0", "/dev/video1", "/dev/video19"]
    assert body["results"][0]["delivers_frames"] is False
    assert "metadata-only" in body["results"][0]["error"]
    assert all(c.released for c in cv2.captures), "probe must not leak handles"