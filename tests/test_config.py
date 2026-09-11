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