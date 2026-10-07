#!/bin/bash
set -euo pipefail

echo "=== VeggieCare RPi5 System Dependencies Setup ==="

sudo apt-get update -y
# No blanket apt-get upgrade (project constraint: avoid pulling in unrelated packages)

echo "--- Installing build essentials and compilers ---"
sudo apt-get install -y --no-install-recommends \
  build-essential \
  cmake \
  pkg-config \
  ninja-build \
  git \
  swig \
  gfortran

echo "--- Installing Python development headers ---"
sudo apt-get install -y --no-install-recommends \
  python3-dev \
  python3-pip \
  python3-venv \
  python3-wheel \
  python3-setuptools

echo "--- Installing image/video/math libraries ---"
sudo apt-get install -y --no-install-recommends \
  libjpeg-dev \
  libpng-dev \
  libtiff-dev \
  libwebp-dev \
  zlib1g-dev \
  libopenblas-dev \
  liblapack-dev

echo "--- Installing FFmpeg/video libraries ---"
sudo apt-get install -y --no-install-recommends \
  libavcodec-dev \
  libavformat-dev \
  libswscale-dev \
  libv4l-dev

echo "--- Installing crypto/ffi/hdf5 libraries ---"
sudo apt-get install -y --no-install-recommends \
  libffi-dev \
  libssl-dev \
  libhdf5-dev \
  libhdf5-serial-dev \
  liblgpio-dev

echo "--- Installing hardware interface tools ---"
sudo apt-get install -y --no-install-recommends \
  i2c-tools \
  spi-tools \
  python3-spidev \
  usbutils \
  udev

sudo apt-get clean
sudo apt-get autoremove -y
sudo rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_spi 0 2>/dev/null || true
  sudo raspi-config nonint do_i2c 0 2>/dev/null || true
  sudo raspi-config nonint do_serial 2 2>/dev/null || true
fi

CURRENT_USER="${USER:-$(whoami)}"
sudo usermod -a -G gpio,dialout,spi,i2c "$CURRENT_USER" 2>/dev/null || true

echo ""
echo "=== System dependencies installed successfully ==="
echo "Log out/in or reboot for group changes."
echo ""