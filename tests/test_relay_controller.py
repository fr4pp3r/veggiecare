"""Tests for relay controller watchdog and safety."""

from __future__ import annotations

import time
import pytest
from hardware.relay_controller import RelayController


def _cfg(*ids, auto_off=10, check_step=0.05):
    return {
        "active_high": True,
        "auto_off_watchdog_seconds": auto_off,
        "watchdog_check_seconds": check_step,
        "default_duration_seconds": 5,
        "items": [
            {"id": rid, "name": f"r{rid}", "label": f"R{rid}", "pin": 10 + rid, "activation_duration_seconds": 5}
            for rid in ids
        ],
    }


def test_relay_controller_starts_all_off():
    ctrl = RelayController(_cfg(1, 2, 3), simulate=True)
    try:
        for rid in (1, 2, 3):
            assert ctrl.is_active(rid) is False
            assert ctrl.available(rid) is True
            entry = ctrl.get(rid)
            assert entry["active"] is False
            assert entry["deadline"] is None
    finally:
        ctrl.shutdown()


def test_activate_sets_deadline_and_state():
    cfg = _cfg(1)
    ctrl = RelayController(cfg, simulate=True)
    try:
        ok = ctrl.activate(1, duration=3.0, trigger="test")
        assert ok is True
        assert ctrl.is_active(1) is True
        entry = ctrl.get(1)
        assert entry["deadline"] is not None
        assert entry["activated_at"] is not None
        assert entry["trigger"] == "test"
        assert entry["deadline"] - time.monotonic() <= 3.1
        assert entry["deadline"] - time.monotonic() >= 2.9
    finally:
        ctrl.shutdown()


def test_watchdog_auto_off_after_duration():
    ctrl = RelayController(_cfg(1, check_step=0.05), simulate=True)
    try:
        ctrl.activate(1, duration=0.2, trigger="test")
        assert ctrl.is_active(1) is True
        time.sleep(0.4)
        assert ctrl.is_active(1) is False
        entry = ctrl.get(1)
        assert entry["deadline"] is None
    finally:
        ctrl.shutdown()


def test_watchdog_caps_at_auto_off_watchdog():
    ctrl = RelayController(_cfg(1, auto_off=0.5, check_step=0.05), simulate=True)
    try:
        ctrl.activate(1, duration=5.0, trigger="test")
        assert ctrl.is_active(1) is True
        time.sleep(0.7)
        assert ctrl.is_active(1) is False
    finally:
        ctrl.shutdown()


def test_all_off_emergency():
    ctrl = RelayController(_cfg(1, 2), simulate=True)
    try:
        ctrl.activate(1, duration=10, trigger="test")
        ctrl.activate(2, duration=10, trigger="test")
        assert ctrl.is_active(1) and ctrl.is_active(2)
        ctrl.all_off(reason="test")
        assert not ctrl.is_active(1)
        assert not ctrl.is_active(2)
        for rid in (1, 2):
            entry = ctrl.get(rid)
            assert entry["deadline"] is None
            assert entry["activated_at"] is None
    finally:
        ctrl.shutdown()


def test_deactivate_clears_deadline():
    ctrl = RelayController(_cfg(1), simulate=True)
    try:
        ctrl.activate(1, duration=5, trigger="test")
        assert ctrl.is_active(1)
        ctrl.deactivate(1)
        assert not ctrl.is_active(1)
        entry = ctrl.get(1)
        assert entry["deadline"] is None
        assert entry["activated_at"] is None
    finally:
        ctrl.shutdown()
