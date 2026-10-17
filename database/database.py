"""SQLite persistence layer for VeggieCare.

All writes are serialized through a single connection protected by a lock
(WAL mode + busy timeout). Timestamps are stored as local-time ISO strings.
Database failures raise :class:`DatabaseError`; callers (the automation
controller) catch them, log to the file logger and surface the failure on
the dashboard - a database outage must never crash the hardware control
loop.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS npk_readings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT NOT NULL,
    nitrogen       REAL NOT NULL,
    phosphorus     REAL NOT NULL,
    potassium      REAL NOT NULL,
    nitrogen_below INTEGER NOT NULL DEFAULT 0,
    phosphorus_below INTEGER NOT NULL DEFAULT 0,
    potassium_below INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_npk_timestamp ON npk_readings(timestamp);

CREATE TABLE IF NOT EXISTS moisture_readings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    moisture        REAL NOT NULL,
    below_threshold INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_moisture_timestamp ON moisture_readings(timestamp);

CREATE TABLE IF NOT EXISTS relay_activations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    relay_id        INTEGER NOT NULL,
    relay_name      TEXT,
    trigger_type    TEXT NOT NULL CHECK (trigger_type IN ('manual','automatic')),
    duration_seconds INTEGER,
    source          TEXT
);
CREATE INDEX IF NOT EXISTS idx_relay_activations ON relay_activations(relay_id, timestamp);

CREATE TABLE IF NOT EXISTS blocked_activations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    relay_id  INTEGER NOT NULL,
    reason    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pest_detections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    detected    INTEGER NOT NULL DEFAULT 0,
    pest_class  TEXT,
    confidence  REAL,
    image_path  TEXT,
    model       TEXT,
    camera      TEXT,
    suggestion  TEXT
);
CREATE INDEX IF NOT EXISTS idx_pest_detections_timestamp ON pest_detections(timestamp);

CREATE TABLE IF NOT EXISTS npk_recommendations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    nitrogen        REAL NOT NULL,
    phosphorus      REAL NOT NULL,
    potassium       REAL NOT NULL,
    fertilizer_id   TEXT,
    fertilizer_name TEXT,
    dosage_g_per_10L REAL,
    deficits        TEXT,  -- JSON string
    reasoning       TEXT,
    priority        TEXT,
    model           TEXT
);
CREATE INDEX IF NOT EXISTS idx_npk_rec_timestamp ON npk_recommendations(timestamp);

CREATE TABLE IF NOT EXISTS system_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level     TEXT NOT NULL CHECK (level IN ('INFO','WARNING','ERROR','CRITICAL')),
    source    TEXT,
    message   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_system_events_timestamp ON system_events(timestamp);

CREATE TABLE IF NOT EXISTS config_changes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    key       TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    source    TEXT
);
"""

_LEVELS = ("INFO", "WARNING", "ERROR", "CRITICAL")


