"""User-facing settings schema for the dashboard Settings page.

Every setting the Settings tab exposes is declared here once, with the
plain-language label/description a non-technical user sees, the control
type used to edit it, and the validation rules. The dashboard API reads
current values from the live config through :func:`get_config_payload`
and converts submitted UI values to stored config values through
:func:`normalize_settings`.

Slider fields distinguish UI units (what the user sees: minutes, %) from
stored units (what the YAML keeps: seconds, 0..1 fractions). The frontend
only ever sees UI units; ``factor`` converts UI -> stored
(``stored = ui * factor``).
"""

from __future__ import annotations

from typing import Any

SETTINGS_GROUPS: list[dict[str, Any]] = [
    {
        "id": "watering",
        "title": "Watering",
        "description": "Controls when your plants get watered.",
        "fields": [
            {
                "key": "soil_moisture.enabled",
                "label": "Automatic watering",
                "description": "When on, the system waters the plants when the soil gets too dry.",
                "type": "toggle",
            },
            {
                "key": "soil_moisture.threshold",
                "label": "Water when soil moisture drops below",
                "description": "The system waters when soil moisture falls below this level. A higher number waters sooner and keeps the soil more moist.",
                "type": "slider",
                "min": 0, "max": 100, "step": 1, "unit": "%", "factor": 1, "integer": True,
            },
            {
                "key": "soil_moisture.watering_cooldown_seconds",
                "label": "Minimum wait between waterings",
                "description": "After watering, the system waits this long before it may water again. Prevents over-watering.",
                "type": "slider",
                "min": 5, "max": 240, "step": 5, "unit": "min", "factor": 60, "integer": True,
            },
        ],
    },
    {
        "id": "fertilizer",
        "title": "Fertilizer (soil nutrients)",
        "description": "Nutrient levels that cause an alert when the soil drops below them. The system alerts but does not fertilize automatically.",
        "fields": [
            {
                "key": "npk.enabled",
                "label": "Nutrient sensor",
                "description": "When on, the system reads the soil nutrient sensor.",
                "type": "toggle",
            },
            {
                "key": "npk.thresholds.nitrogen",
                "label": "Nitrogen alert level",
                "description": "You get an alert when nitrogen falls below this level.",
                "type": "slider",
                "min": 0, "max": 200, "step": 1, "unit": "mg/kg", "factor": 1, "integer": True,
            },
            {
                "key": "npk.thresholds.phosphorus",
                "label": "Phosphorus alert level",
                "description": "You get an alert when phosphorus falls below this level.",
                "type": "slider",
                "min": 0, "max": 200, "step": 1, "unit": "mg/kg", "factor": 1, "integer": True,
            },
            {
                "key": "npk.thresholds.potassium",
                "label": "Potassium alert level",
                "description": "You get an alert when potassium falls below this level.",
                "type": "slider",
                "min": 0, "max": 200, "step": 1, "unit": "mg/kg", "factor": 1, "integer": True,
            },
        ],
    },
    {
        "id": "pest",
        "title": "Pest control",
        "description": "Automatic pest detection and response. Requires the camera to be installed.",
        "fields": [
            {
                "key": "pest_detection.enabled",
                "label": "Pest control",
                "description": "When on, the system watches for pests and can spray automatically.",
                "type": "toggle",
            },
            {
                "key": "pest_detection.confidence_threshold",
                "label": "Confidence required to respond",
                "description": "How sure the system must be before it reacts to a pest. Higher = fewer reactions, but weaker detections are ignored.",
                "type": "slider",
                "min": 50, "max": 100, "step": 5, "unit": "%", "factor": 0.01, "integer": False,
            },
            {
                "key": "pest_detection.max_activations_per_month",
                "label": "Pest responses per month",
                "description": "How many times per month the system may spray for pests. Further detections only send an alert.",
                "type": "slider",
                "min": 0, "max": 30, "step": 1, "unit": "times", "factor": 1, "integer": True,
            },
        ],
    },
    {
        "id": "safety",
        "title": "Safety",
        "description": "Protection so pumps are never left running by accident.",
        "fields": [
            {
                "key": "relays.auto_off_watchdog_seconds",
                "label": "Automatic pump shut-off",
                "description": "If any pump stays on longer than this, the system switches it off automatically. Protects the pumps even if something goes wrong.",
                "type": "slider",
                "min": 30, "max": 600, "step": 30, "unit": "sec", "factor": 1, "integer": True,
            },
        ],
    },
    {
        "id": "system",
        "title": "System",
        "description": "General information and preferences.",
        "fields": [
            {
                "key": "system.name",
                "label": "System name",
                "description": "The name shown at the top of the dashboard.",
                "type": "text",
            },
            {
                "key": "system.timezone",
                "label": "Time zone",
                "description": "The time zone used for timestamps.",
                "type": "select",
                "options": [
                    "Asia/Manila", "UTC", "America/New_York", "America/Los_Angeles",
                    "Europe/London", "Europe/Berlin", "Asia/Singapore", "Asia/Tokyo",
                    "Australia/Sydney",
                ],
            },
        ],
    },
]


