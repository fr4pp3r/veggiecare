# VeggieCare — Agent Guide

Raspberry Pi 5 smart plant monitoring and control system. Dashboard on port 5000.

---

## Quick Commands

### Development (desktop/laptop)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install --no-deps "ultralytics>=8.4.0"   # MUST be separate — see OpenCV note below
python app.py --simulate                     # runs without hardware
# Dashboard: http://localhost:5000
```

### Testing
```bash
# Run all tests (uses .venv-test for isolation)
pytest

# Single test file
pytest tests/test_config.py -v

# Single test
pytest tests/test_config.py::test_camera_rejects_serial_ports -v
```

### Raspberry Pi 5 Setup
```bash
# Automated (recommended)
cd /home/veggiecare/veggiecare
chmod +x scripts/setup_rpi5.sh scripts/setup_rpi5_deps.sh
./scripts/setup_rpi5.sh

# Verify
.venv/bin/python -c "import cv2, torch, ultralytics; print('torch', torch.__version__); print('cv2', cv2.__version__); print('ultralytics', ultralytics.__version__)"
sudo systemctl start veggiecare
sudo journalctl -u veggiecare -f
```

**Critical Pi setup notes:**
- OS user MUST be named `veggiecare` (systemd unit uses `User=veggiecare`)
- `sudo usermod -aG video veggiecare` for camera access (NOT done by setup script)
- `data/` and `logs/` must exist before starting service (systemd `ReadWritePaths`)

---

## Architecture Overview

```
app.py                          # Entry point, wires everything together
├── config/                     # Configuration loading + validation
│   ├── config.yaml             # Single source of truth (edit, then restart)
│   └── config.py               # Strict validation, path expansion, deep-merge
├── database/database.py        # SQLite (WAL mode), thread-safe, health checks
├── state.py                    # Thread-safe shared state (controller writes, dashboard reads)
├── automation/controller.py    # Background loop: sensors → rules → relays → DB
├── hardware/
│   └── relay_controller.py     # 3 relays + watchdog (hard OFF deadline)
├── sensors/
│   ├── npk.py                  # RS485 Modbus RTU (JXCT JXBS-3001)
│   └── soil_moisture.py        # MCP3008 ADC over SPI
├── camera/
│   ├── usb_camera.py           # V4L2 USB webcam, auto-probe, MJPEG stream
│   └── camera.py               # Base + NotConfiguredCamera
├── pest_detection/
│   ├── yolov11_nano_detector.py # Real YOLOv11n (CPU-only torch)
│   └── mock_detector.py        # Fake detector for testing
└── dashboard/                  # Flask + waitress, reads state.snapshot()
```

---

## Key Quirks & Gotchas

### OpenCV + ultralytics (two-step install)
`requirements.txt` has `opencv-python-headless`. `ultralytics` hard-requires GUI `opencv-python` (same `cv2/` dir, needs `libGL.so.1`). **Install order matters:**
```bash
pip install -r requirements.txt          # headless OpenCV
pip install --no-deps "ultralytics>=8.4.0"  # skips its deps, keeps headless build
```
If you see `ImportError: libGL.so.1`: reinstall headless `pip install --force-reinstall opencv-python-headless` then re-run the `--no-deps` line.

### GPIO backend (lgpio vs adafruit-lgpio)
`gpiozero>=2.0` has no built-in backend. `lgpio` wheels exist for Python ≤3.12; on 3.13+ (Trixie) it falls back to sdist + SWIG (needs `liblgpio-dev`). `requirements.txt` uses platform markers:
```text
lgpio>=0.2.2.0; python_version<'3.13' or (platform_machine!='aarch64' and platform_machine!='armv7l')
adafruit-lgpio>=0.2.2.0; sys_platform=='linux' and python_version>='3.13' and (platform_machine=='aarch64' or platform_machine=='armv7l')
```
Without a backend, every relay is marked `available: false` and fails silently.

### Configuration
- Single file: `config/config.yaml` (validated on load, reject on any error)
- Edit YAML → restart app (or systemd service)
- Paths in config are relative to project root, expanded at load time (`config.expand_paths`)
- `VEGGIECARE_CONFIG` env var overrides config path
- Dashboard Settings page writes back to YAML via `ruamel.yaml` (preserves comments)

### Camera device_path
- USB webcam = V4L2 node (`/dev/video0`, `/dev/video1`, ...)
- **NEVER** `/dev/ttyUSB0` — that's the NPK sensor's RS485/FTDI adapter
- Validation in `config.py` explicitly rejects `/dev/tty*` with a helpful error
- `device_path: auto` probes all `/dev/video*` and picks the first that delivers frames (recommended on Pi 5 where libcamera registers many metadata-only nodes)

### Pest Detection Monthly Limit
Only the pest response relay (Relay 3) has a monthly activation limit (`max_activations_per_month: 2`). NPK alerts and soil-moisture watering are **never limited**. The limit resets each calendar month.

### Watering Cooldown
After auto-watering, `watering_cooldown_seconds` (default 3600) prevents immediate re-trigger even if moisture is still low.

### Simulated Hardware Mode
`system.simulate_hardware: true` or `python app.py --simulate` — no GPIO/serial/SPI touched. Used for development and tests.

---

## Testing Patterns

- Fixtures in `tests/conftest.py`: `project_root`, `temp_config`, `temp_db_path`
- Mock sensors return `Reading` objects from `sensors.base`
- Camera tests inject fake `cv2` module (see `test_camera.py` for patterns)
- Automation tests use `_wait_until(predicate, timeout=5.0)` to poll background thread
- Controller tick interval shortened to `0.05s` in test configs (`system.tick_seconds`)

---

## Important Files Reference

| File | Purpose |
|------|---------|
| `app.py` | Entry point, CLI args, wiring, shutdown safety |
| `config/config.yaml` | All tunable settings |
| `config/config.py` | Validation, loading, merging, path expansion |
| `automation/controller.py` | Rules: NPK alert, moisture→watering, pest→relay3 |
| `hardware/relay_controller.py` | Watchdog-enforced relay OFF, simulated fallback |
| `database/database.py` | SQLite WAL, thread-safe, health checks |
| `state.py` | Shared state (controller writes, dashboard reads via `snapshot()`) |
| `camera/usb_camera.py` | V4L2 capture, auto-probe, MJPEG stream, error reporting |
| `pest_detection/yolov11_nano_detector.py` | Real YOLO inference (weights resolved: run dir → config → repo root) |
| `deploy/veggiecare.service` | Systemd unit (User=veggiecare, ReadWritePaths=logs+data) |
| `scripts/setup_rpi5.sh` | Full Pi install (deps, user, dirs, venv, systemd) |
| `scripts/setup_rpi5_deps.sh` | System packages only (build tools, libs, raspi-config) |

---

## Common Troubleshooting

| Symptom | Fix |
|---------|-----|
| `OSError: [Errno 28] No space left on device` | `pip cache purge`, `rm -rf /tmp/pip-*`, verify `--extra-index-url https://download.pytorch.org/whl/cpu` in requirements |
| `ImportError: libGL.so.1` | `pip install --force-reinstall opencv-python-headless` + re-run ultralytics `--no-deps` |
| `ModuleNotFoundError: ultralytics` | Run `pip install --no-deps "ultralytics>=8.4.0"` |
| Camera "Not installed" | `sudo usermod -aG video veggiecare`, restart service |
| Detector falls back to mock | Check `detector: yolov11n` in config, weights exist, `journalctl -u veggiecare -f` |
| systemd `status=217/USER` | OS user not named `veggiecare` |
| systemd `226/NAMESPACE` | `data/` or `logs/` missing |
| Relays all unavailable | `lgpio`/`adafruit-lgpio` not installed for this Python/arch |

---

## Directory Layout (relevant parts)
```
veggiecare/
├── app.py
├── config/
│   ├── config.yaml
│   └── config.py
├── automation/
├── camera/
├── database/
├── hardware/
├── pest_detection/
├── sensors/
├── dashboard/
├── deploy/veggiecare.service
├── scripts/setup_rpi5.sh
├── scripts/setup_rpi5_deps.sh
├── tests/
├── requirements.txt
├── requirements-test.txt
├── data/              # SQLite DB (created at runtime)
├── logs/              # App logs (created at runtime)
├── runs/              # Trained YOLO weights (shipped)
├── Pest-Data/         # Training images (optional, ~70 MB)
└── .venv/             # Virtual environment (gitignored)
```