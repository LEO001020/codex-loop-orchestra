"""test_sol_tool_gate_v2.py — the fail-closed root-turn gate hook.

Covers: default-deny (fail-closed), external enforcement (the constrained
session cannot flip its own exemption), budget/meter check integration, and
audit logging of every decision.
"""
from __future__ import annotations

import json
import time

import pytest

from tests.orchestration_v2.conftest import make_root

from l2_consumer import load_policy
from sol_tool_gate_v2 import GATED_TOOL_PREFIXES, SolToolGateV2

NOW = 1_000_000.0


@pytest.fixture
def env(tmp_path):
    root = make_root(tmp_path)
    policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
    clock = [NOW]
    gate = SolToolGateV2(root, policy=policy, clock=lambda: clock[0])
    return gate, policy, clock, root


def _sol_payload(policy, tool="bash", **over):
    payload = {"tool_name": tool, "model": policy["models"]["sol_model"]}
    payload.update(over)
    return payload


def _ledger(root, packets: dict, **extra):
    (root / "data").mkdir(parents=True, exist_ok=True)
    doc = {"packets": packets}
    doc.update(extra)
    (root / "data" / "progress_ledger.json").write_text(json.dumps(doc))


def _share_report(gate, *, budget_state="NORMAL", generated_ts=None,
                  critical_roots=()):
    report = {"generated_ts": NOW if generated_ts is None else generated_ts,
              "budget_state": budget_state,
              "sol_share_5h_effective": 0.30,
              "windows": {"rolling_1h": {"critical_roots":
                                         list(critical_roots)}}}
    gate.share_report.parent.mkdir(parents=True, exist_ok=True)
    gate.share_report.write_text(json.dumps(report))


# ---------------------------------------------------------------------------
# fail-closed (default deny)
# ---------------------------------------------------------------------------
def test_missing_ledger_denies_gated_tool(env):
    """v1 failed OPEN on every error. v2: unreadable ledger => deny."""
    gate, policy, _, _ = env
    decision = gate.decide(_sol_payload(policy))
    assert not decision.allow
    assert decision.rule == "ledger_unreadable_fail_closed"
    assert "decompose and dispatch" in decision.reason, \
        "delegation remains available (escape hatch)"


def test_corrupt_ledger_denies(env):
    gate, policy, _, root = env
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "progress_ledger.json").write_text("{oops")
    assert not gate.decide(_sol_payload(policy)).allow


def test_unknown_model_denies(env):
    """A gated operation cannot bypass the Sol policy by omitting identity."""
    gate, policy, _, _ = env
    assert not gate.decide({"tool_name": "bash"}).allow
    assert not gate.decide({"tool_name": "bash", "model": "who/knows"}).allow