class DatabaseError(Exception):
    """Raised when a SQLite operation fails."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    """Lightweight, thread-safe wrapper around a SQLite database."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            # Idempotent migration: add suggestion column to pre-existing DBs.
            try:
                cols = [r[1] for r in self._conn.execute("PRAGMA table_info(pest_detections)").fetchall()]
                if "suggestion" not in cols:
                    self._conn.execute("ALTER TABLE pest_detections ADD COLUMN suggestion TEXT")
                    self._conn.commit()
            except sqlite3.Error as exc:
                raise DatabaseError(f"cannot migrate database {self.path}: {exc}") from exc
            try:
                cols = [r[1] for r in self._conn.execute("PRAGMA table_info(system_events)").fetchall()]
                # ensure columns exist
            except sqlite3.Error:
                pass
            # add tank event tables if missing
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS tank_refills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    tank TEXT NOT NULL,
                    tank_type TEXT,
                    npk_n REAL DEFAULT 0,
                    npk_p REAL DEFAULT 0,
                    npk_k REAL DEFAULT 0,
                    total_ml REAL DEFAULT 0,
                    ml_per_dose_or_cycle REAL DEFAULT 0,
                    max_doses INTEGER DEFAULT 0,
                    mode TEXT,
                    pump_lph REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tank_auto_doses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    tank TEXT NOT NULL,
                    tank_type TEXT,
                    npk_n REAL DEFAULT 0,
                    npk_p REAL DEFAULT 0,
                    npk_k REAL DEFAULT 0,
                    ml_dispensed REAL DEFAULT 0,
                    activation_seconds REAL DEFAULT 0,
                    doses_used_before INTEGER DEFAULT 0,
                    doses_used_after INTEGER DEFAULT 0,
                    ml_remaining_est REAL DEFAULT 0,
                    combined_with_watering INTEGER DEFAULT 0,
                    mode TEXT
                );
                CREATE TABLE IF NOT EXISTS tank_auto_dose_blocked (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    tank TEXT NOT NULL,
                    reason TEXT,
                    cooldown_until_at TEXT,
                    blocked_until_at TEXT,
                    doses_used INTEGER DEFAULT 0,
                    max_doses INTEGER DEFAULT 0,
                    ml_remaining_est REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tank_auto_dose_deferred (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    tank TEXT NOT NULL,
                    reason TEXT
                );
            """)
            self._conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"cannot open database {self.path}: {exc}") from exc

    # ------------------------------------------------------------------
    # low-level helpers
    # ------------------------------------------------------------------

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            try:
                cursor = self._conn.execute(sql, tuple(params))
                self._conn.commit()
                return cursor
            except sqlite3.Error as exc:
                raise DatabaseError(f"SQLite error: {exc}") from exc

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return self._conn.execute(sql, tuple(params)).fetchall()
            except sqlite3.Error as exc:
                raise DatabaseError(f"SQLite error: {exc}") from exc

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------

    def health(self) -> tuple[bool, str | None]:
        """Return (ok, error_message); runs a trivial query."""
        try:
            self._query("SELECT 1")
            return True, None
        except DatabaseError as exc:
            return False, str(exc)

    # ------------------------------------------------------------------
    # inserts
    # ------------------------------------------------------------------

    def insert_npk_reading(self, nitrogen: float, phosphorus: float, potassium: float,
                           below: dict[str, bool], timestamp: str | None = None) -> None:
        self._execute(
            """INSERT INTO npk_readings
               (timestamp, nitrogen, phosphorus, potassium,
                nitrogen_below, phosphorus_below, potassium_below)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (timestamp or _now(), nitrogen, phosphorus, potassium,
             int(below.get("nitrogen", False)), int(below.get("phosphorus", False)),
             int(below.get("potassium", False))),
        )

    def insert_moisture_reading(self, moisture: float, below: bool,
                                timestamp: str | None = None) -> None:
        self._execute(
            "INSERT INTO moisture_readings (timestamp, moisture, below_threshold) VALUES (?, ?, ?)",
            (timestamp or _now(), moisture, int(below)),
        )

    def insert_relay_activation(self, relay_id: int, relay_name: str, trigger_type: str,
                                duration_seconds: int | None, source: str,
                                timestamp: str | None = None) -> None:
        if trigger_type not in ("manual", "automatic"):
            raise ValueError(f"invalid trigger_type {trigger_type!r}")
        self._execute(
            """INSERT INTO relay_activations
               (timestamp, relay_id, relay_name, trigger_type, duration_seconds, source)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (timestamp or _now(), relay_id, relay_name, trigger_type, duration_seconds, source),
        )

    def insert_blocked_activation(self, relay_id: int, reason: str,
                                  timestamp: str | None = None) -> None:
        self._execute(
            "INSERT INTO blocked_activations (timestamp, relay_id, reason) VALUES (?, ?, ?)",
            (timestamp or _now(), relay_id, reason),
        )

    def insert_pest_detection(self, detected: bool, pest_class: str | None,
                              confidence: float | None, image_path: str | None,
                              model: str | None, camera: str | None,
                              timestamp: str | None = None,
                              suggestion: dict | None = None) -> None:
        self._execute(
            """INSERT INTO pest_detections
               (timestamp, detected, pest_class, confidence, image_path, model, camera, suggestion)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp or _now(), int(detected), pest_class, confidence, image_path, model, camera,
             json.dumps(suggestion) if suggestion else None),
        )

    def insert_npk_recommendation(self, nitrogen: float, phosphorus: float, potassium: float,
                                   fertilizer_id: str | None, fertilizer_name: str | None,
                                   dosage_g_per_10L: float | None, deficits: dict | None,
                                   reasoning: str | None, priority: str | None, model: str,
                                   timestamp: str | None = None) -> None:
        self._execute(
            """INSERT INTO npk_recommendations
               (timestamp, nitrogen, phosphorus, potassium,
                fertilizer_id, fertilizer_name, dosage_g_per_10L,
                deficits, reasoning, priority, model)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp or _now(), nitrogen, phosphorus, potassium,
             fertilizer_id, fertilizer_name, dosage_g_per_10L,
             json.dumps(deficits) if deficits else None,
             reasoning, priority, model),
        )

    def insert_event(self, level: str, source: str, message: str,
                     timestamp: str | None = None) -> None:
        if level not in _LEVELS:
            raise ValueError(f"invalid level {level!r}")
        self._execute(
            "INSERT INTO system_events (timestamp, level, source, message) VALUES (?, ?, ?, ?)",
            (timestamp or _now(), level, source, message),
        )

    def insert_config_change(self, key: str, old_value: str | None, new_value: str | None,
                             source: str, timestamp: str | None = None) -> None:
        self._execute(
            "INSERT INTO config_changes (timestamp, key, old_value, new_value, source) VALUES (?, ?, ?, ?, ?)",
            (timestamp or _now(), key, old_value, new_value, source),
        )

    def insert_tank_refill(self, tank: str, timestamp: str | None = None, **fields: Any) -> None:
        self._execute(
            """INSERT INTO tank_refills
               (timestamp, tank, tank_type, npk_n, npk_p, npk_k,
                total_ml, ml_per_dose_or_cycle, max_doses, mode, pump_lph)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp or _now(), tank,
             fields.get("tank_type", tank),
             float(fields.get("npk_n", 0) or 0),
             float(fields.get("npk_p", 0) or 0),
             float(fields.get("npk_k", 0) or 0),
             float(fields.get("total_ml", 0) or 0),
             float(fields.get("ml_per_dose_or_cycle", 0) or 0),
             int(fields.get("max_doses", 0) or 0),
             fields.get("mode"),
             float(fields.get("pump_lph", 0) or 0)),
        )

    def insert_tank_auto_dose(self, tank: str, timestamp: str | None = None, **fields: Any) -> None:
        self._execute(
            """INSERT INTO tank_auto_doses
               (timestamp, tank, tank_type, npk_n, npk_p, npk_k,
                ml_dispensed, activation_seconds, doses_used_before, doses_used_after,
                ml_remaining_est, combined_with_watering, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp or _now(), tank,
             fields.get("tank_type", tank),
             float(fields.get("npk_n", 0) or 0),
             float(fields.get("npk_p", 0) or 0),
             float(fields.get("npk_k", 0) or 0),
             float(fields.get("ml_dispensed", 0) or 0),
             float(fields.get("activation_seconds", 0) or 0),
             int(fields.get("doses_used_before", 0) or 0),
             int(fields.get("doses_used_after", 0) or 0),
             float(fields.get("ml_remaining_est", 0) or 0),
             1 if fields.get("combined_with_watering") else 0,
             fields.get("mode")),
        )

    def insert_tank_auto_dose_blocked(self, tank: str, reason: str,
                                      timestamp: str | None = None, **fields: Any) -> None:
        self._execute(
            """INSERT INTO tank_auto_dose_blocked
               (timestamp, tank, reason, cooldown_until_at, blocked_until_at,
                doses_used, max_doses, ml_remaining_est)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp or _now(), tank, reason,
             fields.get("cooldown_until_at"),
             fields.get("blocked_until_at"),
             int(fields.get("doses_used", 0) or 0),
             int(fields.get("max_doses", 0) or 0),
             float(fields.get("ml_remaining_est", 0) or 0)),
        )

    def recent_tank_refills(self, limit: int = 20) -> list[dict]:
        rows = self._query(
            "SELECT * FROM tank_refills ORDER BY id DESC LIMIT ?", (limit,),
        )
        return [dict(r) for r in rows]

    def recent_tank_doses(self, limit: int = 20) -> list[dict]:
        rows = self._query(
            "SELECT * FROM tank_auto_doses ORDER BY id DESC LIMIT ?", (limit,),
        )
        return [dict(r) for r in rows]

    def latest_tank_refill(self, tank: str) -> dict | None:
        """Most recent refill declared for *tank*, or None."""
        rows = self._query(
            "SELECT * FROM tank_refills WHERE tank = ? ORDER BY id DESC LIMIT 1",
            (tank,),
        )
        return dict(rows[0]) if rows else None

    def tank_doses_since(self, tank: str, since_ts: str) -> list[dict]:
        """Auto-doses for *tank* recorded at or after *since_ts* (oldest first)."""
        rows = self._query(
            "SELECT * FROM tank_auto_doses WHERE tank = ? AND timestamp >= ? ORDER BY id ASC",
            (tank, since_ts),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def count_relay_activations(self, relay_id: int, year: int, month: int) -> int:
        """Count activations of a relay in a calendar month (prefix match on
        the local 'YYYY-MM' timestamp). Counting by month means the counter
        "resets" automatically at the start of each new calendar month."""
        prefix = f"{year:04d}-{month:02d}"
        row = self._query(
            "SELECT COUNT(*) AS n FROM relay_activations WHERE relay_id = ? AND timestamp LIKE ?",
            (relay_id, prefix + "%"),
        )
        return int(row[0]["n"]) if row else 0

    def last_relay_activation(self, relay_id: int) -> dict | None:
        """Return the most recent relay activation row (timestamp +
        duration_seconds) for *relay_id*, or None if there is none.
        Tolerant of NULL duration_seconds."""
        rows = self._query(
            "SELECT timestamp, COALESCE(duration_seconds, 0) AS duration_seconds "
            "FROM relay_activations WHERE relay_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (relay_id,),
        )
        return dict(rows[0]) if rows else None

    def watering_seconds_since(self, relay_id: int, since: str) -> float:
        """Sum of activation durations for *relay_id* with timestamp >= since."""
        row = self._query(
            "SELECT COALESCE(SUM(COALESCE(duration_seconds, 0)), 0) AS s "
            "FROM relay_activations WHERE relay_id = ? AND timestamp >= ?",
            (relay_id, since),
        )
        return float(row[0]["s"]) if row else 0.0

    def recent_events(self, limit: int = 50, min_level: str | None = None) -> list[dict]:
        levels = _LEVELS
        if min_level:
            levels = _LEVELS[_LEVELS.index(min_level):]
        placeholders = ",".join("?" for _ in levels)
        rows = self._query(
            f"""SELECT timestamp, level, source, message FROM system_events
                WHERE level IN ({placeholders})
                ORDER BY id DESC LIMIT ?""",
            (*levels, limit),
        )
        return [dict(r) for r in rows]

    def recent_pest_detections(self, limit: int = 10) -> list[dict]:
        rows = self._query(
            "SELECT timestamp, detected, pest_class, confidence, image_path, model, camera, suggestion "
            "FROM pest_detections ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        result = []
        for r in rows:
            d = dict(r)
            raw = d.get("suggestion")
            if raw:
                try:
                    d["suggestion"] = json.loads(raw)
                except (json.JSONDecodeError, ValueError, TypeError):
                    d["suggestion"] = None
            else:
                d["suggestion"] = None
            result.append(d)
        return result

    def recent_npk_recommendations(self, limit: int = 50) -> list[dict]:
        rows = self._query(
            """SELECT timestamp, nitrogen, phosphorus, potassium,
                      fertilizer_id, fertilizer_name, dosage_g_per_10L,
                      deficits, reasoning, priority, model
               FROM npk_recommendations ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

    def last_npk_reading(self) -> dict | None:
        rows = self._query(
            "SELECT timestamp, nitrogen, phosphorus, potassium FROM npk_readings ORDER BY id DESC LIMIT 1"
        )
        return dict(rows[0]) if rows else None

    def last_moisture_reading(self) -> dict | None:
        rows = self._query(
            "SELECT timestamp, moisture FROM moisture_readings ORDER BY id DESC LIMIT 1"
        )
        return dict(rows[0]) if rows else None

    def npk_history(self, limit: int = 100) -> list[dict]:
        rows = self._query(
            "SELECT timestamp, nitrogen, phosphorus, potassium FROM npk_readings ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in reversed(rows)]

    def moisture_history(self, limit: int = 200) -> list[dict]:
        rows = self._query(
            "SELECT timestamp, moisture FROM moisture_readings ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in reversed(rows)]

    def activation_history(self, limit: int = 100) -> list[dict]:
        rows = self._query(
            """SELECT timestamp, relay_id, relay_name, trigger_type, duration_seconds, source
               FROM relay_activations ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

    def backup_to(self, dest: str | Path) -> None:
        """Create a consistent snapshot of the database at *dest*."""
        dest = Path(dest)
        if dest.exists():
            dest.unlink()
        with sqlite3.connect(str(dest)) as target:
            self._conn.commit()
            self._conn.backup(target)
            target.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # Export helpers

    def export_all_tables(self) -> dict[str, list[dict]]:
        """Export all tables as a dictionary of table names to row lists."""
        tables = [
            "npk_readings",
            "moisture_readings",
            "relay_activations",
            "blocked_activations",
            "pest_detections",
            "npk_recommendations",
            "system_events",
            "config_changes",
        ]
        result: dict[str, list[dict]] = {}
        for table in tables:
            try:
                rows = self._query(f"SELECT * FROM {table} ORDER BY id DESC")
                result[table] = [dict(r) for r in rows]
            except DatabaseError:
                result[table] = []
        return result

    def get_table_names(self) -> list[str]:
        """Get all table names in the database."""
        rows = self._query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        return [r["name"] for r in rows]