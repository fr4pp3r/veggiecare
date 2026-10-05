#!/usr/bin/env python3
"""VeggieCare — Raspberry Pi 5 smart plant monitoring and control system.

Entry point. Wires configuration, database, sensors, relays, automation
controller and the web dashboard together, then serves the dashboard over
the LAN. Relays are guaranteed OFF at startup and on any shutdown.

Run::

    python app.py                # use config/config.yaml
    python app.py --simulate     # force simulated hardware (dev/testing)
    python app.py --config X.yaml
"""

from __future__ import annotations

import argparse
import atexit
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path

from config import ConfigError, config_file_path, load_config
from database.database import Database
from state import SystemState


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VeggieCare monitoring system")
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Path to a config YAML file (default: config/config.yaml)",
    )
    parser.add_argument(
        "--simulate", action="store_true",
        help="Force simulated hardware (no GPIO/serial/SPI)",
    )
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="Address to bind the dashboard (default: 0.0.0.0 = all interfaces)",
    )
    parser.add_argument(
        "--port", type=int, default=5000,
        help="Port for the dashboard (default: 5000)",
    )
    return parser.parse_args()


def setup_logging(cfg: dict) -> None:
    level = getattr(logging, str(cfg.get("logging", {}).get("level", "INFO")).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    log_file = cfg.get("logging", {}).get("file")
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)


def main() -> int:
    args = parse_args()

    # 1. Configuration
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    simulate = args.simulate or bool(cfg["system"].get("simulate_hardware", False))
    if simulate:
        print("*** SIMULATED HARDWARE MODE — no GPIO/serial/SPI will be touched ***")

    # 2. Logging
    setup_logging(cfg)
    log = logging.getLogger("veggiecare")
    log.info("Starting VeggieCare (simulate=%s)", simulate)

    # 3. Database
    db = Database(cfg["database"]["path"])
    log.info("Database ready: %s", db.path)

    # 4. Shared state
    state = SystemState(cfg)

    # 5. Hardware — sensors + relays
    sensors = {}
    try:
        from sensors.npk import NpkSensor
        sensors["npk"] = NpkSensor(cfg["npk"], simulate=simulate)
    except Exception as exc:
        log.error("Could not create NPK sensor: %s", exc)

    try:
        from sensors.soil_moisture import SoilMoistureSensor
        sensors["moisture"] = SoilMoistureSensor(cfg["soil_moisture"], simulate=simulate)
    except Exception as exc:
        log.error("Could not create soil moisture sensor: %s", exc)

    from hardware.relay_controller import RelayController
    relays = RelayController(cfg["relays"], simulate=simulate)

    # Stepper motor for camera movement
    stepper = None
    stepper_cfg = cfg.get("stepper", {})
    try:
        from hardware.stepper import StepperMotor
        pins = stepper_cfg.get("pins", [23, 24, 25, 26])
        stepper = StepperMotor(pins=pins, simulate=simulate or stepper_cfg.get("simulate", False))
    except Exception as exc:
        log.error("Failed to initialize stepper: %s", exc)
        stepper = None

    # 6. Automation controller
    from automation.controller import AutomationController
    controller = AutomationController(
        config=cfg, db=db, relays=relays, state=state, sensors=sensors,
    )

    # 7. Pest detection and camera
    camera_cfg = cfg.get("camera", {})
    camera = None
    if camera_cfg.get("enabled"):
        cam_type = camera_cfg.get("type", "usb")
        try:
            if cam_type == "usb":
                from camera.usb_camera import UsbCamera
                # An empty device_path means "fall back to device_index".
                device_path = str(camera_cfg.get("device_path") or "").strip() or None
                camera = UsbCamera(
                    device_index=int(camera_cfg.get("device_index", 0)),
                    device_path=device_path,
                    width=camera_cfg.get("width"),
                    height=camera_cfg.get("height"),
                    fps=camera_cfg.get("fps"),
                    image_dir=camera_cfg.get("image_dir", "data/images"),
                    simulate=simulate,
                )
            else:
                from camera.camera import NotConfiguredCamera
                camera = NotConfiguredCamera()
        except Exception as exc:
            log.error("Failed to initialize camera: %s", exc)
            from camera.camera import NotConfiguredCamera
            camera = NotConfiguredCamera()
    else:
        from camera.camera import NotConfiguredCamera
        camera = NotConfiguredCamera()
    controller.attach_camera(camera)

    cam_status = camera.status() if hasattr(camera, "status") else {"configured": False}
    state.update_camera(
        configured=bool(cam_status.get("configured", False)),
        name=cam_status.get("name"),
        device_path=cam_status.get("device_path"),
        resolution=(
            f"{cam_status['width']}x{cam_status['height']}"
            if cam_status.get("width") and cam_status.get("height")
            else None
        ),
        simulated=bool(cam_status.get("simulated", False)),
        error=cam_status.get("error"),
    )
    if cam_status.get("configured"):
        log.info("Camera ready: %s", cam_status.get("message", cam_status.get("name")))
    else:
        log.warning(
            "Camera not available: %s",
            cam_status.get("error") or cam_status.get("message") or "unknown reason",
        )

    pest_cfg = cfg.get("pest_detection", {})
    if pest_cfg.get("enabled"):
        det_type = pest_cfg.get("detector", "mock")
        try:
            if det_type == "yolov11n":
                from pest_detection.yolov11_nano_detector import YoloV11NanoDetector
                detector = YoloV11NanoDetector(pest_cfg)
            else:
                from pest_detection.mock_detector import MockDetector
                detector = MockDetector(pest_cfg)
        except Exception as exc:
            log.error("Failed to initialize detector: %s", exc)
            from pest_detection.mock_detector import MockDetector
            detector = MockDetector(pest_cfg)
        controller.attach_detector(detector)
        st = detector.status() if hasattr(detector, "status") else {"configured": True, "model": getattr(detector, "name", "unknown")}
        state.update_pest(configured=st.get("configured", True), model=st.get("model"), error=None)
        log.info("Pest detector enabled (%s)", st.get("model", "detector"))
    else:
        state.update_pest(configured=False, model=None, error=None)
        log.info("Pest detection disabled")

    controller.start()

    # 8. Shutdown safety — relays OFF on exit, no matter how we leave.
    #    Guarded: systemd sends SIGTERM once via ExecStop and again itself
    #    (KillMode=control-group), and atexit re-runs this on normal exit.
    _shutting_down = False

    def shutdown() -> None:
        nonlocal _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        log.info("Shutting down — forcing all relays OFF")
        controller.stop()
        relays.shutdown()
        db.close()
        for sensor in sensors.values():
            try:
                sensor.close()
            except Exception:
                pass

    def _handle_signal(signum, frame) -> None:
        shutdown()
        # Never return to waitress.serve() — it would keep serving forever
        # and systemd would SIGKILL us after TimeoutStopSec. os._exit skips
        # the atexit re-run of shutdown() (already done above).
        os._exit(0)

    atexit.register(shutdown)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            pass  # not available on every platform (e.g. Windows prohibits)

    # 9. Dashboard
    from dashboard import create_app
    app = create_app(
        state=state,
        config=cfg,
        controller=controller,
        db=db,
        camera=camera,
        config_path=config_file_path(args.config),
    )

    # Serve with waitress (production-grade, pure-Python WSGI). Dashboard
    # must never block on sensor reads — it only reads the shared state.
    log.info("Dashboard listening on http://%s:%d", args.host, args.port)
    try:
        from waitress import serve
        serve(app, host=args.host, port=args.port, threads=8)
    except ImportError:
        app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())