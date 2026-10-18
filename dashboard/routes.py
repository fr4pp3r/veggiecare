"""VeggieCare dashboard routes.

Page routes render server-side templates. JSON API endpoints serve the
live state that the frontend JavaScript polls for updates.
"""

from __future__ import annotations

import copy
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
        "pest": float(_config().get("pest_detection", {}).get("confidence_threshold", 0.70)),
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
# JSON API — tanks (fertilizer/pesticide)
# ======================================================================


def _fnum(body: dict, key: str, default: float) -> float:
    """Parse a float from a JSON body, falling back to *default*."""
    try:
        value = body.get(key, default)
        return float(default) if value is None else float(value)
    except (TypeError, ValueError):
        return float(default)


def _fnum_or_none(body: dict, key: str) -> float | None:
    """Parse an optional positive float, returning None when absent/invalid."""
    try:
        value = float(body.get(key, 0))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _fnum_opt(body: dict, key: str) -> float | None:
    """Parse an optional float, returning None only when the key is absent.

    Unlike ``_fnum_or_none`` this preserves an explicit 0 (e.g. ``soak_days: 0``
    is meaningful). Returning None lets the controller fall back to the value
    already on the tank, so a refill patches rather than silently resetting
    settings the operator never touched.
    """
    if key not in body:
        return None
    try:
        return float(body[key])
    except (TypeError, ValueError):
        return None


@bp.route("/api/tanks/<tank_name>/refill", methods=["POST"])
def api_tank_refill(tank_name: str):
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503

    body = request.get_json(silent=True) or {}
    tank_name = tank_name.lower()

    if tank_name not in ("fertilizer", "pesticide"):
        return jsonify({"ok": False, "error": "Invalid tank name"}), 400

    # Fields the form does not always send are passed as None so the
    # controller inherits the current tank value instead of resetting it to a
    # hardcoded default (see Controller._resolve).
    mode = str(body["mode"]).lower() if body.get("mode") else None

    if tank_name == "fertilizer":
        ok, msg = ctrl.refill_fertilizer_tank(
            fertilizer_type=str(body.get("fertilizer_type", "")).strip(),
            npk_n=_fnum(body, "npk_n", 0),
            npk_p=_fnum(body, "npk_p", 0),
            npk_k=_fnum(body, "npk_k", 0),
            total_ml=_fnum(body, "total_ml", 0),
            tank_capacity_ml=_fnum_or_none(body, "tank_capacity_ml"),
            pump_lph=_fnum_opt(body, "pump_lph"),
            fert_ml_per_cycle=_fnum_opt(body, "fert_ml_per_cycle"),
            min_dose_seconds=_fnum_opt(body, "min_dose_seconds"),
            soak_days=_fnum_opt(body, "soak_days"),
            mode=mode,
        )
    else:
        ok, msg = ctrl.refill_pesticide_tank(
            pesticide_type=str(body.get("pesticide_type", "")).strip(),
            total_ml=_fnum(body, "total_ml", 0),
            tank_capacity_ml=_fnum_or_none(body, "tank_capacity_ml"),
            pump_lph=_fnum_opt(body, "pump_lph"),
            ml_per_activation=_fnum_opt(body, "ml_per_activation"),
            min_dose_seconds=_fnum_opt(body, "min_dose_seconds"),
            soak_days=_fnum_opt(body, "soak_days"),
        )
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@bp.route("/api/tanks/<tank_name>/auto", methods=["POST"])
def api_tank_auto(tank_name: str):
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503

    body = request.get_json(silent=True) or {}
    tank_name = tank_name.lower()

    if tank_name not in ("fertilizer", "pesticide"):
        return jsonify({"ok": False, "error": "Invalid tank name"}), 400

    enabled = bool(body.get("enabled", True))
    ok, msg = ctrl.set_tank_auto(tank_name, enabled)
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@bp.route("/api/tanks/<tank_name>/enable", methods=["POST"])
def api_tank_enable(tank_name: str):
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503

    tank_name = tank_name.lower()
    if tank_name not in ("fertilizer", "pesticide"):
        return jsonify({"ok": False, "error": "Invalid tank name"}), 400

    ok, msg = ctrl.set_tank_enabled(tank_name, True)
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


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


@bp.route("/api/pest/scan", methods=["POST"])
def api_pest_scan():
    """Trigger a pest operation now, outside the scheduled monitor interval.

    Body ``{"mode": "stepper"}`` runs a full camera sweep (motor out and
    back); ``{"mode": "camera"}`` (the default) analyses a single capture.
    """
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503
    body = request.get_json(silent=True) or {}
    with_stepper = body.get("mode") == "stepper"
    ok, message = ctrl.request_pest_scan(with_stepper)
    return jsonify({"ok": ok, "message": message})


