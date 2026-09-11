"""VeggieCare dashboard routes.

Page routes render server-side templates. JSON API endpoints serve the
live state that the frontend JavaScript polls for updates.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request

from state import SystemState

bp = Blueprint("dashboard", __name__)


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