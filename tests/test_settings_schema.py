"""Tests for the dashboard settings-schema dotted-path helpers."""

from __future__ import annotations

from dashboard.settings_schema import normalize_settings, read_path, set_path


def _cfg() -> dict:
    return {
        "relays": {
            "items": [
                {"id": 1, "activation_duration_seconds": 120},
                {"id": 2, "activation_duration_seconds": 120},
            ],
        },
    }


def test_read_path_list_index():
    cfg = _cfg()
    assert read_path(cfg, "relays.items.1.activation_duration_seconds") == 120
    assert read_path(cfg, "relays.items.5.activation_duration_seconds") is None
    assert read_path(cfg, "relays.items.1.nope") is None


def test_set_path_list_index_round_trips():
    cfg = _cfg()
    set_path(cfg, "relays.items.1.activation_duration_seconds", 60)
    assert cfg["relays"]["items"][1]["activation_duration_seconds"] == 60
    assert read_path(cfg, "relays.items.1.activation_duration_seconds") == 60


def test_normalize_settings_accepts_relay_duration():
    normalized, labels, errors = normalize_settings(
        {"relays.items.1.activation_duration_seconds": 60},
    )
    assert errors == []
    assert normalized["relays.items.1.activation_duration_seconds"] == 60
    assert labels == ["Watering pump (Relay 2)"]


def test_normalize_settings_rejects_out_of_range_relay_duration():
    _, _, errors = normalize_settings(
        {"relays.items.1.activation_duration_seconds": 5000},
    )
    assert errors