@bp.route("/api/watering/reset", methods=["POST"])
def api_watering_reset():
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503

    ok, msg = ctrl.reset_watering_fault()
    return jsonify({"ok": ok, "message": msg})


@bp.route("/api/pest/treatment", methods=["GET"])
def api_pest_treatment():
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503

    pest_class = (request.args.get("class") or "").strip()
    if not pest_class:
        pest_class = None

    try:
        advice = ctrl.suggest_treatment(pest_class)
    except Exception as exc:
        log.exception("suggest_treatment failed")
        advice = None
    return jsonify({"ok": True, "class": pest_class, "advice": advice})


# ======================================================================
# JSON API — system test (diagnostics)
# ======================================================================

def _run_relay_test(ctrl, relay_id: int, duration: float = 2.0) -> dict:
    """Test a single relay activation."""
    try:
        ok, msg = ctrl.activate_relay(
            relay_id, duration=duration, trigger="manual", source="system_test"
        )
        if not ok:
            return {"relay_id": relay_id, "ok": False, "message": msg}
        # Wait for watchdog to turn it off
        time.sleep(min(duration + 0.5, 5.0))
        still_on = ctrl._relays.is_active(relay_id)
        return {
            "relay_id": relay_id,
            "ok": True,
            "message": f"Relay {relay_id} activated and auto-off confirmed" if not still_on else f"Relay {relay_id} activated (watchdog will clear)",
        }
    except Exception as exc:
        return {"relay_id": relay_id, "ok": False, "message": f"Error: {exc}"}


def _run_sensor_tests(ctrl) -> dict:
    """Test NPK and soil moisture sensors."""
    results = {}
    # Test NPK
    try:
        npk_sensor = ctrl._npk_sensor
        if npk_sensor:
            reading = npk_sensor.read()
            results["npk"] = {
                "ok": True,
                "message": f"N={reading.values.get('nitrogen', '?')} P={reading.values.get('phosphorus', '?')} K={reading.values.get('potassium', '?')}",
                "values": reading.values,
            }
        else:
            results["npk"] = {"ok": False, "message": "NPK sensor not initialized"}
    except Exception as exc:
        results["npk"] = {"ok": False, "message": f"Error: {exc}"}

    # Test soil moisture
    try:
        moisture_sensor = ctrl._moisture_sensor
        if moisture_sensor:
            reading = moisture_sensor.read()
            results["moisture"] = {
                "ok": True,
                "message": f"Moisture: {reading.values.get('moisture', '?')}%",
                "values": reading.values,
            }
        else:
            results["moisture"] = {"ok": False, "message": "Moisture sensor not initialized"}
    except Exception as exc:
        results["moisture"] = {"ok": False, "message": f"Error: {exc}"}

    return results


def _run_camera_test(ctrl) -> dict:
    """Test camera capture."""
    try:
        camera = ctrl._camera
        if camera:
            status = camera.status()
            if status.get("configured"):
                # Try a capture
                import tempfile
                from pathlib import Path
                tmp_dir = tempfile.mkdtemp(prefix="veggiecare-systest-")
                try:
                    path = camera.capture(save_path=Path(tmp_dir) / "test.jpg")
                    if path and Path(path).exists():
                        size = Path(path).stat().st_size
                        return {
                            "ok": True,
                            "message": f"Captured {size} bytes",
                            "status": status,
                        }
                    else:
                        return {"ok": False, "message": "Capture returned no file", "status": status}
                finally:
                    import shutil
                    try:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                    except Exception:
                        pass
            else:
                return {"ok": False, "message": f"Camera not configured: {status.get('message', 'unknown')}", "status": status}
        else:
            return {"ok": False, "message": "Camera not initialized"}
    except Exception as exc:
        return {"ok": False, "message": f"Error: {exc}"}


def _run_stepper_test(ctrl) -> dict:
    """Test stepper motor movement."""
    try:
        stepper = ctrl._stepper
        if stepper:
            stepper.step(steps=10, delay=0.001, clockwise=True)
            stepper.step(steps=10, delay=0.001, clockwise=False)
            stepper.off()
            return {"ok": True, "message": "Stepper moved forward and back (simulated)"}
        else:
            return {"ok": False, "message": "Stepper not initialized"}
    except Exception as exc:
        return {"ok": False, "message": f"Error: {exc}"}


