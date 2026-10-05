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

from flask import Blueprint, Response, current_app, jsonify, render_template, request

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


@bp.route("/camera")
def camera_page():
    return render_template("camera.html")


# ======================================================================
# JSON API — camera hardware and live view
# ======================================================================

def _camera():
    return getattr(current_app, "veggiecare_camera", None)


def _camera_status() -> dict:
    cam = _camera()
    if cam is None:
        return {
            "configured": False,
            "name": None,
            "device_path": None,
            "device_index": None,
            "width": None,
            "height": None,
            "fps": None,
            "simulated": False,
            "error": "No camera backend was initialized.",
            "message": "Camera unavailable",
        }
    try:
        status = cam.status()
    except Exception as exc:
        log.exception("camera.status() failed")
        return {
            "configured": False,
            "name": getattr(cam, "name", None),
            "device_path": None,
            "device_index": None,
            "width": None,
            "height": None,
            "fps": None,
            "simulated": False,
            "error": str(exc),
            "message": "Camera unavailable",
        }
    status.setdefault("configured", False)
    status.setdefault("error", None)
    return status


@bp.route("/api/camera/status")
def api_camera_status():
    from camera.usb_camera import cv2_availability, list_video_devices

    available, cv2_error = cv2_availability()
    return jsonify({
        "camera": _camera_status(),
        "devices": list_video_devices(),
        "cv2_available": available,
        "cv2_error": cv2_error,
    })


@bp.route("/api/camera/devices")
def api_camera_devices():
    from camera.usb_camera import list_video_devices

    return jsonify({"devices": list_video_devices()})


@bp.route("/api/camera/probe", methods=["POST"])
def api_camera_probe():
    """Test whether specific video nodes actually deliver frames.

    On a Pi with libcamera there are many /dev/video* nodes and most are
    metadata-only, so this is how the operator finds the real webcam
    without guesswork.
    """
    from camera.usb_camera import candidate_devices, cv2_availability, load_cv2, probe_device

    available, cv2_error = cv2_availability()
    if not available:
        return jsonify({"ok": False, "error": cv2_error}), 503

    body = request.get_json(silent=True) or {}
    targets = body.get("targets")

    if isinstance(targets, list) and targets:
        chosen = [str(t) for t in targets][:40]
    else:
        chosen = candidate_devices()[:40]

    if not chosen:
        return jsonify({
            "ok": False,
            "error": "No /dev/video* nodes found. The kernel sees no camera.",
            "results": [],
        }), 404

    cv2, _ = load_cv2()
    results = [probe_device(target, cv2) for target in chosen]
    working = [r["target"] for r in results if r["delivers_frames"]]
    return jsonify({
        "ok": True,
        "results": results,
        "working": working,
        "recommended": working[0] if working else None,
    })


@bp.route("/api/camera/stream")
def api_camera_stream():
    """MJPEG stream for the dashboard live view.

    Streams ``multipart/x-mixed-replace``. Returns 503 with a JSON body
    when no working camera is available, so the frontend can show the
    reason instead of a broken image.
    """
    cam = _camera()
    if cam is None:
        return jsonify({"ok": False, "error": "No camera backend was initialized."}), 503

    status = _camera_status()
    if not status.get("configured"):
        return jsonify({
            "ok": False,
            "error": status.get("error") or status.get("message") or "Camera not available.",
        }), 503

    boundary = "veggiecareframe"
    stream = cam.stream()

    def generate():
        for frame in stream:
            header = (
                f"--{boundary}\r\n"
                f"Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n"
                f"\r\n"
            ).encode()
            yield header + frame + b"\r\n"
        yield f"--{boundary}--\r\n".encode()

    return Response(
        generate(),
        mimetype=f"multipart/x-mixed-replace; boundary={boundary}",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@bp.route("/api/camera/snapshot")
def api_camera_snapshot():
    """A single JPEG frame."""
    cam = _camera()
    if cam is None:
        return jsonify({"ok": False, "error": "No camera backend was initialized."}), 503

    status = _camera_status()
    if not status.get("configured"):
        return jsonify({
            "ok": False,
            "error": status.get("error") or status.get("message") or "Camera not available.",
        }), 503

    import tempfile
    from pathlib import Path as _Path

    tmp_dir = tempfile.mkdtemp(prefix="veggiecare-snap-")
    try:
        path = cam.capture(save_path=_Path(tmp_dir) / "snapshot.jpg")
        if not path:
            return jsonify({"ok": False, "error": "Failed to capture a frame."}), 500
        data = _Path(path).read_bytes()
    finally:
        for leftover in _Path(tmp_dir).glob("*"):
            try:
                leftover.unlink()
            except OSError:
                pass
        try:
            _Path(tmp_dir).rmdir()
        except OSError:
            pass

    return Response(
        data,
        mimetype="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


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