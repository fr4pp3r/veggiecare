# VeggieCare

Raspberry Pi 5 smart plant monitoring and control system.

## Hardware

| Component | Model | Interface | Pins / Config |
|-----------|-------|-----------|---------------|
| NPK Sensor | JXCT JXBS-3001 (7-in-1) | RS485 -> USB (FTDI) | /dev/ttyUSB0, slave 1, 4800 8-N-1, Modbus RTU |
| Soil Moisture | Capacitive probe | MCP3008 ADC (SPI) | SPI0 CE0, CH0, 3.3 V |
| Relay 1 (Fertilizer) | 5 V module, active-high | GPIO 17 | gpiozero.LED(17, active_high=True) |
| Relay 2 (Watering) | 5 V module, active-high | GPIO 27 | gpiozero.LED(27, active_high=True) |
| Relay 3 (Pest) | 5 V module, active-high | GPIO 22 | gpiozero.LED(22, active_high=True) |
| Camera | USB webcam | USB -> V4L2 | `camera.device_path: auto` (or `/dev/videoN`) |

## Quick Start (Development)

Desktop/laptop install. The Pi steps are in the next section.

```bash
# 1. Clone / copy project
cd veggiecare

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate

# 3. Install dependencies (includes CPU-only torch + YOLO support)
pip install -r requirements.txt
# ultralytics goes in separately with --no-deps: its GUI opencv-python
# dependency would break the headless camera build. See requirements.txt.
pip install --no-deps "ultralytics>=8.4.0"

# 4. Run in simulated mode (no hardware needed)
python app.py --simulate

# 5. Open dashboard
# http://localhost:5000
```

## Raspberry Pi 5 Setup

### Before you start

| Requirement | Notes |
|-------------|-------|
| Raspberry Pi 5 | 4 GB+ recommended |
| Raspberry Pi OS | Bookworm, 64-bit, freshly imaged |
| Storage | 32 GB+ SD card or USB SSD, **6 GB free minimum** |
| Power | Official 5 V/5 A USB-C supply |
| Network | SSH access to the Pi |

Two things to decide before imaging:

- **Name the OS user `veggiecare`.** `deploy/veggiecare.service` runs as `User=veggiecare`. Using the legacy `pi` user makes systemd fail with `status=217/USER`.
- **Budget the disk.** CPU-only torch plus YOLO and OpenCV needs roughly 6 GB free. The install also needs ~2x the largest wheel in temp space at once.

### 1. Prepare the OS

Enable the hardware interfaces (the automated script does this too):

```bash
sudo raspi-config nonint do_spi 0      # MCP3008 soil sensor
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_serial 2   # free the serial console
sudo raspi-config nonint expand_filesystem
sudo reboot
```

### 2. Copy the project

The repo tracks `runs/` (~97 MB of trained weights) and `Pest-Data/` (~70 MB of
training images). Copy the weights; skip `Pest-Data/` unless you plan to retrain.

```bash
# from your workstation
rsync -a --exclude .venv --exclude .venv-test --exclude .git \
      ./ veggiecare@<pi-ip>:/home/veggiecare/veggiecare/
```

If you `git clone` directly on the Pi instead, clone into `/home/veggiecare/veggiecare`
so the paths in `deploy/veggiecare.service` resolve.

### 3. Install dependencies

#### Automated (recommended)

```bash
cd /home/veggiecare/veggiecare
chmod +x scripts/setup_rpi5.sh scripts/setup_rpi5_deps.sh
./scripts/setup_rpi5.sh
```

The script:

- installs system build deps (build-essential, cmake, swig, gfortran, python3-dev, libjpeg/libpng/libtiff, openblas/lapack, FFmpeg and crypto/ffi/HDF5 headers, `libv4l-dev`, i2c/spi tools)
- enables SPI, I2C and frees the serial console
- creates the `veggiecare` user and adds it to `gpio`, `dialout`, `spi`, `i2c`
- creates `data/`, `logs/`, `tmp/`, `.pip-cache/` and sets ownership
- purges the pip cache, remounts `/tmp` at 3 GB, and points `TMPDIR` into the project
- creates `.venv`, installs `requirements.txt` then `ultralytics --no-deps`
- installs and enables the systemd unit

#### Manual

