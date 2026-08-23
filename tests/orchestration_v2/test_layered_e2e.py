"""test_layered_e2e.py — end-to-end layered orchestration flow.

Covers the migration path cold_start → shadow → layered, the full packet
lifecycle skeleton_ready → EXPAND_K3 → PLANNED → … → REPORTED → L2_VERIFY →
ACCEPTED (driven by real events through the real state machine and the real
L2 consumer), K3-first dispatch, and budget enforcement inside the flow.
"""
from __future__ import annotations

import json

import pytest

from tests.orchestration_v2.conftest import (
    emit_event,
    good_short_result,
    green_guards,
    make_root,
    read_events,
    set_routing_mode,
    write_packet,
)

from agent_router import AgentRouter, Route, RoutingMode
from budget_controller import BudgetController, BudgetState
from dispatch_v2 import DispatcherV2, resolve_role_pin, sol_budget_block_v2
from l2_consumer import L2Consumer, L2Record, load_policy
from orchestration_common import LoopPaths, OrchestrationPolicy
from root_turn_governor import RootTurnGovernor
from statemachine_v2 import StateMachine
from trigger_eval_v2 import apply_routing_mode


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


def _router(root):
    paths = LoopPaths.resolve(root)
    return AgentRouter(paths, OrchestrationPolicy.load(paths))


# ---------------------------------------------------------------------------
# migration: cold_start -> shadow -> layered
# ---------------------------------------------------------------------------
def test_mode_progression_cold_start_shadow_layered(root):
    # Phase 0: fresh install runs cold_start (byte-identical legacy)
    assert _router(root).effective_mode()[0] is RoutingMode.COLD_START

    # Phase 1: shadow — same execution, calibration corpus accumulates
    set_routing_mode(root, "shadow")
    router = _router(root)
    assert router.effective_mode()[0] is RoutingMode.SHADOW
    router.route_action("p1", "send_l2")
    assert (root / "data" / "router" / "shadow_log.ndjsonl").exists()

    # Phase 2: layered requested but guards red => mechanical downgrade
    set_routing_mode(root, "layered")
    assert _router(root).effective_mode()[0] is RoutingMode.SHADOW

    # Phase 3: guards green => layered engages
    green_guards(root)
    assert _router(root).effective_mode()[0] is RoutingMode.LAYERED


# ---------------------------------------------------------------------------
# full packet lifecycle through the real state machine
# ---------------------------------------------------------------------------
def test_full_flow_skeleton_to_accepted_to_merged(root):
    paths = LoopPaths.resolve(root)
    sm = StateMachine(paths)
    pid = "wave1-p1"

    # plan pipeline: skeleton -> K3 expansion -> validated plan
    emit_event(root, pid, "skeleton_ready")                # t27
    assert sm.step()[pid] == "EXPAND_K3"
    emit_event(root, pid, "expansion_valid")               # t28
    assert sm.step()[pid] == "PLANNED"

    # execution: DAG ok -> dispatched -> report lands
    emit_event(root, pid, "dag_assert_pass")               # t2
    emit_event(root, pid, "dispatched")                    # t3
    assert sm.step()[pid] == "RUNNING"
    rdir = root / "data" / "reports" / pid
    rdir.mkdir(parents=True)
    (rdir / "report.json").write_text(json.dumps(
        good_short_result(packet_id=pid)))
    emit_event(root, pid, "subagent_stop")                 # t4 (report gated)
    assert sm.step()[pid] == "REPORTED"

    # L2 verification band: request -> verdict
    emit_event(root, pid, "l2_requested")                  # t30
    assert sm.step()[pid] == "L2_VERIFY"
    emit_event(root, pid, "verdict_pass")                  # t31
    assert sm.step()[pid] == "ACCEPTED"

    # mechanical acceptance still owns the release path
    emit_event(root, pid, "merged")                        # t16
    assert sm.step()[pid] == "MERGED"
    assert sm.dead_this_step == 0, "zero dead letters across the happy path"


def test_full_flow_with_real_l2_consumer(root):
    """trigger send_l2 -> pending record -> consumer claim (emits t30) ->
    completion (emits t31) -> state machine drives REPORTED..ACCEPTED."""
    set_routing_mode(root, "layered")
    green_guards(root)
    paths = LoopPaths.resolve(root)
    sm = StateMachine(paths)
    pid = "wave1-p2"

    # drive the packet to REPORTED
    for ev in ("skeleton_ready", "expansion_valid", "dag_assert_pass",
               "dispatched"):
        emit_event(root, pid, ev)
    rdir = root / "data" / "reports" / pid
    rdir.mkdir(parents=True)
    (rdir / "report.json").write_text(json.dumps(
        good_short_result(packet_id=pid)))
    emit_event(root, pid, "subagent_stop")
    assert sm.step()[pid] == "REPORTED"

    # trigger eval (layered): send_l2 becomes a REAL queue record
    action, mode, effects = apply_routing_mode(
        "send_l2", pid, "run-1", 1, paths, OrchestrationPolicy.load(paths))
    assert (action, mode) == ("send_l2", "layered")
    idem = effects["l2_record"]

    # the consumer drains it exactly once and emits l2_requested (t30)
    policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
    dispatched: list[L2Record] = []
    consumer = L2Consumer(root, policy=policy,
                          dispatcher=lambda r: dispatched.append(r) or True)
    stats = consumer.drain()
    assert stats.dispatched == 1 and dispatched[0].packet_id == pid
    assert sm.step()[pid] == "L2_VERIFY"

    # the verifier publishes a valid pass verdict -> t31 -> ACCEPTED
    verdict_report = rdir / "verifier_report.json"
    verdict_report.write_text(json.dumps(
        good_short_result(packet_id=pid, verdict="pass")))
    result = consumer.complete(dispatched[0].idem_key, verdict_report,
                               expected_revision=2)
    assert result.ok
    assert sm.step()[pid] == "ACCEPTED"

    # exactly-once held end to end
    consumer.drain()
    assert len(dispatched) == 1
    keys = [e["detail"].get("idem_key") for e in read_events(root)
            if e["event"] == "l2_requested"]
    assert keys.count(dispatched[0].idem_key) == 1


