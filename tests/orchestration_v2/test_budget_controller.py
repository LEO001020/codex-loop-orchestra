"""test_budget_controller.py — three-tier runtime budget enforcement.

Covers: the 60 % throttle, 85 % degrade and 100 % circuit-breaker triggers,
reclaim/redistribute, the Sol hard cap (15 %), the K3 floor (20 %), and the
hysteresis rule that prevents THROTTLE↔NORMAL oscillation.
"""
from __future__ import annotations

import json

import pytest

from tests.orchestration_v2.conftest import make_root

from budget_controller import (
    BudgetController,
    BudgetExceeded,
    BudgetState,
)
from orchestration_common import LoopPaths

CEILING = 100_000


@pytest.fixture
def ctl(tmp_path):
    root = make_root(tmp_path)
    controller = BudgetController(LoopPaths.resolve(root))
    controller.open_task("task-1", CEILING, {"worker": 2})
    return controller


def _state_doc(ctl: BudgetController) -> dict:
    return json.loads(ctl.state_path.read_text(encoding="utf-8"))


def _write_state(ctl: BudgetController, **over) -> None:
    doc = _state_doc(ctl)
    doc.update(over)
    ctl.state_path.write_text(json.dumps(doc), encoding="utf-8")


# ---------------------------------------------------------------------------
# ladder triggers (thresholds read from config, not hardcoded)
# ---------------------------------------------------------------------------
def test_thresholds_come_from_policy(ctl):
    assert ctl.throttle_at == ctl.policy.value("budget", "throttle_at")
    assert ctl.degrade_at == ctl.policy.value("budget", "degrade_at")
    assert ctl.break_at == ctl.policy.value("budget", "break_at")


def test_normal_below_throttle(ctl):
    ctl.register_agent("a1", "worker")
    state = ctl.record_usage("a1", int(CEILING * ctl.throttle_at) - 1)
    assert state is BudgetState.NORMAL


def test_60_percent_triggers_throttle(ctl):
    ctl.register_agent("a1", "worker")
    state = ctl.record_usage("a1", int(CEILING * ctl.throttle_at))
    assert state is BudgetState.THROTTLE
    events = (ctl.paths.budget_dir / "events.ndjson").read_text()
    assert "budget_state_change" in events, "state change is ledgered"


def test_85_percent_triggers_degrade(ctl):
    ctl.register_agent("a1", "worker")
    state = ctl.record_usage("a1", int(CEILING * ctl.degrade_at))
    assert state is BudgetState.DEGRADE


def test_100_percent_triggers_circuit_breaker(ctl):
    ctl.register_agent("a1", "worker")
    state = ctl.record_usage("a1", int(CEILING * ctl.break_at))
    assert state is BudgetState.DEGRADE, "exactly 100% is still DEGRADE"
    with pytest.raises(BudgetExceeded) as exc:
        # over 100% AND over the agent's own allocation => BREAK raises
        ctl.record_usage("a1", CEILING)
    assert exc.value.agent_id == "a1"
    assert ctl.state() is BudgetState.BREAK


def test_break_is_control_event_not_bare_failure(ctl):
    """BudgetExceeded carries the actionable fields the control loop needs."""
    ctl.register_agent("a1", "worker")
    try:
        ctl.record_usage("a1", 2 * CEILING)
    except BudgetExceeded as exc:
        assert exc.remaining < 0
        assert exc.agent_id == "a1"
    else:
        pytest.fail("BREAK breach must surface as BudgetExceeded")


def test_tracker_block_guidance_per_state(ctl):
    ctl.register_agent("a1", "worker")
    block = ctl.tracker_block("a1")
    assert "[BUDGET-TRACKER]" in block and "budget_state: NORMAL" in block
    ctl.record_usage("a1", int(CEILING * ctl.throttle_at))
    assert "delegate" in ctl.tracker_block("a1"), "THROTTLE guidance present"


# ---------------------------------------------------------------------------
# reclaim / redistribute
# ---------------------------------------------------------------------------
def test_reclaim_returns_unused_to_pool(ctl):
    grant = ctl.register_agent("a1", "worker")
    ctl.record_usage("a1", 1_000)
    reclaimed = ctl.reclaim("a1")
    assert reclaimed == grant - 1_000
    doc = _state_doc(ctl)
    assert doc["pool_returned"] == reclaimed
    assert "a1" not in doc["allocated"]


def test_register_agent_is_idempotent(ctl):
    first = ctl.register_agent("run-1", "worker")
    second = ctl.register_agent("run-1", "worker")
    assert second == first
    assert _state_doc(ctl)["allocated"]["run-1"] == first


def test_open_task_budget_exhaustion_refuses_new_agent(ctl):
    ctl.register_agent("a1", "worker")
    ctl.register_agent("a2", "worker")
    with pytest.raises(BudgetExceeded, match="no allocation left"):
        ctl.register_agent("a3", "worker")


def test_redistribute_extends_near_limit_agent(ctl):
    grant = ctl.register_agent("a1", "worker")
    ctl.register_agent("a2", "worker")
    ctl.reclaim("a2")  # a2 finishes untouched => pool has headroom
    ctl.record_usage("a1", int(grant * 0.9))  # a1 near its limit
    decision = ctl.extend_or_wrap_up("a1", verification_state="CONTINUE")
    assert decision.action == "extend" and decision.extension_tokens > 0
    doc = _state_doc(ctl)
    assert doc["allocated"]["a1"] == grant + decision.extension_tokens


