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
sudo mkdir -p "$VEGGIECARE_DIR/data" "$VEGGIECARE_DIR/logs"

echo "--- Setting ownership ---"
sudo chown -R $SERVICE_USER:$SERVICE_USER /home/veggiecare/veggiecare

echo "--- Setting up Python virtual environment ---"
sudo -u $SERVICE_USER bash -c "
  cd $VEGGIECARE_DIR
  python3 -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip wheel setuptools
  pip install -r requirements.txt
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
