"""End-to-end API tests for the tank (fertilizer/pesticide) endpoints.

These exercise the real ``AutomationController`` through the Flask test
client so the route -> controller -> state contract is covered, not just
the controller in isolation (see ``test_tanks.py`` for the unit level).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from automation.controller import AutomationController
from camera.camera import NotConfiguredCamera
from dashboard import create_app
from database.database import Database
from hardware.relay_controller import RelayController
from state import SystemState


def _make_config(tmp_path):
    return {
        "system": {"name": "Test", "simulate_hardware": True, "timezone": "UTC", "tick_seconds": 0.05},
        "database": {"path": str(tmp_path / "api.db")},
        "npk": {"enabled": False, "thresholds": {"nitrogen": 20, "phosphorus": 20, "potassium": 20}},
        "soil_moisture": {
            "enabled": True, "threshold": 30, "relay_id": 2,
            "watering_cooldown_seconds": 10, "hysteresis": 5,
            "max_watering_seconds_per_day": 1000,
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
        "pest_detection": {"enabled": False, "max_activations_per_month": 2},
        "camera": {"enabled": False},
    }


@pytest.fixture
def api(tmp_path):
    cfg = _make_config(tmp_path)
    state = SystemState(cfg)
    db = Database(cfg["database"]["path"])
    relays = RelayController(cfg["relays"], simulate=True)
    sensors = {"npk": None, "moisture": MagicMock()}
    ctrl = AutomationController(config=cfg, db=db, relays=relays, state=state, sensors=sensors)
    app = create_app(
        state=state, config=cfg, controller=ctrl, db=db,
        camera=NotConfiguredCamera(), config_path=tmp_path / "c.yaml",
    )
    app.config["TESTING"] = True
    yield app.test_client(), state, db
    for closer in (ctrl.stop, relays.shutdown, db.close):
        try:
            closer()
        except Exception:
            pass


# ----------------------------------------------------------------------
# Fertilizer refill
# ----------------------------------------------------------------------

def test_refill_fertilizer_returns_ok_and_updates_state(api):
    client, state, _ = api
    resp = client.post("/api/tanks/fertilizer/refill", json={
        "fertilizer_type": "NPK 10-10-10",
        "npk_n": 10, "npk_p": 10, "npk_k": 10,
        "total_ml": 1500,
        "tank_capacity_ml": 5000,
        "fert_ml_per_cycle": 250,
        "soak_days": 7,
        "mode": "with_watering",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    tank = state.snapshot()["tanks"]["fertilizer"]
    assert tank["fertilizer_type"] == "NPK 10-10-10"
    assert tank["ml_remaining_est"] == 1500
    assert tank["enabled"] is True


def test_refill_fertilizer_defaults_dose_when_omitted(api):
    """Dose is DERIVED from the relay cycle (2 s) x pump (800 L/H)."""
    client, state, _ = api
    resp = client.post("/api/tanks/fertilizer/refill", json={"total_ml": 1200})
    assert resp.status_code == 200
    derived = 800.0 * 1000.0 * 2.0 / 3600.0
    body = resp.get_json()
    assert body["fert_ml_per_cycle"] == pytest.approx(derived, abs=0.01)
    assert body["dose_cycle_seconds"] == pytest.approx(2.0, abs=0.001)
    assert body["ml_remaining_est"] == pytest.approx(1200.0)
    assert body["pump_lph"] == pytest.approx(800.0)
    tank = state.snapshot()["tanks"]["fertilizer"]
    assert tank["fert_ml_per_cycle"] == pytest.approx(derived, abs=0.01)
    assert tank["dose_cycle_seconds"] == pytest.approx(2.0, abs=0.001)


def test_refill_uses_litres_and_derives_dose(api):
    """Form contract: total_liters in, derived dose + cycle seconds out."""
    client, state, _ = api
    resp = client.post("/api/tanks/fertilizer/refill", json={"total_liters": 5.0})
    assert resp.status_code == 200
    derived = 800.0 * 1000.0 * 2.0 / 3600.0  # ~444.4 mL
    tank = state.snapshot()["tanks"]["fertilizer"]
    assert tank["total_ml"] == pytest.approx(5000.0)
    assert tank["ml_remaining_est"] == pytest.approx(5000.0)
    assert tank["fert_ml_per_cycle"] == pytest.approx(derived, abs=0.01)
    assert tank["dose_cycle_seconds"] == pytest.approx(2.0, abs=0.001)


def test_refill_capacity_bumps_to_refill_amount(api):
    """Refilling more than the declared capacity raises the capacity."""
    client, state, _ = api
    resp = client.post("/api/tanks/fertilizer/refill", json={"total_liters": 10.0})
    assert resp.status_code == 200
    tank = state.snapshot()["tanks"]["fertilizer"]
    assert tank["total_ml"] == pytest.approx(10000.0)
    assert tank["tank_capacity_ml"] == pytest.approx(10000.0)
    assert resp.get_json()["tank_capacity_ml"] == pytest.approx(10000.0)


def test_refill_rejects_unmeterable_dose_with_400(api):
    client, _, _ = api
    resp = client.post("/api/tanks/fertilizer/refill", json={
        "total_ml": 1500, "fert_ml_per_cycle": 150,
    })
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert "at least" in body["message"]


def test_refill_patches_unspecified_fields(api):
    """A refill must not silently reset settings the operator never sent.

    The dose itself is now re-derived each refill (pump_lph x relay cycle),
    so it IS allowed to change; soak window, floor and pump rate are inherited.
    """
    client, state, _ = api
    client.post("/api/tanks/fertilizer/refill", json={
        "total_ml": 1500,
        "fert_ml_per_cycle": 400,
        "soak_days": 3,
        "min_dose_seconds": 2.0,
        "pump_lph": 400,
    })
    # Top up without re-specifying the dose settings: they must be inherited
    # rather than reset to the route defaults.
    client.post("/api/tanks/fertilizer/refill", json={"total_ml": 2000})
    tank = state.snapshot()["tanks"]["fertilizer"]
    # Derived dose: 400 L/h x 2 s (relay cycle) = ~222.2 mL
    assert tank["fert_ml_per_cycle"] == pytest.approx(400.0 * 1000.0 * 2.0 / 3600.0, abs=0.01)
    assert tank["soak_days"] == 3
    assert tank["min_dose_seconds"] == 2.0
    assert tank["pump_lph"] == 400
    assert tank["ml_remaining_est"] == 2000


def test_refill_rejects_liters_that_are_not_numbers(api):
    client, _, _ = api
    resp = client.post("/api/tanks/fertilizer/refill", json={"total_liters": "five"})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_refill_rejects_zero_amount(api):
    client, _, _ = api
    resp = client.post("/api/tanks/fertilizer/refill", json={"total_ml": 0})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_tolerates_garbage_numeric_fields(api):
    """Non-numeric JSON values must not raise a 500."""
    client, _, _ = api
    resp = client.post("/api/tanks/fertilizer/refill", json={
        "total_ml": 1000, "npk_n": "abc", "pump_lph": None,
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


# ----------------------------------------------------------------------
# Pesticide refill
# ----------------------------------------------------------------------

def test_refill_pesticide_returns_ok(api):
    client, state, _ = api
    resp = client.post("/api/tanks/pesticide/refill", json={
        "pesticide_type": "Neem", "total_ml": 2000, "ml_per_activation": 250,
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    tank = state.snapshot()["tanks"]["pesticide"]
    assert tank["pesticide_type"] == "Neem"
    assert tank["ml_remaining_est"] == 2000


def test_refill_pesticide_rejects_unmeterable_dose(api):
    client, _, _ = api
    resp = client.post("/api/tanks/pesticide/refill", json={
        "total_ml": 2000, "ml_per_activation": 100,
    })
    assert resp.status_code == 400
    assert "at least" in resp.get_json()["message"]


# ----------------------------------------------------------------------
# Auto / enable toggles
# ----------------------------------------------------------------------

@pytest.mark.parametrize("tank", ["fertilizer", "pesticide"])
def test_auto_toggle_round_trip(api, tank):
    client, state, _ = api
    resp = client.post(f"/api/tanks/{tank}/auto", json={"enabled": True})
    assert resp.status_code == 200
    assert state.snapshot()["tanks"][tank]["auto_enabled"] is True
    client.post(f"/api/tanks/{tank}/auto", json={"enabled": False})
    assert state.snapshot()["tanks"][tank]["auto_enabled"] is False


def test_enable_endpoint_toggles_enabled_flag(api):
    client, state, _ = api
    resp = client.post("/api/tanks/fertilizer/enable", json={"enabled": True})
    assert resp.status_code == 200
    assert state.snapshot()["tanks"]["fertilizer"]["enabled"] is True


# ----------------------------------------------------------------------
# Validation / contract
# ----------------------------------------------------------------------

@pytest.mark.parametrize("tank", ["bogus", "water", "npk"])
def test_unknown_tank_name_is_rejected(api, tank):
    client, _, _ = api
    resp = client.post(f"/api/tanks/{tank}/refill", json={"total_ml": 100})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_unknown_tank_auto_and_enable_rejected(api):
    client, _, _ = api
    assert client.post("/api/tanks/bogus/auto", json={"enabled": True}).status_code == 400
    assert client.post("/api/tanks/bogus/enable", json={"enabled": True}).status_code == 400


def test_state_endpoint_exposes_tanks(api):
    client, _, _ = api
    body = client.get("/api/state").get_json()
    assert "tanks" in body
    assert set(body["tanks"]) == {"fertilizer", "pesticide"}


def test_refill_without_controller_returns_503(tmp_path):
    cfg = _make_config(tmp_path)
    app = create_app(
        state=SystemState(cfg), config=cfg, controller=None,
        camera=NotConfiguredCamera(), config_path=tmp_path / "c.yaml",
    )
    app.config["TESTING"] = True
    resp = app.test_client().post("/api/tanks/fertilizer/refill", json={"total_ml": 100})
    assert resp.status_code == 503
