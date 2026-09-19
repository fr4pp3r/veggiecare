# VeggieCare

Raspberry Pi 5 smart plant monitoring and control system.

## Hardware

| Component | Model | Interface | Pins / Config |
|-----------|-------|-----------|---------------|
| NPK Sensor | JXCT JXBS-3001 (7-in-1) | RS485 → USB (FTDI) | `/dev/ttyUSB0`, slave 1, 4800 8N1, Modbus RTU |
| Soil Moisture | Capacitive probe | MCP3008 ADC (SPI) | SPI0 CE0, CH0, 3.3 V |
| Relay 1 (Fertilizer) | 5 V module, active-high | GPIO 17 | `gpiozero.LED(17, active_high=True)` |
| Relay 2 (Watering) | 5 V module, active-high | GPIO 27 | `gpiozero.LED(27, active_high=True)` |
| Relay 3 (Pest) | 5 V module, active-high | GPIO 22 | `gpiozero.LED(22, active_high=True)` |
| Camera | *not installed yet* | — | — |

## Quick Start (Development)

```bash
# 1. Clone / copy project
cd veggiecare

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run in simulated mode (no hardware needed)
python app.py --simulate

# 5. Open dashboard
# http://localhost:5000
```

## Configuration

Edit `config/config.yaml`. All settings are validated at startup.

Key sections:

```yaml
system:
  simulate_hardware: false      # true for dev on non-Pi machines
  timezone: Asia/Manila

npk:
  enabled: true
  port: /dev/ttyUSB0
  slave_id: 1
  baudrate: 4800
  thresholds:
    nitrogen: 20      # mg/kg
    phosphorus: 20
    potassium: 20
  read_interval_seconds: 300
  log_interval_seconds: 900

soil_moisture:
  enabled: true
  threshold: 30                 # %
  watering_cooldown_seconds: 3600

relays:
  active_high: true
  auto_off_watchdog_seconds: 600
  items:
    - id: 1; name: fertilizer; pin: 17; activation_duration_seconds: 120
    - id: 2; name: watering;   pin: 27; activation_duration_seconds: 300
    - id: 3; name: pest_response; pin: 22; activation_duration_seconds: 120

pest_detection:
  enabled: false                # set true when camera + model are ready
  detector: mock
  confidence_threshold: 0.70
  max_activations_per_month: 2  # ONLY pest response is monthly-limited
```

## Dashboard

- **URL**: `http://<pi-ip>:5000`
- **Live updates**: polls `/api/state` every 5 s
- **Sections**: NPK, Soil Moisture, Relays, Pest Detection, System Status, Alerts
- **Manual controls**: Relay 1 (fertilizer) button, emergency stop, pause/resume automation, pest simulation
- **Settings tab**: edit grouped settings in plain language (sliders/toggles/selects), save to `config/config.yaml`, then restart the server to apply. Restart switches all pumps off for ~30 s and is only available when running as a systemd service.

## Deployment (systemd)

```bash
# 1. Copy project into your home directory
sudo cp -r veggiecare /home/veggiecare/
# data/ and logs/ are gitignored — create them explicitly (systemd
# ReadWritePaths fails with status=226/NAMESPACE if they don't exist)
mkdir -p /home/veggiecare/veggiecare/data /home/veggiecare/veggiecare/logs

# 2. Ownership — the service runs as 'veggiecare' (your Pi login user, set
#    via Raspberry Pi Imager / first-boot wizard). Never use 'pi' — that
#    user does not exist on modern images; systemd fails with 217/USER.
sudo chown -R veggiecare:veggiecare /home/veggiecare/veggiecare

# 3. Create venv & install (.venv is gitignored, so it never ships with
#    the repo) — if this fails with "ensurepip is not available":
#      sudo apt install -y python3-venv
cd /home/veggiecare/veggiecare
/usr/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# Existing setups: re-run the pip line above to pick up ruamel.yaml,
# which the dashboard Settings page needs to save config changes.

# 4. Install systemd unit
sudo cp deploy/veggiecare.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable veggiecare
sudo systemctl start veggiecare

# 5. Check status
sudo systemctl status veggiecare
sudo journalctl -u veggiecare -f
```