```bash
cd /home/veggiecare/veggiecare

# system packages + interfaces
sudo bash scripts/setup_rpi5_deps.sh

# service user
sudo adduser --disabled-password --gecos "" veggiecare
sudo usermod -a -G gpio,dialout,spi,i2c veggiecare

# runtime dirs — systemd fails with 226/NAMESPACE if these are missing
sudo mkdir -p data logs tmp
sudo chown -R veggiecare:veggiecare /home/veggiecare/veggiecare

# venv + Python deps (as the service user)
sudo -u veggiecare bash -c '
  cd /home/veggiecare/veggiecare
  export TMPDIR=/home/veggiecare/veggiecare/tmp
  python3 -m venv .venv
  . .venv/bin/activate
  pip install --upgrade pip wheel setuptools --prefer-binary
  pip install -r requirements.txt --prefer-binary
  pip install --no-deps "ultralytics>=8.4.0"
'

# systemd unit
sudo cp deploy/veggiecare.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable veggiecare
```

> The `ultralytics` line is separate on purpose. `ultralytics` hard-requires the GUI
> `opencv-python`, which installs into the same `cv2/` directory as
> `opencv-python-headless` and needs `libGL.so.1`, which a headless Pi lacks.
> `--no-deps` keeps the headless build as the only OpenCV. `requirements.txt`
> already lists ultralytics' other dependencies.

### 4. Grant camera access

```bash
sudo usermod -aG video veggiecare
sudo systemctl restart veggiecare
```

Neither `setup_rpi5.sh` nor the unit file adds the `video` group — the unit only
carries `gpio`, `dialout`, `spi`, `i2c`. Without this the webcam cannot be opened
and the dashboard reports it as missing.

### 5. Verify the install

```bash
cd /home/veggiecare/veggiecare
.venv/bin/python -c "import cv2, torch, ultralytics; print('torch', torch.__version__); print('cv2', cv2.__version__); print('ultralytics', ultralytics.__version__)"
```

`torch` should report a `+cpu` build. Then start the service:

```bash
sudo systemctl start veggiecare
sudo systemctl status veggiecare
sudo journalctl -u veggiecare -f
```

Dashboard: **http://<pi-ip>:5000**

### 6. Pest detection (YOLO)

Dependencies are already installed. To run real inference, set the detector in
`config/config.yaml`:

```yaml
pest_detection:
  detector: yolov11n     # `mock` fakes results and needs no model
```

Weights are resolved in this order, so nothing is downloaded at first run:

1. `runs/veggiecare_cls_v1/weights/best.pt` — your trained checkpoint (ships with the repo)
2. `model_path` from `config/config.yaml`, if set (relative paths resolve against the repo root)
3. `yolo11n-cls.pt` — stock weights in the repo root

This matters on the Pi: the unit sets `ProtectSystem=strict` and only `logs/` and
`data/` are writable, so if a weight file were missing ultralytics could not
download it into the install directory. Expect a few seconds per frame on CPU
inference — the detector runs on a captured still, not on a video stream.

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

```bash
v4l2-ctl --list-devices          # needs the v4l-utils package
lsusb | grep -iE 'camera|webcam'
```

### Permissions

The service user must be in the `video` group:

```bash
sudo usermod -aG video veggiecare
sudo systemctl restart veggiecare
```
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

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `OSError: [Errno 28] No space left on device` during install | Free space (`pip cache purge`, `rm -rf /tmp/pip-*`). Confirm `requirements.txt` still has the `--extra-index-url .../whl/cpu` line — without it pip pulls the ~454 MB CUDA wheel. |
| `ImportError: libGL.so.1: cannot open shared object file` | The GUI `opencv-python` got installed. Reinstall with `pip install --force-reinstall opencv-python-headless` and re-run the `--no-deps ultralytics` line. |
| `ModuleNotFoundError: ultralytics` | The `--no-deps` install step was skipped. Run it. |
| Camera reported as "Not installed" | `sudo usermod -aG video veggiecare`, then restart the service. |
| Detector always falls back to mock | `detector: yolov11n` not set, weights missing, or ultralytics failed to import. `journalctl -u veggiecare -f` logs the reason. |
| systemd `status=217/USER` | The OS user is not named `veggiecare`. |
| systemd `226/NAMESPACE` | `data/` or `logs/` missing — see `ReadWritePaths` in the unit file. |
| Relays all unavailable | `lgpio` is not installed. Without a GPIO backend every `GpioRelay` is marked unavailable and fails silently. |

## Configuration