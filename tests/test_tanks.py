"""Tank volume-tracking tests (800 L/H pumps, refill-as-source-of-truth).

Design under test
~~~~~~~~~~~~~~~~~
* Volume is the single unit of account: ``ml = pump_lph * 1000 * seconds / 3600``.
* A refill declares what is present; stock is consumed from real run time.
* Fertigation happens *with* watering and honours a multi-day soak cooldown.
* Everything is opt-in and fail-closed: no stock, no auto, or an in-effect
  soak window means no dose.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from automation.controller import AutomationController
from database.database import Database
from hardware.relay_controller import RelayController
from state import SystemState

PUMP_LPH = 800.0
ML_PER_SECOND = PUMP_LPH * 1000.0 / 3600.0  # ~222.2 mL/s


def _make_config(tmp_path, watchdog_seconds=10.0, pest_max=2, **fert_overrides):
    fertilizer = {
        "enabled": True,
        "auto_enabled": True,
        "fertilizer_type": "NPK 10-10-10",
        "total_ml": 0,
        "tank_capacity_ml": 5000,
        "pump_lph": PUMP_LPH,
        "mode": "with_watering",
        "fert_ml_per_cycle": 250,
        "min_ml_for_cycle": 250,
        "min_dose_seconds": 1.0,
        "soak_days": 7,
        "min_watering_seconds_for_fert": 1,
    }
    fertilizer.update(fert_overrides)
    return {
        "system": {"name": "Test", "simulate_hardware": True, "timezone": "UTC", "tick_seconds": 0.05},
        "database": {"path": str(tmp_path / "test.db")},
        "npk": {
            "enabled": False,
            "read_interval_seconds": 0.1,
            "log_interval_seconds": 0.1,
            "thresholds": {"nitrogen": 20, "phosphorus": 20, "potassium": 20},
        },
        "soil_moisture": {
            "enabled": True,
            "read_interval_seconds": 0.1,
            "log_interval_seconds": 0.1,
            "threshold": 30,
            "relay_id": 2,
            "watering_cooldown_seconds": 10,
            "hysteresis": 5,
            "max_watering_seconds_per_day": 1000,
        },
        "relays": {
            "active_high": True,
            "auto_off_watchdog_seconds": watchdog_seconds,
            "watchdog_check_seconds": 0.05,
            "default_duration_seconds": 5,
            "items": [
                {"id": 1, "name": "fertilizer", "label": "Fertilizer", "pin": 17, "activation_duration_seconds": 2},
                {"id": 2, "name": "watering", "label": "Watering", "pin": 27, "activation_duration_seconds": 2},
                {"id": 3, "name": "pest_response", "label": "Pest", "pin": 22, "activation_duration_seconds": 2},
            ],
        },
        "pest_detection": {"enabled": False, "max_activations_per_month": pest_max},
        "camera": {"enabled": False},
        "tanks": {"fertilizer": fertilizer},
    }


def _make_stack(tmp_path, **fert_overrides):
    cfg = _make_config(tmp_path, **fert_overrides)
    db = Database(cfg["database"]["path"])
    relays = RelayController(cfg["relays"], simulate=True)
    state = SystemState(cfg)
    sensors = {"npk": None, "moisture": MagicMock()}
    ctrl = AutomationController(config=cfg, db=db, relays=relays, state=state, sensors=sensors)
    return cfg, ctrl, state, relays, db, sensors


def _cleanup(ctrl, relays, db):
    for closer in (ctrl.stop, relays.shutdown, db.close):
        try:
            closer()
        except Exception:
            pass


@pytest.fixture
def stack(tmp_path):
    cfg, ctrl, state, relays, db, sensors = _make_stack(tmp_path)
    yield cfg, ctrl, state, relays, db, sensors
    _cleanup(ctrl, relays, db)


# ----------------------------------------------------------------------
# State + volume maths
# ----------------------------------------------------------------------


def test_state_exposes_tank_defaults():
    cfg = {
        "system": {"name": "T", "simulate_hardware": True},
        "relays": {"items": []},
        "pest_detection": {"enabled": False},
    }
    tanks = SystemState(cfg).snapshot()["tanks"]
    assert tanks["fertilizer"]["pump_lph"] == 800
    assert tanks["fertilizer"]["mode"] == "with_watering"
    assert tanks["fertilizer"]["min_dose_seconds"] == 1.0
    assert tanks["pesticide"]["mode"] == "separate"
    assert tanks["pesticide"]["min_dose_seconds"] == 1.0


def test_ml_from_seconds_is_millilitres():
    # 800 L/H for 10 s = 2.222 L = 2222.2 mL (a missing x1000 here would be
    # a 1000x dosing error, so pin it down).
    assert AutomationController._ml_from_seconds(10, 800) == pytest.approx(2222.22, abs=0.1)
    assert AutomationController._ml_from_seconds(1, 800) == pytest.approx(222.22, abs=0.1)
    assert AutomationController._ml_from_seconds(0, 800) == 0.0
    assert AutomationController._ml_from_seconds(10, 0) == 0.0


def test_seconds_for_ml_round_trips():
    seconds = AutomationController._seconds_for_ml(250, 800)
    assert seconds == pytest.approx(1.125, abs=0.001)
    assert AutomationController._ml_from_seconds(seconds, 800) == pytest.approx(250, abs=0.01)
    assert AutomationController._seconds_for_ml(0, 800) == 0.0


# ----------------------------------------------------------------------
# Refill
# ----------------------------------------------------------------------


def test_refill_sets_stock_and_counts_cycles(stack):
    _, ctrl, state, _, _, _ = stack
    ok, msg = ctrl.refill_fertilizer_tank(
        fertilizer_type="NPK 10-10-10", npk_n=10, npk_p=10, npk_k=10,
        total_ml=1500, fert_ml_per_cycle=250,
    )
    assert ok, msg
    tank = state.snapshot()["tanks"]["fertilizer"]
    assert tank["fertilizer_type"] == "NPK 10-10-10"
    assert tank["total_ml"] == 1500
    assert tank["ml_remaining_est"] == 1500
    assert tank["ml_dispensed_since_refill"] == 0
    assert tank["status"] == "full"


def test_refill_defaults_to_a_meterable_dose(stack):
    _, ctrl, state, _, _, _ = stack
    ok, msg = ctrl.refill_fertilizer_tank(total_ml=1000)
    assert ok, msg
    assert state.snapshot()["tanks"]["fertilizer"]["fert_ml_per_cycle"] == 250


@pytest.mark.parametrize(
    "kwargs",
    [
        {"total_ml": 0},                                  # nothing declared
        {"total_ml": -5},                                 # nonsense amount
        {"total_ml": 1000, "fert_ml_per_cycle": 0},       # no dose size
    ],
)
def test_refill_rejects_invalid_batches(stack, kwargs):
    _, ctrl, _, _, _, _ = stack
    ok, _ = ctrl.refill_fertilizer_tank(**kwargs)
    assert ok is False


def test_refill_rejects_unmeterable_dose(stack):
    """At 800 L/H a 1 s pulse is ~222 mL; a 150 mL target is not dispensible."""
    _, ctrl, _, _, _, _ = stack
    ok, msg = ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=150)
    assert ok is False
    assert "at least" in msg


# ----------------------------------------------------------------------
# Fertigation (fertilizer WITH watering)
# ----------------------------------------------------------------------


def test_fertigation_dispenses_the_target_volume(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    tank = state.snapshot()["tanks"]["fertilizer"]
    assert tank["ml_remaining_est"] == pytest.approx(1250, abs=0.01)
    assert tank["ml_dispensed_since_refill"] == pytest.approx(250, abs=0.01)
    assert tank["last_fertilized_at"] is not None


def test_fertigation_honours_the_minimum_pulse_floor(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    # Operator raises the floor after refill; the floor must now bind
    # (1.125 s worth of target, but the pump may not pulse < 3 s reliably).
    state.update_tank("fertilizer", min_dose_seconds=3.0)
    ctrl._maybe_apply_fertigation(0.0, watering_duration=20.0)
    dispensed = 1500 - state.snapshot()["tanks"]["fertilizer"]["ml_remaining_est"]
    assert dispensed == pytest.approx(3.0 * ML_PER_SECOND, abs=0.01)


def test_fertigation_is_skipped_when_auto_disabled(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.set_tank_auto("fertilizer", False)
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    assert state.snapshot()["tanks"]["fertilizer"]["ml_remaining_est"] == 1500


def test_fertigation_skipped_when_watering_too_short(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    ctrl._maybe_apply_fertigation(0.0, watering_duration=0.5)  # < min_watering_seconds_for_fert
    assert state.snapshot()["tanks"]["fertilizer"]["ml_remaining_est"] == 1500


def test_fertigation_blocks_when_stock_below_one_dose(tmp_path):
    _, ctrl, state, _, db, _ = _make_stack(tmp_path, soak_days=0)
    ctrl.refill_fertilizer_tank(total_ml=300, fert_ml_per_cycle=250)
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)   # 300 -> 50
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)   # 50 < 250 -> blocked
    tank = state.snapshot()["tanks"]["fertilizer"]
    assert tank["ml_remaining_est"] == pytest.approx(50, abs=0.01)
    assert tank["blocked_reason"] == "fertilizer_insufficient_for_cycle"
    blocked = db._query("SELECT reason FROM tank_auto_dose_blocked")
    assert [r["reason"] for r in blocked] == ["fertilizer_insufficient_for_cycle"]


def test_fertigation_skipped_when_tank_empty(stack):
    _, ctrl, state, _, _, _ = stack
    # Never refilled: ml_remaining_est is 0, so nothing happens (and notably
    # no dose is attempted with no declared stock).
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    assert state.snapshot()["tanks"]["fertilizer"]["ml_remaining_est"] == 0


def test_fertigation_skips_when_window_cannot_fit_a_dose(stack):
    """A watering window shorter than the pulse must not be truncated."""
    _, ctrl, state, _, db, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    # dose_seconds is ~1.125s; a 0.5s window cannot hold it. Truncating would
    # command a sub-floor pulse, so the dose is skipped and explained.
    ctrl._maybe_apply_fertigation(0.0, watering_duration=0.5)
    tank = state.snapshot()["tanks"]["fertilizer"]
    assert tank["ml_remaining_est"] == 1500
    assert tank["blocked_reason"] == "fertilizer_watering_too_short"
    assert db._query("SELECT * FROM tank_auto_doses") == []


def test_fertigation_bills_the_watchdog_clamped_duration(tmp_path):
    """Stock is debited for what the relay will actually run, not what we asked."""
    _, ctrl, state, relays, _, _ = _make_stack(tmp_path, watchdog_seconds=0.5)
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    # The 1.125s request is clamped to the 0.5s watchdog cap, so only
    # 0.5 * 222.2 = ~111 mL is debited instead of the full 250 mL.
    assert state.snapshot()["tanks"]["fertilizer"]["ml_remaining_est"] == pytest.approx(
        1500 - 0.5 * ML_PER_SECOND, abs=0.01
    )
    assert relays.get(1)["duration"] == pytest.approx(0.5, abs=0.001)


def test_fertigation_records_relay_unavailable(stack):
    _, ctrl, state, relays, _, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    dead = MagicMock()
    dead.available = False
    dead.is_active = False
    relays._relays[1]["relay"] = dead
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    assert state.snapshot()["tanks"]["fertilizer"]["blocked_reason"] == "fertilizer_relay_unavailable"


def test_fertigation_reports_unsupported_separate_mode(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250, mode="separate")
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    assert (
        state.snapshot()["tanks"]["fertilizer"]["blocked_reason"]
        == "fertilizer_mode_separate_unsupported"
    )


# ----------------------------------------------------------------------
# Soak cooldown ("fertilizer needs days to be absorbed")
# ----------------------------------------------------------------------


def test_soak_cooldown_blocks_immediate_reapplication(stack):
    _, ctrl, state, _, _, _ = stack  # soak_days=7
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    first = state.snapshot()["tanks"]["fertilizer"]["ml_remaining_est"]
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    assert state.snapshot()["tanks"]["fertilizer"]["ml_remaining_est"] == first
    assert first == pytest.approx(1250, abs=0.01)


def test_expired_soak_cooldown_allows_reapplication(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    past = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    state.update_tank("fertilizer", cooldown_until_at=past)
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    assert state.snapshot()["tanks"]["fertilizer"]["ml_remaining_est"] == pytest.approx(1000, abs=0.01)


def test_zero_soak_days_never_cools_down(tmp_path):
    cfg, ctrl, state, relays, db, _ = _make_stack(tmp_path, soak_days=0)
    try:
        ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
        ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
        ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
        assert state.snapshot()["tanks"]["fertilizer"]["ml_remaining_est"] == pytest.approx(1000, abs=0.01)
    finally:
        _cleanup(ctrl, relays, db)


def test_refill_preserves_an_active_cooldown(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    cooldown = state.snapshot()["tanks"]["fertilizer"]["cooldown_until_at"]
    assert cooldown is not None
    # Topping up stock must not silently clear the soak window.
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    assert state.snapshot()["tanks"]["fertilizer"]["cooldown_until_at"] == cooldown


# ----------------------------------------------------------------------
# Pesticide
# ----------------------------------------------------------------------


def test_refill_pesticide_sets_stock(stack):
    _, ctrl, state, _, _, _ = stack
    ok, msg = ctrl.refill_pesticide_tank(
        pesticide_type="Neem", total_ml=2000, ml_per_activation=250,
    )
    assert ok, msg
    tank = state.snapshot()["tanks"]["pesticide"]
    assert tank["pesticide_type"] == "Neem"
    assert tank["total_ml"] == 2000
    assert tank["ml_remaining_est"] == 2000


def test_refill_pesticide_rejects_unmeterable_dose(stack):
    _, ctrl, _, _, _, _ = stack
    ok, msg = ctrl.refill_pesticide_tank(total_ml=2000, ml_per_activation=100)
    assert ok is False
    assert "at least" in msg


def test_pesticide_duration_is_volume_driven(stack):
    """A 120 s ceiling must not become a ~26 L dose at 800 L/H."""
    _, ctrl, _, _, _, _ = stack
    ctrl.refill_pesticide_tank(total_ml=2000, ml_per_activation=250)
    ctrl.set_tank_auto("pesticide", True)
    assert ctrl._pesticide_effective_duration(120.0) == pytest.approx(1.125, abs=0.001)


def test_pesticide_refuses_dose_when_ceiling_cannot_fit_the_pulse(stack):
    """A ceiling below the minimum pulse must not force a sub-floor dose."""
    _, ctrl, state, relays, _, _ = stack
    ctrl.refill_pesticide_tank(total_ml=2000, ml_per_activation=250)
    ctrl.set_tank_auto("pesticide", True)
    # 0.5s < min_dose_seconds (1.0s). Truncating would meter ~111 mL, below the
    # floor, so the dose is refused outright rather than under-dosed.
    assert ctrl._pesticide_effective_duration(0.5) == 0.0
    ctrl._activate_pest_response(3, 0.5, datetime.now().isoformat(timespec="seconds"), "test")
    assert relays.is_active(3) is False
    assert (
        state.snapshot()["tanks"]["pesticide"]["blocked_reason"]
        == "pesticide_dose_exceeds_relay_limit"
    )


def test_pesticide_uses_floor_when_ceiling_is_generous(stack):
    _, ctrl, _, _, _, _ = stack
    ctrl.refill_pesticide_tank(total_ml=2000, ml_per_activation=250)
    ctrl.set_tank_auto("pesticide", True)
    assert ctrl._pesticide_effective_duration(60.0) == pytest.approx(1.125, abs=0.001)


def test_pesticide_duration_passthrough_when_untracked(stack):
    _, ctrl, _, _, _, _ = stack
    assert ctrl._pesticide_effective_duration(120.0) == 120.0


def test_pesticide_stock_gate_blocks_and_skips_activation(stack):
    _, ctrl, state, relays, db, _ = stack
    ctrl.refill_pesticide_tank(total_ml=2000, ml_per_activation=250)
    ctrl.set_tank_auto("pesticide", True)
    # Drain the tank below one dose, then try to respond.
    state.update_tank("pesticide", ml_remaining_est=10.0)
    assert ctrl._pesticide_has_stock(1.125) is False
    ctrl._activate_pest_response(3, 120.0, datetime.now().isoformat(timespec="seconds"), "test")
    tank = state.snapshot()["tanks"]["pesticide"]
    assert tank["ml_remaining_est"] == 10.0                     # not consumed
    assert tank["blocked_reason"] == "pesticide_insufficient_for_cycle"
    assert relays.is_active(3) is False                         # never energised
    blocked = db._query("SELECT reason FROM tank_auto_dose_blocked")
    assert [r["reason"] for r in blocked] == ["pesticide_insufficient_for_cycle"]


def test_pesticide_stock_is_consumed_only_after_activation(stack):
    _, ctrl, state, _, db, _ = stack
    ctrl.refill_pesticide_tank(total_ml=2000, ml_per_activation=250)
    ctrl.set_tank_auto("pesticide", True)
    ctrl._activate_pest_response(3, 120.0, datetime.now().isoformat(timespec="seconds"), "test")
    tank = state.snapshot()["tanks"]["pesticide"]
    assert tank["ml_remaining_est"] == pytest.approx(2000 - 1.125 * ML_PER_SECOND, abs=0.01)
    assert tank["last_pesticided_at"] is not None
    doses = db._query("SELECT ml_dispensed FROM tank_auto_doses WHERE tank = 'pesticide'")
    assert len(doses) == 1
    assert doses[0]["ml_dispensed"] == pytest.approx(250, abs=0.1)


# ----------------------------------------------------------------------
# Restart persistence (H2) — a restart must not forget stock or the soak window
# ----------------------------------------------------------------------

def test_seed_tank_state_restores_stock_and_cooldown(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    assert state.snapshot()["tanks"]["fertilizer"]["ml_remaining_est"] == pytest.approx(
        1250, abs=0.01
    )

    # Simulate a restart: in-memory state falls back to the config defaults.
    state.update_tank(
        "fertilizer",
        ml_remaining_est=0.0,
        ml_dispensed_since_refill=0.0,
        cooldown_until_at=None,
        last_fertilized_at=None,
    )
    ctrl._seed_tank_state()
    tank = state.snapshot()["tanks"]["fertilizer"]
    assert tank["ml_remaining_est"] == pytest.approx(1250, abs=0.01)
    assert tank["ml_dispensed_since_refill"] == pytest.approx(250, abs=0.01)
    assert tank["cooldown_until_at"] is not None
    assert ctrl._soak_active(tank) is True


def test_seed_tank_state_restores_pesticide_type(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.refill_pesticide_tank(pesticide_type="Neem Oil", total_ml=2000, ml_per_activation=250)
    state.update_tank("pesticide", pesticide_type="")
    ctrl._seed_tank_state()
    assert state.snapshot()["tanks"]["pesticide"]["pesticide_type"] == "Neem Oil"


def test_seed_tank_state_is_a_noop_without_a_refill(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl._seed_tank_state()
    assert state.snapshot()["tanks"]["fertilizer"]["ml_remaining_est"] == 0


# ----------------------------------------------------------------------
# Monthly limit has a single source of truth (H3)
# ----------------------------------------------------------------------

def test_pesticide_monthly_cap_cannot_override_the_enforced_limit(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.refill_pesticide_tank(
        total_ml=2000, ml_per_activation=250, max_activations_per_month=99
    )
    tank = state.snapshot()["tanks"]["pesticide"]
    assert tank["max_activations_per_month"] == 2
    assert ctrl._pest_max_activations_per_month() == 2


def test_pesticide_monthly_mirror_follows_pest_detection(tmp_path):
    _, ctrl, state, _, _, _ = _make_stack(tmp_path, pest_max=5)
    ctrl.refill_pesticide_tank(total_ml=2000, ml_per_activation=250)
    assert state.snapshot()["tanks"]["pesticide"]["max_activations_per_month"] == 5
    assert ctrl._pest_max_activations_per_month() == 5


# ----------------------------------------------------------------------
# Deferral signal ("fertilizer is due but nothing will water it")
# ----------------------------------------------------------------------

def test_deferral_reason_when_nutrients_due_but_no_watering(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    state.update_recommendation(recommended=True, fertilizer_id="npk-10-10-10")
    ctrl._refresh_fertilization_gate_reason()
    assert state.snapshot()["tanks"]["fertilizer"]["blocked_reason"] == "fertilization_deferred_no_watering"


def test_deferral_reason_prefers_soak_cooldown(stack):
    _, ctrl, state, _, _, _ = stack  # soak_days=7
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    future = (datetime.now() + timedelta(days=3)).isoformat(timespec="seconds")
    state.update_tank("fertilizer", cooldown_until_at=future)
    state.update_recommendation(recommended=True, fertilizer_id="npk-10-10-10")
    ctrl._refresh_fertilization_gate_reason()
    assert state.snapshot()["tanks"]["fertilizer"]["blocked_reason"] == "fertilizer_soak_cooldown"


def test_deferral_reason_reports_empty_tank(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=300, fert_ml_per_cycle=250)
    state.update_tank("fertilizer", ml_remaining_est=0.0)
    state.update_recommendation(recommended=True, fertilizer_id="npk-10-10-10")
    ctrl._refresh_fertilization_gate_reason()
    assert state.snapshot()["tanks"]["fertilizer"]["blocked_reason"] == "fertilizer_needs_refill"


def test_deferral_note_cleared_once_nutrients_recover(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    state.update_recommendation(recommended=True, fertilizer_id="npk-10-10-10")
    ctrl._refresh_fertilization_gate_reason()
    assert state.snapshot()["tanks"]["fertilizer"]["blocked_reason"] is not None
    state.update_recommendation(recommended=False)
    ctrl._refresh_fertilization_gate_reason()
    assert state.snapshot()["tanks"]["fertilizer"]["blocked_reason"] is None


def test_no_deferral_reason_when_auto_disabled(stack):
    _, ctrl, state, _, _, _ = stack
    ctrl.set_tank_auto("fertilizer", False)
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    state.update_recommendation(recommended=True, fertilizer_id="npk-10-10-10")
    ctrl._refresh_fertilization_gate_reason()
    assert state.snapshot()["tanks"]["fertilizer"]["blocked_reason"] is None


# ----------------------------------------------------------------------
# Opt-in toggles
# ----------------------------------------------------------------------


def test_set_tank_auto_toggles_and_rejects_unknown(stack):
    _, ctrl, state, _, _, _ = stack
    ok, _ = ctrl.set_tank_auto("fertilizer", False)
    assert ok and state.snapshot()["tanks"]["fertilizer"]["auto_enabled"] is False
    ok, _ = ctrl.set_tank_auto("fertilizer", True)
    assert ok and state.snapshot()["tanks"]["fertilizer"]["auto_enabled"] is True
    ok, _ = ctrl.set_tank_auto("bogus", True)
    assert ok is False


def test_set_tank_enabled_rejects_unknown(stack):
    _, ctrl, _, _, _, _ = stack
    ok, _ = ctrl.set_tank_enabled("bogus", True)
    assert ok is False


# ----------------------------------------------------------------------
# Audit trail
# ----------------------------------------------------------------------


def test_refill_writes_a_tank_refill_event(stack):
    _, ctrl, _, _, db, _ = stack
    ctrl.refill_fertilizer_tank(
        fertilizer_type="NPK 10-10-10", total_ml=1500, fert_ml_per_cycle=250,
    )
    rows = db.recent_tank_refills()
    assert len(rows) == 1
    assert rows[0]["tank"] == "fertilizer"
    assert rows[0]["total_ml"] == 1500
    assert rows[0]["max_doses"] == 6


def test_fertigation_writes_a_dose_event(stack):
    _, ctrl, _, _, db, _ = stack
    ctrl.refill_fertilizer_tank(total_ml=1500, fert_ml_per_cycle=250)
    ctrl._maybe_apply_fertigation(0.0, watering_duration=5.0)
    rows = db.recent_tank_doses()
    assert len(rows) == 1
    assert rows[0]["tank"] == "fertilizer"
    assert rows[0]["ml_dispensed"] == pytest.approx(250, abs=0.01)
    assert rows[0]["combined_with_watering"] == 1


def test_tanks_appear_in_snapshot(stack):
    _, _, state, _, _, _ = stack
    snapshot = state.snapshot()
    assert set(snapshot["tanks"]) == {"fertilizer", "pesticide"}
