# ============================================================================
# test_trigger_eval.py — Unit tests for harness/trigger_eval.py (L1 tier)
# Cases: all 35 table rules hit individually (parametrized), off-table
#        default send_l2 (fail-visible), F2 cold start upgrades everything
#        to direct_l3, HARDCODED high-risk path is non-overridable even with
#        a doctored trigger table, escalation log append, usage error.
# ============================================================================
import json

import pytest

from tests.conftest import PY, CONFIG

# Baseline: every family in its healthiest configuration -> all hits are
# pass-severity (exit_clean, retry_none, diff_within_budget, test_count_increased).
BASE = {"packet_id": "p1", "exit_codes": [0], "retry_count": 0,
        "run_level_budget": 6, "diff_lines": 10, "diff_budget": 400,
        "test_count_before": 5, "test_count_after": 6,
        "paths_touched": ["src/mod/a.py"], "command_history": ["pytest -q"],
        "observation_lengths": [100]}


def evaluate(loop, tmp_path, signals, passthrough="true", triggers=None):
    sig = tmp_path / "signals.json"
    sig.write_text(json.dumps(signals))
    log = loop.data / "escalation_log.jsonl"
    p = loop.run([PY, loop.harness("trigger_eval.py"), "--signals", sig,
                  "--triggers", triggers or CONFIG / "triggers.yaml",
                  "--passthrough-enabled", passthrough, "--log", log])
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# (rule_name, signal overrides, expected raw action with passthrough=true)
RULE_CASES = [
    # --- exit code sequence family -------------------------------------------
    ("exit_clean_single_run", {}, "pass"),
    ("exit_nonzero_then_clean", {"exit_codes": [1, 0]}, "annotated_pass"),
    ("exit_flapping", {"exit_codes": [0, 1, 0]}, "send_l2"),
    ("exit_signal_kill", {"exit_codes": [137]}, "send_l2"),
    ("exit_persistent_failure", {"exit_codes": [1, 1]}, "send_l2"),
    # --- retry count family ------------------------------------------------------
    ("retry_none", {}, "pass"),
    ("retry_single", {"retry_count": 1}, "annotated_pass"),
    ("retry_double", {"retry_count": 2}, "send_l2"),
    ("retry_exhausted", {"retry_count": 6}, "direct_l3"),
    # --- diff budget family ---------------------------------------------------------
    ("diff_within_budget", {}, "pass"),
    ("diff_near_budget", {"diff_lines": 450}, "annotated_pass"),
    ("diff_over_budget", {"diff_lines": 700}, "send_l2"),
    ("diff_runaway", {"diff_lines": 900}, "direct_l3"),
    ("diff_empty", {"diff_lines": 0}, "send_l2"),
    # --- path boundary family -----------------------------------------------------
    ("path_boundary_attempt", {"path_boundary_attempts": 1}, "direct_l3"),
    ("path_boundary_probe_in_log", {"boundary_probe_in_log": True}, "send_l2"),
    # --- test count family -------------------------------------------------------------
    ("test_count_increased", {}, "pass"),
    ("test_count_unchanged", {"test_count_after": 5, "requires_new_tests": True},
     "annotated_pass"),
    ("test_count_decreased", {"test_count_after": 4}, "direct_l3"),
    ("test_assertions_modified", {"test_assertions_modified": True}, "direct_l3"),
    ("test_below_min_count", {"min_test_count": 10}, "send_l2"),
    # --- high-risk path family ------------------------------------------------------------
    ("high_risk_migration_path", {"paths_touched": ["migrations/0001_init.sql"]},
     "direct_l3"),
    ("high_risk_credential_path", {"paths_touched": [".aws/credentials"]},
     "direct_l3"),
    ("high_risk_ci_path", {"paths_touched": [".github/workflows/ci.yml"]},
     "direct_l3"),
    ("high_risk_mass_deletion", {"deleted_lines": 300}, "direct_l3"),
    ("high_risk_hook_or_config", {"paths_touched": ["hooks/subagent_stop.sh"]},
     "direct_l3"),
    # --- observation length family -----------------------------------------------------------
    ("observation_oversize", {"observation_lengths": [3500]}, "annotated_pass"),
    ("observation_oversize_repeated", {"observation_lengths": [3500, 4000, 5000]},
     "send_l2"),
    # --- loop fingerprint family ------------------------------------------------------------
    ("loop_fingerprint_warn", {"command_history": ["pytest -q"] * 3},
     "annotated_pass"),
    ("loop_fingerprint_hit", {"command_history": ["pytest -q"] * 5}, "send_l2"),
    ("loop_fingerprint_hard", {"command_history": ["pytest -q"] * 10}, "direct_l3"),
    # --- duty officer partition ------------------------------------------------------------
    ("consecutive_same_type_failures", {"consecutive_same_type_failures": True},
     "spawn_duty_officer"),
    ("retry_regex_no_match", {"retry_class_no_match": True}, "spawn_duty_officer"),
    ("low_confidence_ruling", {"prior_ruling_confidence": 0.5},
     "spawn_duty_officer"),
    ("evidence_missing", {"evidence_missing": True}, "spawn_duty_officer"),
]