def test_wrap_up_when_no_headroom(ctl):
    grant = ctl.register_agent("a1", "worker")
    ctl.record_usage("a1", int(grant * 0.9))
    _write_state(ctl, pool_returned=0)
    decision = ctl.extend_or_wrap_up("a1", verification_state="CONTINUE")
    assert decision.action == "wrap_up"


def test_wrap_up_when_verification_not_continue(ctl):
    grant = ctl.register_agent("a1", "worker")
    ctl.register_agent("a2", "worker")
    ctl.reclaim("a2")
    ctl.record_usage("a1", int(grant * 0.9))
    decision = ctl.extend_or_wrap_up("a1", verification_state="BLOCKED")
    assert decision.action == "wrap_up"


# ---------------------------------------------------------------------------
# Sol hard cap (15 %) and K3 floor (20 %)
# ---------------------------------------------------------------------------
def test_sol_hard_cap_at_allocation(tmp_path):
    root = make_root(tmp_path)
    ctl = BudgetController(LoopPaths.resolve(root))
    doc = ctl.open_task("t", CEILING, {"sol": 4, "worker": 1})
    cap = int(CEILING * ctl.policy.sol_hard_cap())
    assert ctl.policy.sol_hard_cap() == 0.15
    assert doc["allocated"]["role:sol"] <= cap
    assert doc["caps"]["sol_hard_cap_tokens"] == cap


def test_k3_floor_at_allocation(tmp_path):
    root = make_root(tmp_path)
    usage = root / "data" / "usage"
    usage.mkdir(parents=True, exist_ok=True)
    # meter history says K3 verifiers are cheap => raw K3 sum under the floor
    (usage / "role_cost_history.json").write_text(json.dumps(
        {"verifier": {"p50": 1_000, "p90": 2_000, "samples": 50}}))
    ctl = BudgetController(LoopPaths.resolve(root))
    doc = ctl.open_task("t", CEILING, {"verifier": 1, "worker": 1})
    floor = int(CEILING * ctl.policy.k3_floor())
    assert ctl.policy.k3_floor() == 0.20
    assert doc["allocated"]["role:verifier"] >= floor - 5, \
        "K3 allocation is scaled up to the 20% floor"


def test_global_quota_check_uses_denominator_floor(tmp_path):
    root = make_root(tmp_path)
    ctl = BudgetController(LoopPaths.resolve(root))
    usage = root / "data" / "usage"
    usage.mkdir(parents=True, exist_ok=True)
    (usage / "meter_v2_report.json").write_text(json.dumps({
        "windows": {"rolling_5h": {"production_effective_tokens": 100,
                                   "sol_share_effective": 0.9}}}))
    result = ctl.global_quota_check()
    assert result["status"] == "INSUFFICIENT_DATA", \
        "the 2M denominator floor must never actuate on thin data"
    (usage / "meter_v2_report.json").write_text(json.dumps({
        "windows": {"rolling_5h": {"production_effective_tokens": 3_000_000,
                                   "sol_share_effective": 0.30,
                                   "k3_share_effective": 0.25}}}))
    result = ctl.global_quota_check()
    assert result["status"] == "OK"
    assert result["sol_ok"] is False, "0.30 > 15% hard cap"
    assert result["k3_ok"] is True, "0.25 >= 20% floor"


# ---------------------------------------------------------------------------
# hysteresis (no oscillation)
# ---------------------------------------------------------------------------
def test_upward_transitions_are_immediate_and_monotonic(ctl):
    ctl.register_agent("a1", "worker")
    assert ctl.record_usage("a1", 61_000) is BudgetState.THROTTLE
    assert ctl.record_usage("a1", 25_000) is BudgetState.DEGRADE


def test_no_downward_transition_before_cooldown(ctl):
    ctl.register_agent("a1", "worker")
    ctl.record_usage("a1", 61_000)
    # usage magically drops (e.g. accounting correction) but the cooldown
    # has not elapsed: the state HOLDS — no THROTTLE->NORMAL flap.
    _write_state(ctl, consumed={"a1": 40_000})
    assert ctl.record_usage("a1", 0) is BudgetState.THROTTLE


def test_no_downward_transition_without_band_gap(ctl):
    ctl.register_agent("a1", "worker")
    ctl.record_usage("a1", 61_000)
    # cooled down, but only just under the threshold (0.58 > 0.60-0.05):
    _write_state(ctl, consumed={"a1": 58_000},
                 state_changed_at=0)  # long past the cooldown
    assert ctl.record_usage("a1", 0) is BudgetState.THROTTLE, \
        "a full 5% band gap is required to leave THROTTLE"


def test_downward_after_cooldown_and_band_gap(ctl):
    ctl.register_agent("a1", "worker")
    ctl.record_usage("a1", 61_000)
    _write_state(ctl, consumed={"a1": 40_000}, state_changed_at=0)
    assert ctl.record_usage("a1", 0) is BudgetState.NORMAL


def test_no_oscillation_over_noisy_sequence(ctl):
    """A ratio dancing around the threshold changes state at most once."""
    ctl.register_agent("a1", "worker")
    states = [ctl.record_usage("a1", tokens)
              for tokens in (59_000, 2_000, 0, 0, 0)]
    transitions = sum(1 for a, b in zip(states, states[1:]) if a is not b)
    assert transitions <= 1
    assert states[-1] is BudgetState.THROTTLE
