"""Tests for dashboard API endpoints."""

from __future__ import annotations

import pytest
from flask import Flask

from dashboard import create_app
from state import SystemState
from automation.controller import AutomationController


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

    app = create_app(state=state, config=test_cfg, controller=controller)
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