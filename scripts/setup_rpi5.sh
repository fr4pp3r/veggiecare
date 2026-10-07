#!/bin/bash
set -euo pipefail

echo "=== VeggieCare RPi5 Full Installation ==="

# --- Pre-flight Check ---
REQUIRED_GB=5
AVAILABLE_KB=$(df / --output=avail | tail -1)
AVAILABLE_GB=$((AVAILABLE_KB / 1024 / 1024))

if [ "$AVAILABLE_GB" -lt "$REQUIRED_GB" ]; then
  echo "ERROR: Insufficient disk space. Need at least ${REQUIRED_GB}GB, but only ${AVAILABLE_GB}GB available."
  exit 1
fi

if [ -f "scripts/setup_rpi5_deps.sh" ]; then
  bash scripts/setup_rpi5_deps.sh
fi

SERVICE_USER="veggiecare"
VEGGIECARE_DIR="/home/veggiecare/veggiecare"

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "--- Creating user $SERVICE_USER ---"
  sudo adduser --disabled-password --gecos "" $SERVICE_USER
fi

sudo usermod -a -G gpio,dialout,spi,i2c $SERVICE_USER 2>/dev/null || true

if [ "$(pwd)" = "$VEGGIECARE_DIR" ]; then
  echo "--- Local setup detected: Running in target directory. Skipping file movement. ---"
elif [ ! -d "$VEGGIECARE_DIR" ]; then
  echo "--- Copying project to $VEGGIECARE_DIR (excluding heavy data) ---"
  sudo mkdir -p /home/veggiecare
  # Use rsync to avoid duplicating large datasets/weights and existing venvs
  sudo rsync -av --exclude='runs' --exclude='Pest-Data' --exclude='.venv' --exclude='data' --exclude='logs' "$(pwd)/" "$VEGGIECARE_DIR/"
else
  echo "--- Project already exists at $VEGGIECARE_DIR. Ensuring content is synced... ---"
  sudo rsync -av --exclude='runs' --exclude='Pest-Data' --exclude='.venv' --exclude='data' --exclude='logs' "$(pwd)/" "$VEGGIECARE_DIR/"
fi

echo "--- Creating data/logs directories ---"
sudo mkdir -p "$VEGGIECARE_DIR/data" "$VEGGIECARE_DIR/logs" "$VEGGIECARE_DIR/tmp" "$VEGGIECARE_DIR/.pip-cache"

echo "--- Setting ownership ---"
sudo chown -R $SERVICE_USER:$SERVICE_USER /home/veggiecare/veggiecare

echo "--- Cleaning pip/cache to free space ---"
pip3 cache purge 2>/dev/null || true
sudo rm -rf /root/.cache/pip /tmp/pip-* /var/tmp/pip-* 2>/dev/null || true

echo "--- Setting up Python virtual environment ---"
sudo -u $SERVICE_USER bash -c '
  set -euo pipefail
  cd '"$VEGGIECARE_DIR"'
  export TMPDIR='"$VEGGIECARE_DIR"'/tmp
  export PIP_CACHE_DIR='"$VEGGIECARE_DIR"'/.pip-cache
  export PIP_NO_CACHE_DIR=1
  python3 -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip wheel setuptools --no-cache-dir --prefer-binary
  pip install -r requirements.txt --no-cache-dir --prefer-binary
  pip install --no-deps "ultralytics>=8.4.0" --no-cache-dir
'

echo "--- Installing systemd service ---"
sudo cp "$VEGGIECARE_DIR/deploy/veggiecare.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable veggiecare

echo ""
echo "=== Installation complete ==="
echo "Start:  sudo systemctl start veggiecare"
echo "Status: sudo systemctl status veggiecare"
echo "Logs:   sudo journalctl -u veggiecare -f"
echo "URL:    http://<pi-ip>:5000"
echo ""