def test_redo_verdict_reenters_retry_path(root):
    paths = LoopPaths.resolve(root)
    sm = StateMachine(paths)
    pid = "wave1-p3"
    for ev in ("skeleton_ready", "expansion_valid", "dag_assert_pass",
               "dispatched"):
        emit_event(root, pid, ev)
    rdir = root / "data" / "reports" / pid
    rdir.mkdir(parents=True)
    (rdir / "report.json").write_text(json.dumps(
        good_short_result(packet_id=pid)))
    emit_event(root, pid, "subagent_stop")
    emit_event(root, pid, "l2_requested")
    emit_event(root, pid, "verdict_redo")                  # t32
    assert sm.step()[pid] == "FAILED"
    # a redo means the old report is superseded: remove it, then re-dispatch
    (rdir / "report.json").unlink()
    emit_event(root, pid, "retry_dispatch")                # t9
    assert sm.step()[pid] == "DISPATCHABLE"
    emit_event(root, pid, "dispatched", {"run_id": "retry-run-1",
                                          "attempt": 1})
    assert sm.step()[pid] == "RUNNING"
    led = sm.load_ledger()
    assert led["packets"][pid]["attempts"] == 1, "retry bumps the generation"


# ---------------------------------------------------------------------------
# K3-first dispatch inside the flow
# ---------------------------------------------------------------------------
def test_k3_first_dispatch_actually_routes_to_k3(root):
    set_routing_mode(root, "layered")
    green_guards(root)
    paths = LoopPaths.resolve(root)
    write_packet(root, "p1", **{"class": "verification"})
    dispatcher = DispatcherV2(paths)
    role, route, _ = dispatcher.decide("p1", action="pass")
    assert (role, route) == ("verifier", Route.K3_VERIFY)
    # a dry-run spawn uses the K3 config pin and no ipybox
    rc = dispatcher.dispatch(["p1"], dry_run=True, action="pass")
    assert rc == 0
    dry = [e for e in read_events(root) if e["event"] == "dispatch_dry_run"]
    assert dry[0]["detail"]["model"] == dispatcher.policy.model_pin("k3")
    assert dry[0]["detail"]["ipybox_enabled"] is False


def test_k3_demand_reaches_refill_pool(root):
    """A drained L2 record leaves K3 demand the refill controller can see."""
    set_routing_mode(root, "layered")
    green_guards(root)
    policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
    consumer = L2Consumer(root, policy=policy, dispatcher=lambda r: True)
    consumer.enqueue("p1", "r1", 1)
    consumer.drain()
    demand_file = root / "data" / "refill" / "k3_demand.ndjsonl"
    rows = [json.loads(l) for l in demand_file.read_text().splitlines()]
    assert rows and rows[0]["pool"] == "k3" and rows[0]["kind"] == "l2_verify"


# ---------------------------------------------------------------------------
# budget enforcement inside the flow
# ---------------------------------------------------------------------------
def test_budget_break_stops_dispatch_in_flow(root):
    set_routing_mode(root, "layered")
    green_guards(root)
    paths = LoopPaths.resolve(root)
    policy = OrchestrationPolicy.load(paths)
    budget = BudgetController(paths, policy)
    budget.open_task("wave-1", 10_000, {"worker": 1})
    budget.register_agent("a1", "worker")
    with pytest.raises(Exception):
        budget.record_usage("a1", 200_000)   # blows straight through BREAK
    assert budget.state() is BudgetState.BREAK

    gov = RootTurnGovernor(paths, policy)
    pin = resolve_role_pin("worker", paths, policy)
    block = sol_budget_block_v2(paths, policy, pin, Route.V4_DIRECT,
                                gov, budget)
    assert block is not None and block["budget_state"] == "BREAK", \
        "BREAK refuses ALL new dispatch inside the flow"

    write_packet(root, "p1")
    dispatcher = DispatcherV2(paths)
    rc = dispatcher.dispatch(["p1"], dry_run=False, action="pass")
    assert rc == 3, "spawn refused as a control event, not a crash"
    events = [e["event"] for e in read_events(root)]
    assert "dispatch_refused" in events or "sol_budget_blocked" in events


def test_budget_throttle_signal_visible_in_flow(root):
    paths = LoopPaths.resolve(root)
    budget = BudgetController(paths, OrchestrationPolicy.load(paths))
    budget.open_task("wave-1", 100_000, {"worker": 2})
    budget.register_agent("a1", "worker")
    budget.record_usage("a1", 65_000)
    assert budget.state() is BudgetState.THROTTLE
    tracker = budget.tracker_block("a1")
    assert "THROTTLE" in tracker and "delegate" in tracker, \
        "the in-loop tracker carries the degrade guidance"
