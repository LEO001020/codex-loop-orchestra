#!/usr/bin/env python3
# ============================================================================
# trigger_eval.py — L1 LLM-free trigger evaluator (zero-token tier routing)
# ----------------------------------------------------------------------------
# Purpose : Consumes purely mechanical signals from L0 output + execution
#           logs and tiers the action for a packet — SIEVE-style
#           deterministic first layer, no LLM consulted. Evaluates the two
#           partitions of config/triggers.yaml (EK ~30 rules + duty officer
#           4 rules): each named rule maps to a deterministic predicate
#           registered below; action/priority/high_risk are read from the
#           table. Signals: exit code sequence, retry count, diff lines vs
#           packet budget, path boundary attempts, test count change
#           direction, high-risk path hits (migration/credential/CI/
#           deletion), observation length >3000 chars, loop fingerprint
#           (same command repeated >=5).
#           Action tiers (severity): direct_l3 > send_l2 >
#           spawn_duty_officer > annotated_pass > pass.
#           HARDCODED power semantics (S5): (1) L1 can only block or
#           escalate, NEVER release — "pass" only forwards to the serial
#           merge queue behind mechanical acceptance + release gate.
#           (2) high_risk rules and the built-in high-risk path regex are
#           DETERMINISTIC direct_l3, non-overridable by any table edit or
#           LLM verdict (LLM judgment is input, never classifier).
#           (3) Off-table situation (no rule hit at all) -> default_action
#           send_l2, fail-visible, never silent. (4) F2 cold start
#           (passthrough_enabled=false): pass/annotated_pass/send_l2 are
#           upgraded to direct_l3 so every packet reaches Sol; the raw
#           action is still logged to escalation_log.jsonl (calibration).
# Input   : --signals signals JSON:
#             {"packet_id", "exit_codes":[...], "retry_count":N,
#              "run_level_budget":N, "diff_lines":N, "deleted_lines":N,
#              "diff_budget":N, "path_boundary_attempts":N,
#              "boundary_probe_in_log":bool, "test_count_before":N,
#              "test_count_after":N, "min_test_count":N,
#              "requires_new_tests":bool, "test_assertions_modified":bool,
#              "file_deletions":N, "paths_touched":[...],
#              "observation_lengths":[...], "command_history":[...],
#              "consecutive_same_type_failures":bool,
#              "retry_class_no_match":bool, "prior_ruling_confidence":F,
#              "evidence_missing":bool}
#           --triggers config/triggers.yaml (embedded fallback if missing)
#           --passthrough-enabled true|false (optional; when OMITTED the
#              default is read from config/config.toml key
#              [escalation] passthrough_enabled — the F2 single-key switch.
#              CLI value, when given, takes precedence over the config file;
#              missing key/file falls back to false = F2 cold start.)
#           --ladder config/escalation_ladder.yaml (L3 per_packet_call_cap /
#              on_cap_exceeded; missing file falls back to cap=2 -> L4)
#           --counters l3_counters.json (per-packet L3 call counter state;
#              default: l3_counters.json next to --log)
#           --log escalation_log.jsonl (append)
# Output  : stdout one-line JSON {"packet_id","action","raw_action",
#              "rules_hit","duty_officer_hit"}; exit 0 = evaluated, 2 = usage
#           L3 cap (spec §3.5): each packet gets at most per_packet_call_cap
#           direct_l3 routings; the next one is FORCED to direct_l4 (human
#           release gate) — escalation only, never a release path.
# Lines   : 302 (over ~100 target: carries per-rule predicates for the
#           full 34-rule table so the YAML stays declarative, plus the L3
#           per-packet cap counter and the passthrough config-key wiring)
# ============================================================================
import argparse
import json
import os
import re
import sys
import time
from loop_config import config_bool

try:
    import yaml
except ImportError:
    yaml = None

