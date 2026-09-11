"""Tests for the database layer."""

from __future__ import annotations

import pytest
from database.database import Database, DatabaseError


def test_database_creates_schema(temp_db_path):
    db = Database(temp_db_path)
    # Tables should exist
    tables = db._query("SELECT name FROM sqlite_master WHERE type='table'")
    names = {row["name"] for row in tables}
    expected = {
        "npk_readings", "moisture_readings", "relay_activations",
        "blocked_activations", "pest_detections", "system_events", "config_changes",
    }
    assert expected.issubset(names)
    db.close()


def test_npk_reading_insert_and_history(temp_db_path):
    db = Database(temp_db_path)
    db.insert_npk_reading(25.0, 15.0, 30.0, {"nitrogen": False, "phosphorus": True, "potassium": False})
    db.insert_npk_reading(18.0, 22.0, 28.0, {"nitrogen": True, "phosphorus": False, "potassium": False})
    history = db.npk_history(limit=10)
    assert len(history) == 2
    assert history[0]["nitrogen"] == 25.0
    assert history[1]["nitrogen"] == 18.0
    db.close()


def test_moisture_reading_insert_and_history(temp_db_path):
    db = Database(temp_db_path)
    db.insert_moisture_reading(45.0, False)
    db.insert_moisture_reading(25.0, True)
    history = db.moisture_history(limit=10)
    assert len(history) == 2
    assert history[0]["moisture"] == 45.0
    assert history[1]["moisture"] == 25.0


def test_relay_activation_logging(temp_db_path):
    db = Database(temp_db_path)
    db.insert_relay_activation(1, "fertilizer", "manual", 120, "dashboard")
    db.insert_relay_activation(2, "watering", "automatic", 300, "automation")
    history = db.activation_history(limit=10)
    assert len(history) == 2
    assert history[0]["trigger_type"] == "automatic"  # newest first
    assert history[1]["trigger_type"] == "manual"
    db.close()


def test_blocked_activation_logging(temp_db_path):
    db = Database(temp_db_path)
    db.insert_blocked_activation(2, "Monthly limit reached")
    # No direct query helper; verify via raw SQL
    rows = db._query("SELECT * FROM blocked_activations")
    assert len(rows) == 1
    assert rows[0]["relay_id"] == 2
    assert rows[0]["reason"] == "Monthly limit reached"
    db.close()


def test_pest_detection_logging(temp_db_path):
    db = Database(temp_db_path)
    db.insert_pest_detection(True, "aphid", 0.91, "/img.jpg", "mock", "not_configured")
    db.insert_pest_detection(False, None, None, None, "mock", "not_configured")
    rows = db.recent_pest_detections(limit=10)
    assert len(rows) == 2
    assert rows[0]["detected"] == 0  # newest first (the False entry)
    assert rows[0]["pest_class"] is None
    assert rows[0]["confidence"] is None
    assert rows[1]["detected"] == 1
    assert rows[1]["pest_class"] == "aphid"
    assert rows[1]["confidence"] == 0.91
    db.close()


def test_monthly_activation_count(temp_db_path):
    db = Database(temp_db_path)
    # Insert two activations in current month
    from datetime import datetime
    now = datetime.now()
    db.insert_relay_activation(2, "watering", "automatic", 300, "automation", timestamp=f"{now:%Y-%m-%d %H:%M:%S}")
    db.insert_relay_activation(2, "watering", "automatic", 300, "automation", timestamp=f"{now:%Y-%m-%d %H:%M:%S}")
    count = db.count_relay_activations(2, now.year, now.month)
    assert count == 2
    # Different month should be 0
    other_month = 1 if now.month == 12 else now.month + 1
    other_year = now.year + 1 if now.month == 12 else now.year
    count2 = db.count_relay_activations(2, other_year, other_month)
    assert count2 == 0
    db.close()


def test_system_events_logging(temp_db_path):
    db = Database(temp_db_path)
    db.insert_event("INFO", "test", "hello")
    db.insert_event("WARNING", "test", "watch out")
    db.insert_event("ERROR", "test", "boom")
    events = db.recent_events(limit=10, min_level="WARNING")
    assert len(events) == 2
    assert events[0]["level"] == "ERROR"
    assert events[1]["level"] == "WARNING"
    db.close()


def test_db_health_check(temp_db_path):
    db = Database(temp_db_path)
    ok, err = db.health()
    assert ok is True
    assert err is None
    db.close()