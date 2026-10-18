"""Configuration loading and validation for VeggieCare.

The configuration is a YAML file (config/config.yaml by default). It is
loaded, deep-merged over the default file, strictly validated, and only
then applied by the application. Any invalid value rejects the whole
configuration so the system never runs with unsafe settings (for example
a relay that could switch on by accident).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_DETECTOR_MODES = ("none", "random", "sequential")
_DETECTORS = ("mock", "yolov11n")

# A real V4L2 node always carries an index; bare "/dev/video" cannot exist.
_VIDEO_NODE_RE = re.compile(r"^/dev/video\d+$")


class ConfigError(Exception):
    """Raised when the configuration is missing, malformed or invalid."""


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _type_names(expected) -> str:
    if isinstance(expected, tuple):
        return " or ".join(t.__name__ for t in expected)
    return expected.__name__


def _field(cfg: dict, key: str, expected, errors: list[str], path: str) -> Any:
    if key not in cfg:
        errors.append(f"{path}.{key}: missing")
        return None
    value = cfg[key]
    if not isinstance(value, expected):
        errors.append(
            f"{path}.{key}: must be {_type_names(expected)}, got {type(value).__name__}"
        )
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

def _validate_str_list(cfg: dict, key: str, errors: list[str], path: str) -> None:
    if key not in cfg:
        return
    value = cfg.get(key)
    if value is None:
        errors.append(f"{path}.{key}: must be a list")
        return
    if not isinstance(value, list):
        errors.append(f"{path}.{key}: must be a list, got {type(value).__name__}")
        return
    for idx, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{path}.{key}[{idx}]: must be a non-empty string")


def _validate_roi(section: dict, errors: list[str], path: str) -> None:
    """Validate ``pest_detection.roi``: 'full' | 'center' | 'grid' | [x, y, w, h]."""
    roi = section.get("roi")
    if isinstance(roi, str):
        if roi not in ("full", "center", "grid"):
            errors.append(
                f"{path}.roi must be 'full', 'center', 'grid' or [x, y, w, h], got {roi!r}"
            )
        return
    if isinstance(roi, list):
        if len(roi) != 4 or not all(_is_number(v) for v in roi):
            errors.append(f"{path}.roi: [x, y, w, h] must be four numbers")
            return
        x, y, w, h = (float(v) for v in roi)
        if not (0 < w <= 1) or not (0 < h <= 1):
            errors.append(f"{path}.roi: width and height must be in (0, 1], got {[w, h]}")
        elif not (0 <= x < 1) or not (0 <= y < 1):
            errors.append(f"{path}.roi: x and y must be in [0, 1), got {[x, y]}")
        elif x + w > 1 + 1e-9 or y + h > 1 + 1e-9:
            errors.append(
                f"{path}.roi: box exceeds the frame (x+w={x + w:g}, y+h={y + h:g} must be <= 1)"
            )
        return
    errors.append(
        f"{path}.roi must be 'full', 'center', 'grid' or [x, y, w, h], got {type(roi).__name__}"
    )


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
    # Closed-loop watering policy keys (all optional; defaults applied in code).
    if "hysteresis" in section:
        _number(section, "hysteresis", errors, path, minimum=0)
    if "response_rise_percent" in section:
        _number(section, "response_rise_percent", errors, path, minimum=0, maximum=100)
    if "max_consecutive_non_responses" in section:
        _int(section, "max_consecutive_non_responses", errors, path, minimum=1)
    if "max_watering_seconds_per_day" in section:
        _number(section, "max_watering_seconds_per_day", errors, path, minimum=1)
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
    _in(section, "detector", _DETECTORS, errors, path)
    _int(section, "relay_id", errors, path, minimum=1)
    relay_id = section.get("relay_id")
    if isinstance(relay_id, int) and not isinstance(relay_id, bool):
        if relay_id not in relay_ids:
            errors.append(f"{path}.relay_id: relay {relay_id} not defined in relays.items")
    _number(section, "confidence_threshold", errors, path, minimum=0, maximum=1)
    # Confidence band + region scanning (optional; absent in older configs).
    if "low_confidence_threshold" in section:
        low = section.get("low_confidence_threshold")
        if not _is_number(low) or not (0 < float(low) < 1):
            errors.append(f"{path}.low_confidence_threshold must be a number in (0, 1), got {low!r}")
        else:
            high = section.get("confidence_threshold")
            if _is_number(high) and float(low) >= float(high):
                errors.append(
                    f"{path}.low_confidence_threshold ({low:g}) must be less than "
                    f"confidence_threshold ({high:g})"
                )
    if "manual_scan_frames" in section:
        _int(section, "manual_scan_frames", errors, path, minimum=1, maximum=20)
    if "manual_scan_delay_seconds" in section:
        _number(section, "manual_scan_delay_seconds", errors, path, minimum=0, maximum=10)
    if "roi" in section and section["roi"] is not None:
        _validate_roi(section, errors, path)
    if "roi_grid" in section:
        _int(section, "roi_grid", errors, path, minimum=2, maximum=4)
    _number(section, "capture_interval_seconds", errors, path, minimum=1)
    # Automated-sweep analysis cadence (optional; absent in older configs).
    if "analysis_interval_seconds" in section:
        _number(section, "analysis_interval_seconds", errors, path, minimum=0.1)
    _int(section, "max_activations_per_month", errors, path, minimum=0)
    # pest_classes is silently ignored for backward compatibility
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

    # IPM treatment suggestions knowledge base (optional).
    if "suggestions" in section:
        suggestions = section.get("suggestions")
        if suggestions is None:
            pass
        elif not isinstance(suggestions, dict):
            errors.append(f"{path}.suggestions: must be a mapping")
        else:
            s_path = f"{path}.suggestions"
            if "enabled" in suggestions:
                _bool(suggestions, "enabled", errors, s_path)
            if "disclaimer" in suggestions:
                _str(suggestions, "disclaimer", errors, s_path)
            if "fallback" in suggestions:
                fallback = suggestions.get("fallback")
                if fallback is None:
                    pass
                elif not isinstance(fallback, dict):
                    errors.append(f"{s_path}.fallback: must be a mapping")
                else:
                    fb_path = f"{s_path}.fallback"
                    for key in ("title", "summary"):
                        if key in fallback:
                            _str(fallback, key, errors, fb_path)
                    for key in ("cultural", "organic", "chemical", "notes"):
                        if key in fallback:
                            _validate_str_list(fallback, key, errors, fb_path)

    if "treatments" in section:
        treatments = section.get("treatments")
        if treatments is None:
            pass
        elif not isinstance(treatments, list):
            errors.append(f"{path}.treatments: must be a list")
        else:
            seen_ids = set()
            seen_aliases = set()
            for idx, entry in enumerate(treatments):
                epath = f"{path}.treatments[{idx}]"
                if not isinstance(entry, dict):
                    errors.append(f"{epath}: must be a mapping")
                    continue
                _str(entry, "id", errors, epath)
                _str(entry, "title", errors, epath)
                _str(entry, "summary", errors, epath)
                _in(entry, "severity", ("low", "moderate", "high"), errors, epath)

                tid = entry.get("id")
                if isinstance(tid, str) and tid.strip():
                    if tid in seen_ids:
                        errors.append(f"{epath}.id: duplicate treatment id {tid!r}")
                    seen_ids.add(tid)

                if "names" not in entry:
                    errors.append(f"{epath}.names: missing")
                else:
                    names = entry.get("names")
                    if not isinstance(names, list) or not names:
                        errors.append(f"{epath}.names: must be a non-empty list")
                    else:
                        all_str = True
                        for i, n in enumerate(names):
                            if not isinstance(n, str) or not n.strip():
                                errors.append(
                                    f"{epath}.names[{i}]: must be a non-empty string"
                                )
                                all_str = False
                        if all_str:
                            for n in names:
                                alias = n.strip().lower()
                                if alias in seen_aliases:
                                    errors.append(
                                        f"{epath}.names: duplicate alias {n!r}"
                                    )
                                seen_aliases.add(alias)

                for key in ("cultural", "organic", "chemical", "notes"):
                    if key in entry:
                        _validate_str_list(entry, key, errors, epath)


def _validate_stepper(section: dict, errors: list[str]) -> None:
    path = "stepper"
    if "enabled" in section:
        _bool(section, "enabled", errors, path)
    if "pins" in section:
        _field(section, "pins", list, errors, path)
    if "delay" in section:
        _number(section, "delay", errors, path, minimum=0.0001, maximum=0.1)
    # Camera-sweep geometry (optional; only checked when present).
    if "wheel_diameter_in" in section:
        _number(section, "wheel_diameter_in", errors, path, minimum=0.1)
    if "extension_distance_in" in section:
        _number(section, "extension_distance_in", errors, path, minimum=0.1)
    if "steps_per_rev" in section:
        _int(section, "steps_per_rev", errors, path, minimum=1)


def _validate_camera(section: dict, errors: list[str]) -> None:
    path = "camera"
    _bool(section, "enabled", errors, path)
    _number(section, "capture_interval_seconds", errors, path, minimum=1)
    _str(section, "image_dir", errors, path)

    # Reject serial ports explicitly: /dev/ttyUSB* is the NPK RS485 adapter
    # and operators have tried to point the camera at it.
    if "device_path" in section and section["device_path"] is not None:
        device_path = section["device_path"]
        if not isinstance(device_path, str):
            errors.append(f"{path}.device_path must be a string")
        elif device_path.strip():
            value = device_path.strip()
            if value.lower() == "auto":
                pass
            elif value.startswith("/dev/tty"):
                errors.append(
                    f"{path}.device_path '{value}' is a serial port. A USB webcam is "
                    f"a V4L2 device like /dev/video0 — /dev/ttyUSB* is the NPK "
                    f"sensor's RS485 adapter."
                )
            elif not _VIDEO_NODE_RE.match(value):
                errors.append(
                    f"{path}.device_path must be 'auto' or a V4L2 camera node such as "
                    f"/dev/video0 (got '{value}')"
                )

    _number(section, "width", errors, path, minimum=1)
    _number(section, "height", errors, path, minimum=1)
    _number(section, "fps", errors, path, minimum=1)


def _validate_tanks(section: dict, errors: list[str]) -> None:
    """Validate the optional ``tanks`` section (volume-based dosing)."""
    path = "tanks"
    for tank_name in ("fertilizer", "pesticide"):
        if tank_name not in section:
            continue
        tank = section[tank_name]
        if not isinstance(tank, dict):
            errors.append(f"{path}.{tank_name}: must be a mapping")
            continue
        tpath = f"{path}.{tank_name}"
        for key in ("enabled", "auto_enabled", "require_refill_confirmation"):
            if key in tank:
                _bool(tank, key, errors, tpath)
        for key in ("fertilizer_type", "pesticide_type"):
            if key in tank:
                _field(tank, key, str, errors, tpath)
        for key in ("npk_n", "npk_p", "npk_k", "total_ml", "tank_capacity_ml",
                    "ml_dispensed_since_refill", "ml_remaining_est",
                    "fert_ml_per_cycle", "min_ml_for_cycle", "ml_per_activation",
                    "min_dose_seconds", "soak_days", "min_watering_seconds_for_fert"):
            if key in tank:
                _number(tank, key, errors, tpath, minimum=0)
        if "pump_lph" in tank:
            _number(tank, "pump_lph", errors, tpath, minimum=1)
        if "max_activations_per_month" in tank:
            _int(tank, "max_activations_per_month", errors, tpath, minimum=0)
        if "mode" in tank:
            _in(tank, "mode", ("with_watering", "separate"), errors, tpath)

        # Invariant: a volume-driven dose must be metrable. The pump cannot
        # reliably deliver less than one minimum pulse, so the configured target
        # must be at least that volume — otherwise a refill would be rejected,
        # or the runtime would be forced to command a sub-floor pulse.
        pump_lph = tank.get("pump_lph", 800)
        min_dose = tank.get("min_dose_seconds", 0)
        target_key = "fert_ml_per_cycle" if tank_name == "fertilizer" else "ml_per_activation"
        target = tank.get(target_key)
        if (_is_number(pump_lph) and pump_lph > 0
                and _is_number(min_dose) and min_dose > 0
                and _is_number(target) and target > 0):
            min_ml = pump_lph * 1000.0 * min_dose / 3600.0
            if target < min_ml - 1e-9:
                errors.append(
                    f"{tpath}.{target_key}: {target:g} mL is below the minimum "
                    f"meterable dose {min_ml:.0f} mL ({min_dose:g}s at {pump_lph:g} L/H); "
                    f"increase {target_key} or lower min_dose_seconds"
                )

        # A fertigation window shorter than the minimum pulse can never
        # dispense a valid dose.
        if tank_name == "fertilizer":
            window = tank.get("min_watering_seconds_for_fert")
            if (_is_number(window) and _is_number(min_dose) and window < min_dose):
                errors.append(
                    f"{tpath}.min_watering_seconds_for_fert: {window:g}s is shorter "
                    f"than min_dose_seconds ({min_dose:g}s); fertigation could never run"
                )


def _validate_npk_recommendations(section: dict, errors: list[str]) -> None:
    path = "npk_recommendations"
    _bool(section, "enabled", errors, path)
    _str(section, "crop_type", errors, path)
    _in(section, "growth_stage", ("seedling", "vegetative", "flowering", "fruiting"), errors, path)

    targets = _field(section, "targets", dict, errors, path)
    if targets is not None:
        for crop, stages in targets.items():
            if not isinstance(stages, dict):
                errors.append(f"{path}.targets.{crop}: must be a mapping")
                continue
            for stage, nutrients in stages.items():
                if not isinstance(nutrients, dict):
                    continue
                for n in ("nitrogen", "phosphorus", "potassium"):
                    val = nutrients.get(n)
                    if val is not None:
                        if not (isinstance(val, (list, tuple)) and len(val) == 2):
                            errors.append(f"{path}.targets.{crop}.{stage}.{n}: must be [min, max]")
                        else:
                            if not all(_is_number(v) and v >= 0 for v in val):
                                errors.append(f"{path}.targets.{crop}.{stage}.{n}: values must be non-negative numbers")
                            elif val[0] > val[1]:
                                errors.append(f"{path}.targets.{crop}.{stage}.{n}: min must be <= max")

    fertilizers = _field(section, "fertilizers", list, errors, path)
    if fertilizers is not None:
        for idx, fert in enumerate(fertilizers):
            fpath = f"{path}.fertilizers[{idx}]"
            if not isinstance(fert, dict):
                errors.append(f"{fpath}: must be a mapping")
                continue
            _str(fert, "id", errors, fpath)
            _str(fert, "name", errors, fpath)
            npk = fert.get("n_p_k")
            if not (isinstance(npk, (list, tuple)) and len(npk) == 3 and all(_is_number(v) for v in npk)):
                errors.append(f"{fpath}.n_p_k: must be [N, P, K] numbers")
            _number(fert, "dosage_g_per_10L", errors, fpath, minimum=0.1)
            suitable = _field(fert, "suitable_for", list, errors, fpath)
            if suitable is not None:
                for stage in suitable:
                    if stage not in ("seedling", "vegetative", "flowering", "fruiting"):
                        errors.append(f"{fpath}.suitable_for: invalid stage '{stage}'")

    mock = _field(section, "mock", dict, errors, path)
    if mock is not None:
        _in(mock, "mode", _DETECTOR_MODES, errors, f"{path}.mock")
        sequence = _field(mock, "sequence", list, errors, f"{path}.mock")
        if sequence is not None:
            for idx, entry in enumerate(sequence):
                entry_path = f"{path}.mock.sequence[{idx}]"
                if not (isinstance(entry, (list, tuple)) and len(entry) == 4):
                    errors.append(f"{entry_path}: must be [recommended, fertilizer_id, dosage, priority]")
                    continue
                recommended, fert_id, dosage, priority = entry
                if not isinstance(recommended, bool):
                    errors.append(f"{entry_path}[0]: recommended must be a boolean")
                if recommended and (not isinstance(fert_id, str) or not fert_id.strip()):
                    errors.append(f"{entry_path}[1]: fertilizer_id must be a non-empty string when recommended=true")
                if recommended and dosage is not None and not (_is_number(dosage) and dosage > 0):
                    errors.append(f"{entry_path}[2]: dosage must be a positive number when recommended=true")
                if recommended and priority not in ("high", "medium", "low", None):
                    errors.append(f"{entry_path}[3]: priority must be 'high', 'medium', or 'low' when recommended=true")
        _number(mock, "recommend_probability", errors, f"{path}.mock", minimum=0, maximum=1)


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

    stepper_cfg = cfg.get("stepper")
    if isinstance(stepper_cfg, dict):
        _validate_stepper(stepper_cfg, errors)

    tanks_cfg = cfg.get("tanks")
    if isinstance(tanks_cfg, dict):
        _validate_tanks(tanks_cfg, errors)

    npk_rec_cfg = _field(cfg, "npk_recommendations", dict, errors, "config")
    if npk_rec_cfg is not None:
        _validate_npk_recommendations(npk_rec_cfg, errors)

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


def config_file_path(path: str | Path | None = None) -> Path:
    """Resolve the effective configuration file path.

    Resolution order: ``path`` argument, then the VEGGIECARE_CONFIG
    environment variable, then the default config/config.yaml.
    """
    if path is not None:
        return Path(path)
    return Path(os.environ.get("VEGGIECARE_CONFIG", DEFAULT_CONFIG_PATH))


def save_user_config(config_path: str | Path, updates: dict[str, Any]) -> None:
    """Apply dotted-key ``updates`` to the YAML file at ``config_path``.

    The file is round-tripped with ruamel.yaml so comments and formatting
    of untouched keys are preserved, and written atomically (temp file +
    ``os.replace``). Validation is the caller's responsibility — this
    function only persists values. Field values are stored as-is; callers
    convert UI units to stored units before passing them here.
    """
    try:
        from ruamel.yaml import YAML
    except ImportError as exc:
        raise ConfigError(
            "Saving settings requires ruamel.yaml."
            " Run: .venv/bin/pip install -r requirements.txt",
        ) from exc

    path = Path(config_path)
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096

    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.load(fh)
        if data is None:
            data = {}
    else:
        data = default_config()

    for dotted, value in updates.items():
        target = data
        parts = dotted.split(".")
        for part in parts[:-1]:
            key = int(part) if part.lstrip("-").isdigit() else part
            if isinstance(target, list):
                if not isinstance(key, int) or not (0 <= key < len(target)):
                    raise ConfigError(
                        f"Cannot save {dotted!r}: invalid list index {part!r}",
                    )
                target = target[key]
            else:
                if not isinstance(target.get(key), (dict, list)):
                    target[key] = {}
                target = target[key]
        last = parts[-1]
        target[int(last) if last.lstrip("-").isdigit() else last] = value

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
    os.replace(tmp_path, path)


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load, merge, validate and expand a configuration file.

    Resolution order: ``path`` argument, then the VEGGIECARE_CONFIG
    environment variable, then the default config/config.yaml.
    """
    cfg = default_config()
    configured_path = config_file_path(path)
    if configured_path.exists():
        with open(configured_path, "r", encoding="utf-8") as fh:
            user_cfg = yaml.safe_load(fh) or {}
        if not isinstance(user_cfg, dict):
            raise ConfigError(f"{configured_path} does not contain a YAML mapping")
        cfg = deep_merge(cfg, user_cfg)
    validate(cfg)
    expand_paths(cfg)
    return cfg