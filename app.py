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

from config import ConfigError, load_config
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

    # 6. Automation controller
    from automation.controller import AutomationController
    controller = AutomationController(
        config=cfg, db=db, relays=relays, state=state, sensors=sensors,
    )

    # 7. Pest detection (mock or disabled) + camera stub
    from camera.camera import NotConfiguredCamera
    camera = NotConfiguredCamera()
    controller.attach_camera(camera)
    state.update_camera(configured=False, error=None)

    pest_cfg = cfg.get("pest_detection", {})
    if pest_cfg.get("enabled"):
        from pest_detection.mock_detector import MockDetector
        detector = MockDetector(pest_cfg)
        controller.attach_detector(detector)
        state.update_pest(configured=True, model="mock", error=None)
        log.info("Pest detector enabled (mock)")
    else:
        state.update_pest(configured=False, model=None, error=None)
        log.info("Pest detection disabled (camera/model not configured yet)")

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
        state=state, config=cfg, controller=controller, db=db,
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