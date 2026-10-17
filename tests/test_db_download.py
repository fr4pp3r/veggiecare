"""Smoke test for the database download endpoint."""
from __future__ import annotations
import tempfile, os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from automation.controller import AutomationController
from camera.camera import NotConfiguredCamera
from dashboard import create_app
from database.database import Database
from hardware.relay_controller import RelayController
from state import SystemState


def test_database_download_returns_sqlite_file(tmp_path):
    cfg = {
        "system": {"name": "T", "simulate_hardware": True, "timezone": "UTC", "tick_seconds": 0.05},
        "database": {"path": str(tmp_path / "test.db")},
        "npk": {"enabled": False, "thresholds": {"nitrogen": 20, "phosphorus": 20, "potassium": 20}},
        "soil_moisture": {"enabled": False},
        "relays": {"active_high": True, "auto_off_watchdog_seconds": 10, "watchdog_check_seconds": 0.05, "default_duration_seconds": 5, "items": []},
        "pest_detection": {"enabled": False},
        "camera": {"enabled": False},
    }
    state = SystemState(cfg)
    db = Database(cfg["database"]["path"])
    ctrl = AutomationController(config=cfg, db=db, relays=RelayController(cfg["relays"], simulate=True), state=state, sensors={"npk": None, "moisture": MagicMock()})
    app = create_app(state=state, config=cfg, controller=ctrl, db=db, camera=NotConfiguredCamera(), config_path=tmp_path / "c.yaml")
    app.config["TESTING"] = True
    client = app.test_client()

    # seed a row
    db._execute("INSERT INTO system_events (timestamp, level, source, message) VALUES (?,?,?,?)",
                ("2026-01-01T00:00:00", "INFO", "test", "hello"))

    resp = client.get("/api/database/download")
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    assert "veggiecare" in resp.headers.get("Content-Disposition", "")
    # It should be a valid sqlite file
    data = resp.data
    assert data[:16] == b"SQLite format 3\x00", data[:16]
    assert len(data) > 100

    # Verify it's a valid, consistent copy by opening it
    import sqlite3
    tmp = tmp_path / "copy.db"
    tmp.write_bytes(data)
    conn = sqlite3.connect(str(tmp))
    rows = conn.execute("SELECT message FROM system_events").fetchall()
    assert rows == [("hello",)]
    conn.close()

    for c in (ctrl.stop, db.close):
        try: c()
        except Exception: pass