"""test_root_turn_governor.py — the bounded-planning-lease governor.

Covers: the 6-turn/30k-token planning lease, lease renewal, expiry-driven
auto-delegation, attested loop-state (bare ledger keys rejected), fail-closed
behavior, the audited break-glass, and the AC3 hysteresis sequence.
"""
from __future__ import annotations

import json

import pytest

from tests.orchestration_v2.conftest import make_root, read_events, write_meter_report

from orchestration_common import LoopPaths, OrchestrationPolicy
from root_turn_governor import (
    HysteresisController,
    PlanningLease,
    RootTurnGovernor,
    Verdict,
    loop_state_set,
)


@pytest.fixture
def env(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    gov = RootTurnGovernor(paths, OrchestrationPolicy.load(paths))
    return gov, paths, root


def _sol(gov: RootTurnGovernor) -> str:
    return gov.policy.model_pin("sol")


def _running_ledger(paths: LoopPaths) -> None:
    paths.data.mkdir(parents=True, exist_ok=True)
    paths.ledger.write_text(json.dumps({"packets": {
        "p1": {"state": "RUNNING", "history": [], "attempts": 1}}}))


# ---------------------------------------------------------------------------
# bounded planning lease (6 turns / 30k tokens)
# ---------------------------------------------------------------------------
def test_lease_bounds_come_from_policy(env):
    gov, _, _ = env
    assert gov.policy.planning_max_turns() == 6
    assert gov.policy.planning_max_new_tokens() == 30_000


def test_planning_lease_allows_then_expires_on_turns(env):
    gov, paths, _ = env
    model = _sol(gov)
    for i in range(gov.policy.planning_max_turns()):
        decision = gov.evaluate("bash", model=model)
        assert decision.verdict is Verdict.ALLOW, "turn %d within lease" % i
    decision = gov.evaluate("bash", model=model)
    assert decision.verdict is Verdict.DENY
    assert "decompose or dispatch" in decision.reason, \
        "expiry triggers the auto-delegation instruction"
    assert "packet" in decision.reason.lower()


def test_planning_lease_expires_on_token_budget(env):
    gov, _, _ = env
    model = _sol(gov)
    assert gov.evaluate("bash", model=model,
                        estimated_new_tokens=30_000).verdict is Verdict.ALLOW
    decision = gov.evaluate("bash", model=model)
    assert decision.verdict is Verdict.DENY
    assert "new tokens" in decision.reason


def test_lease_renewal_restores_allowance(env):
    gov, paths, _ = env
    model = _sol(gov)
    for _ in range(gov.policy.planning_max_turns() + 1):
        gov.evaluate("bash", model=model)
    assert gov.evaluate("bash", model=model).verdict is Verdict.DENY
    PlanningLease.renew(paths, "packet_created:wave-1")
    assert gov.evaluate("bash", model=model).verdict is Verdict.ALLOW
    lease = json.loads((paths.governor_dir / "planning_lease.json").read_text())
    assert lease["turns_used"] == 1, "renewal resets the counters"


def test_lease_persists_across_governor_instances(env):
    gov, paths, root = env
    model = _sol(gov)
    gov.evaluate("bash", model=model)
    gov2 = RootTurnGovernor(paths, gov.policy)  # separate hook invocation
    lease = PlanningLease.load(paths)
    assert lease.turns_used == 1, "lease state is on disk, not in-process"


# ---------------------------------------------------------------------------
# attested loop-state
# ---------------------------------------------------------------------------
def test_bare_loop_state_key_rejected_and_flagged(env):
    """A loop_state key written by the constrained session (no attestation)
    is ignored — the derived state wins — and flagged fail-visible."""
    gov, paths, root = env
    paths.ledger.write_text(json.dumps(
        {"packets": {"p1": {"state": "RUNNING", "history": []}},
         "loop_state": "adjudication"}))  # bare key, no attestation
    state, trusted = gov.loop_state()
    assert trusted and state == "execution", "bare key must not grant exemption"
    assert any(e["event"] == "governor.state_key_unattested"
               for e in read_events(root))


def test_attested_loop_state_honoured(env):
    gov, paths, _ = env
    _running_ledger(paths)
    loop_state_set("adjudication", "wave summary review", paths)
    state, trusted = gov.loop_state()
    assert (state, trusted) == ("adjudication", True)
    decision = gov.evaluate("bash", model=_sol(gov))
    assert decision.verdict is Verdict.ALLOW
    assert "adjudication" in decision.reason


def test_attestation_ledger_records_reason_and_key(env):
    gov, paths, _ = env
    record = loop_state_set("release_finalize", "wave complete", paths)
    lines = (paths.governor_dir / "state_attestations.ndjsonl"
             ).read_text().splitlines()
    saved = json.loads(lines[-1])
    assert saved["idem_key"] == record["idem_key"]
    assert saved["reason"] == "wave complete"


def test_loop_state_set_rejects_unknown_state(env):
    _, paths, _ = env
    with pytest.raises(ValueError):
        loop_state_set("do_whatever", "nope", paths)


def test_k3_states_count_as_execution(env):
    """EXPAND_K3/L2_VERIFY/L2_RANK never re-open Sol's tool window (§7)."""
    gov, paths, _ = env
    for state in ("EXPAND_K3", "L2_VERIFY", "L2_RANK"):
        paths.ledger.write_text(json.dumps({"packets": {
            "p1": {"state": state, "history": []}}}))
        assert gov.loop_state() == ("execution", True)


# ---------------------------------------------------------------------------
# fail-closed
# ---------------------------------------------------------------------------
def test_unreadable_ledger_fails_closed(env):
    gov, paths, root = env
    paths.data.mkdir(parents=True, exist_ok=True)
    paths.ledger.write_text("{corrupted json!!!")
    decision = gov.evaluate("bash", model=_sol(gov))
    assert decision.verdict is Verdict.DENY
    assert "failing closed" in decision.reason
    assert "dispatch" in decision.reason, "delegation stays available"
    assert any(e["event"] == "governor.fail_closed" for e in read_events(root))


def test_unknown_model_fails_closed(env):
    gov, _, _ = env
    decision = gov.evaluate("bash", model="mystery/unpinned-model")
    assert decision.verdict is Verdict.DENY
    decision = gov.evaluate("bash", model=None)
    assert decision.verdict is Verdict.DENY, "omitting identity cannot bypass"


def test_dispatch_target_families_never_gated(env):
    gov, _, _ = env
    for family in ("v4", "k3"):
        decision = gov.evaluate("bash", model=gov.policy.model_pin(family))
        assert decision.verdict is Verdict.ALLOW


def test_ungated_tools_always_allowed(env):
    gov, _, _ = env
    assert gov.evaluate("create_packet", model=None).verdict is Verdict.ALLOW
    assert gov.evaluate("dispatch_packet", model=None).verdict is Verdict.ALLOW


def test_meter_missing_fails_closed_in_execution(env):
    gov, paths, root = env
    _running_ledger(paths)
    decision = gov.evaluate("bash", model=_sol(gov))
    assert decision.verdict is Verdict.DENY
    assert "MISSING" in decision.reason
    assert any(e["event"] == "governor.fail_closed" for e in read_events(root))


def test_meter_stale_fails_closed_in_execution(env):
    gov, paths, root = env
    _running_ledger(paths)
    write_meter_report(root, 0.05, generated_at=0)  # ancient
    decision = gov.evaluate("bash", model=_sol(gov))
    assert decision.verdict is Verdict.DENY and "STALE" in decision.reason


def test_budget_degrade_denies_gated_tools(env):
    gov, paths, _ = env
    (paths.budget_dir).mkdir(parents=True, exist_ok=True)
    (paths.budget_dir / "active.json").write_text(json.dumps(
        {"state": "DEGRADE", "task_id": "t"}))
    decision = gov.evaluate("bash", model=_sol(gov))
    assert decision.verdict is Verdict.DENY
    assert "DEGRADE" in decision.reason and "dispatch" in decision.reason


def test_deny_hook_output_shape(env):
    gov, paths, _ = env
    paths.ledger.write_text("{broken")
    output = gov.evaluate("bash", model=_sol(gov)).hook_output()
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    allow = gov.evaluate("create_packet", model=None).hook_output()
    assert allow is None


# ---------------------------------------------------------------------------
# audited break-glass
# ---------------------------------------------------------------------------
def test_break_glass_allows_but_audits(env, monkeypatch):
    gov, paths, root = env
    paths.ledger.write_text("{broken")  # would otherwise deny
    monkeypatch.setenv("LOOP_GOVERNOR_OVERRIDE", "incident-2026-08-11")
    decision = gov.evaluate("bash", model=_sol(gov))
    assert decision.verdict is Verdict.ALLOW
    assert "incident-2026-08-11" in decision.reason
    events = [e for e in read_events(root)
              if e["event"] == "governor.break_glass"]
    assert events and events[0]["detail"]["reason"] == "incident-2026-08-11", \
        "every bypass appends an audit event — observable, never silent"


# ---------------------------------------------------------------------------
# hysteresis (AC3 sequence)
# ---------------------------------------------------------------------------
def test_hysteresis_ac3_sequence(env):
    """0.26, 0.26, 0.23, 0.21, 0.21 => HIGH at sample 2, NORMAL at sample 5."""
    gov, paths, _ = env
    ctl = HysteresisController(paths, gov.policy.hysteresis())
    seq = [0.26, 0.26, 0.23, 0.21, 0.21]
    bands = [ctl.observe(s, sample_id="s%d" % i) for i, s in enumerate(seq)]
    assert bands == ["NORMAL", "HIGH", "HIGH", "HIGH", "NORMAL"]


def test_hysteresis_sample_id_dedup(env):
    """A chatty hook re-observing the same meter report cannot fast-forward
    the sample counters."""
    gov, paths, _ = env
    ctl = HysteresisController(paths, gov.policy.hysteresis())
    assert ctl.observe(0.30, sample_id="same") == "NORMAL"
    assert ctl.observe(0.30, sample_id="same") == "NORMAL", "dedup holds"
    assert ctl.observe(0.30, sample_id="other") == "HIGH"


def test_share_high_denies_with_delegation_message(env):
    gov, paths, root = env
    _running_ledger(paths)
    model = _sol(gov)
    write_meter_report(root, 0.30)
    gov.evaluate("bash", model=model)          # sample 1
    write_meter_report(root, 0.300001)         # distinct sample id
    decision = gov.evaluate("bash", model=model)   # sample 2 -> HIGH
    assert decision.verdict is Verdict.DENY
    assert "HIGH band" in decision.reason
    assert "verifier" in decision.reason, \
        "the deny names the delegation command (auto-delegation)"
