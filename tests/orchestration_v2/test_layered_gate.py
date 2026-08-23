"""test_layered_gate.py — the six-condition mechanical layered-mode gate.

Covers: all six conditions checked, any single failure refusing layered
mode, all-pass enabling it, and gate-result logging.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
import hashlib

from tests.orchestration_v2.conftest import green_guards, make_root

from l2_consumer import load_policy
from layered_gate import LayeredGate

NOW = 1_000_000.0

EXPECTED_CONDITIONS = ("consumer_heartbeat", "exactly_once_canary",
                       "k3_model_pinned", "validator_enabled",
                       "statemachine_schema", "rollback_key",
                       "default_adapter", "meter_v2_fresh", "plan_pipeline",
                       "provider_health", "lifecycle_roster",
                       "rollback_rehearsal", "dual_plane_hash")


def _gate(root, policy=None, canary_ok=True, clock=None):
    policy = policy if policy is not None else load_policy(
        root / "config" / "orchestration_policy_v2.toml")
    return LayeredGate(
        root, policy=policy,
        canary_runner=lambda: SimpleNamespace(ok=canary_ok,
                                              detail="injected canary"),
        clock=clock or time.time)


@pytest.fixture
def green(tmp_path):
    root = make_root(tmp_path)
    green_guards(root)
    return root


# ---------------------------------------------------------------------------
# all six conditions checked
# ---------------------------------------------------------------------------
def test_all_conditions_evaluated(green):
    gate = _gate(green)
    result = gate.check_all()
    names = tuple(c.name for c in result.conditions)
    assert names == EXPECTED_CONDITIONS, "all guards, in declared order"
    assert len(result.conditions) == len(EXPECTED_CONDITIONS)


def test_all_pass_enables_layered_mode(green):
    gate = _gate(green)
    result = gate.enable()
    assert result.allow and result.failed == ()
    marker = json.loads((green / "data" / "governor" /
                         "layered_authorization.json").read_text())
    assert marker["authorized_mode"] == "layered"
    assert marker["policy_sha256"] == hashlib.sha256(
        (green / "config" / "orchestration_policy_v2.toml").read_bytes()
    ).hexdigest()
    assert 'mode = "layered"' in (green / "config" /
        "orchestration_policy_v2.toml").read_text(encoding="utf-8")
    log = [json.loads(l) for l in gate.log_path.read_text().splitlines()]
    assert any(e["event"] == "layered_mode_authorized" for e in log)


def test_check_never_short_circuits(tmp_path):
    """Every condition runs even after a failure so ONE pass names every
    broken precondition (actionable remediation)."""
    root = make_root(tmp_path)  # nothing green at all
    gate = _gate(root, canary_ok=False)
    result = gate.check_all()
    assert len(result.conditions) == len(EXPECTED_CONDITIONS)
    assert "consumer_heartbeat" in result.failed
    assert "exactly_once_canary" in result.failed


# ---------------------------------------------------------------------------
# any failure refuses layered mode
# ---------------------------------------------------------------------------
def test_stale_heartbeat_refuses(green):
    policy = load_policy(green / "config" / "orchestration_policy_v2.toml")
    max_age = policy["l2_queue"]["consumer_heartbeat_max_age_s"]
    hb = green / "data" / "l2_queue" / "consumer_heartbeat.json"
    hb.write_text(json.dumps({"ts": NOW - max_age - 1}))
    gate = _gate(green, policy=policy, clock=lambda: NOW)
    result = gate.enable()
    assert not result.allow and "consumer_heartbeat" in result.failed
    log = gate.log_path.read_text()
    assert "layered_mode_refused" in log


def test_missing_heartbeat_refuses(green):
    (green / "data" / "l2_queue" / "consumer_heartbeat.json").unlink()
    result = _gate(green).check_all()
    assert not result.allow and "consumer_heartbeat" in result.failed


def test_failed_canary_refuses(green):
    result = _gate(green, canary_ok=False).check_all()
    assert not result.allow and result.failed == ("exactly_once_canary",)


def test_canary_exception_refuses(green):
    policy = load_policy(green / "config" / "orchestration_policy_v2.toml")

    def exploding():
        raise RuntimeError("canary infra down")

    gate = LayeredGate(green, policy=policy, canary_runner=exploding)
    result = gate.check_all()
    assert "exactly_once_canary" in result.failed


def test_unpinned_k3_model_refuses(green):
    policy = dict(load_policy(green / "config" /
                              "orchestration_policy_v2.toml"))
    policy["models"] = {**policy["models"], "k3_model": "  "}
    result = _gate(green, policy=policy).check_all()
    assert "k3_model_pinned" in result.failed


def test_validator_disabled_refuses(green):
    policy = dict(load_policy(green / "config" /
                              "orchestration_policy_v2.toml"))
    policy["validator"] = {"enabled": False}
    result = _gate(green, policy=policy).check_all()
    assert result.failed == ("validator_enabled",)


def test_missing_schema_file_refuses(green):
    (green / "config" / "statemachine_v2_transitions.json").unlink()
    result = _gate(green).check_all()
    assert "statemachine_schema" in result.failed


def test_incomplete_transition_manifest_refuses(green):
    schema_path = green / "config" / "statemachine_v2_transitions.json"
    manifest = json.loads(schema_path.read_text())
    del manifest["transitions"]["t37"]
    schema_path.write_text(json.dumps(manifest))
    result = _gate(green).check_all()
    assert "statemachine_schema" in result.failed
    detail = {c.name: c.detail for c in result.conditions}
    assert "t37" in detail["statemachine_schema"], "names the missing edge"


def test_sol_adjudicate_terminal_refuses(green):
    schema_path = green / "config" / "statemachine_v2_transitions.json"
    manifest = json.loads(schema_path.read_text())
    manifest["terminal_states"].append("SOL_ADJUDICATE")
    schema_path.write_text(json.dumps(manifest))
    result = _gate(green).check_all()
    assert "statemachine_schema" in result.failed


def test_missing_rollback_key_refuses(green):
    policy = dict(load_policy(green / "config" /
                              "orchestration_policy_v2.toml"))
    policy["routing"] = {**policy.get("routing", {}), "rollback_mode": ""}
    result = _gate(green, policy=policy).check_all()
    assert result.failed == ("rollback_key",)


def test_wrong_rollback_mode_refuses(green):
    policy = dict(load_policy(green / "config" /
                              "orchestration_policy_v2.toml"))
    policy["routing"] = {**policy.get("routing", {}),
                         "rollback_mode": "shadow"}
    result = _gate(green, policy=policy).check_all()
    assert "rollback_key" in result.failed, \
        "the single-key rollback target must be cold_start"


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------
def test_every_check_logged_pass_and_fail(green):
    gate = _gate(green)
    gate.check_all()                              # green pass
    (green / "data" / "l2_queue" / "consumer_heartbeat.json").unlink()
    gate.check_all()                              # now failing
    entries = [json.loads(l) for l in gate.log_path.read_text().splitlines()]
    checks = [e for e in entries if e["event"] == "gate_check"]
    assert len(checks) == 2
    assert checks[0]["allow"] is True and checks[1]["allow"] is False
    assert all(len(c["conditions"]) == len(EXPECTED_CONDITIONS) for c in checks), \
        "a gate without a proof of firing is prose (V10 doctrine)"
    assert checks[1]["failed"] == ["consumer_heartbeat"]


def test_policy_skipped_condition_is_logged_as_skipped(green):
    policy = dict(load_policy(green / "config" /
                              "orchestration_policy_v2.toml"))
    policy["gate_guard"] = {**policy.get("gate_guard", {}),
                            "require_exactly_once_canary": False}
    gate = _gate(green, policy=policy, canary_ok=False)
    result = gate.check_all()
    canary = [c for c in result.conditions
              if c.name == "exactly_once_canary"][0]
    assert canary.ok and "SKIPPED" in canary.detail, \
        "an operator skip is possible but permanently on the record"


def test_live_canary_integration(green):
    """Without an injected canary the gate runs the REAL exactly-once canary."""
    policy = load_policy(green / "config" / "orchestration_policy_v2.toml")
    gate = LayeredGate(green, policy=policy)
    result = gate.check_all()
    canary = [c for c in result.conditions
              if c.name == "exactly_once_canary"][0]
    assert canary.ok, canary.detail
