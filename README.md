# VeggieCare

Raspberry Pi 5 smart plant monitoring and control system.

## Hardware

| Component | Model | Interface | Pins / Config |
|-----------|-------|-----------|---------------|
| NPK Sensor | JXCT JXBS-3001 (7-in-1) | RS485 -> USB (FTDI) | /dev/ttyUSB0, slave 1, 4800 8N1, Modbus RTU |
| Soil Moisture | Capacitive probe | MCP3008 ADC (SPI) | SPI0 CE0, CH0, 3.3 V |
| Relay 1 (Fertilizer) | 5 V module, active-high | GPIO 17 | gpiozero.LED(17, active_high=True) |
| Relay 2 (Watering) | 5 V module, active-high | GPIO 27 | gpiozero.LED(27, active_high=True) |
| Relay 3 (Pest) | 5 V module, active-high | GPIO 22 | gpiozero.LED(22, active_high=True) |
| Camera | USB webcam | USB -> V4L2 | `camera.device_path: auto` (or `/dev/videoN`) |

## Quick Start (Development)

`ash
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
`

## Raspberry Pi 5 Deployment

### Option 1: Full automated setup (recommended)

On the Raspberry Pi (with the project copied/cloned to the Pi), run from inside the repo:

`ash
chmod +x scripts/setup_rpi5.sh scripts/setup_rpi5_deps.sh
./scripts/setup_rpi5.sh
`

This will:
- Install all system build dependencies (build-essential, cmake, pkg-config, swig, gfortran, python3-dev, libjpeg/libpng/libtiff, openblas/lapack, FFmpeg libs, libffi/libssl, spidev tools, i2c/spi utils)
- Create the eggiecare service user (with gpio/dialout/spi/i2c groups)
- Copy project to /home/veggiecare/veggiecare, create data/ and logs/
- Create venv, upgrade pip/wheel/setuptools, install all Python deps from equirements.txt
- Install and enable the eggiecare systemd service

### Option 2: System deps only

`ash
chmod +x scripts/setup_rpi5_deps.sh
./scripts/setup_rpi5_deps.sh
`

Then proceed with the venv + service steps below.

## Camera (USB Webcam)

A webcam is a **V4L2 video node** (`/dev/video0`, `/dev/video1`, ...). It is never
`/dev/ttyUSB0` — that is the NPK sensor's RS485/FTDI serial adapter.

### Finding the right node

The Pi's libcamera/pipewire stack registers many nodes. Most are *metadata-only*:
they open successfully, report a resolution, and then never deliver a single frame.
So "the node exists" and "the node works" are different questions.

`camera.device_path` accepts three values:

| Value | Behaviour |
|-------|-----------|
| `auto` | Probe every `/dev/video*` node and use the first that actually delivers frames (**recommended**) |
| `/dev/videoN` | Pin one specific node |
| `''` | Fall back to `device_index` |

To identify your webcam interactively, open **http://<pi>:5000/camera** and click
**Find my camera**. It probes each node and reports which ones genuinely deliver
frames, then tells you the value to pin.

From the shell:

`bash
v4l2-ctl --list-devices          # needs the v4l-utils package
lsusb | grep -iE 'camera|webcam'
`

### Permissions

The service user must be in the `video` group:

`bash
sudo usermod -aG video veggiecare
sudo systemctl restart veggiecare
`
(Log out and back in, or restart the service, for the group change to apply.)

### Live view

The dashboard exposes:

| Endpoint | Purpose |
|----------|---------|
| `/camera` | Live MJPEG view, snapshot, device list |
| `/api/camera/status` | Backend, node, resolution, fps, and the error when unavailable |
| `/api/camera/devices` | Enumerated V4L2 nodes with capture-capable flags |
| `/api/camera/probe` (POST) | Tests nodes and reports which deliver frames |
| `/api/camera/stream` | `multipart/x-mixed-replace` MJPEG stream |
| `/api/camera/snapshot` | Single JPEG frame |

A camera that is present but unusable reports the specific reason (missing node,
permission denied, driver refused to open, or opened-but-no-frames) instead of
the misleading "Camera not installed".

## Configuration