def test_internal_error_denies(env, monkeypatch):
    """Absolute backstop: an unexpected exception inside evaluate => deny."""
    gate, policy, _, _ = env
    monkeypatch.setattr(gate, "loop_state",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    decision = gate.decide(_sol_payload(policy))
    assert not decision.allow and decision.rule == "internal_error_fail_closed"


def test_policy_unreadable_denies(tmp_path):
    root = make_root(tmp_path)
    (root / "config" / "orchestration_policy_v2.toml").unlink()
    gate = SolToolGateV2(root)  # remembers the load error
    # a Sol-shaped model can't be resolved either => unknown model deny
    decision = gate.decide({"tool_name": "bash", "model": "any/model"})
    assert not decision.allow


def test_ungated_tools_always_allowed(env):
    gate, _, _, _ = env
    decision = gate.decide({"tool_name": "create_packet"})
    assert decision.allow and decision.rule == "ungated_tool"
    assert not any("create_packet".startswith(p)
                   for p in GATED_TOOL_PREFIXES), \
        "packet/dispatch tools are never gated by design"


def test_worker_family_models_allowed(env):
    """V4/K3 children are exactly where the work SHOULD run."""
    gate, policy, _, _ = env
    for key, role in (("v4_model", "worker"), ("k3_model", "verifier")):
        decision = gate.decide({"tool_name": "bash",
                                "model": policy["models"][key],
                                "agent_type": role})
        assert decision.allow and decision.rule == "worker_model"


# ---------------------------------------------------------------------------
# external enforcement — no self-controlled exemption switch
# ---------------------------------------------------------------------------
def test_bare_loop_state_key_ignored(env):
    """The v1 defect: the constrained session wrote loop_state itself. v2
    honours the key only with a matching harness attestation."""
    gate, policy, _, root = env
    _ledger(root, {"p1": {"state": "RUNNING"}}, loop_state="adjudication")
    assert gate.loop_state() == "execution", "bare key must not win"
    audit = gate.decision_log.read_text()
    assert "governor.state_key_unattested" in audit


def test_attested_loop_state_honoured(env):
    gate, policy, _, root = env
    _ledger(root, {"p1": {"state": "RUNNING"}}, loop_state="adjudication")
    gate.attestations.parent.mkdir(parents=True, exist_ok=True)
    gate.attestations.write_text(json.dumps({
        "event": "governor.state_set", "state": "adjudication",
        "reason": "wave review", "idem_key": "k3:state_set:x"}) + "\n")
    assert gate.loop_state() == "adjudication"
    _share_report(gate)
    decision = gate.decide(_sol_payload(policy))
    assert decision.allow and decision.rule == "allowed_state"


def test_stale_attestation_does_not_cover_new_claim(env):
    gate, policy, _, root = env
    gate.attestations.parent.mkdir(parents=True, exist_ok=True)
    gate.attestations.write_text(json.dumps({
        "event": "governor.state_set", "state": "adjudication"}) + "\n")
    _ledger(root, {"p1": {"state": "RUNNING"}}, loop_state="release_finalize")
    assert gate.loop_state() == "execution", \
        "the newest attestation must match the claimed state exactly"


# ---------------------------------------------------------------------------
# budget / meter integration
# ---------------------------------------------------------------------------
def test_budget_high_denies_with_delegate_instruction(env):
    gate, policy, _, root = env
    _ledger(root, {"p1": {"state": "RUNNING"}})
    _share_report(gate, budget_state="HIGH")
    decision = gate.decide(_sol_payload(policy))
    assert not decision.allow and decision.rule == "budget_high"
    assert "dispatch" in decision.reason, "deny is an actionable delegate"


def test_critical_root_denies(env):
    gate, policy, _, root = env
    _ledger(root, {"p1": {"state": "RUNNING"}})
    _share_report(gate, critical_roots=["runaway-root"])
    decision = gate.decide(_sol_payload(policy))
    assert not decision.allow and decision.rule == "critical_root_1h"


def test_stale_meter_fails_closed_for_execution(env):
    gate, policy, _, root = env
    _ledger(root, {"p1": {"state": "RUNNING"}})
    _share_report(gate, generated_ts=NOW - gate.stale_after_s - 1)
    decision = gate.decide(_sol_payload(policy))
    assert not decision.allow and decision.rule == "meter_stale_fail_closed"


def test_stale_meter_does_not_block_planning(env):
    """Planning turns stay usable while sensors heal (bounded by the lease)."""
    gate, policy, _, root = env
    _ledger(root, {})  # empty => planning
    decision = gate.decide(_sol_payload(policy))
    assert decision.allow, "planning + healthy lease + blind meter => allow"


def test_planning_lease_exhausts_in_gate(env):
    gate, policy, _, root = env
    _ledger(root, {})
    for _ in range(gate.planning_max_turns):
        assert gate.decide(_sol_payload(policy)).allow
    decision = gate.decide(_sol_payload(policy))
    assert not decision.allow and decision.rule == "planning_lease_exhausted"
    assert "decompose or dispatch" in decision.reason


def test_execution_state_denies_by_default(env):
    gate, policy, _, root = env
    _ledger(root, {"p1": {"state": "RUNNING"}})
    _share_report(gate)  # fresh, NORMAL, no critical roots
    decision = gate.decide(_sol_payload(policy))
    assert not decision.allow and decision.rule == "state_gated"
    assert "verifier" in decision.reason, "deny names the K3 alternative"


def test_k3_states_are_execution(env):
    gate, policy, _, root = env
    _ledger(root, {"p1": {"state": "L2_VERIFY"}})
    assert gate.loop_state() == "execution"


# ---------------------------------------------------------------------------
# audit logging
# ---------------------------------------------------------------------------
def test_every_decision_is_audited(env):
    gate, policy, _, root = env
    _ledger(root, {})
    _share_report(gate)
    gate.decide(_sol_payload(policy))               # allow
    gate.decide({"tool_name": "bash"})              # deny (unknown model)
    lines = [json.loads(l) for l in
             gate.decision_log.read_text().splitlines()]
    decisions = [l for l in lines if l["event"] == "governor.decision"]
    assert len(decisions) == 2
    assert {d["allow"] for d in decisions} == {True, False}
    assert all("rule" in d and "reason" in d for d in decisions)


def test_break_glass_allows_but_audits_per_call(env, monkeypatch):
    gate, policy, _, root = env
    monkeypatch.setenv(gate.break_glass_env, "sev1-hotfix")
    d1 = gate.decide(_sol_payload(policy))
    d2 = gate.decide(_sol_payload(policy))
    assert d1.allow and d1.break_glass and d2.allow
    audit = gate.decision_log.read_text()
    assert audit.count("governor.break_glass") == 2, \
        "one audit event per bypassed call — never silent"


def test_deny_hook_output_contract(env):
    gate, policy, _, _ = env
    decision = gate.decide(_sol_payload(policy))  # no ledger => deny
    out = json.loads(decision.to_hook_output())
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"]
