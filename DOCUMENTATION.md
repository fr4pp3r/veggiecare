# VeggieCare - System Documentation

## Table of Contents
- [Overview](#overview)
- [Architecture](#architecture)
- [Components](#components)
- [Hardware Integration](#hardware-integration)
- [AI/Pest Detection](#aipest-detection)
- [Configuration](#configuration)
- [API & Dashboard](#api--dashboard)
- [Database Schema](#database-schema)
- [Automation Logic](#automation-logic)
- [Development & Testing](#development--testing)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)

## Overview

VeggieCare is a Raspberry Pi 5 based smart plant monitoring and control system. It monitors environmental conditions (NPK, soil moisture), controls relays for irrigation/fertilization/pest response, captures images via USB webcam, detects pests using AI (YOLOv11 Nano), and provides a web dashboard for monitoring and control.

Key features:
- Real-time sensor monitoring (NPK via RS485/Modbus, soil moisture via MCP3008 ADC)
- Automated irrigation with cooldown periods
- Pest detection using YOLOv11 Nano classification model
- Camera movement via stepper motor for multi-plant coverage
- Web dashboard with live updates and manual controls
- Thread-safe state management with SQLite logging
- Safety features (relay watchdog, auto-off on shutdown)

## Architecture

The system follows a modular, component-based architecture:

```
app.py (Entry Point)
├── Configuration (config/config.yaml + validation)
├── Database (SQLite, WAL mode)
├── SystemState (shared in-memory state with locks)
├── Sensors (NPK, Soil Moisture)
├── Hardware (Relays, Stepper Motor)
├── Camera (USB Webcam)
├── Pest Detection (YOLOv11 Nano / Mock)
├── Automation Controller (background thread)
└── Flask Dashboard (REST API + web UI)
```

All components are designed with simulation fallbacks for development without hardware.

## Components

### Core Modules

#### `app.py`
Main entry point. Initializes all subsystems, sets up signal handlers for graceful shutdown, and starts the Flask server. Guarantees all relays are turned OFF on startup and shutdown.

#### `state.py`
`SystemState` class - thread-safe in-memory state container using `threading.RLock`. Tracks sensor readings, relay states, alerts, automation status, camera/pest detector status. All state updates go through this centralized store.

#### `config/`
- `config.py` - Configuration loading, validation, and default values. Validates GPIO pin conflicts, ADC ranges, relay polarity, etc.
- `config.yaml` - Runtime configuration (editable via dashboard)

#### `database/`
- `database.py` - SQLite database with WAL mode for concurrent access. Stores NPK readings, moisture readings, relay activations, pest detections, blocked activations, system events. Includes monthly activation counting for rate limiting.

#### `automation/controller.py`
Background thread (`AutomationController`) that runs the control loop at configurable tick rate. Evaluates sensor readings against thresholds, applies automation rules, triggers alerts on state transitions, and logs events. Runs independently of the dashboard.

#### `hardware/`
- `relay_controller.py` - Manages relay states with watchdog timer (auto-off after max duration). Supports active-high/low configuration. Thread-safe.
- `stepper.py` - 28BYJ-48 stepper motor controller using gpiozero. Provides `step()` method for precise movement and `off()` for cleanup. Supports simulation mode.

#### `camera/`
- `camera.py` - Abstract base class and implementations. `Camera` ABC defines interface; `NotConfiguredCamera` for disabled state; `PiCamera` placeholder.
- `usb_camera.py` - USB webcam implementation using OpenCV. Captures JPEG images, saves to configured directory with timestamped filenames. Falls back to simulated mode if hardware unavailable.

#### `pest_detection/`
- `detector.py` - Abstract `PestDetector` base class and `DetectionResult` dataclass. Defines contract for all detectors.
- `mock_detector.py` - Simulated detector for testing (modes: none, random, sequential).
- `yolov11_nano_detector.py` - YOLOv11 Nano based detector. Supports both object detection and classification models. Prefers trained custom model if available (`runs/veggiecare_cls_v1/weights/best.pt`), otherwise falls back to base weights. Maps predictions to configured pest classes.

#### `sensors/`
- `npk.py` - JXCT JXBS-3001 7-in-1 NPK sensor via RS485/Modbus (minimalmodbus). Reads nitrogen, phosphorus, potassium levels.
- `soil_moisture.py` - Capacitive soil moisture sensor via MCP3008 ADC (SPI). Reads moisture percentage with calibration (wet/dry ADC values).

#### `dashboard/`
- `__init__.py` - Flask app factory. Creates REST API endpoints and serves dashboard UI.
- `static/` - CSS/JS for web interface
- `templates/` - HTML templates

#### `scripts/`
Utility scripts for system management (autohotspot, etc.)

#### `tests/`
Pytest test suite covering config validation, database operations, automation logic, and dashboard API. All tests run in simulated mode.

## Hardware Integration

### USB Webcam
- **Library**: OpenCV (`opencv-python-headless`)
- **Interface**: USB Video Class (UVC)
- **Config**: `camera.type: usb`, `camera.device_index: 0`, `camera.image_dir: data/images`
- **Implementation**: `camera.usb_camera.UsbCamera`
- **Behavior**: On init, tries to open camera; if fails or in simulate mode, generates placeholder images. Captures frames and saves as JPEG with timestamp.

### Stepper Motor (28BYJ-48)
- **Driver**: ULN2003
- **Control**: GPIO via `gpiozero.OutputDevice`
- **Pins**: Default GPIO 23,24,25,26 (IN1,IN2,IN3,IN4) - configurable
- **Sequence**: Standard 4-step sequence for 28BYJ-48
- **Config**: `stepper.pins`, `stepper.steps_per_plant`, `stepper.delay`, `stepper.simulate`
- **Usage**: `stepper.step(steps, delay, clockwise)` moves camera between plants. `stepper.off()` cleans up pins.
- **Reference**: `tests/stepper_test.py` demonstrates usage.

### Relays
- **Type**: 5V relay modules (active-high/low configurable)
- **Control**: GPIO via `gpiozero.LED` (works well for relay control)
- **Watchdog**: Auto-off after `auto_off_watchdog_seconds` (default 600s) to prevent stuck relays
- **Safety**: All relays forced OFF at process start and shutdown

### NPK Sensor (JXCT JXBS-3001)
- **Interface**: RS485 → USB (FTDI)
- **Protocol**: Modbus RTU
- **Port**: `/dev/ttyUSB0` (Raspberry Pi), slave ID 1, baudrate 4800
- **Library**: `minimalmodbus`

### Soil Moisture (Capacitive)
- **Interface**: MCP3008 ADC over SPI
- **Channel**: CH0
- **Calibration**: wet/dry ADC values for percentage conversion
- **Voltage**: 3.3V

## AI/Pest Detection

### Model
- **Base**: YOLOv11 Nano (classification variant `yolo11n-cls.pt`)
- **Type**: Image classification (9 pest classes)
- **Classes**: aphids, armyworm, beetle, bollworm, grasshopper, mites, mosquito, sawfly, stem_borer
- **Input Size**: 224x224 (classification)
- **Training**: Fine-tuned on local dataset in `Pest-Data/`

### Dataset Structure
```
Pest-Data/
├── train/
│   ├── aphids/
│   ├── armyworm/
│   ├── beetle/
│   ├── bollworm/
│   ├── grasshopper/
│   ├── mites/
│   ├── mosquito/
│   ├── sawfly/
│   └── stem_borer/
└── test/
    └── [same classes]/
```

Images in each class folder; no YOLO label files needed for classification training.

### Training
Run `python train_cls.py` to train. Configuration in script:
- Epochs: 30 (configurable)
- Batch size: 8
- Image size: 224
- Patience: 10
- Output: `runs/veggiecare_cls_v1/weights/best.pt`

Training automatically creates necessary directories and saves checkpoints.

### Inference
`YoloV11NanoDetector` loads model in priority order:
1. Trained custom model: `runs/veggiecare_cls_v1/weights/best.pt` (if exists)
2. Configured `model_path` from config
3. Default `yolo11n-cls.pt` (downloads if missing)

Returns `DetectionResult` with `detected`, `pest_class`, `confidence`, `image_path`, `timestamp`, `model`. Only reports detection if confidence >= `confidence_threshold` (default 0.70).

## Configuration

Configuration is loaded from `config/config.yaml` with validation in `config/config.py`. The dashboard can edit config via Settings tab (preserves YAML comments when using ruamel.yaml).

### Key Sections

#### System
```yaml
system:
  simulate_hardware: false  # true for dev without hardware
  timezone: Asia/Manila
  tick_seconds: 1.0
```

#### Camera
```yaml
camera:
  enabled: true
  type: usb  # usb or pi_camera
  device_index: 0
  capture_interval_seconds: 300
  image_dir: data/images
```

#### Pest Detection
```yaml
pest_detection:
  enabled: true
  relay_id: 3
  detector: yolov11n  # yolov11n or mock
  model_path: runs/veggiecare_cls_v1/weights/best.pt
  confidence_threshold: 0.70
  capture_interval_seconds: 300
  relay_activation_duration_seconds: 120
  max_activations_per_month: 2  # monthly limit for pest response only
  pest_classes: [...]
```

#### Stepper
```yaml
stepper:
  enabled: true
  pins: [23, 24, 25, 26]
  steps_per_plant: 512  # ~90 degrees for 28BYJ-48
  delay: 0.003
  simulate: false
```

#### Relays
```yaml
relays:
  active_high: true
  auto_off_watchdog_seconds: 600
  default_duration_seconds: 60
  items: [...]  # per-relay config with pins and durations
```

## API & Dashboard

Flask dashboard serves on `http://0.0.0.0:5000` by default.

### API Endpoints
- `GET /api/state` - Current system state (JSON)
- `POST /api/relay/<id>/activate` - Manually activate relay
- `POST /api/relay/<id>/off` - Turn relay off
- `POST /api/emergency-stop` - Emergency stop (all relays off, pause automation)
- `POST /api/automation/pause` / `resume` - Control automation
- `POST /api/pest/simulate` - Trigger mock pest detection
- `GET /api/detections` - Recent pest detections from DB
- `GET /api/config/schema` - Config schema for settings UI
- `GET /api/settings` - Current config (readable form)
- `POST /api/settings` - Update config (preserves comments with ruamel.yaml)
- `POST /api/system/restart` - Restart service (requires systemd)

Dashboard polls `/api/state` every 5 seconds for live updates. Never blocks on hardware operations.

## Database Schema

SQLite tables (WAL mode, foreign keys enabled conceptually):

- **npk_readings** (id, timestamp, nitrogen, phosphorus, potassium, below_threshold, created_at)
- **moisture_readings** (id, timestamp, moisture, below_threshold, created_at)
- **relay_activations** (id, relay_id, relay_name, trigger_type, duration_seconds, source, timestamp, created_at)
- **blocked_activations** (id, relay_id, relay_name, reason, trigger_type, source, timestamp, month_key, created_at)
- **pest_detections** (id, timestamp, detected, pest_class, confidence, image_path, model, raw_json, created_at)
- **system_events** (id, timestamp, level, category, message, details_json, created_at)

Monthly activation counting uses `month_key` (YYYY-MM) format.

## Automation Logic

Automation runs in background loop every `tick_seconds`.

### 1. NPK Monitoring
- Reads at `npk.read_interval_seconds` (default 300s)
- Compares N,P,K against thresholds
- Fires WARNING alert only on transition from OK → below threshold (not every tick)
- **No automatic relay activation** (alert-only as per design)

### 2. Soil Moisture & Watering
- Reads at `soil_moisture.read_interval_seconds` (default 30s)
- Auto-waters (relay 2 by default) when moisture < threshold
- **Cooldown**: `watering_cooldown_seconds` prevents retriggering immediately
- Logs activations to DB
- Fires alert on transition to low moisture

### 3. Pest Detection
- Runs every `pest_detection.capture_interval_seconds` (default 300s) when enabled
- Captures image via camera
- Runs detector inference
- If detected with confidence >= threshold:
  - Checks monthly activation count against `max_activations_per_month` (applies ONLY to pest response relay)
  - If under limit: activates relay 3 (pest response) for configured duration
  - If over limit: logs blocked activation, emits alert once per month
- Stores detection results in DB

### Safety Features
- All relays OFF at startup and shutdown (atexit + signal handlers)
- Watchdog enforces max ON time per relay
- Database errors never crash control loop (logged and handled)
- Automation can be paused/resumed from dashboard
- Emergency stop forces all off and pauses automation

## Development & Testing

### Setup
```bash
cd veggiecare
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### Running in Simulation
```bash
python app.py --simulate
# Dashboard at http://localhost:5000
```

### Running Tests
```bash
# All tests
pytest -v

# Specific modules
pytest tests/test_config.py -v
pytest tests/test_database.py -v
pytest tests/test_automation.py -v
pytest tests/test_dashboard.py -v
```

All tests pass in simulated mode with no hardware required.

### Training AI Model
```bash
python train_cls.py
```
Requires Pest-Data in correct structure. Trained weights saved to `runs/veggiecare_cls_v1/weights/best.pt`.

### Code Style
- Python type hints used throughout
- Abstract base classes for extensibility (Camera, PestDetector)
- Thread-safe design with locks
- Defensive error handling - hardware failures fall back to simulation/logging
- No `as any`, no suppressed type errors

## Deployment

### On Raspberry Pi (systemd)
1. Copy project to `/home/veggiecare/veggiecare/`
2. Create data and logs dirs: `mkdir -p data logs`
3. Set ownership to `veggiecare` user
4. Create venv and install dependencies
5. Install systemd unit from `deploy/veggiecare.service`
6. Enable and start service

See README.md for detailed deployment steps.

Service runs as non-root user with gpio/dialout/spi/i2c groups for hardware access.

## Troubleshooting

| Issue | Cause | Solution |
|---|---|---|
| Camera not opening (USB) | Wrong device index, camera in use | Check `camera.device_index`, try different index, ensure no other process using camera |
| YOLO model slow on Pi | CPU-only inference | Normal for Nano model on Pi; consider smaller batch sizes or reduce capture frequency |
| Stepper not moving | GPIO permissions, wiring | Check group membership (gpio), verify pin wiring to ULN2003 IN1-IN4 |
| NPK read fails | RS485 wiring, port | Check `/dev/ttyUSB*`, verify A/B lines, slave ID/baud |
| Relays don't switch | active_high setting wrong | Flip `relays.active_high` in config |
| Model not loading | Missing weights | Training creates weights in `runs/veggiecare_cls_v1/weights/best.pt`; detector falls back gracefully |

## License & Compliance

See [LICENSE](LICENSE) for project license. Third-party dependencies have various licenses (MIT, BSD, Apache 2.0, AGPL-3.0, ZPL 2.1). Key note: ultralytics (AGPL-3.0) is used - for internal/local deployment this is fine; if redistributing as combined work, AGPL-3.0 compliance applies.

## Future Enhancements

- Multi-plant stepper positioning with position tracking
- Object detection (bounding boxes) instead of pure classification
- Time-series analytics and alerts
- Remote notifications (email/SMS)
- OTA updates
- Expanded pest classes and model retraining pipeline