@pytest.mark.parametrize("rule,overrides,expected", RULE_CASES,
                         ids=[c[0] for c in RULE_CASES])
def test_each_rule_hits_and_tiers_correctly(loop, tmp_path, rule, overrides, expected):
    signals = dict(BASE, **overrides)
    out = evaluate(loop, tmp_path, signals, passthrough="true")
    assert rule in out["rules_hit"], "expected %s in %s" % (rule, out["rules_hit"])
    assert out["action"] == expected
    assert out["raw_action"] == expected      # passthrough=true: no upgrade
    if expected == "spawn_duty_officer":
        assert out["duty_officer_hit"] is True


def test_off_table_signal_is_fail_visible_send_l2(loop, tmp_path):
    signals = {"packet_id": "p1", "exit_codes": [], "retry_count": 3,
               "run_level_budget": 10, "diff_lines": 5,
               "paths_touched": ["src/a.py"], "command_history": ["a", "b"]}
    out = evaluate(loop, tmp_path, signals, passthrough="true")
    assert out["rules_hit"] == ["OFF_TABLE-default"]
    assert out["action"] == "send_l2"          # default_action, never silent


def test_f2_cold_start_upgrades_everything_to_l3(loop, tmp_path):
    out = evaluate(loop, tmp_path, BASE, passthrough="false")
    assert out["action"] == "direct_l3"        # every packet reaches Sol
    assert out["raw_action"] == "pass"         # calibration data preserved


def test_high_risk_path_not_overridable_by_table_edit(loop, tmp_path):
    # Doctored table: an attacker/model deletes ALL high-risk rules and even
    # remaps the default. The hardcoded path regex must still force direct_l3.
    doctored = {"version": 1, "default_action": "pass",
                "partitions": {"ek": {"rules": [
                    {"name": "exit_clean_single_run", "action": "pass",
                     "priority": 100}]}}}
    tfile = tmp_path / "doctored.yaml"
    tfile.write_text(json.dumps(doctored))     # yaml superset of json
    signals = dict(BASE, paths_touched=["migrations/0002_drop_users.sql"])
    out = evaluate(loop, tmp_path, signals, passthrough="true", triggers=tfile)
    assert out["action"] == "direct_l3"
    assert "HARDCODED-high-risk-path" in out["rules_hit"]


def test_every_evaluation_is_appended_to_escalation_log(loop, tmp_path):
    evaluate(loop, tmp_path, BASE)
    evaluate(loop, tmp_path, dict(BASE, packet_id="p2"))
    rows = loop.escalations()
    assert [r["packet_id"] for r in rows] == ["p1", "p2"]
    assert all("raw_action" in r and "ts" in r for r in rows)


def test_missing_signals_file_is_usage_error(loop, tmp_path):
    p = loop.run([PY, loop.harness("trigger_eval.py"),
                  "--signals", tmp_path / "absent.json"])
    assert p.returncode == 2
