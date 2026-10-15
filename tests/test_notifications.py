"""Notification-stream tests: alert ids, fertilizer and pesticide alerts."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from automation.controller import AutomationController
from database.database import Database
from hardware.relay_controller import RelayController
from sensors.base import Reading
from state import SystemState


def _make_config(tmp_path):
    return {
        "system": {"name": "Test", "simulate_hardware": True, "timezone": "UTC", "tick_seconds": 0.05},
        "database": {"path": str(tmp_path / "test.db")},
        "npk": {
            "enabled": True,
            "read_interval_seconds": 0.1,
            "log_interval_seconds": 0.1,
            "thresholds": {"nitrogen": 20, "phosphorus": 20, "potassium": 20},
        },
        "soil_moisture": {
            "enabled": False,
            "read_interval_seconds": 0.1,
            "log_interval_seconds": 0.1,
            "threshold": 30,
            "relay_id": 2,
            "watering_cooldown_seconds": 10,
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
        "pest_detection": {
            "enabled": True,
            "relay_id": 3,
            "detector": "mock",
            "confidence_threshold": 0.70,
            "capture_interval_seconds": 1,
            "max_activations_per_month": 2,
            "relay_activation_duration_seconds": 2,
        },
        "camera": {"enabled": False},
    }


class _StubRecommender:
    name = "stub"

    def __init__(self, result):
        self._result = result

    def recommend(self, values):
        return self._result

    def status(self):
        return {"configured": True, "model": "stub"}


class _StubAdvice:
    def __init__(self):
        self.matched = True
        self.pest_class = "aphid"
        self.treatment_id = "T1"
        self.title = "Aphid control"
        self.severity = "moderate"
        self.summary = "summary"
        self.cultural = ["row covers"]
        self.organic = ["neem oil"]
        self.chemical = ["insecticidal soap"]
        self.notes = []
        self.disclaimer = "d"
        self.model = "stub"

    def to_dict(self):
        return {
            "matched": self.matched,
            "pest_class": self.pest_class,
            "treatment_id": self.treatment_id,
            "title": self.title,
            "severity": self.severity,
            "summary": self.summary,
            "cultural": list(self.cultural),
            "organic": list(self.organic),
            "chemical": list(self.chemical),
            "notes": list(self.notes),
            "disclaimer": self.disclaimer,
            "model": self.model,
        }


class _StubAdvisor:
    name = "stub"

    def suggest(self, pest_class):
        return _StubAdvice()

    def status(self):
        return {"configured": True, "model": "stub"}


def _make_stack(tmp_path):
    cfg = _make_config(tmp_path)
    db = Database(cfg["database"]["path"])
    relays = RelayController(cfg["relays"], simulate=True)
    state = SystemState(cfg)
    sensors = {"npk": MagicMock(), "moisture": MagicMock()}
    sensors["npk"].read.return_value = Reading(
        timestamp=datetime.now(),
        values={"nitrogen": 10, "phosphorus": 10, "potassium": 10},
    )
    ctrl = AutomationController(config=cfg, db=db, relays=relays, state=state, sensors=sensors)
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


class _StubResult:
    """Stand-in for RecommendationResult (avoids the joblib-dependent package import)."""

    def __init__(self, recommended=True, fertilizer_id="F1", fertilizer_name="Tomato feed"):
        self.recommended = recommended
        self.fertilizer_id = fertilizer_id if recommended else None
        self.fertilizer_name = fertilizer_name if recommended else None
        self.dosage_g_per_10L = 5.0 if recommended else None
        self.deficits = {"nitrogen": 20.0} if recommended else {}
        self.reasoning = "nitrogen low" if recommended else None
        self.priority = "high" if recommended else None
        self.timestamp = datetime.now()
        self.model = "stub"


def _recommendation(fertilizer_id="F1", name="Tomato feed"):
    return _StubResult(recommended=True, fertilizer_id=fertilizer_id, fertilizer_name=name)


def test_alerts_have_unique_incrementing_ids(stack):
    _, _, state, _, _, _ = stack
    state.add_alert("INFO", "first", "test")
    state.add_alert("WARNING", "second", "test")
    state.add_alert("ERROR", "third", "test")

    alerts = state.snapshot()["alerts"]
    ids = [a["id"] for a in alerts]
    assert len(ids) == 3
    assert len(set(ids)) == 3
    assert ids == sorted(ids, reverse=True)  # newest first
    assert all(isinstance(i, int) for i in ids)
    assert alerts[0]["message"] == "third"
    assert {"id", "level", "message", "source", "timestamp"} <= set(alerts[0])


def test_fertilizer_alert_fires_once_per_recommendation(stack):
    cfg, ctrl, state, _, _, _ = stack
    ctrl.attach_recommender(_StubRecommender(_recommendation()))
    npk_cfg = cfg["npk"]

    ctrl._read_npk(0.0, npk_cfg)
    ctrl._read_npk(1.0, npk_cfg)

    fert_alerts = [a for a in state.snapshot()["alerts"] if a["source"] == "npk_recommendation"]
    assert len(fert_alerts) == 1
    assert "Tomato feed" in fert_alerts[0]["message"]


def test_fertilizer_alert_refires_after_clearing(stack):
    cfg, ctrl, state, _, _, _ = stack
    npk_cfg = cfg["npk"]

    recorder = _StubRecommender(_recommendation())
    ctrl.attach_recommender(recorder)

    ctrl._read_npk(0.0, npk_cfg)
    assert len([a for a in state.snapshot()["alerts"] if a["source"] == "npk_recommendation"]) == 1

    # All nutrients in range → recommendation cleared.
    recorder._result = _StubResult(recommended=False)
    ctrl._read_npk(1.0, npk_cfg)
    assert ctrl._last_fert_alert_id is None

    # Same fertilizer recommended again → alert fires again.
    recorder._result = _recommendation()
    ctrl._read_npk(2.0, npk_cfg)
    assert len([a for a in state.snapshot()["alerts"] if a["source"] == "npk_recommendation"]) == 2


def test_pest_detection_also_notifies_pesticide(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.attach_treatment_advisor(_StubAdvisor())

    ctrl.inject_pest_detection(detected=True, pest_class="aphid", confidence=0.9)

    alerts = state.snapshot()["alerts"]
    sources = [a["source"] for a in alerts]
    assert "pest_treatment" in sources
    pesticide = next(a for a in alerts if a["source"] == "pest_treatment")
    assert pesticide["message"] == "Pesticide suggested: insecticidal soap"
    assert pesticide["level"] == "INFO"


def test_no_pesticide_alert_without_advisor(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.inject_pest_detection(detected=True, pest_class="aphid", confidence=0.9)
    assert all(a["source"] != "pest_treatment" for a in state.snapshot()["alerts"])


def test_pesticide_label_prefers_chemical_then_title():
    make = AutomationController._pesticide_label
    assert make(None) is None
    assert make({"matched": False, "chemical": ["x"]}) is None
    assert make({"chemical": ["soap"], "organic": ["neem"], "title": "t"}) == "soap"
    assert make({"chemical": [], "organic": ["neem"], "title": "t"}) == "neem"
    assert make({"title": "Aphid control"}) == "Aphid control"
    assert make({"chemical": []}) is None
