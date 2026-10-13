"""Integration tests for pest treatment suggestion wiring (Lane B)."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from automation.controller import AutomationController
from database.database import Database
from hardware.relay_controller import RelayController
from state import SystemState


def _make_config(tmp_path):
    return {
        "system": {"name": "Test", "simulate_hardware": True, "timezone": "UTC", "tick_seconds": 0.05},
        "database": {"path": str(tmp_path / "test.db")},
        "npk": {
            "enabled": False,
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
            "enabled": True,
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


class _StubAdvice:
    def __init__(self, pest_class):
        self.matched = True
        self.pest_class = pest_class
        self.treatment_id = "T1"
        self.title = "Treat it"
        self.severity = "low"
        self.summary = "summary"
        self.cultural = ["c"]
        self.organic = ["o"]
        self.chemical = ["ch"]
        self.notes = ["n"]
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
        return _StubAdvice(pest_class)

    def status(self):
        return {"configured": True, "model": "stub"}


def _make_controller(tmp_path, advisor=None):
    cfg = _make_config(tmp_path)
    db = Database(cfg["database"]["path"])
    relays = RelayController(cfg["relays"], simulate=True)
    state = SystemState(cfg)
    sensors = {"npk": MagicMock(), "moisture": MagicMock()}
    npk_reading = None
    try:
        from sensors.base import Reading
        npk_reading = Reading(timestamp=datetime.now(), values={"nitrogen": 25, "phosphorus": 25, "potassium": 25})
        moist_reading = Reading(timestamp=datetime.now(), values={"moisture": 45})
        sensors["npk"].read.return_value = npk_reading
        sensors["moisture"].read.return_value = moist_reading
    except Exception:
        pass

    ctrl = AutomationController(config=cfg, db=db, relays=relays, state=state, sensors=sensors)
    from camera.camera import NotConfiguredCamera
    ctrl.attach_camera(NotConfiguredCamera())
    if advisor is not None:
        ctrl.attach_treatment_advisor(advisor)
    return ctrl, state, db, relays


def test_controller_stores_suggestion_on_detection(tmp_path):
    advisor = _StubAdvisor()
    ctrl, state, db, relays = _make_controller(tmp_path, advisor=advisor)
    try:
        ctrl.inject_pest_detection(detected=True, pest_class="aphid", confidence=0.85)
        snap = state.snapshot()
        sug = snap["pest"]["suggestion"]
        assert sug is not None
        assert sug["pest_class"] == "aphid"
        assert sug["title"] == "Treat it"
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


def test_controller_clears_suggestion_on_non_detection(tmp_path):
    advisor = _StubAdvisor()
    ctrl, state, db, relays = _make_controller(tmp_path, advisor=advisor)
    try:
        ctrl.inject_pest_detection(detected=True, pest_class="aphid", confidence=0.85)
        assert state.snapshot()["pest"]["suggestion"] is not None
        ctrl.inject_pest_detection(detected=False, pest_class="aphid", confidence=0.85)
        assert state.snapshot()["pest"]["suggestion"] is None
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


def test_insert_and_recent_round_trip_suggestion(tmp_path):
    db = Database(tmp_path / "rt.db")
    try:
        suggestion = {"matched": True, "pest_class": "aphid", "title": "Treat"}
        db.insert_pest_detection(
            detected=True, pest_class="aphid", confidence=0.91,
            image_path=None, model="mock", camera="mock", suggestion=suggestion,
        )
        rows = db.recent_pest_detections(limit=1)
        assert len(rows) == 1
        assert rows[0]["suggestion"] == suggestion
        # null case
        db.insert_pest_detection(
            detected=False, pest_class=None, confidence=None,
            image_path=None, model="mock", camera="mock", suggestion=None,
        )
        rows2 = db.recent_pest_detections(limit=1)
        assert rows2[0]["suggestion"] is None
    finally:
        db.close()


def test_recent_pest_decodes_malformed_json_as_none(tmp_path):
    db = Database(tmp_path / "mal.db")
    try:
        # insert raw malformed suggestion via direct SQL bypass? Use insert then tamper.
        db.insert_pest_detection(
            detected=True, pest_class="aphid", confidence=0.9,
            image_path=None, model="mock", camera="mock", suggestion={"a": 1},
        )
        # tamper the stored value to malformed JSON
        db._execute("UPDATE pest_detections SET suggestion = ? WHERE id = (SELECT MAX(id) FROM pest_detections)", ("{not json",))
        rows = db.recent_pest_detections(limit=1)
        assert rows[0]["suggestion"] is None
    finally:
        db.close()


def test_db_migration_adds_suggestion_column(tmp_path):
    dbpath = tmp_path / "old.db"
    conn = sqlite3.connect(str(dbpath))
    conn.execute(
        """CREATE TABLE pest_detections (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            detected    INTEGER NOT NULL DEFAULT 0,
            pest_class  TEXT,
            confidence  REAL,
            image_path  TEXT,
            model       TEXT,
            camera      TEXT
        )"""
    )
    conn.commit()
    conn.close()

    cols_before = None
    try:
        c2 = sqlite3.connect(str(dbpath))
        cols_before = [r[1] for r in c2.execute("PRAGMA table_info(pest_detections)").fetchall()]
        c2.close()
    except Exception:
        pass
    assert "suggestion" not in (cols_before or [])

    db = Database(dbpath)
    try:
        cols = [r[1] for r in db._query("PRAGMA table_info(pest_detections)")]
        assert "suggestion" in cols
        # insert with suggestion works against migrated DB
        db.insert_pest_detection(
            detected=True, pest_class="aphid", confidence=0.5,
            image_path=None, model="m", camera="c", suggestion={"x": 2},
        )
        rows = db.recent_pest_detections(limit=1)
        assert rows[0]["suggestion"] == {"x": 2}
    finally:
        db.close()


def test_suggest_treatment_none_when_no_advisor(tmp_path):
    ctrl, state, db, relays = _make_controller(tmp_path, advisor=None)
    try:
        assert ctrl.suggest_treatment("aphid") is None
        assert ctrl.suggest_treatment(None) is None
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


def test_suggest_treatment_returns_dict(tmp_path):
    advisor = _StubAdvisor()
    ctrl, state, db, relays = _make_controller(tmp_path, advisor=advisor)
    try:
        res = ctrl.suggest_treatment("aphid")
        assert isinstance(res, dict)
        assert res["pest_class"] == "aphid"
        # None class still produces advice (stub doesn't care)
        res2 = ctrl.suggest_treatment(None)
        assert isinstance(res2, dict)
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


def test_suggest_treatment_never_raises_when_advisor_throws(tmp_path):
    class _ThrowingAdvisor:
        name = "throwing"

        def suggest(self, pest_class):
            raise RuntimeError("boom")

        def status(self):
            return {}

    ctrl, state, db, relays = _make_controller(tmp_path, advisor=_ThrowingAdvisor())
    try:
        assert ctrl.suggest_treatment("aphid") is None
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


def test_controller_does_not_crash_when_advisor_throws_during_detection(tmp_path):
    class _ThrowingAdvisor:
        name = "throwing"

        def suggest(self, pest_class):
            raise RuntimeError("boom")

        def status(self):
            return {}

    ctrl, state, db, relays = _make_controller(tmp_path, advisor=_ThrowingAdvisor())
    try:
        ctrl.inject_pest_detection(detected=True, pest_class="aphid", confidence=0.85)
        assert state.snapshot()["pest"]["suggestion"] is None
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()


def test_dashboard_treatment_endpoint(tmp_path):
    from dashboard import create_app
    from camera.camera import NotConfiguredCamera

    advisor = _StubAdvisor()
    ctrl, state, db, relays = _make_controller(tmp_path, advisor=advisor)
    cfg = _make_config(tmp_path)
    app = create_app(state=state, config=cfg, controller=ctrl, db=db, camera=NotConfiguredCamera())
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.get("/api/pest/treatment?class=aphid")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["class"] == "aphid"
    assert isinstance(data["advice"], dict)

    resp2 = client.get("/api/pest/treatment")
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2["class"] is None
    assert data2["advice"] is None or isinstance(data2["advice"], dict)

    # 503 when no controller
    app2 = create_app(state=state, config=cfg, controller=None, db=db, camera=NotConfiguredCamera())
    app2.config["TESTING"] = True
    assert app2.test_client().get("/api/pest/treatment").status_code == 503

    ctrl.stop()
    relays.shutdown()
    db.close()



def test_pest_cycle_clears_stale_suggestion_on_detection_failure(tmp_path):
    """A detector failure during a pest cycle must clear any stale suggestion."""

    class _ThrowingDetector:
        name = "throwing"

        def detect(self, image_path=None):
            raise RuntimeError("detector boom")

    advisor = _StubAdvisor()
    ctrl, state, db, relays = _make_controller(tmp_path, advisor=advisor)
    try:
        # First establish a stale suggestion from a successful detection.
        ctrl.inject_pest_detection(detected=True, pest_class="aphid", confidence=0.85)
        assert state.snapshot()["pest"]["suggestion"] is not None

        # Trigger the camera-cycle error path.
        pest_cfg = _make_config(tmp_path)["pest_detection"]
        ctrl.attach_detector(_ThrowingDetector())
        ctrl._pest_cycle(__import__("time").monotonic(), pest_cfg)

        assert state.snapshot()["pest"]["error"] is not None
        assert state.snapshot()["pest"]["suggestion"] is None
    finally:
        ctrl.stop()
        relays.shutdown()
        db.close()
