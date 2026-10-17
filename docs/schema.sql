-- VeggieCare SQLite schema (WAL mode, busy_timeout=5000)
-- Source of truth: database/database.py (_SCHEMA + tank tables block)

PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

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

CREATE TABLE IF NOT EXISTS tank_refills (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT NOT NULL,
    tank                TEXT NOT NULL,
    tank_type           TEXT,
    npk_n               REAL DEFAULT 0,
    npk_p               REAL DEFAULT 0,
    npk_k               REAL DEFAULT 0,
    total_ml            REAL DEFAULT 0,
    ml_per_dose_or_cycle REAL DEFAULT 0,
    max_doses           INTEGER DEFAULT 0,
    mode                TEXT,
    pump_lph            REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tank_auto_doses (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp             TEXT NOT NULL,
    tank                  TEXT NOT NULL,
    tank_type             TEXT,
    npk_n                 REAL DEFAULT 0,
    npk_p                 REAL DEFAULT 0,
    npk_k                 REAL DEFAULT 0,
    ml_dispensed          REAL DEFAULT 0,
    activation_seconds    REAL DEFAULT 0,
    doses_used_before     INTEGER DEFAULT 0,
    doses_used_after      INTEGER DEFAULT 0,
    ml_remaining_est      REAL DEFAULT 0,
    combined_with_watering INTEGER DEFAULT 0,
    mode                  TEXT
);

CREATE TABLE IF NOT EXISTS tank_auto_dose_blocked (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT NOT NULL,
    tank              TEXT NOT NULL,
    reason            TEXT,
    cooldown_until_at TEXT,
    blocked_until_at  TEXT,
    doses_used        INTEGER DEFAULT 0,
    max_doses         INTEGER DEFAULT 0,
    ml_remaining_est  REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tank_auto_dose_deferred (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    tank      TEXT NOT NULL,
    reason    TEXT
);
