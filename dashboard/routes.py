"""VeggieCare dashboard routes.

Page routes render server-side templates. JSON API endpoints serve the
live state that the frontend JavaScript polls for updates.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request

from config import (
    ConfigError,
    config_file_path,
    deep_merge,
    load_config,
    save_user_config,
    validate,
)
from database.database import DatabaseError
from state import SystemState

bp = Blueprint("dashboard", __name__)

log = logging.getLogger("veggiecare.dashboard")

# Serializes config file writes and guards the restart flag.
_files_lock = threading.Lock()
_restart_scheduled = False


def _state() -> SystemState:
    return current_app.veggiecare_state  # type: ignore[no-any-return]


def _config() -> dict:
    return current_app.veggiecare_config  # type: ignore[no-any-return]


def _controller():
    return current_app.veggiecare_controller


# ======================================================================
# Page routes
# ======================================================================

@bp.route("/")
def index():
    return render_template("dashboard.html")


# ======================================================================
# JSON API — live state
# ======================================================================

@bp.route("/api/state")
def api_state():
    snapshot = _state().snapshot()
    # Attach relay thresholds from config so the frontend can display them.
    thresholds = _config().get("npk", {}).get("thresholds", {})
    moisture_threshold = _config().get("soil_moisture", {}).get("threshold", 30)
    snapshot["thresholds"] = {
        "npk": thresholds,
        "moisture": moisture_threshold,
    }
    # Relay item metadata (labels, durations).
    relay_items = {int(r["id"]): r for r in _config().get("relays", {}).get("items", [])}
    for relay in snapshot.get("relays", []):
        item = relay_items.get(relay["id"], {})
        relay["label"] = item.get("label", relay.get("name", ""))
        relay["activation_duration_seconds"] = item.get("activation_duration_seconds", 60)
    # Shape into the documented API contract (sensors + database groups).
    snapshot["sensors"] = {
        "npk": snapshot.pop("npk"),
        "moisture": snapshot.pop("moisture"),
    }
    snapshot["database"] = snapshot.pop("db")
    return jsonify(snapshot)


# ======================================================================
# JSON API — relay control
# ======================================================================

@bp.route("/api/relays/<int:relay_id>/activate", methods=["POST"])
def api_relay_activate(relay_id: int):
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503

    body = request.get_json(silent=True) or {}
    duration = body.get("duration")
    duration = float(duration) if duration is not None else None

    ok, msg = ctrl.activate_relay(
        relay_id, duration=duration, trigger="manual", source="dashboard",
    )
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@bp.route("/api/relays/<int:relay_id>/off", methods=["POST"])
def api_relay_off(relay_id: int):
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503

    ctrl._relays.deactivate(relay_id)
    _state().set_relay(relay_id, state="off")
    return jsonify({"ok": True, "message": f"Relay {relay_id} turned off"})


@bp.route("/api/relays/all-off", methods=["POST"])
def api_relays_all_off():
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503

    ctrl.emergency_stop(source="dashboard")
    return jsonify({"ok": True, "message": "Emergency stop activated"})


@bp.route("/api/automation/pause", methods=["POST"])
def api_automation_pause():
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503
    ctrl.pause()
    return jsonify({"ok": True, "message": "Automation paused"})


@bp.route("/api/automation/resume", methods=["POST"])
def api_automation_resume():
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503
    ctrl.resume()
    return jsonify({"ok": True, "message": "Automation resumed"})


# ======================================================================
# JSON API — pest simulation (testing only)
# ======================================================================

@bp.route("/api/pest/simulate", methods=["POST"])
def api_pest_simulate():
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503

    body = request.get_json(silent=True) or {}
    result = ctrl.inject_pest_detection(
        detected=body.get("detected", True),
        pest_class=body.get("pest_class", "aphid"),
        confidence=float(body.get("confidence", 0.85)),
        source="dashboard_simulate",
    )
    return jsonify({
        "ok": True,
        "detected": result.detected,
        "pest_class": result.pest_class,
        "confidence": result.confidence,
    })


# ======================================================================
# JSON API — history
# ======================================================================

@bp.route("/api/events")
def api_events():
    from database.database import Database
    # Access DB from the app context
    # The controller holds the DB reference — get it via the db health snapshot
    # For events, we can access the database through a helper
    db = getattr(current_app, "veggiecare_db", None)
    if db is None:
        return jsonify([]), 200
    limit = request.args.get("limit", 50, type=int)
    events = db.recent_events(limit=limit)
    return jsonify(events)


@bp.route("/api/readings/npk")
def api_npk_history():
    db = getattr(current_app, "veggiecare_db", None)
    if db is None:
        return jsonify([]), 200
    limit = request.args.get("limit", 100, type=int)
    return jsonify(db.npk_history(limit=limit))


@bp.route("/api/readings/moisture")
def api_moisture_history():
    db = getattr(current_app, "veggiecare_db", None)
    if db is None:
        return jsonify([]), 200
    limit = request.args.get("limit", 200, type=int)
    return jsonify(db.moisture_history(limit=limit))


@bp.route("/api/activations")
def api_activation_history():
    db = getattr(current_app, "veggiecare_db", None)
    if db is None:
        return jsonify([]), 200
    limit = request.args.get("limit", 100, type=int)
    return jsonify(db.activation_history(limit=limit))


@bp.route("/api/detections")
def api_detections():
    db = getattr(current_app, "veggiecare_db", None)
    if db is None:
        return jsonify([]), 200
    limit = request.args.get("limit", 50, type=int)
    return jsonify(db.recent_pest_detections(limit=limit))


# ======================================================================
# JSON API — settings (dashboard config page)
# ======================================================================

def _config_path() -> Path:
    path = getattr(current_app, "veggiecare_config_path", None)
    return Path(path) if path else config_file_path()


@bp.route("/api/config")
def api_config():
    from dashboard.settings_schema import get_config_payload

    pending = getattr(current_app, "veggiecare_config_pending", None)
    payload = get_config_payload(_config())
    payload["path"] = str(_config_path())
    payload["restart_supported"] = bool(os.environ.get("INVOCATION_ID"))
    payload["dirty"] = pending is not None
    payload["pending"] = pending
    return jsonify(payload)


@bp.route("/api/settings", methods=["POST"])
def api_settings_save():
    from dashboard.settings_schema import normalize_settings, read_path, set_path

    body = request.get_json(silent=True) or {}
    settings = body.get("settings")

    with _files_lock:
        try:
            normalized, labels, errors = normalize_settings(settings)
            if errors:
                return jsonify({
                    "ok": False,
                    "error": "Check the highlighted settings.",
                    "errors": errors,
                }), 400

            path = _config_path()
            base = load_config(path)
            working = deep_merge({}, base)
            for key, value in normalized.items():
                set_path(working, key, value)
            try:
                validate(working)
            except ConfigError as exc:
                return jsonify({
                    "ok": False,
                    "error": "Settings failed validation.",
                    "errors": [str(exc)],
                }), 400

            save_user_config(path, normalized)

            pending = {
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "fields": labels,
            }
            current_app.veggiecare_config_pending = pending  # type: ignore[attr-defined]

            db = getattr(current_app, "veggiecare_db", None)
            if db is not None:
                for key, value in normalized.items():
                    old = read_path(base, key)
                    try:
                        db.insert_config_change(
                            key, str(old), str(value), source="dashboard",
                        )
                    except DatabaseError:
                        log.warning("Config change audit failed for %s", key)

            return jsonify({
                "ok": True,
                "message": "Settings saved. Restart the server to apply them.",
                "restart_required": True,
                "changed": labels,
                "pending": pending,
            })
        except ConfigError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        except OSError as exc:
            return jsonify({
                "ok": False,
                "error": f"Could not write the config file: {exc}",
            }), 500


@bp.route("/api/system/restart", methods=["POST"])
def api_system_restart():
    global _restart_scheduled

    if not os.environ.get("INVOCATION_ID"):
        return jsonify({
            "ok": False,
            "error": "Restart is only available when VeggieCare runs as a systemd service.",
        }), 409

    with _files_lock:
        if _restart_scheduled:
            return jsonify({
                "ok": True,
                "message": "VeggieCare is already restarting — the dashboard will reconnect in about 30 seconds.",
            })
        _restart_scheduled = True

    def _do_restart() -> None:
        time.sleep(1.5)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            os._exit(1)

    threading.Thread(target=_do_restart, daemon=True, name="veggiecare-restart").start()
    return jsonify({
        "ok": True,
        "message": "VeggieCare is restarting — the dashboard will reconnect in about 30 seconds.",
    })