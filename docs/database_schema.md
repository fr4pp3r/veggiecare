# VeggieCare — Database Schema & Migrations

Full SQLite schema (WAL mode) and migration history. Source of truth:
`database/database.py`. A consolidated DDL dump is in `schema.sql`.

## Tables

See `schema.sql` for the complete DDL. Summary:

| Table | Purpose |
|---|---|
| `npk_readings` | Periodic N/P/K readings with below-threshold flags |
| `moisture_readings` | Soil moisture readings with below-threshold flag |
| `relay_activations` | Every relay ON event (manual or automatic) |
| `blocked_activations` | Relay activations blocked (e.g. monthly limit) |
| `pest_detections` | Pest detection results (class, confidence, image, suggestion) |
| `npk_recommendations` | Fertilizer recommendations derived from NPK |
| `system_events` | Leveled system log (INFO/WARNING/ERROR/CRITICAL) |
| `config_changes` | Config edits recorded from the dashboard |
| `tank_refills` | Fertilizer/pesticide tank refill declarations |
| `tank_auto_doses` | Applied fertigation/pesticide auto-doses |
| `tank_auto_dose_blocked` | Blocked auto-dose attempts (reason + stock) |
| `tank_auto_dose_deferred` | Deferred auto-dose attempts |

## Indexes

- `idx_npk_timestamp` on `npk_readings(timestamp)`
- `idx_moisture_timestamp` on `moisture_readings(timestamp)`
- `idx_relay_activations` on `relay_activations(relay_id, timestamp)`
- `idx_pest_detections_timestamp` on `pest_detections(timestamp)`
- `idx_npk_rec_timestamp` on `npk_recommendations(timestamp)`
- `idx_system_events_timestamp` on `system_events(timestamp)`

## Migrations

Migrations are idempotent and run at `Database.__init__` time.

1. **Base schema** — `_SCHEMA` creates all core tables (npk_readings,
   moisture_readings, relay_activations, blocked_activations, pest_detections,
   npk_recommendations, system_events, config_changes) plus indexes via
   `executescript`, guarded by `CREATE TABLE IF NOT EXISTS`.

2. **Pest suggestion column** — for pre-existing databases created before the
   pest-treatment advisor, `ALTER TABLE pest_detections ADD COLUMN suggestion TEXT`
   is run only if the column is missing (checked via `PRAGMA table_info`).
   Covered by `tests/test_pest_treatment_integration.py::test_db_migration_adds_suggestion_column`.

3. **Tank tables** — `tank_refills`, `tank_auto_doses`,
   `tank_auto_dose_blocked`, `tank_auto_dose_deferred` are created with
   `CREATE TABLE IF NOT EXISTS` in a separate `executescript` block, so older
   databases gain them on next startup.

## Runtime settings

- Journal mode: WAL (`PRAGMA journal_mode=WAL`)
- Busy timeout: 5000 ms (`PRAGMA busy_timeout=5000`)
- Foreign keys: not enforced (no FK constraints declared)
- Thread safety: single `RLock` serializes all reads/writes; `check_same_thread=False`

## Notes

- Timestamps are local-time ISO strings (`YYYY-MM-DDTHH:MM:SS`).
- `deficits` (npk_recommendations) and `suggestion` (pest_detections) store
  JSON-encoded text.
- `trigger_type` is constrained to `'manual'` or `'automatic'`.
- `system_events.level` is constrained to `INFO/WARNING/ERROR/CRITICAL`.
- Monthly activation counting uses a `YYYY-MM` prefix match on the
  `timestamp` column of `relay_activations` (no separate counter table).