"""test_cold_start_rollback.py — the single-key rollback path.

Covers: rollback from layered to cold_start, rollback-key availability, and
state preservation across the mode flip.
"""
from __future__ import annotations

import json

import pytest
import hashlib
import time

from tests.orchestration_v2.conftest import (
    emit_event,
    green_guards,
    make_root,
    set_routing_mode,
)

from agent_router import AgentRouter, GateGuard, Route, RoutingMode, \
    check_layered_gate_guards
from l2_consumer import L2Consumer, load_policy
from layered_gate import LayeredGate
from orchestration_common import LoopPaths, OrchestrationPolicy
from statemachine_v2 import StateMachine
from routing_mode import layered_authorized


def _router(root):
    paths = LoopPaths.resolve(root)
    return AgentRouter(paths, OrchestrationPolicy.load(paths))


@pytest.fixture
def layered_root(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    green_guards(root)
    assert _router(root).effective_mode()[0] is RoutingMode.LAYERED
    return root


# ---------------------------------------------------------------------------
# rollback layered -> cold_start
# ---------------------------------------------------------------------------
def test_one_key_rollback_to_cold_start(layered_root):
    root = layered_root
    set_routing_mode(root, "cold_start")  # THE one-line rollback
    mode, _ = _router(root).effective_mode()
    assert mode is RoutingMode.COLD_START


def test_rollback_restores_legacy_routing_byte_identical(layered_root):
    root = layered_root
    decision = _router(root).route_action("p1", "send_l2")
    assert decision.route is Route.K3_VERIFY, "layered before rollback"
    set_routing_mode(root, "cold_start")
    decision = _router(root).route_action("p1", "send_l2")
    assert decision.route is Route.SOL_ADJUDICATE
    assert decision.effective_action == "direct_l3", \
        "cold_start behavior is byte-identical legacy: everything upgrades"


def test_rollback_needs_no_gate(layered_root):
    """Rolling FORWARD requires all six guards; rolling BACK never does —
    cold_start must be reachable even with everything on fire."""
    root = layered_root
    # burn the world down: no heartbeat, no canary, no validator marker
    (root / "data" / "l2_queue" / "consumer_heartbeat.json").unlink()
    (root / "data" / "l2_queue" / "exactly_once_canary.json").unlink()
    set_routing_mode(root, "cold_start")
    mode, report = _router(root).effective_mode()
    assert mode is RoutingMode.COLD_START and report is None


# ---------------------------------------------------------------------------
# rollback key availability
# ---------------------------------------------------------------------------
def test_rollback_key_declared_in_canonical_policy(layered_root):
    root = layered_root
    policy = OrchestrationPolicy.load(LoopPaths.resolve(root))
    assert policy.value("routing", "rollback_mode") == "cold_start"
    assert policy.path == root / "config" / "orchestration_policy_v2.toml"


def test_rollback_key_is_gate_condition_six(layered_root):
    root = layered_root
    v2 = load_policy(root / "config" / "orchestration_policy_v2.toml")
    gate = LayeredGate(root, policy=v2,
                       canary_runner=lambda: type("C", (), {"ok": True,
                                                            "detail": ""})())
    cond = gate.check_rollback_key()
    assert cond.ok and "cold_start" in cond.detail


def test_missing_rollback_key_blocks_layered(layered_root):
    root = layered_root
    paths = LoopPaths.resolve(root)
    policy = OrchestrationPolicy.load(paths)
    policy.doc["routing"]["rollback_mode"] = ""
    report = check_layered_gate_guards(paths, policy)
    assert GateGuard.ROLLBACK_KEY_AVAILABLE.value in report.failing(), \
        "no rollback path => layered mode is unrepresentable"


# ---------------------------------------------------------------------------
# state preservation during rollback
# ---------------------------------------------------------------------------
def test_ledger_and_queue_state_survive_rollback(layered_root):
    root = layered_root
    paths = LoopPaths.resolve(root)
    # build live state while layered: packets in flight + a pending L2 record
    ledger = {"schema": "codex-loop-statemachine/v2", "packets": {
        "p1": {"state": "RUNNING", "history": [], "attempts": 1},
        "p2": {"state": "L2_VERIFY", "history": [], "attempts": 0},
    }}
    paths.ledger.write_text(json.dumps(ledger))
    policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
    consumer = L2Consumer(root, policy=policy, dispatcher=lambda r: True)
    consumer.enqueue("p2", "run-1", 1)

    set_routing_mode(root, "cold_start")

    preserved = json.loads(paths.ledger.read_text())
    assert preserved["packets"] == ledger["packets"], \
        "the mode flip is a routing change only — packet state is untouched"
    assert len(list(consumer.iter_pending())) == 1, \
        "pending L2 records survive; the consumer can still drain them"


def test_statemachine_keeps_working_after_rollback(layered_root):
    """The v2 table (including L2 states) still drives packets after the
    routing rollback — in-flight L2 work completes instead of dead-lettering."""
    root = layered_root
    paths = LoopPaths.resolve(root)
    paths.ledger.write_text(json.dumps({
        "schema": "codex-loop-statemachine/v2",
        "packets": {"p2": {"state": "L2_VERIFY", "history": [],
                           "attempts": 0}}}))
    set_routing_mode(root, "cold_start")
    emit_event(root, "p2", "verdict_pass")
    sm = StateMachine(paths)
    states = sm.step()
    assert states["p2"] == "ACCEPTED"
    assert sm.dead_this_step == 0


def test_rollback_then_reenable_requires_fresh_gate(layered_root):
    """After a rollback, going layered again re-runs the full gate."""
    root = layered_root
    set_routing_mode(root, "cold_start")
    (root / "data" / "l2_queue" / "consumer_heartbeat.json").unlink()
    set_routing_mode(root, "layered")
    mode, report = _router(root).effective_mode()
    assert mode is RoutingMode.SHADOW, \
        "stale prerequisites downgrade the re-flip mechanically"
    assert report is not None and not report.passed


def test_direct_layered_flip_requires_exact_prospective_authorization(tmp_path):
    root = make_root(tmp_path)
    policy = root / "config" / "orchestration_policy_v2.toml"
    candidate = policy.read_text(encoding="utf-8").replace(
        'mode = "cold_start"', 'mode = "layered"', 1)
    marker = root / "data" / "governor" / "layered_authorization.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "status": "PASS", "authorized_mode": "layered", "ts": time.time(),
        "policy_sha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
        "conditions": [{"name": "gate", "ok": True}],
    }), encoding="utf-8")
    assert layered_authorized(policy)
    marker_doc = json.loads(marker.read_text(encoding="utf-8"))
    marker_doc["policy_sha256"] = "0" * 64
    marker.write_text(json.dumps(marker_doc), encoding="utf-8")
    assert not layered_authorized(policy)