def _field_by_key(key: str) -> dict[str, Any] | None:
    for group in SETTINGS_GROUPS:
        for field in group["fields"]:
            if field["key"] == key:
                return field
    return None


def read_path(cfg: dict, dotted: str) -> Any:
    """Read a dotted config path (e.g. ``npk.thresholds.nitrogen``)."""
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_path(cfg: dict, dotted: str, value: Any) -> None:
    """Set a dotted config path, creating missing intermediate dicts."""
    node = cfg
    parts = dotted.split(".")
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def get_config_payload(cfg: dict) -> dict[str, Any]:
    """Build the ``groups`` payload of GET /api/config from a config dict."""
    groups: list[dict[str, Any]] = []
    for group in SETTINGS_GROUPS:
        out: dict[str, Any] = {
            "id": group["id"],
            "title": group["title"],
            "description": group["description"],
            "fields": [],
        }
        for field in group["fields"]:
            entry = dict(field)
            entry["value"] = read_path(cfg, field["key"])
            out["fields"].append(entry)
        groups.append(out)
    return {"groups": groups}


def normalize_settings(
    settings: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Convert submitted UI-unit values into stored config values.

    Returns ``(dotted_key -> stored value, changed field labels, error
    strings)``. Errors are plain-language so the frontend can show them
    directly to non-technical users.
    """
    normalized: dict[str, Any] = {}
    labels: list[str] = []
    errors: list[str] = []

    items: dict[str, Any] = settings or {}
    if not items:
        return normalized, labels, ["No settings provided."]

    for key, value in items.items():
        field = _field_by_key(key)
        if field is None:
            errors.append(f"{key}: unknown setting")
            continue
        label = field["label"]
        ftype = field["type"]

        if ftype == "toggle":
            if not isinstance(value, bool):
                errors.append(f"{label}: must be on or off")
                continue
            stored: Any = value
        elif ftype == "text":
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label}: must not be empty")
                continue
            stored = value.strip()
        elif ftype == "select":
            options = field.get("options", [])
            if value not in options:
                errors.append(f"{label}: choose one of the listed options")
                continue
            stored = value
        elif ftype == "slider":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"{label}: must be a number")
                continue
            fmin = float(field.get("min", 0))
            fmax = float(field.get("max", 100))
            if value < fmin or value > fmax:
                errors.append(
                    f"{label}: must be between {fmin:g} and {fmax:g} {field.get('unit', '')}".rstrip(),
                )
                continue
            raw = float(value) * float(field.get("factor", 1))
            stored = int(round(raw)) if field.get("integer", True) else float(round(raw, 4))
        else:
            errors.append(f"{label}: unsupported setting type")
            continue

        normalized[key] = stored
        labels.append(label)

    return normalized, labels, errors