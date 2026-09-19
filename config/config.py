"""Configuration loading and validation for VeggieCare.

The configuration is a YAML file (config/config.yaml by default). It is
loaded, deep-merged over the default file, strictly validated, and only
then applied by the application. Any invalid value rejects the whole
configuration so the system never runs with unsafe settings (for example
a relay that could switch on by accident).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_DETECTOR_MODES = ("none", "random", "sequential")


class ConfigError(Exception):
    """Raised when the configuration is missing, malformed or invalid."""


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _field(cfg: dict, key: str, expected: type, errors: list[str], path: str) -> Any:
    if key not in cfg:
        errors.append(f"{path}.{key}: missing")
        return None
    value = cfg[key]
    if not isinstance(value, expected):
        errors.append(f"{path}.{key}: must be {expected.__name__}, got {type(value).__name__}")
        return None
    return value


def _number(cfg: dict, key: str, errors: list[str], path: str,
            minimum: float | None = None, maximum: float | None = None) -> None:
    value = _field(cfg, key, (int, float), errors, path)
    if value is None:
        return
    if minimum is not None and value < minimum:
        errors.append(f"{path}.{key}: must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        errors.append(f"{path}.{key}: must be <= {maximum}, got {value}")


def _int(cfg: dict, key: str, errors: list[str], path: str,
         minimum: int | None = None, maximum: int | None = None) -> None:
    value = _field(cfg, key, int, errors, path)
    if value is None:
        return
    if isinstance(value, bool):
        errors.append(f"{path}.{key}: must be an integer, got bool")
        return
    if minimum is not None and value < minimum:
        errors.append(f"{path}.{key}: must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        errors.append(f"{path}.{key}: must be <= {maximum}, got {value}")


def _bool(cfg: dict, key: str, errors: list[str], path: str) -> None:
    _field(cfg, key, bool, errors, path)


def _str(cfg: dict, key: str, errors: list[str], path: str) -> None:
    value = _field(cfg, key, str, errors, path)
    if value is not None and not value.strip():
        errors.append(f"{path}.{key}: must not be empty")


def _in(cfg: dict, key: str, allowed: tuple, errors: list[str], path: str) -> None:
    value = _field(cfg, key, str, errors, path)
    if value is not None and value not in allowed:
        errors.append(f"{path}.{key}: must be one of {allowed}, got {value!r}")


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------

def _validate_relays(section: dict, errors: list[str]) -> list[dict]:
    path = "relays"
    _bool(section, "active_high", errors, path)
    _number(section, "auto_off_watchdog_seconds", errors, path, minimum=1)
    _number(section, "default_duration_seconds", errors, path, minimum=1)
    items = _field(section, "items", list, errors, path)
    relays: list[dict] = []
    if items is None:
        return relays
    seen_ids: set[int] = set()
    seen_pins: set[int] = set()
    for idx, item in enumerate(items):
        item_path = f"{path}.items[{idx}]"
        if not isinstance(item, dict):
            errors.append(f"{item_path}: must be a mapping")
            continue
        relay_id = item.get("id")
        _int(item, "id", errors, item_path, minimum=1)
        if isinstance(relay_id, int) and not isinstance(relay_id, bool):
            if relay_id in seen_ids:
                errors.append(f"{item_path}.id: duplicate relay id {relay_id}")
            seen_ids.add(relay_id)
        _str(item, "name", errors, item_path)
        _str(item, "label", errors, item_path)
        pin = item.get("pin")
        _int(item, "pin", errors, item_path, minimum=1, maximum=40)
        if isinstance(pin, int) and not isinstance(pin, bool):
            if pin in seen_pins:
                errors.append(f"{item_path}.pin: duplicate GPIO pin {pin}")
            seen_pins.add(pin)
        _number(item, "activation_duration_seconds", errors, item_path, minimum=1)
        relays.append(item)
    return relays


def _validate_soil_moisture(section: dict, errors: list[str], relay_ids: set[int]) -> None:
    path = "soil_moisture"
    _bool(section, "enabled", errors, path)
    _int(section, "spi_bus", errors, path, minimum=0)
    _int(section, "spi_device", errors, path, minimum=0)
    _int(section, "channel", errors, path, minimum=0, maximum=7)
    _int(section, "adc_max", errors, path, minimum=1)
    _number(section, "vref", errors, path, minimum=0.1)
    _int(section, "dry_adc", errors, path, minimum=0)
    _int(section, "wet_adc", errors, path, minimum=0)
    dry = section.get("dry_adc")
    wet = section.get("wet_adc")
    if isinstance(dry, int) and isinstance(wet, int) and not isinstance(dry, bool) and not isinstance(wet, bool):
        if dry <= wet:
            errors.append(f"{path}: dry_adc ({dry}) must be greater than wet_adc ({wet})")
    _int(section, "samples", errors, path, minimum=1)
    _number(section, "read_interval_seconds", errors, path, minimum=1)
    _number(section, "log_interval_seconds", errors, path, minimum=1)
    _number(section, "threshold", errors, path, minimum=0, maximum=100)
    _int(section, "relay_id", errors, path, minimum=1)
    relay_id = section.get("relay_id")
    if isinstance(relay_id, int) and not isinstance(relay_id, bool):
        if relay_id not in relay_ids:
            errors.append(f"{path}.relay_id: relay {relay_id} not defined in relays.items")
    _number(section, "watering_cooldown_seconds", errors, path, minimum=0)
    simulated = _field(section, "simulated", dict, errors, path)
    if simulated is not None:
        _number(simulated, "moisture", errors, f"{path}.simulated", minimum=0, maximum=100)


def _validate_npk(section: dict, errors: list[str]) -> None:
    path = "npk"
    _bool(section, "enabled", errors, path)
    _str(section, "port", errors, path)
    _int(section, "slave_id", errors, path, minimum=1, maximum=247)
    _int(section, "baudrate", errors, path, minimum=1)
    _number(section, "timeout", errors, path, minimum=0.1)
    _int(section, "register_start", errors, path, minimum=0)
    _int(section, "register_count", errors, path, minimum=1)
    _number(section, "scale", errors, path, minimum=0.000001)
    _number(section, "read_interval_seconds", errors, path, minimum=1)
    _number(section, "log_interval_seconds", errors, path, minimum=1)
    thresholds = _field(section, "thresholds", dict, errors, path)
    if thresholds is not None:
        for nutrient in ("nitrogen", "phosphorus", "potassium"):
            _number(thresholds, nutrient, errors, f"{path}.thresholds", minimum=0)
    simulated = _field(section, "simulated", dict, errors, path)
    if simulated is not None:
        for nutrient in ("nitrogen", "phosphorus", "potassium"):
            _number(simulated, nutrient, errors, f"{path}.simulated", minimum=0)


def _validate_pest(section: dict, errors: list[str], relay_ids: set[int]) -> None:
    path = "pest_detection"
    _bool(section, "enabled", errors, path)
    _in(section, "detector", ("mock",), errors, path)
    _int(section, "relay_id", errors, path, minimum=1)
    relay_id = section.get("relay_id")
    if isinstance(relay_id, int) and not isinstance(relay_id, bool):
        if relay_id not in relay_ids:
            errors.append(f"{path}.relay_id: relay {relay_id} not defined in relays.items")
    _number(section, "confidence_threshold", errors, path, minimum=0, maximum=1)
    _number(section, "capture_interval_seconds", errors, path, minimum=1)
    _int(section, "max_activations_per_month", errors, path, minimum=0)
    classes = _field(section, "pest_classes", list, errors, path)
    if classes is not None:
        for idx, cls in enumerate(classes):
            if not isinstance(cls, str) or not cls.strip():
                errors.append(f"{path}.pest_classes[{idx}]: must be a non-empty string")
    mock = _field(section, "mock", dict, errors, path)
    if mock is not None:
        _in(mock, "mode", _DETECTOR_MODES, errors, f"{path}.mock")
        sequence = _field(mock, "sequence", list, errors, f"{path}.mock")
        if sequence is not None:
            for idx, entry in enumerate(sequence):
                entry_path = f"{path}.mock.sequence[{idx}]"
                if not (isinstance(entry, (list, tuple)) and len(entry) == 3):
                    errors.append(f"{entry_path}: must be [class, confidence, detected]")
                    continue
                cls, conf, detected = entry
                if not isinstance(cls, str) or not cls.strip():
                    errors.append(f"{entry_path}[0]: pest class must be a non-empty string")
                if not _is_number(conf) or not (0 <= float(conf) <= 1):
                    errors.append(f"{entry_path}[1]: confidence must be 0..1")
                if not isinstance(detected, bool):
                    errors.append(f"{entry_path}[2]: detected must be a boolean")
        random_cfg = _field(mock, "random", dict, errors, f"{path}.mock")
        if random_cfg is not None:
            _number(random_cfg, "detection_probability", errors, f"{path}.mock.random", minimum=0, maximum=1)
            _number(random_cfg, "confidence_min", errors, f"{path}.mock.random", minimum=0, maximum=1)
            _number(random_cfg, "confidence_max", errors, f"{path}.mock.random", minimum=0, maximum=1)
            cmin = random_cfg.get("confidence_min")
            cmax = random_cfg.get("confidence_max")
            if _is_number(cmin) and _is_number(cmax) and float(cmin) > float(cmax):
                errors.append(f"{path}.mock.random: confidence_min must be <= confidence_max")


def _validate_camera(section: dict, errors: list[str]) -> None:
    path = "camera"
    _bool(section, "enabled", errors, path)
    _number(section, "capture_interval_seconds", errors, path, minimum=1)
    _str(section, "image_dir", errors, path)


def validate(cfg: dict) -> None:
    """Validate a configuration dict; raise ConfigError with all problems."""
    errors: list[str] = []

    system = _field(cfg, "system", dict, errors, "config")
    if system is not None:
        _str(system, "name", errors, "system")
        _bool(system, "simulate_hardware", errors, "system")
        _str(system, "timezone", errors, "system")

    database = _field(cfg, "database", dict, errors, "config")
    if database is not None:
        _str(database, "path", errors, "database")

    logging_cfg = _field(cfg, "logging", dict, errors, "config")
    if logging_cfg is not None:
        _in(logging_cfg, "level", _LEVELS, errors, "logging")
        _str(logging_cfg, "file", errors, "logging")

    relays: list[dict] = []
    relays_cfg = _field(cfg, "relays", dict, errors, "config")
    if relays_cfg is not None:
        relays = _validate_relays(relays_cfg, errors)
    relay_ids = {r["id"] for r in relays if isinstance(r.get("id"), int) and not isinstance(r.get("id"), bool)}

    npk_cfg = _field(cfg, "npk", dict, errors, "config")
    if npk_cfg is not None:
        _validate_npk(npk_cfg, errors)

    moisture_cfg = _field(cfg, "soil_moisture", dict, errors, "config")
    if moisture_cfg is not None:
        _validate_soil_moisture(moisture_cfg, errors, relay_ids)

    pest_cfg = _field(cfg, "pest_detection", dict, errors, "config")
    if pest_cfg is not None:
        _validate_pest(pest_cfg, errors, relay_ids)

    camera_cfg = _field(cfg, "camera", dict, errors, "config")
    if camera_cfg is not None:
        _validate_camera(camera_cfg, errors)

    if errors:
        raise ConfigError("Configuration is invalid:\n  - " + "\n  - ".join(errors))


# ----------------------------------------------------------------------
# Loading / merging / path expansion
# ----------------------------------------------------------------------

def default_config() -> dict[str, Any]:
    """Return the shipped default configuration."""
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError("Default configuration is not a YAML mapping")
    return data


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into ``base`` (dicts merge, others replace)."""
    result = dict(base)
    for key, value in (override or {}).items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def expand_paths(cfg: dict) -> dict:
    """Resolve relative paths in the config against the project root."""
    root = PROJECT_ROOT
    if isinstance(cfg.get("database"), dict):
        p = cfg["database"].get("path")
        if isinstance(p, str):
            cfg["database"]["path"] = str(root / p) if not Path(p).is_absolute() else p
    if isinstance(cfg.get("logging"), dict):
        p = cfg["logging"].get("file")
        if isinstance(p, str):
            cfg["logging"]["file"] = str(root / p) if not Path(p).is_absolute() else p
    if isinstance(cfg.get("camera"), dict):
        p = cfg["camera"].get("image_dir")
        if isinstance(p, str):
            cfg["camera"]["image_dir"] = str(root / p) if not Path(p).is_absolute() else p
    return cfg


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load, merge, validate and expand a configuration file.

    Resolution order: ``path`` argument, then the VEGGIECARE_CONFIG
    environment variable, then the default config/config.yaml.
    """
    cfg = default_config()
    configured_path = (
        Path(path)
        if path is not None
        else Path(os.environ.get("VEGGIECARE_CONFIG", DEFAULT_CONFIG_PATH))
    )
    if configured_path.exists():
        with open(configured_path, "r", encoding="utf-8") as fh:
            user_cfg = yaml.safe_load(fh) or {}
        if not isinstance(user_cfg, dict):
            raise ConfigError(f"{configured_path} does not contain a YAML mapping")
        cfg = deep_merge(cfg, user_cfg)
    validate(cfg)
    expand_paths(cfg)
    return cfg