@bp.route("/api/stepper/test", methods=["POST"])
def api_stepper_test():
    """Test stepper motor movement (forward + back)."""
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503

    body = request.get_json(silent=True) or {}
    steps = body.get("steps", 512)
    delay = body.get("delay", 0.003)

    try:
        stepper = ctrl._stepper
        if not stepper:
            return jsonify({"ok": False, "error": "Stepper not initialized"}), 503

        stepper.step(steps=steps, delay=delay, clockwise=True)
        time.sleep(0.5)
        stepper.step(steps=steps, delay=delay, clockwise=False)
        stepper.off()

        return jsonify({
            "ok": True,
            "message": f"Stepper moved {steps} steps forward and back",
            "steps": steps,
            "delay": delay,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Error: {exc}"}), 500


@bp.route("/api/system/test", methods=["POST"])
def api_system_test():
    """Run a full system test: relays, sensors, camera, stepper."""
    ctrl = _controller()
    if ctrl is None:
        return jsonify({"ok": False, "error": "Controller not ready"}), 503

    body = request.get_json(silent=True) or {}
    # Which tests to run (default: all)
    test_relays = body.get("relays", True)
    test_sensors = body.get("sensors", True)
    test_camera = body.get("camera", True)
    test_stepper = body.get("stepper", True)

    results = {
        "ok": True,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "tests": {},
    }

    # Test relays
    if test_relays:
        relay_results = []
        for rid in (1, 2, 3):
            r = _run_relay_test(ctrl, rid)
            relay_results.append(r)
            if not r.get("ok"):
                results["ok"] = False
        results["tests"]["relays"] = relay_results

    # Test sensors
    if test_sensors:
        sensor_results = _run_sensor_tests(ctrl)
        results["tests"]["sensors"] = sensor_results
        if not sensor_results.get("npk", {}).get("ok"):
            results["ok"] = False
        if not sensor_results.get("moisture", {}).get("ok"):
            results["ok"] = False

    # Test camera
    if test_camera:
        camera_result = _run_camera_test(ctrl)
        results["tests"]["camera"] = camera_result
        if not camera_result.get("ok"):
            results["ok"] = False

    # Test stepper
    if test_stepper:
        stepper_result = _run_stepper_test(ctrl)
        results["tests"]["stepper"] = stepper_result
        if not stepper_result.get("ok"):
            results["ok"] = False

    return jsonify(results)


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
# JSON API — database export
# ======================================================================

@bp.route("/api/database/download")
def api_database_download():
    """Download a consistent snapshot of the SQLite database file."""
    db = getattr(current_app, "veggiecare_db", None)
    if db is None:
        return jsonify({"ok": False, "error": "Database not available"}), 503
    try:
        import os
        import tempfile

        from flask import after_this_request, send_file

        fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="veggiecare-")
        os.close(fd)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        db.backup_to(tmp_path)

        @after_this_request
        def _cleanup(response):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return response

        return send_file(
            tmp_path,
            as_attachment=True,
            download_name="veggiecare.db",
            mimetype="application/x-sqlite3",
        )
    except Exception as exc:
        log.exception("Database download failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/database/export")
def api_database_export():
    """Export all database tables as JSON."""
    db = getattr(current_app, "veggiecare_db", None)
    if db is None:
        return jsonify({"ok": False, "error": "Database not available"}), 503
    try:
        data = db.export_all_tables()
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        log.exception("Database export failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/database/tables")
def api_database_tables():
    """List all database table names."""
    db = getattr(current_app, "veggiecare_db", None)
    if db is None:
        return jsonify({"ok": False, "error": "Database not available"}), 503
    try:
        tables = db.get_table_names()
        return jsonify({"ok": True, "tables": tables})
    except Exception as exc:
        log.exception("Database tables list failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/database/table/<table_name>")
def api_database_table(table_name: str):
    """View table data with pagination."""
    db = getattr(current_app, "veggiecare_db", None)
    if db is None:
        return jsonify({"ok": False, "error": "Database not available"}), 503

    # Validate table name to prevent SQL injection
    valid_tables = {
        "npk_readings",
        "moisture_readings",
        "relay_activations",
        "blocked_activations",
        "pest_detections",
        "npk_recommendations",
        "system_events",
        "config_changes",
    }
    if table_name not in valid_tables:
        return jsonify({"ok": False, "error": "Invalid table name"}), 400

    try:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)
        per_page = max(1, min(per_page, 500))  # Clamp to reasonable range
        offset = (page - 1) * per_page

        # Get total count
        count_row = db._query(f"SELECT COUNT(*) AS n FROM {table_name}")
        total = int(count_row[0]["n"]) if count_row else 0

        # Get paginated data
        rows = db._query(
            f"SELECT * FROM {table_name} ORDER BY id DESC LIMIT ? OFFSET ?",
            (per_page, offset),
        )
        data = [dict(r) for r in rows]

        # Get column names
        cols_row = db._query(f"PRAGMA table_info({table_name})")
        columns = [r["name"] for r in cols_row] if cols_row else []

        return jsonify({
            "ok": True,
            "table": table_name,
            "columns": columns,
            "data": data,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": (total + per_page - 1) // per_page if total else 1,
            },
        })
    except Exception as exc:
        log.exception("Database table view failed for %s", table_name)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ======================================================================
# Page routes - Database
# ======================================================================

@bp.route("/database")
def database_page():
    return render_template("database.html")


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
            working = copy.deepcopy(base)
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