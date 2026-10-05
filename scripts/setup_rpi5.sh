#!/bin/bash
set -e

VEGGIECARE_DIR="/home/veggiecare/veggiecare"
SERVICE_USER="veggiecare"

echo "=== VeggieCare RPi5 Full Installation ==="

if [ -f "scripts/setup_rpi5_deps.sh" ]; then
  bash scripts/setup_rpi5_deps.sh
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "--- Creating user $SERVICE_USER ---"
  sudo adduser --disabled-password --gecos "" $SERVICE_USER
fi

sudo usermod -a -G gpio,dialout,spi,i2c $SERVICE_USER 2>/dev/null || true

if [ ! -d "$VEGGIECARE_DIR" ]; then
  echo "--- Copying project to $VEGGIECARE_DIR ---"
  sudo mkdir -p /home/veggiecare
  sudo cp -r "$(pwd)" "$VEGGIECARE_DIR"
fi

echo "--- Creating data/logs directories ---"
sudo mkdir -p "$VEGGIECARE_DIR/data" "$VEGGIECARE_DIR/logs" "$VEGGIECARE_DIR/tmp" "$VEGGIECARE_DIR/.pip-cache"

echo "--- Setting ownership ---"
sudo chown -R $SERVICE_USER:$SERVICE_USER /home/veggiecare/veggiecare

echo "--- Checking disk space ---"
df -h / /tmp /var/tmp /home 2>/dev/null || true

echo "--- Cleaning pip/cache to free space ---"
pip3 cache purge 2>/dev/null || true
sudo rm -rf /root/.cache/pip /tmp/pip-* /var/tmp/pip-* 2>/dev/null || true

echo "--- Increasing /tmp size for this session (if needed) ---"
sudo mount -o remount,size=3G /tmp 2>/dev/null || true
df -h /tmp 2>/dev/null || true

echo "--- Setting up Python virtual environment ---"
sudo -u $SERVICE_USER bash -c "
  set -e
  cd $VEGGIECARE_DIR
  export TMPDIR=$VEGGIECARE_DIR/tmp
  export PIP_CACHE_DIR=$VEGGIECARE_DIR/.pip-cache
  python3 -m venv .venv
source .venv/bin/activate
  pip install --upgrade pip wheel setuptools --no-cache-dir --prefer-binary
  pip install -r requirements.txt --no-cache-dir --prefer-binary
  # ultralytics is installed with --no-deps on purpose: its hard dependency on
  # the GUI opencv-python would overwrite the headless cv2/ and break the
  # camera import on a headless Pi (libGL.so.1). requirements.txt already lists
  # the rest of ultralytics' dependency tree explicitly.
  pip install --no-deps \"ultralytics>=8.4.0\" --no-cache-dir
"

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
