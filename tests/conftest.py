"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture
def temp_config(tmp_path: Path) -> Path:
    """Create a minimal valid config file for testing."""
    import yaml

    cfg = {
        "system": {"name": "TestVeggieCare", "simulate_hardware": True, "timezone": "UTC"},
        "database": {"path": str(tmp_path / "test.db")},
        "logging": {"level": "DEBUG", "file": str(tmp_path / "test.log")},
        "npk": {
            "enabled": True,
            "port": "/dev/ttyUSB0",
            "slave_id": 1,
            "baudrate": 4800,
            "timeout": 1.0,
            "register_start": 0,
            "register_count": 3,
            "scale": 1.0,
            "read_interval_seconds": 1,
            "log_interval_seconds": 1,
            "thresholds": {"nitrogen": 20, "phosphorus": 20, "potassium": 20},
            "simulated": {"nitrogen": 25, "phosphorus": 15, "potassium": 30},
        },
        "soil_moisture": {
            "enabled": True,
            "spi_bus": 0,
            "spi_device": 0,
            "channel": 0,
            "adc_max": 1023,
            "vref": 3.3,
            "dry_adc": 500,
            "wet_adc": 50,
            "samples": 2,
            "read_interval_seconds": 1,
            "log_interval_seconds": 1,
            "threshold": 30,
            "relay_id": 2,
            "max_activations_per_month": 2,
            "watering_cooldown_seconds": 1,
            "simulated": {"moisture": 45},
        },
        "relays": {
            "active_high": True,
            "auto_off_watchdog_seconds": 10,
            "watchdog_check_seconds": 0.05,
            "default_duration_seconds": 5,
            "items": [
                {"id": 1, "name": "fertilizer", "label": "NPK/Fertilizer", "pin": 17, "activation_duration_seconds": 5},
                {"id": 2, "name": "watering", "label": "Soil Moisture/Watering", "pin": 27, "activation_duration_seconds": 5},
                {"id": 3, "name": "pest_response", "label": "Pest Response", "pin": 22, "activation_duration_seconds": 5},
            ],
        },
        "pest_detection": {
            "enabled": False,
            "relay_id": 3,
            "detector": "mock",
            "confidence_threshold": 0.70,
            "capture_interval_seconds": 300,
            "relay_activation_duration_seconds": 5,
            "pest_classes": ["aphid", "caterpillar", "fungus"],
            "mock": {"mode": "none", "sequence": [], "random": {"detection_probability": 0.25, "confidence_min": 0.50, "confidence_max": 0.98}},
        },
        "camera": {"enabled": False, "capture_interval_seconds": 300, "image_dir": str(tmp_path / "images")},
    }
    path = tmp_path / "test_config.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f)
    return path


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"