ROOT = os.environ.get("LOOP_ROOT",
                      os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SEVERITY = {"pass": 0, "annotated_pass": 1, "spawn_duty_officer": 2,
            "send_l2": 3, "direct_l3": 4, "direct_l4": 5}
HIGH_RISK_PATH_RE = re.compile(
    r"(migrations?/|schema/|\.sql$|\.env|secrets|credentials|\.aws/|\.ssh/|"
    r"auth\.json|\.github/workflows/|\.gitlab-ci|Jenkinsfile|\.circleci/|"
    r"hooks/|AGENTS\.md)", re.IGNORECASE)


def derive(s):
    """Fold raw signal JSON into the values predicates consume."""
    cmds = s.get("command_history", [])
    counts = {}
    for c in cmds:
        counts[c] = counts.get(c, 0) + 1
    obs = s.get("observation_lengths",
                [s["observation_length"]] if "observation_length" in s else [])
    d = dict(s)
    d.setdefault("exit_codes", [])
    d.setdefault("retry_count", 0)
    d.setdefault("run_level_budget", 3)
    d.setdefault("diff_lines", 0)
    d.setdefault("deleted_lines", 0)
    d.setdefault("diff_budget", None)
    d.setdefault("path_boundary_attempts", 0)
    d.setdefault("test_count_before", None)
    d.setdefault("test_count_after", None)
    d.setdefault("min_test_count", None)
    d["loop_max"] = max(counts.values()) if counts else 0
    d["obs_over_3000"] = sum(1 for o in obs if o > 3000)
    d["high_risk_path_hits"] = sorted(
        p for p in d.get("paths_touched", []) if HIGH_RISK_PATH_RE.search(str(p)))
    ec = d["exit_codes"]
    d["flapping"] = (len(ec) >= 3 and
                     sum(1 for i in range(1, len(ec))
                         if (ec[i] == 0) != (ec[i - 1] == 0)) >= 2)
    return d


def _num(d, *keys):
    return all(d.get(k) is not None for k in keys)


def read_toggle(section, key, default=False):
    return config_bool(section, key, default)


def load_l3_cap(ladder_path):
    """Read levels.L3.per_packet_call_cap / on_cap_exceeded from the ladder
    yaml. Missing file/keys fall back to the spec defaults (cap=2 -> L4):
    the cap is a hard ceiling either way — fail toward the human gate."""
    cap, target = 2, "L4"
    try:
        with open(ladder_path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) if yaml else {}
        l3 = (doc or {}).get("levels", {}).get("L3", {})
        cap = int(l3.get("per_packet_call_cap", cap))
        target = str(l3.get("on_cap_exceeded", target))
    except (OSError, ValueError, AttributeError):
        pass  # defaults above; never silently disable the cap
    return cap, target


def bump_l3_counter(counters_path, pid, cap):
    """Increment the per-packet L3 call counter (state on disk). Returns
    (count_after, exceeded). A counter-file I/O failure counts as exceeded
    — fail toward L4 (human), never toward unlimited L3."""
    try:
        counters = json.load(open(counters_path, encoding="utf-8")) \
            if os.path.exists(counters_path) else {}
    except (OSError, ValueError):
        return cap + 1, True  # unreadable state: assume over-cap, go L4
    count = int(counters.get(pid, 0)) + 1
    counters[pid] = count
    try:
        tmp = counters_path + ".tmp"
        json.dump(counters, open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, counters_path)
    except OSError:
        return cap + 1, True  # cannot persist: assume over-cap, go L4
    return count, count > cap


PREDICATES = {
    # --- exit code sequence family ------------------------------------------
    "exit_clean_single_run": lambda d: d["exit_codes"] == [0],
    "exit_nonzero_then_clean": lambda d: (len(d["exit_codes"]) >= 2 and
        d["exit_codes"][-1] == 0 and any(c != 0 for c in d["exit_codes"][:-1])),
    "exit_flapping": lambda d: d["flapping"],
    "exit_signal_kill": lambda d: any(c in (137, 139, 143)
                                      for c in d["exit_codes"]),
    "exit_persistent_failure": lambda d: (len(d["exit_codes"]) >= 2 and
        d["exit_codes"][-1] != 0 and d["exit_codes"][-1] == d["exit_codes"][-2]),
    # --- retry count family ---------------------------------------------------
    "retry_none": lambda d: d["retry_count"] == 0,
    "retry_single": lambda d: d["retry_count"] == 1,
    "retry_double": lambda d: d["retry_count"] == 2,
    "retry_exhausted": lambda d: d["retry_count"] >= d["run_level_budget"],
    # --- diff lines vs budget family -------------------------------------------
    "diff_within_budget": lambda d: _num(d, "diff_budget") and
        0 < d["diff_lines"] <= d["diff_budget"],
    "diff_near_budget": lambda d: _num(d, "diff_budget") and
        d["diff_budget"] < d["diff_lines"] <= 1.2 * d["diff_budget"],
    "diff_over_budget": lambda d: _num(d, "diff_budget") and
        1.2 * d["diff_budget"] < d["diff_lines"] <= 2 * d["diff_budget"],
    "diff_runaway": lambda d: _num(d, "diff_budget") and
        d["diff_lines"] > 2 * d["diff_budget"],
    "diff_empty": lambda d: d["diff_lines"] == 0,
    # --- path boundary family ---------------------------------------------------
    "path_boundary_attempt": lambda d: d["path_boundary_attempts"] > 0,
    "path_boundary_probe_in_log": lambda d: bool(d.get("boundary_probe_in_log")),
    # --- test count family --------------------------------------------------------
    "test_count_increased": lambda d: _num(d, "test_count_before",
        "test_count_after") and d["test_count_after"] > d["test_count_before"],
    "test_count_unchanged": lambda d: _num(d, "test_count_before",
        "test_count_after") and d["test_count_after"] == d["test_count_before"]
        and bool(d.get("requires_new_tests")),
    "test_count_decreased": lambda d: _num(d, "test_count_before",
        "test_count_after") and d["test_count_after"] < d["test_count_before"],
    "test_assertions_modified": lambda d: bool(d.get("test_assertions_modified")),
    "test_below_min_count": lambda d: _num(d, "min_test_count",
        "test_count_after") and d["test_count_after"] < d["min_test_count"],
    # --- high-risk path family (also HARDCODED below) ----------------------------
    "high_risk_migration_path": lambda d: any(re.search(
        r"(migrations?/|schema/|\.sql$)", p, re.I) for p in d.get("paths_touched", [])),
    "high_risk_credential_path": lambda d: any(re.search(
        r"(\.env|secrets|credentials|\.aws/|\.ssh/|auth\.json)", p, re.I)
        for p in d.get("paths_touched", [])),
    "high_risk_ci_path": lambda d: any(re.search(
        r"(\.github/workflows/|\.gitlab-ci|Jenkinsfile|\.circleci/)", p, re.I)
        for p in d.get("paths_touched", [])),
    "high_risk_mass_deletion": lambda d: d["deleted_lines"] > 200 or
        d.get("file_deletions", 0) > 0,
    "high_risk_hook_or_config": lambda d: any(re.search(
        r"(hooks/|config/.*\.(toml|yaml)|AGENTS\.md)", p, re.I)
        for p in d.get("paths_touched", [])),
    # --- observation length family --------------------------------------------------
    "observation_oversize": lambda d: d["obs_over_3000"] >= 1,
    "observation_oversize_repeated": lambda d: d["obs_over_3000"] >= 3,
    # --- loop fingerprint family --------------------------------------------------
    "loop_fingerprint_warn": lambda d: 3 <= d["loop_max"] <= 4,
    "loop_fingerprint_hit": lambda d: d["loop_max"] >= 5,
    "loop_fingerprint_hard": lambda d: d["loop_max"] >= 10,
    # --- duty officer partition ------------------------------------------------------
    "consecutive_same_type_failures":
        lambda d: bool(d.get("consecutive_same_type_failures")),
    "retry_regex_no_match": lambda d: bool(d.get("retry_class_no_match")),
    "low_confidence_ruling": lambda d: d.get("prior_ruling_confidence")
        is not None and d["prior_ruling_confidence"] < 0.7,
    "evidence_missing": lambda d: bool(d.get("evidence_missing")),
}


def main():
    # Keep this shipped command as the stable entry.  One policy key selects
    # v2 observation/enforcement; cold_start remains the instant rollback.
    policy_path = os.path.join(ROOT, "config", "orchestration_policy_v2.toml")
    try:
        import tomllib
        with open(policy_path, "rb") as handle:
            routing_mode = str(tomllib.load(handle).get("routing", {}).get(
                "mode", "cold_start"))
    except (OSError, ValueError):
        routing_mode = "cold_start"
    if routing_mode in ("shadow", "layered"):
        from trigger_eval_v2 import main as v2_main
        return v2_main(sys.argv[1:])
    ap = argparse.ArgumentParser(description="L1 trigger evaluator")
    ap.add_argument("--signals", required=True)
    ap.add_argument("--triggers", default="config/triggers.yaml")
    ap.add_argument("--passthrough-enabled", default=None,
                    choices=["true", "false"],
                    help="override config [escalation] passthrough_enabled")
    ap.add_argument("--ladder",
                    default=os.path.join(ROOT, "config", "escalation_ladder.yaml"))
    ap.add_argument("--counters", default=None,
                    help="L3 per-packet counter JSON (default: next to --log)")
    ap.add_argument("--log", default="data/escalation_log.jsonl")
    args = ap.parse_args()
    if args.passthrough_enabled is None:
        # F2 single-key switch: config/config.toml [escalation]
        # passthrough_enabled is the default; CLI wins when given (P-05).
        args.passthrough_enabled = "true" if read_toggle(
            "escalation", "passthrough_enabled", False) else "false"
    try:
        raw = json.load(open(args.signals, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("usage error: signals: %s" % exc, file=sys.stderr)
        sys.exit(2)
    try:
        with open(args.triggers, encoding="utf-8") as fh:
            table = yaml.safe_load(fh) if yaml else json.load(fh)
    except OSError:
        print("warn: %s missing — minimal fail-visible fallback (everything "
              "send_l2)" % args.triggers, file=sys.stderr)
        table = {"default_action": "send_l2", "partitions": {}}

    d = derive(raw)
    default_action = table.get("default_action", "send_l2")
    rules_hit, action, high_risk_hit, duty_hit = [], None, False, False
    parts = table.get("partitions", {})
    rules = sorted((r for p in parts.values() for r in p.get("rules", [])),
                   key=lambda r: r.get("priority", 10**9))
    for rule in rules:
        pred = PREDICATES.get(rule.get("name", ""))
        if pred is None or not pred(d):
            continue
        rules_hit.append(rule.get("name"))
        act = rule.get("action", default_action)
        if rule.get("high_risk"):
            high_risk_hit = True
        if act == "spawn_duty_officer":
            duty_hit = True
        if action is None or SEVERITY.get(act, 3) > SEVERITY.get(action, 0):
            action = act
    # HARDCODED: built-in high-risk path regex — deterministic direct_l3 even
    # if the YAML table was edited to drop the high_risk rules. Never
    # overridable by any table entry or model verdict.
    if d["high_risk_path_hits"]:
        rules_hit.append("HARDCODED-high-risk-path")
        high_risk_hit = True
    if high_risk_hit:
        action = "direct_l3"
    if action is None:  # off-table: no rule matched — fail-visible
        rules_hit.append("OFF_TABLE-default")
        action = default_action
    raw_action = action
    if args.passthrough_enabled == "false" and action != "direct_l3":
        action = "direct_l3"  # F2 cold start: everything reaches Sol (L3)
    # L3 per-packet call cap (ladder levels.L3.per_packet_call_cap, spec
    # §3.5): count every direct_l3 routing per packet; over-cap routings are
    # FORCED to direct_l4 (human release gate). Deterministic, state on disk.
    l3_calls = None
    if action == "direct_l3":
        cap, cap_target = load_l3_cap(args.ladder)
        counters_path = args.counters or os.path.join(
            os.path.dirname(os.path.abspath(args.log)) or ".",
            "l3_counters.json")
        l3_calls, exceeded = bump_l3_counter(
            counters_path, raw.get("packet_id", "?"), cap)
        if exceeded:
            rules_hit.append("L3_CAP_EXCEEDED->%s" % cap_target)
            action = "direct_l4"  # escalation only — never a release path
    out = {"packet_id": raw.get("packet_id", "?"), "action": action,
           "raw_action": raw_action, "rules_hit": rules_hit,
           "duty_officer_hit": duty_hit,
           "high_risk": high_risk_hit,
           "l3_calls": l3_calls,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:  # calibration data accumulates even while passthrough is closed
        with open(args.log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(out) + "\n")
    except OSError as exc:
        print("warn: cannot append escalation log: %s" % exc, file=sys.stderr)
    print(json.dumps({k: out[k] for k in ("packet_id", "action", "raw_action",
                                          "rules_hit", "duty_officer_hit")}))
    sys.exit(0)


if __name__ == "__main__":
    main()
