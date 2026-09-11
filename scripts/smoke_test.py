"""Smoke test: wire the full app (simulated) against a temp config and
exercise every dashboard endpoint through the Flask test client."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from config.config import default_config
from database.database import Database
from state import SystemState


def main() -> int:
    sim = tempfile.mkdtemp(prefix="veggiecare-smoke-")
    cfg = default_config()
    cfg["database"]["path"] = str(Path(sim) / "smoke.db")
    cfg["system"]["simulate_hardware"] = True
    cfg["system"]["tick_seconds"] = 0.1
    cfg["logging"]["level"] = "WARNING"
    cfg["logging"]["file"] = str(Path(sim) / "smoke.log")

    cfg_path = Path(sim) / "smoke.yaml"
    with open(cfg_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh)
    os.environ["VEGGIECARE_CONFIG"] = str(cfg_path)

    # 1. Config
    from config.config import load_config

    loaded = load_config(cfg_path)
    simulate = True

    # 2. Database
    db = Database(loaded["database"]["path"])

    # 3. State
    state = SystemState(loaded)

    # 4. Sensors (simulated)
    sensors = {}
    try:
        from sensors.npk import NpkSensor

        sensors["npk"] = NpkSensor(loaded["npk"], simulate=simulate)
    except Exception as exc:  # pragma: no cover
        print(f"NPK sensor init failed (expected on non-Pi?): {exc}")
    try:
        from sensors.soil_moisture import SoilMoistureSensor

        sensors["moisture"] = SoilMoistureSensor(loaded["soil_moisture"], simulate=simulate)
    except Exception as exc:  # pragma: no cover
        print(f"Moisture sensor init failed (expected on non-Pi?): {exc}")

    # 5. Relays
    from hardware.relay_controller import RelayController

    relays = RelayController(loaded["relays"], simulate=simulate)

    # 6. Automation controller
    from automation.controller import AutomationController

    controller = AutomationController(
        config=loaded, db=db, relays=relays, state=state, sensors=sensors,
    )
    from camera.camera import NotConfiguredCamera

    camera = NotConfiguredCamera()
    controller.attach_camera(camera)
    state.update_camera(configured=False, error=None)

    pest_cfg = loaded.get("pest_detection", {})
    if pest_cfg.get("enabled"):
        from pest_detection.mock_detector import MockDetector

        controller.attach_detector(MockDetector(pest_cfg))
        state.update_pest(configured=True, model="mock", error=None)
    else:
        state.update_pest(configured=False, model=None, error=None)

    controller.start()

    # 7. Dashboard
    from dashboard import create_app

    flask_app = create_app(state=state, config=loaded, controller=controller, db=db)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    failures = []

    def check(label: str, cond: bool, extra: str = "") -> None:
        print(f"{label:<32} {'PASS' if cond else 'FAIL'} {extra}")
        if not cond:
            failures.append(label)

    # Page route
    r = client.get("/")
    check("GET /", r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        check("  template has title", b"VeggieCare" in r.data)

    # State API
    r = client.get("/api/state")
    check("GET /api/state", r.status_code == 200, f"status={r.status_code}")
    data = r.get_json() if r.status_code == 200 else {}
    check("  state has sensors", "sensors" in data and "npk" in data.get("sensors", {}))
    check("  state has database", "database" in data)
    check("  state has relays", len(data.get("relays", [])) == 3)
    check("  state has thresholds", "npk" in data.get("thresholds", {}))
    check("  state has alerts", "alerts" in data)
    check("  relays have labels", all("label" in x for x in data.get("relays", [])))

    # Relay activation
    r = client.post("/api/relays/1/activate", json={"duration": 1})
    j = r.get_json(silent=True) or {}
    check("POST /api/relays/1/activate", r.status_code == 200 and j.get("ok") is True, str(j))

    r = client.post("/api/relays/1/off")
    j = r.get_json(silent=True) or {}
    check("POST /api/relays/1/off", r.status_code == 200 and j.get("ok") is True, str(j))

    r = client.post("/api/relays/all-off")
    j = r.get_json(silent=True) or {}
    check("POST /api/relays/all-off", r.status_code == 200 and j.get("ok") is True, str(j))

    # Automation
    r = client.post("/api/automation/pause")
    j = r.get_json(silent=True) or {}
    check("POST /api/automation/pause", r.status_code == 200 and j.get("ok") is True, str(j))
    r = client.post("/api/automation/resume")
    j = r.get_json(silent=True) or {}
    check("POST /api/automation/resume", r.status_code == 200 and j.get("ok") is True, str(j))

    # Pest simulate (disabled mode matches current state)
    r = client.post("/api/pest/simulate", json={"detected": True, "pest_class": "aphid", "confidence": 0.9})
    j = r.get_json(silent=True) or {}
    check("POST /api/pest/simulate", r.status_code == 200 and j.get("ok") is True, str(j))

    # Static assets
    for path in ("/static/style.css", "/static/app.js"):
        r = client.get(path)
        check(f"GET {path}", r.status_code == 200, f"status={r.status_code}")

    # Shutdown safety
    controller.stop()
    relays.shutdown()
    db.close()

    print()
    if failures:
        print(f"SMOKE TEST FAILED: {failures}")
        return 1
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())