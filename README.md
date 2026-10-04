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
| Camera | *not installed yet* | - | - |

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

## Configuration