The service runs as the `veggiecare` login account, restarts on failure, and logs to the systemd journal. GPIO/SPI/serial access comes from `SupplementaryGroups=gpio dialout spi i2c`, plus the built-in membership your login user already has in those groups.

## Running Tests

```bash
# All tests (simulated hardware, no Pi required)
pytest -v

# Specific module
pytest tests/test_database.py -v
pytest tests/test_automation.py -v
```

Tests run entirely in simulated mode and cover:
- Config loading & validation
- Database schema & monthly activation counting
- Relay watchdog auto-off
- Automation rules (NPK alert only, watering + cooldown, pest → relay 3 with monthly limit)
- Mock pest detector modes
- Dashboard API endpoints

## Architecture

```
app.py
├── config.load_config()  → validated config dict
├── Database()            → SQLite (WAL mode, thread-safe)
├── SystemState()         → shared in-memory state (RLock)
├── Sensors (NPK, Moisture)  → lazy hardware imports, simulated fallback
├── RelayController()     → gpiozero + watchdog thread (hard OFF at startup)
├── AutomationController  → background thread: read → rules → actuate → log
│   ├── NPK: alert only (no auto relay)
│   ├── Moisture: auto-water with cooldown (no monthly limit)
│   └── Pest: detect → relay 3 (monthly-limited)
├── Pest Detector (mock)  → swappable interface
└── Flask dashboard       → reads SystemState, never blocks on hardware
```

**Safety guarantees**:
- All relays forced OFF at process start and on any exit (signal/atex)
- Watchdog thread enforces max ON time per relay (configurable, default 600 s)
- Database errors never crash the control loop
- Automation can be paused from dashboard

## Future Camera / AI Integration

The pest detection module is already wired but disabled (`enabled: false`).

When PiCamera 3 + model arrive:

1. Implement `camera/camera.py:PiCamera.capture()` → save JPEG → return path
2. Implement `pest_detection/detector.py:PestDetector.detect(image_path)` → run model → return `DetectionResult`
3. Set `pest_detection.enabled: true` and `camera.enabled: true` in config
4. Restart service — no other code changes needed

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `systemd`: `Failed at step USER ... status=217/USER` | `User=` in the unit doesn't exist — `pi` is gone on newer RPi OS images | Create the `veggiecare` account (Deployment §2) or set `User=` to a real account, then `daemon-reload` + `restart` |
| `systemd`: `Failed to set up mount namespacing ... status=226/NAMESPACE` | `data/` or `logs/` missing (gitignored, so fresh clones lack them) | `sudo mkdir -p /home/pi/veggiecare/data /home/pi/veggiecare/logs` then `systemctl restart veggiecare` |
| `systemd`: `Failed at step EXEC ... status=203/EXEC` | ExecStart binary missing — `.venv` is gitignored and was never created on the Pi | Create & install the venv as `veggiecare` (Deployment §3), then `systemctl restart veggiecare` |
| `gpiozero` import error | Not on Pi or missing libs | `pip install gpiozero lgpio` or run `--simulate` |
| `spidev` build fails | Missing kernel headers | `sudo apt install python3-spidev` or `--simulate` |
| NPK read fails | RS485 wiring / slave ID / baud | Check A/B lines, power, `ls /dev/ttyUSB*`, try `minimalmodbus` debug |
| Moisture reads 0 | MCP3008 not powered / wrong channel | Check 3.3 V, GND, SPI enable (`dtparam=spi=on`), CH0 wiring |
| Relays don't switch | `active_high` wrong | Flip `relays.active_high` in config |
| Dashboard not loading | Port 5000 blocked / service down | `sudo systemctl status veggiecare`, check firewall |
| DB locked | Multiple processes | Ensure only one `app.py` runs; WAL handles concurrent readers |

## License

MIT