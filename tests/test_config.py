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


# ----------------------------------------------------------------------
# save_user_config — dotted paths into list items
# ----------------------------------------------------------------------

def test_save_user_config_updates_list_item(tmp_path):
    import yaml
    from config import save_user_config

    path = tmp_path / "cfg.yaml"
    path.write_text(
        "relays:\n"
        "  items:\n"
        "    - id: 1\n"
        "      activation_duration_seconds: 120\n"
        "    - id: 2\n"
        "      activation_duration_seconds: 120\n",
        encoding="utf-8",
    )

    save_user_config(path, {"relays.items.1.activation_duration_seconds": 60})

    with open(path, "r", encoding="utf-8") as fh:
        saved = yaml.safe_load(fh)
    assert saved["relays"]["items"][1]["activation_duration_seconds"] == 60
    assert saved["relays"]["items"][0]["activation_duration_seconds"] == 120


# ----------------------------------------------------------------------
# Closed-loop watering policy keys
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Tank dosing invariants (volume-driven doses must be metrable)
# ----------------------------------------------------------------------

def _with_tanks(temp_config, tanks):
    import yaml

    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data["tanks"] = tanks
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)


def test_config_rejects_fertilizer_dose_below_minimum_meterable_volume(temp_config):
    """At 800 L/H a 1s pulse is ~222 mL, so 150 mL cannot be metered."""
    _with_tanks(temp_config, {"fertilizer": {
        "fert_ml_per_cycle": 150, "min_dose_seconds": 1.0, "pump_lph": 800,
    }})
    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "fert_ml_per_cycle" in str(exc.value)
    assert "minimum" in str(exc.value)


def test_config_rejects_pesticide_dose_below_minimum_meterable_volume(temp_config):
    _with_tanks(temp_config, {"pesticide": {
        "ml_per_activation": 100, "min_dose_seconds": 1.0, "pump_lph": 800,
    }})
    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "ml_per_activation" in str(exc.value)


def test_config_rejects_fertigation_window_shorter_than_min_pulse(temp_config):
    _with_tanks(temp_config, {"fertilizer": {
        "fert_ml_per_cycle": 250, "min_dose_seconds": 1.0,
        "min_watering_seconds_for_fert": 0.5,
    }})
    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "min_watering_seconds_for_fert" in str(exc.value)


def test_config_accepts_meterable_dose(temp_config):
    _with_tanks(temp_config, {
        "fertilizer": {
            "fert_ml_per_cycle": 250, "min_dose_seconds": 1.0, "pump_lph": 800,
            "min_watering_seconds_for_fert": 15,
        },
        "pesticide": {"ml_per_activation": 250, "min_dose_seconds": 1.0, "pump_lph": 800},
    })
    cfg = load_config(temp_config)
    assert cfg["tanks"]["fertilizer"]["fert_ml_per_cycle"] == 250
    assert cfg["tanks"]["pesticide"]["ml_per_activation"] == 250

def _set_soil_moisture_key(temp_config, key, value):
    import yaml
    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data["soil_moisture"][key] = value
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)


def test_soil_moisture_accepts_new_closed_loop_keys(temp_config):
    import yaml
    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data["soil_moisture"]["hysteresis"] = 5
    data["soil_moisture"]["response_rise_percent"] = 2.0
    data["soil_moisture"]["max_consecutive_non_responses"] = 3
    data["soil_moisture"]["max_watering_seconds_per_day"] = 1800
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)

    cfg = load_config(temp_config)
    assert cfg["soil_moisture"]["hysteresis"] == 5
    assert cfg["soil_moisture"]["response_rise_percent"] == 2.0
    assert cfg["soil_moisture"]["max_consecutive_non_responses"] == 3
    assert cfg["soil_moisture"]["max_watering_seconds_per_day"] == 1800


def test_soil_moisture_rejects_negative_hysteresis(temp_config):
    _set_soil_moisture_key(temp_config, "hysteresis", -1)
    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "hysteresis" in str(exc.value)


def test_soil_moisture_rejects_response_rise_percent_over_100(temp_config):
    _set_soil_moisture_key(temp_config, "response_rise_percent", 200)
    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "response_rise_percent" in str(exc.value)


def test_soil_moisture_rejects_zero_max_consecutive_non_responses(temp_config):
    _set_soil_moisture_key(temp_config, "max_consecutive_non_responses", 0)
    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "max_consecutive_non_responses" in str(exc.value)


def test_soil_moisture_rejects_zero_max_watering_seconds_per_day(temp_config):
    _set_soil_moisture_key(temp_config, "max_watering_seconds_per_day", 0)
    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "max_watering_seconds_per_day" in str(exc.value)


def test_soil_moisture_validates_without_new_closed_loop_keys(temp_config):
    import yaml
    with open(temp_config) as f:
        data = yaml.safe_load(f)
    for key in ("hysteresis", "response_rise_percent",
                "max_consecutive_non_responses", "max_watering_seconds_per_day"):
        data["soil_moisture"].pop(key, None)
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)
    load_config(temp_config)