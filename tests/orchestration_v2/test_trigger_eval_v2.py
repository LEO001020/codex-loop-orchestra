"""test_trigger_eval_v2.py — three-mode trigger routing (P0-1 fix).

Covers: cold_start/shadow/layered routing, NO default direct_l3 upgrade in
layered mode, send_l2 producing idempotent l2_queue records, and the
deterministic 10 % sampled verification.
"""
from __future__ import annotations

import json

import pytest

from tests.orchestration_v2.conftest import green_guards, make_root, set_routing_mode

from orchestration_common import LoopPaths, OrchestrationPolicy, idem_key
from trigger_eval_v2 import (
    HIGH_RISK_PATH_RE,
    SEVERITY,
    apply_routing_mode,
    bump_l3_counter,
    derive,
    evaluate_table,
    sampled_for_verification,
)


@pytest.fixture
def env(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    return root, paths


def _policy(paths):
    return OrchestrationPolicy.load(paths)


def _find_pid(sampled: bool, rate: float = 0.10) -> str:
    """Deterministically find a packet id that is (not) hash-sampled."""
    for i in range(1000):
        pid = "pkt-%04d" % i
        if sampled_for_verification(pid, rate) is sampled:
            return pid
    raise AssertionError("no candidate pid found")


# ---------------------------------------------------------------------------
# three-mode routing
# ---------------------------------------------------------------------------
def test_cold_start_upgrades_to_direct_l3(env):
    root, paths = env
    action, mode, effects = apply_routing_mode(
        "pass", "p1", "run-1", 0, paths, _policy(paths))
    assert (action, mode) == ("direct_l3", "cold_start")
    assert effects == {}, "cold_start is byte-identical legacy behavior"


def test_cold_start_preserves_l3_l4(env):
    root, paths = env
    for raw in ("direct_l3", "direct_l4"):
        action, _, _ = apply_routing_mode(raw, "p1", "run-1", 0, paths,
                                          _policy(paths))
        assert action == raw


def test_shadow_executes_cold_start_and_logs(env):
    root, paths = env
    set_routing_mode(root, "shadow")
    action, mode, _ = apply_routing_mode(
        "send_l2", "p1", "run-1", 0, paths, _policy(paths),
        rules_hit=["exit_flapping"])
    assert (action, mode) == ("direct_l3", "shadow")
    entry = json.loads((paths.router_dir / "shadow_log.ndjsonl"
                        ).read_text().splitlines()[0])
    assert entry["would_execute"] == "send_l2"
    assert entry["executed_as"] == "direct_l3"


def test_layered_no_default_direct_l3_upgrade(env):
    """THE P0-1 fix: in layered mode the table verdict stands."""
    root, paths = env
    set_routing_mode(root, "layered")
    green_guards(root)
    pid = _find_pid(sampled=False)
    action, mode, _ = apply_routing_mode("pass", pid, "run-1", 0, paths,
                                         _policy(paths))
    assert (action, mode) == ("pass", "layered"), \
        "a healthy unsampled pass NEVER upgrades to Sol"


def test_layered_explicit_l3_still_routes_l3(env):
    root, paths = env
    set_routing_mode(root, "layered")
    green_guards(root)
    action, _, _ = apply_routing_mode("direct_l3", "p1", "run-1", 0, paths,
                                      _policy(paths))
    assert action == "direct_l3"


def test_layered_without_guards_downgrades_to_shadow(env):
    root, paths = env
    set_routing_mode(root, "layered")  # guards NOT green
    action, mode, _ = apply_routing_mode("pass", _find_pid(False), "run-1",
                                         0, paths, _policy(paths))
    assert mode == "shadow" and action == "direct_l3", \
        "no consumer => observe, never actuate"


# ---------------------------------------------------------------------------
# send_l2 -> idempotent l2_queue records
# ---------------------------------------------------------------------------
def test_send_l2_appends_pending_record(env):
    root, paths = env
    set_routing_mode(root, "layered")
    green_guards(root)
    action, _, effects = apply_routing_mode("send_l2", "p1", "run-9", 2,
                                            paths, _policy(paths))
    assert action == "send_l2"
    key = idem_key("l2req", "p1", "run-9", "2")
    assert effects["l2_record"] == key
    record = json.loads(paths.l2_pending.read_text().splitlines()[0])
    assert record["idem_key"] == key
    assert record["packet_id"] == "p1" and record["attempt"] == 2


def test_send_l2_rerun_is_idempotent(env):
    """Re-running the evaluator produces 0 new records (§2.1 AC2)."""
    root, paths = env
    set_routing_mode(root, "layered")
    green_guards(root)
    policy = _policy(paths)
    apply_routing_mode("send_l2", "p1", "run-9", 2, paths, policy)
    _, _, effects = apply_routing_mode("send_l2", "p1", "run-9", 2, paths,
                                       policy)
    assert "l2_record_duplicate_suppressed" in effects
    assert len(paths.l2_pending.read_text().splitlines()) == 1


def test_new_attempt_is_new_record(env):
    root, paths = env
    set_routing_mode(root, "layered")
    green_guards(root)
    policy = _policy(paths)
    apply_routing_mode("send_l2", "p1", "run-9", 1, paths, policy)
    apply_routing_mode("send_l2", "p1", "run-9", 2, paths, policy)
    assert len(paths.l2_pending.read_text().splitlines()) == 2


# ---------------------------------------------------------------------------
# 10% sampled verification (deterministic)
# ---------------------------------------------------------------------------
def test_sampling_is_deterministic():
    for pid in ("a", "b", "wave-3-packet-7"):
        assert sampled_for_verification(pid, 0.10) == \
            sampled_for_verification(pid, 0.10), "replays reproduce exactly"


def test_sampling_rate_bounds():
    assert sampled_for_verification("anything", 0.0) is False
    assert sampled_for_verification("anything", 1.0) is True


def test_sampling_rate_close_to_configured():
    hits = sum(sampled_for_verification("pkt-%d" % i, 0.10)
               for i in range(2000))
    assert 120 <= hits <= 280, "empirical rate near 10% (deterministic set)"


def test_sampled_pass_becomes_send_l2(env):
    root, paths = env
    set_routing_mode(root, "layered")
    green_guards(root)
    policy = _policy(paths)
    pid = _find_pid(sampled=True, rate=policy.verify_sample_rate())
    action, _, effects = apply_routing_mode("pass", pid, "run-1", 0, paths,
                                            policy)
    assert action == "send_l2" and effects.get("sampled") is True
    assert paths.l2_pending.exists(), "the K3 verification floor is demand-backed"


# ---------------------------------------------------------------------------
# preserved rails
# ---------------------------------------------------------------------------
def test_high_risk_path_rail_hardcoded():
    d = derive({"paths_touched": [".github/workflows/deploy.yml"],
                "exit_codes": [0]})
    action, rules, high_risk, _ = evaluate_table(d, {"partitions": {}})
    assert high_risk and action == "direct_l3"
    assert "HARDCODED-high-risk-path" in rules


@pytest.mark.parametrize("path", [
    "db/migrations/001.sql", ".env", "secrets/prod.yaml", ".ssh/id_rsa",
    ".github/workflows/x.yml", "Jenkinsfile", "hooks/pre_tool.py",
    "AGENTS.md"])
def test_high_risk_regex_coverage(path):
    assert HIGH_RISK_PATH_RE.search(path), path


def test_off_table_defaults_fail_visible(env):
    d = derive({"exit_codes": []})
    action, rules, _, _ = evaluate_table(
        d, {"default_action": "send_l2", "partitions": {}})
    assert action == "send_l2"
    assert "OFF_TABLE-default" in rules, "never a silent pass"


def test_severity_max_wins():
    d = derive({"exit_codes": [1, 1], "retry_count": 0})
    table = {"default_action": "send_l2", "partitions": {"p": {"rules": [
        {"name": "retry_none", "action": "pass", "priority": 1},
        {"name": "exit_persistent_failure", "action": "send_l2",
         "priority": 2},
    ]}}}
    action, rules, _, _ = evaluate_table(d, table)
    assert action == "send_l2", "the most severe hit wins"
    assert set(rules) == {"retry_none", "exit_persistent_failure"}
    assert SEVERITY["send_l2"] > SEVERITY["pass"]


def test_l3_cap_counter_fails_toward_human(tmp_path):
    counters = tmp_path / "l3_counters.json"
    for expected in (1, 2):
        count, exceeded = bump_l3_counter(counters, "p1", cap=2)
        assert (count, exceeded) == (expected, False)
    count, exceeded = bump_l3_counter(counters, "p1", cap=2)
    assert (count, exceeded) == (3, True), "over cap => direct_l4 human gate"


def test_l3_counter_io_failure_counts_as_exceeded(tmp_path):
    unwritable = tmp_path / "no_dir_here" / "x" / "l3.json"
    unwritable.parent.parent.mkdir()
    (tmp_path / "no_dir_here" / "x").write_text("a file, not a dir")
    count, exceeded = bump_l3_counter(unwritable, "p1", cap=2)
    assert exceeded, "counter I/O failure fails toward L4, never unlimited L3"
