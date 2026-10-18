# VeggieCare User Manual

**A practical guide to operating the VeggieCare smart plant monitoring and control system.**

VeggieCare watches soil moisture and nutrient levels, monitors for pests with a camera and AI model, and automatically controls pumps for watering, fertilizing, and pest treatment. Everything is managed from a web dashboard.

---

## Table of Contents

1. [Getting Started](#1-getting-started)
2. [Dashboard Tour](#2-dashboard-tour)
3. [Using the Overview Page](#3-using-the-overview-page)
4. [The Logs Page](#4-the-logs-page)
5. [The Settings Page](#5-the-settings-page)
6. [The Camera Page](#6-the-camera-page)
7. [The Database Page](#7-the-database-page)
8. [How the Automation Works](#8-how-the-automation-works)
9. [Safety Features](#9-safety-features)
10. [Command-Line Options](#10-command-line-options)
11. [Configuration File Reference](#11-configuration-file-reference)
12. [Running on the Raspberry Pi](#12-running-on-the-raspberry-pi)
13. [Troubleshooting](#13-troubleshooting)
14. [Frequently Asked Questions](#14-frequently-asked-questions)

---

## 1. Getting Started

### What VeggieCare does

| Subsystem | What it does |
|---|---|
| **Soil moisture** | Reads a capacitive probe and automatically waters when moisture drops below your threshold. |
| **NPK sensor** | Measures nitrogen, phosphorus, and potassium in the soil (mg/kg) and alerts you when a nutrient falls below its alert level. |
| **Pest detection** | Periodically scans plants with a USB camera and a YOLOv11 AI model; can trigger the pest-treatment sprayer when a pest is found. |
| **Fertilizer recommendation** | Suggests a fertilizer product and dosage based on your crop, growth stage, and current NPK readings. |
| **Relays / pumps** | Controls 3 relay-driven pumps: fertilizer, watering, and pest treatment. |
| **Dashboard** | A web interface for monitoring, manual control, settings, logs, and data export. |

### Accessing the dashboard

When VeggieCare is running, open a web browser:

```
http://localhost:5000          # on the machine running VeggieCare
http://<veggiecare-ip>:5000     # from another device on the same network
```

There is **no login** — anyone who can reach port 5000 on your network can view and control the system. Keep VeggieCare on a trusted local network only.

### Trying it without hardware (simulated mode)

You can run the entire system on a PC with no sensors, relays, or camera attached:

```bash
python app.py --simulate
```

Or set `simulate_hardware: true` in `config/config.yaml` (see [Settings](#5-the-settings-page)). In simulated mode:

- No GPIO, serial, or SPI hardware is touched.
- Sensors report simulated values (default: moisture 45%, N 25, P 15, K 30 mg/kg).
- Relays and the camera stepper run as no-ops.
- The console prints: `*** SIMULATED HARDWARE MODE ***`

> On the real Raspberry Pi, `simulate_hardware` must be `false`.

### First-time hardware setup (Raspberry Pi)

See [Running on the Raspberry Pi](#12-running-on-the-raspberry-pi) for the full install. The short version:

```bash
cd /home/veggiecare/veggiecare
chmod +x scripts/setup_rpi5.sh scripts/setup_rpi5_deps.sh
./scripts/setup_rpi5.sh
sudo usermod -aG video veggiecare     # camera access — run this once, then reboot
sudo systemctl start veggiecare
```

Then open `http://<pi-ip>:5000`.

---

## 2. Dashboard Tour

The dashboard has three main tabs and two extra pages:

| Where | What's there |
|---|---|
| **Overview** (tab) | Live status of everything: alerts, moisture, NPK, relays, pest detection, tanks, fertilizer advice, system health, and manual controls. |
| **Logs** (tab) | Historical tables: sensor readings, relay activations, system events, and pest detections. |
| **Settings** (tab) | Edit every tunable setting and save it back to the configuration file. |
| **Camera** (page) | Live camera video, device info, and a camera finder tool. |
| **Database** (page) | Browse every database table and download a backup. |

Use the top navigation bar (or the hamburger menu / bottom bar on mobile) to move between them.

**Live updates:** the Overview refreshes automatically every **5 seconds**. The camera page checks camera status every 10 seconds while streaming. Nothing requires a manual page refresh.

**Notifications:** a bell icon shows recent alerts. New items appear as they happen; you can dismiss individual notifications or clear them all. Dismissal is remembered by your browser.

---

## 3. Using the Overview Page

The Overview is your main screen. From top to bottom:

### Header — running time and mode

- **Running for …** — how long the server has been running.
- **Mode badge** — `Live hardware` (real sensors and pumps) or `Demo mode` (simulated test data). Always check this before trusting the readings.

### At-a-glance strip

The first thing on the Overview page is a row of five status chips: **Soil, Watering, Pests, Camera, Alerts**. Each shows a colored dot and a short plain-language status, so you can see whether anything needs attention before reading any card:

| Chip | Example readings |
|---|---|
| Soil | `Moisture is fine` / `Dry — needs water` / `No reading` |
| Watering | `Pumps off` / `Watering now` / `A pump is running` |
| Pests | `No pests found` / `Pest found: aphid` / `Pest watch off` |
| Camera | `Working` / `Not connected` / `Error` |
| Alerts | `None` / `2 new` / `1 need attention` |

Green means fine, amber means look closer, red means act.

### Alerts card

Shows the latest system alerts (newest first, last 30 kept):

| Level | Meaning |
|---|---|
| `WARNING` | Something needs attention: low moisture, low NPK, pest detected, monthly limit reached, daily watering budget exceeded. |
| `CRITICAL` | Serious fault: the soil isn't getting wetter after watering (pump/water problem). |
| `INFO` | Informational: fertilizer recommended, fault cleared, etc. |

Click **Clear** to clear the alert list. Persistent history is always kept in the Logs page → Events.

### Soil Moisture card

- Current moisture as a **percentage** with a threshold bar.
- The status reads **"Soil is dry — plants need water"** or **"Soil moisture is fine"**.
- When moisture is low and automatic watering is on, the watering pump starts automatically (see [How the Automation Works](#8-how-the-automation-works)).

### NPK Sensor card

- Nitrogen, Phosphorus, and Potassium in **mg/kg**.
- Each nutrient reads a plain verdict — **"Nitrogen is fine"** or **"Nitrogen is low"** — next to its value.
- Click **Add plant food now** to run the plant food pump manually.

### Relays card

Shows the state of all 3 pumps (each labeled by what it does, not just its relay number):

| Relay | Controls | Default run time |
|---|---|---|
| 1 — NPK/Fertilizer | Plant food pump | 120 s |
| 2 — Soil Moisture/Watering | Watering pump | 120 s |
| 3 — Pest Response | Pest sprayer | 120 s |

- **Turn on / Turn off** buttons control each pump manually. Manual runs are still capped by the safety watchdog (max 180 s).
- **Stop everything** immediately switches every pump off.
- If a pump shows as **unavailable**, the Pi cannot control that pin — see [Troubleshooting](#13-troubleshooting).

### Pest Detection card

- Shows the last detection (pest name, confidence) or "no pests detected".
- **Check for pests now** — runs a scan of the current camera view only.
- **Check for pests now (move camera)** — runs a full scan, rotating the camera through all plants.
- Automatic scans run every **10 minutes** by default (configurable in Settings).
- When a pest is found at or above the confidence threshold (default **70%**), the pest-treatment relay (Relay 3) activates automatically — up to the monthly limit — and a treatment suggestion is shown.
- Treatment suggestions include cultural, organic, and chemical options with an important disclaimer: **these are guidance only — always follow product labels and local regulations.**

### Tanks (Fertilizer & Pesticide) card

If tank tracking is enabled, this shows estimated volume remaining and lets you:

- **Mark refilled** (under "Mark tank refilled") — record that you refilled the tank, resetting the estimate. The main card shows the few key facts (amount left, status, automatic on/off); everything else folds under **Tank details**.
- **Turn automatic on / Turn automatic off** — turn automatic dose tracking on or off.

When tank tracking is on and stock is insufficient, automatic pest treatment is safely refused (fail-closed).

### Fertilizer Recommendation card

Based on your crop type, growth stage, and current NPK readings, suggests a fertilizer (e.g. "Balanced 20-20-20") with a dosage in grams per 10 L. Choose your crop and stage in [Settings](#5-the-settings-page).

### System Status card

Health at a glance: **Running for, Database, NPK Sensor, Moisture Sensor, Camera, Pest Model, Automation**. Each row shows a colored dot plus a plain word (Connected, Error, OK, Running, Paused, Stopped, Not configured). Green means healthy; an error message explains anything that isn't working. A missing sensor never crashes the system — it is simply reported as unavailable while everything else keeps running.

### Pause / Resume automation

The **Pause Automation** button freezes all automatic actions (watering, pest scans, alerts from new readings) without stopping the server or camera. **Resume Automation** re-enables them. Use this while you are doing maintenance on the beds.

### System Test card

Run health checks on demand:

- Tick the components to test: **Relays, Sensors, Camera, Stepper**.
- **Run test** — exercises the selected components and reports results.
- **Move camera arm out and back** — verifies the camera-positioner motor moves out and returns. The steps/delay settings live under an **Advanced** disclosure and are safe to ignore.

---

## 4. The Logs Page

The Logs page shows history stored in the local database, in four sub-tabs:

| Sub-tab | Contents |
|---|---|
| **Readings** | NPK readings (N/P/K mg/kg) and soil moisture (%) with timestamps. |
| **Activations** | Every relay run: which relay, how long, and whether it was **manual** or **automatic**. |
| **Events** | System events and alerts by level (INFO / WARNING / ERROR / CRITICAL). |
| **Detections** | Every pest detection: pest name, confidence, and the treatment suggestion. |

- Choose how many rows to show: **25 / 50 / 100 / 200**, then **Refresh**.
- History is tabular (no charts). For analysis or long-term storage, download the database from the [Database page](#7-the-database-page).

> History is never automatically deleted, so these tables grow over time. The dashboard only ever loads the most recent rows, so performance is unaffected.

---

## 5. The Settings Page

Every setting below can be edited in the browser and saved. Saving **writes back to `config/config.yaml`** (preserving your comments) and records an entry in the configuration-change log.

> **Important:** settings take effect after a restart. After saving you'll see *"Settings saved. Restart the server to apply them."* — restart with the **Restart Server** button (when running under systemd) or `sudo systemctl restart veggiecare`.

Invalid values are rejected with a message on the offending row; nothing is saved until all values are valid.

### Watering

| Setting | Range | What it does |
|---|---|---|
| Automatic watering | on/off | Master switch for automatic watering. |
| Water when soil moisture drops below | 0–100 % | The moisture threshold that triggers watering (default 30 %). |
| Minimum wait between waterings | 0–600 s | Cooldown after each watering before another can start (default 10 s). Also the window used to check whether watering actually worked. |
| Threshold buffer | 0–20 % | Hysteresis: after a low event, moisture must rise this far above the threshold before the "low" state clears (default 5 %). |
| Most watering allowed per day | 1–120 min | Daily watering budget; watering is blocked once used up (default 30 min). |

### Fertilizer (soil nutrients)

| Setting | Range | What it does |
|---|---|---|
| Nutrient sensor | on/off | Enable or disable the NPK sensor. |
| Nitrogen alert level | 0–200 mg/kg | Alert when nitrogen drops below this (default 20). |
| Phosphorus alert level | 0–200 mg/kg | Alert when phosphorus drops below this (default 20). |
| Potassium alert level | 0–200 mg/kg | Alert when potassium drops below this (default 20). |

### Pest control

| Setting | Range | What it does |
|---|---|---|
| Pest control | on/off | Master switch for automatic pest monitoring. |
| Treatment suggestions | on/off | Show IPM treatment advice with detections. |
| How often to run the pest monitor | 1–60 min | Automatic scan interval (default 10 min). |
| Confidence required to respond | 50–100 % | Minimum AI confidence to count a detection (default 70 %). |
| Pest responses per month | 0–30 | Monthly cap on automatic pest-treatment sprays (default 2). **0 disables automatic spraying** while still detecting and alerting. |
| Camera wheel diameter | 0.5–10 in | Physical size of the stepper camera-positioner wheel (default 2.125 in). |
| Max sweep steps | read-only | Computed from the wheel diameter and bed length — how far the camera travels per plant. |

### Fertilizer Recommendations

| Setting | Choices | What it does |
|---|---|---|
| Fertilizer recommendations | on/off | Master switch for the recommendation engine. |
| Crop type | tomato / pepper / lettuce / cucumber / custom | Which crop target ranges to use. |
| Growth stage | seedling / vegetative / flowering / fruiting | Which stage target ranges to use. |
| Recommendation engine | rule_engine / sklearn / onnx / mock | How advice is computed. Leave as `onnx` unless you know otherwise. |

### Safety

| Setting | Range | What it does |
|---|---|---|
| Automatic pump shut-off | 30–600 s | The hardware watchdog: **every** relay run — manual or automatic — is force-switched off after this many seconds (default 180 s). Cannot be disabled. |

### Pump run times

| Setting | Range | What it does |
|---|---|---|
| Fertilizer pump (Relay 1) | 5–600 s | How long the fertilizer pump runs per activation (default 120 s). |
| Watering pump (Relay 2) | 5–600 s | How long the watering pump runs per activation (default 120 s). |
| Pest sprayer (Relay 3) | 5–600 s | How long the pest sprayer runs per activation (default 120 s). |

### System

| Setting | What it does |
|---|---|
| System name | Display name shown in the interface. |
| Time zone | Timezone for timestamps (default Asia/Manila). |

### Restart Server

At the bottom of Settings, a **Restart Server** button restarts the application (takes about 30 seconds). It is only enabled when VeggieCare runs under systemd — otherwise restart manually: `sudo systemctl restart veggiecare`.

---

## 6. The Camera Page

Open **Camera** from the top navigation.

### Live View

- Video streams continuously once the camera starts (it auto-starts on page load when a camera is configured).
- Status badge: **Live / Ready / Connected / Disconnected / Stopped / Unavailable / Offline**.
- **Start / Stop** — toggle the stream.
- **Snapshot** — opens the current frame as a full-size image in a new tab (handy for records or sharing).
- **Retry** — appears after several failed reconnection attempts; restarts the stream manually.

### Device card

Shows the camera's actual **Status, Backend, Device path, Resolution, Frame rate, and OpenCV version**. If the status reports an error, the message tells you exactly what to fix (permission, cable, missing OpenCV, etc.).

### Detected Video Devices card

- **Find my camera** probes every video device on the system and reports which one actually delivers frames.
- On success it tells you the working device path to put into `camera.device_path` in `config/config.yaml`.
- This is the recommended way to find your camera on a Raspberry Pi 5, where many camera nodes appear that never produce images.

> **Never set the camera to `/dev/ttyUSB0`** — that is the NPK sensor's serial adapter, not a camera. The configuration validator will reject it with an explanatory error. Cameras use `/dev/video0`, `/dev/video1`, and so on.

---

## 7. The Database Page

Open **Database** from the top navigation to inspect and export the recorded history.

- **Table list** — click **Refresh Table List**, then **View** on any table to browse it with pagination. Tables include readings, relay activations, blocked activations, pest detections, fertilizer recommendations, system events, configuration changes, and tank records.
- **Download SQLite Database** — downloads a consistent snapshot of the entire database (`veggiecare.db`) while the system keeps running. This is the recommended backup and export method; you can open the file with any SQLite tool or import it into other software.
  - If the **Download JSON Export** button reports an error, use **Download SQLite Database** instead — the JSON export endpoint is currently unavailable.

### Restoring a backup

1. Stop the service: `sudo systemctl stop veggiecare`
2. Replace `data/veggiecare.db` with your downloaded snapshot (renamed to `veggiecare.db`).
3. Start the service: `sudo systemctl start veggiecare`

---

## 8. How the Automation Works

The automation loop runs about once per second in the background. It never blocks the dashboard, and a failing sensor never crashes it — the failure is logged and shown in System Status while everything else continues.

### Watering (automatic)

1. The moisture probe is read (every 5 seconds by default) and converted to a **0–100 %** value using your probe's dry/wet calibration.
2. When moisture drops **below the threshold** (default 30 %), a "soil moisture low" warning is raised.
3. If automatic watering is on, the watering relay (Relay 2) activates for its configured run time (default 120 s), subject to:
   - **Cooldown** — no re-trigger until the minimum wait has passed (default 10 s).
   - **Daily budget** — total watering per day cannot exceed the configured cap (default 30 minutes); when exceeded you get a once-a-day warning and watering pauses until the next day.
   - **Pause switch** — automation paused = no watering.
4. **Self-check:** after the cooldown, if moisture did not rise by at least **2 %**, it counts as a failed watering. After **3 consecutive failures**, a **CRITICAL** alert *"The soil isn't getting wetter after watering"* latches and automatic watering is blocked — this usually means the pump is dry, a hose is kinked, or the probe failed. The system re-enables itself once moisture recovers above the threshold, or an operator can clear it with the **Reset** button on the Overview.
5. If tank tracking is enabled, watering can also dispense a measured fertilizer dose (fertigation).

### Nutrient (NPK) monitoring

- The NPK sensor is read every **5 minutes** by default; readings are stored in the database every **15 minutes**.
- Values are in **mg/kg**. When any of N, P, or K drops **below its alert level** (default 20 mg/kg each), a warning is raised **once per drop** (no repeated alerts while it stays low).
- NPK alerts never trigger a relay by themselves — they inform you and feed the fertilizer recommendation.

### Fertilizer recommendations

When enabled, each new set of NPK readings is matched against target ranges for your **crop type** and **growth stage**. If a nutrient is outside its target, a fertilizer from the built-in list is recommended with a dosage (g per 10 L). The recommendation and the reasoning are shown on the Overview and logged.

### Pest monitoring and response

1. Every **10 minutes** (default), the system runs a scan: the stepper motor moves the camera through each plant position (or a single camera-only shot if there's no stepper).
2. Each frame is analyzed by the YOLOv11 model. A detection counts only if its **confidence ≥ the threshold** (default 70 %).
3. On a positive detection:
   - A **WARNING** *"Pest detected: …"* is raised and shown with a treatment suggestion.
   - The pest-treatment relay (Relay 3) activates automatically for its run time (default 120 s) — **unless** the **monthly limit** has been reached (default **2** sprays per calendar month; resets on the 1st) or pesticide stock (if tracked) is insufficient.
4. When the limit is reached you get a warning *"Monthly pest response limit reached"* and detections continue to be recorded and alerted — only the spraying stops. Raise or lower the cap in Settings → Pest control.

The monthly limit applies **only** to the pest-treatment relay. Watering and fertilizer are never monthly-limited.

---

## 9. Safety Features

VeggieCare is designed so a software fault cannot leave a pump running unattended:

| Safety | Behavior |
|---|---|
| **Watchdog auto-off** | A separate hardware watchdog thread force-switches **every** relay OFF after the configured limit (default 180 s), no matter what requested the run. It cannot be set to "never". |
| **All relays OFF on exit** | On shutdown, restart, Ctrl-C, or systemd stop, every relay is switched OFF before the process exits. |
| **Watering budget** | Caps total automatic watering per day so a stuck sensor can't drain the tank. |
| **Watering self-check** | Detects a pump that runs but doesn't wet the soil (3 strikes → CRITICAL lockout). |
| **Monthly pest cap** | Prevents unlimited pesticide spraying. |
| **Fail-closed dosing** | If tank tracking is on and estimated stock is insufficient, spraying/dosing is refused and logged as a blocked activation. |
| **Strict configuration validation** | The system refuses to start with an invalid configuration and tells you every problem at once. |

---

## 10. Command-Line Options

```bash
python app.py [--simulate] [--config PATH] [--host HOST] [--port PORT]
```

| Option | Default | Description |
|---|---|---|
| `--simulate` | off | Force simulated hardware mode (same as `simulate_hardware: true`). |
| `--config PATH` | `config/config.yaml` | Use a different configuration file. |
| `--host HOST` | `0.0.0.0` | Network interface to bind the dashboard to. |
| `--port PORT` | `5000` | Dashboard port. |

You can also point VeggieCare at a config file with the `VEGGIECARE_CONFIG` environment variable. Precedence: `--config` flag → `VEGGIECARE_CONFIG` → default path.

---

## 11. Configuration File Reference

All settings live in one file: **`config/config.yaml`**. It is the single source of truth — the Settings page edits this same file. Edit it by hand if you prefer, then restart. The most important sections:

### system

| Key | Default | Notes |
|---|---|---|
| `name` | `VeggieCare` | Display name. |
| `simulate_hardware` | `false` | **Must be false on the real Pi.** |
| `timezone` | `Asia/Manila` | Timestamp timezone. |

### database / logging

| Key | Default | Notes |
|---|---|---|
| `database.path` | `data/veggiecare.db` | SQLite file; relative paths resolve from the project root. |
| `logging.level` | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `logging.file` | `logs/veggiecare.log` | Rotating log: ~1 MB per file, 3 backups. |

### soil_moisture (key values)

| Key | Default | Notes |
|---|---|---|
| `threshold` | `30` | Water below this moisture (%). |
| `hysteresis` | `5` | Rise needed to clear the low state (%). |
| `watering_cooldown_seconds` | `10` | Minimum wait between automatic waterings. |
| `max_watering_seconds_per_day` | `1800` | Daily watering budget (30 min). |
| `response_rise_percent` | `2.0` | Rise counted as a successful watering. |
| `max_consecutive_non_responses` | `3` | Failures before the CRITICAL lockout. |
| `dry_adc` / `wet_adc` | `500` / `50` | Probe calibration (raw ADC values; dry must be greater than wet). |
| `relay_id` | `2` | Which relay waters. |

### npk (key values)

| Key | Default | Notes |
|---|---|---|
| `port` | `/dev/ttyUSB0` | RS485 serial adapter for the sensor. |
| `thresholds.nitrogen / phosphorus / potassium` | `20` each | Alert levels (mg/kg). |
| `read_interval_seconds` | `300` | Sensor read cadence. |
| `log_interval_seconds` | `900` | Database write cadence. |

### relays

| Key | Default | Notes |
|---|---|---|
| `auto_off_watchdog_seconds` | `180` | Hard failsafe OFF deadline for every run. |
| `default_duration_seconds` | `60` | Default manual-activation length. |
| `items` | 3 relays | Relay 1 fertilizer (GPIO 17), Relay 2 watering (GPIO 27), Relay 3 pest response (GPIO 22); 120 s each. |

### pest_detection (key values)

| Key | Default | Notes |
|---|---|---|
| `enabled` | `true` | Master switch. |
| `detector` | `yolov11n` | `yolov11n` (real AI) or `mock` (testing). |
| `confidence_threshold` | `0.70` | Minimum confidence to act. |
| `capture_interval_seconds` | `600` | Automatic scan interval (10 min). |
| `relay_activation_duration_seconds` | `120` | Spray duration. |
| `max_activations_per_month` | `2` | Monthly automatic-spray cap (pest relay only). |

### camera

| Key | Default | Notes |
|---|---|---|
| `enabled` | `true` | Master switch. |
| `device_path` | `auto` | `auto` probes all `/dev/video*` and picks the first that delivers frames (recommended), or pin a specific `/dev/videoN`. **Never a `/dev/tty*` path.** |
| `width` / `height` / `fps` | `640` / `480` / `15` | Requested capture format. |

### Advanced sections

`tanks` (fertilizer/pesticide volume tracking and automatic dosing), `npk_recommendations` (crop targets, fertilizer recipes, ML engine), and `stepper` (camera-positioner motor: pins, steps per plant, wheel diameter, bed extension distance) have many more keys — see the comments inside `config/config.yaml`, which document every option.

**Validation:** the configuration is strictly checked at startup. If anything is wrong, the system refuses to start and prints **every** problem in one message (e.g. *"soil_moisture.dry_adc: must be > soil_moisture.wet_adc, got 10"*). Fix the listed keys and start again.

---

## 12. Running on the Raspberry Pi

### Install (automated)

```bash
cd /home/veggiecare/veggiecare
chmod +x scripts/setup_rpi5.sh scripts/setup_rpi5_deps.sh
./scripts/setup_rpi5.sh
```

The script checks disk space and hardware health, creates the `veggiecare` user and service folders, builds the virtual environment, installs all Python dependencies (including the CPU-only PyTorch/YOLO stack), and installs the systemd service.

Two things the script does **not** do:

```bash
sudo usermod -aG video veggiecare     # camera access — required once
sudo systemctl reboot                 # apply the new group membership
```

`data/` and `logs/` must exist before the service starts (the installer creates them).

### Service commands

| Task | Command |
|---|---|
| Start | `sudo systemctl start veggiecare` |
| Stop | `sudo systemctl stop veggiecare` |
| Restart (e.g. after changing settings) | `sudo systemctl restart veggiecare` |
| Check status | `sudo systemctl status veggiecare` |
| Watch live logs | `sudo journalctl -u veggiecare -f` |
| Start automatically at boot | `sudo systemctl enable veggiecare` |

The service runs the dashboard on **port 5000** and restarts automatically if it crashes (`Restart=always`).

### Updating the software

```bash
cd /home/veggiecare/veggiecare
git pull
sudo systemctl restart veggiecare
```

(If you deploy from a workstation instead of git, sync the project files over with `rsync`, excluding `.venv`, then restart.)

### Backing up data

- **Easiest:** Database page → **Download SQLite Database**.
- **On the Pi:** copy `data/veggiecare.db` while the service is stopped, or any time as a live snapshot via the download button (it uses SQLite's safe backup API).
- Settings live in `config/config.yaml` — back that file up too.

### Log files

| Where | Contents |
|---|---|
| `logs/veggiecare.log` | Application log (rotating, ~1 MB × 4 files). Startup/shutdown, relay actions, watchdog events, sensor and camera errors. |
| `sudo journalctl -u veggiecare -f` | The same output plus service-level messages, live. |

Increase detail with `logging.level: DEBUG` in the config, then restart.

---

## 13. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Dashboard won't open | Service not running | `sudo systemctl status veggiecare`; `sudo systemctl start veggiecare` |
| Service exits with `status=217/USER` | The `veggiecare` OS user doesn't exist | Create the user, or reinstall with `./scripts/setup_rpi5.sh` |
| Service exits with `226/NAMESPACE` | `data/` or `logs/` folder missing | `mkdir -p /home/veggiecare/veggiecare/{data,logs}` then restart |
| All relays show **unavailable** | GPIO backend missing (`lgpio` / `adafruit-lgpio`) | Install the backend for your Python/architecture; in simulate mode relays always show available |
| Camera: "Permission denied" | User not in the `video` group | `sudo usermod -aG video veggiecare`, then reboot |
| Camera: "Not installed" / no OpenCV | OpenCV missing | `pip install opencv-python-headless` (inside `.venv`) |
| `ImportError: libGL.so.1` | GUI OpenCV installed over headless | `pip install --force-reinstall opencv-python-headless` |
| `ModuleNotFoundError: ultralytics` | AI dependency skipped during install | `pip install --no-deps "ultralytics>=8.4.0"` |
| Camera never shows video on Pi 5 | Many metadata-only camera nodes exist | Camera page → **Find my camera**, then set `camera.device_path` to the reported node |
| Camera config rejected: "is a serial port" | `device_path` points at `/dev/ttyUSB0` | That's the NPK adapter, not a camera. Use `/dev/videoN` or `auto` |
| Detector always says *mock* | Weights missing or wrong detector selected | Set `detector: yolov11n`, confirm the weights folder exists, check `journalctl -u veggiecare -f` |
| "The soil isn't getting wetter after watering" (CRITICAL) | Pump ran but moisture didn't rise 3 times in a row | Check the pump has water, tubing isn't kinked, and the probe is seated; then press **Reset** on the Overview |
| "Daily watering budget exceeded" | Watering used its daily allowance | Wait for the next day, or raise **Most watering allowed per day** in Settings |
| "Monthly pest response limit reached" | Pest relay hit its monthly cap | Intended behavior — the cap resets on the 1st, or raise **Pest responses per month** in Settings |
| NPK always errors / stale | Serial adapter missing or wrong port | Check the adapter enumerates (`ls /dev/ttyUSB*`), verify `npk.port` in config |
| Settings won't save | A value is out of range | The offending row is highlighted with the allowed range — fix it and save again |
| Settings saved but nothing changed | Settings apply on restart only | Press **Restart Server** on the Settings page, or `sudo systemctl restart veggiecare` |
| `No space left on device` during install | Disk full | `pip cache purge`, remove `/tmp/pip-*`, re-run the install |
| Sensor readings look wrong | Probe needs calibration | Adjust `soil_moisture.dry_adc` (probe in dry air) and `wet_adc` (probe in water); NPK `scale` multiplier |

---

## 14. Frequently Asked Questions

**Do I have to use real hardware to try it?**
No — run `python app.py --simulate` on any PC. Simulated sensors produce plausible values and all dashboard features work.

**What happens if a sensor breaks or is unplugged?**
The system keeps running. The sensor shows an error in System Status and the event log, and the automation simply skips that reading. Nothing else stops.

**Can I water or fertilize manually?**
Yes — use the **Turn on / Turn off** buttons on each pump card, or **Add plant food now** on the NPK card. Manual runs still respect the safety watchdog. **Stop everything** kills all pumps at once.

**Why did my pest sprayer not activate after a detection?**
Either the monthly cap (default 2) is used up, the confidence was below the threshold, or — with tank tracking on — the estimated pesticide stock was insufficient. The Logs → Events tab records the reason.

**How do I change a setting permanently?**
Use the Settings page (writes to `config/config.yaml`) or edit the YAML directly, then restart. The YAML file's own comments document every advanced option.

**Does the system work without internet?**
Yes. VeggieCare runs entirely locally — sensors, AI model, dashboard, and database all operate offline.

**Is my data sent anywhere?**
No. All readings, detections, and images stay on the device (`data/` and `logs/`). The dashboard has no login, so keep the device on a trusted network.

**How much history is kept?**
All of it — readings, activations, events, and detections are never auto-deleted. Back up or prune the database from the Database page as needed.

**The dashboard looks stale.**
It refreshes every 5 seconds on its own; check System Status for database or automation errors, and confirm the mode badge says `Live hardware` rather than `Demo mode`.

---

*VeggieCare — smart plant monitoring and control. Dashboard default: port 5000.*
