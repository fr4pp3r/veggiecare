"""Tests for config loading and validation."""

from __future__ import annotations

import pytest
from config import load_config, ConfigError


def test_load_default_config(temp_config):
    cfg = load_config(temp_config)
    assert cfg["system"]["name"] == "TestVeggieCare"
    assert cfg["system"]["simulate_hardware"] is True
    assert cfg["npk"]["thresholds"]["nitrogen"] == 20


def test_config_validation_rejects_invalid_relay_polarity(temp_config):
    import yaml

    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data["relays"]["active_high"] = "not-a-bool"
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "active_high" in str(exc.value)


def test_config_validation_rejects_duplicate_gpio_pins(temp_config):
    import yaml

    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data["relays"]["items"][1]["pin"] = 17  # same as relay 1
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "duplicate GPIO pin" in str(exc.value)


def test_config_validation_rejects_dry_adc_not_greater_than_wet(temp_config):
    import yaml

    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data["soil_moisture"]["dry_adc"] = 50
    data["soil_moisture"]["wet_adc"] = 500
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "dry_adc" in str(exc.value) and "must be greater" in str(exc.value)


def test_config_validation_rejects_invalid_pest_mode(temp_config):
    import yaml

    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data["pest_detection"]["mock"]["mode"] = "invalid_mode"
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "mode" in str(exc.value) and "none" in str(exc.value) and "sequential" in str(exc.value)


# ----------------------------------------------------------------------
# Camera device_path
# ----------------------------------------------------------------------

def _set_camera_device_path(temp_config, value):
    import yaml

    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data["camera"]["device_path"] = value
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)


@pytest.mark.parametrize("value", [
    "/dev/ttyUSB0",   # the NPK sensor's RS485 adapter
    "/dev/ttyACM0",
])
def test_camera_rejects_serial_ports(temp_config, value):
    _set_camera_device_path(temp_config, value)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    message = str(exc.value)
    assert "serial port" in message and "npk" in message.lower()


@pytest.mark.parametrize("value", [
    "video0",          # missing /dev prefix
    "/dev/null",
    "/dev/video",
    "0",               # a bare index is not a path
])
def test_camera_rejects_non_video_device_paths(temp_config, value):
    _set_camera_device_path(temp_config, value)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "device_path" in str(exc.value)


def test_camera_rejects_non_string_device_path(temp_config):
    _set_camera_device_path(temp_config, 0)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "must be a string" in str(exc.value)


@pytest.mark.parametrize("value", [
    "auto",
    "/dev/video0",
    "/dev/video31",
])
def test_camera_accepts_auto_and_explicit_video_nodes(temp_config, value):
    _set_camera_device_path(temp_config, value)

    assert load_config(temp_config)["camera"]["device_path"] == value


@pytest.mark.parametrize("value", ["", None])
def test_camera_allows_empty_path_to_fall_back_to_index(temp_config, value):
    _set_camera_device_path(temp_config, value)

    load_config(temp_config)


def test_camera_rejects_non_numeric_dimensions(temp_config):
    import yaml

    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data["camera"]["width"] = "wide"
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "width" in str(exc.value)