#!/usr/bin/env python3
"""trigger_eval_v2.py — L1 trigger evaluator with three-mode routing (P0-1 fix).

Replaces the boolean cold-start collapse at shipped
``trigger_eval.py:290-291`` — where every non-``direct_l3`` action (including
``send_l2`` and plain ``pass``) was rewritten to ``direct_l3`` — with the
three-mode switch from architecture §2.1:

* ``cold_start`` — legacy upgrade behavior preserved for rollback
  (byte-identical routing);
* ``shadow`` — executes as cold_start, logs the as-if-layered action to the
  router's shadow corpus;
* ``layered`` — the table verdict STANDS: ``pass``/``annotated_pass`` go to
  the mechanical merge queue (no Sol), ``send_l2`` appends a real L2 request
  record to ``data/l2_queue/pending.ndjsonl`` with a semantic idempotency key
  (the actual ``send_l2 → K3`` route, fixing P0-2's demand side), and only
  explicit high-risk / off-table / L2-escalations reach ``direct_l3``.

**Nothing is upgraded to ``direct_l3`` by default in layered mode.**

Preserved non-negotiable rails (all three modes):

* the hardcoded high-risk path regex → ``direct_l3`` (never overridable by
  table edits or model verdicts);
* the L3 per-packet call cap → ``direct_l4`` (human gate);
* off-table (no rule hit) → the table's ``default_action`` (``send_l2``),
  fail-visible, never silent.

New in layered mode: **sampled verification** — a deterministic hash of the
packet id routes ``verify_sample_rate`` (default 10 %) of healthy ``pass``
packets to ``send_l2``, giving K3 a steady demand-backed verification floor
(§2.6.2; reproducible and auditable, never random).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Final, Mapping

try:
    import yaml
except ImportError:  # pragma: no cover - yaml optional, JSON fallback
    yaml = None  # type: ignore[assignment]

try:
    from orchestration_common import (LoopPaths, OrchestrationPolicy, PolicyError,
                                      append_ndjson, atomic_write_json, get_logger,
                                      idem_key, read_json, utc_now)
    from agent_router import AgentRouter, RoutingMode
except ImportError:  # pragma: no cover - direct CLI execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from orchestration_common import (LoopPaths, OrchestrationPolicy, PolicyError,
                                      append_ndjson, atomic_write_json, get_logger,
                                      idem_key, read_json, utc_now)
    from agent_router import AgentRouter, RoutingMode

__all__ = [
    "SEVERITY",
    "PREDICATES",
    "derive",
    "evaluate_table",
    "apply_routing_mode",
    "sampled_for_verification",
    "main",
]

log = get_logger("loop.trigger_eval_v2")

SEVERITY: Final[dict[str, int]] = {
    "pass": 0, "annotated_pass": 1, "spawn_duty_officer": 2,
    "send_l2": 3, "direct_l3": 4, "direct_l4": 5,
}

#: HARDCODED high-risk path rail — deterministic direct_l3 in ALL modes,
#: byte-identical to the shipped regex (trigger_eval.py:79-82).
HIGH_RISK_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"(migrations?/|schema/|\.sql$|\.env|secrets|credentials|\.aws/|\.ssh/|"
    r"auth\.json|\.github/workflows/|\.gitlab-ci|Jenkinsfile|\.circleci/|"
    r"hooks/|AGENTS\.md)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Signal derivation + predicates (preserved verbatim from v1 semantics)
# ---------------------------------------------------------------------------
def derive(signals: Mapping[str, Any]) -> dict[str, Any]:
    """Fold raw signal JSON into the values the predicates consume."""
    cmds = signals.get("command_history", [])
    counts: dict[str, int] = {}
    for cmd in cmds:
        counts[cmd] = counts.get(cmd, 0) + 1
    obs = signals.get("observation_lengths",
                      [signals["observation_length"]]
                      if "observation_length" in signals else [])
    d = dict(signals)
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
        p for p in d.get("paths_touched", [])
        if HIGH_RISK_PATH_RE.search(str(p)))
    ec = d["exit_codes"]
    d["flapping"] = (len(ec) >= 3 and
                     sum(1 for i in range(1, len(ec))
                         if (ec[i] == 0) != (ec[i - 1] == 0)) >= 2)
    return d


def _num(d: Mapping[str, Any], *keys: str) -> bool:
    return all(d.get(k) is not None for k in keys)


PREDICATES: Final[dict[str, Callable[[Mapping[str, Any]], bool]]] = {
    # --- exit code sequence family -----------------------------------------
    "exit_clean_single_run": lambda d: d["exit_codes"] == [0],
    "exit_nonzero_then_clean": lambda d: (
        len(d["exit_codes"]) >= 2 and d["exit_codes"][-1] == 0
        and any(c != 0 for c in d["exit_codes"][:-1])),
    "exit_flapping": lambda d: d["flapping"],
    "exit_signal_kill": lambda d: any(c in (137, 139, 143)
                                      for c in d["exit_codes"]),
    "exit_persistent_failure": lambda d: (
        len(d["exit_codes"]) >= 2 and d["exit_codes"][-1] != 0
        and d["exit_codes"][-1] == d["exit_codes"][-2]),
    # --- retry count family ----------------------------------------------------
    "retry_none": lambda d: d["retry_count"] == 0,
    "retry_single": lambda d: d["retry_count"] == 1,
    "retry_double": lambda d: d["retry_count"] == 2,
    "retry_exhausted": lambda d: d["retry_count"] >= d["run_level_budget"],
    # --- diff lines vs budget family -----------------------------------------------
    "diff_within_budget": lambda d: _num(d, "diff_budget")
        and 0 < d["diff_lines"] <= d["diff_budget"],
    "diff_near_budget": lambda d: _num(d, "diff_budget")
        and d["diff_budget"] < d["diff_lines"] <= 1.2 * d["diff_budget"],
    "diff_over_budget": lambda d: _num(d, "diff_budget")
        and 1.2 * d["diff_budget"] < d["diff_lines"] <= 2 * d["diff_budget"],
    "diff_runaway": lambda d: _num(d, "diff_budget")
        and d["diff_lines"] > 2 * d["diff_budget"],
    "diff_empty": lambda d: d["diff_lines"] == 0,
    # --- path boundary family --------------------------------------------------------
    "path_boundary_attempt": lambda d: d["path_boundary_attempts"] > 0,
    "path_boundary_probe_in_log": lambda d: bool(d.get("boundary_probe_in_log")),
    # --- test count family ---------------------------------------------------------------
    "test_count_increased": lambda d: _num(d, "test_count_before", "test_count_after")
        and d["test_count_after"] > d["test_count_before"],
    "test_count_unchanged": lambda d: _num(d, "test_count_before", "test_count_after")
        and d["test_count_after"] == d["test_count_before"]
        and bool(d.get("requires_new_tests")),
    "test_count_decreased": lambda d: _num(d, "test_count_before", "test_count_after")
        and d["test_count_after"] < d["test_count_before"],
    "test_assertions_modified": lambda d: bool(d.get("test_assertions_modified")),
    "test_below_min_count": lambda d: _num(d, "min_test_count", "test_count_after")
        and d["test_count_after"] < d["min_test_count"],
    # --- high-risk path family (also HARDCODED above) ------------------------------------
    "high_risk_migration_path": lambda d: any(re.search(
        r"(migrations?/|schema/|\.sql$)", p, re.I)
        for p in d.get("paths_touched", [])),
    "high_risk_credential_path": lambda d: any(re.search(
        r"(\.env|secrets|credentials|\.aws/|\.ssh/|auth\.json)", p, re.I)
        for p in d.get("paths_touched", [])),
    "high_risk_ci_path": lambda d: any(re.search(
        r"(\.github/workflows/|\.gitlab-ci|Jenkinsfile|\.circleci/)", p, re.I)
        for p in d.get("paths_touched", [])),
    "high_risk_mass_deletion": lambda d: d["deleted_lines"] > 200
        or d.get("file_deletions", 0) > 0,
    "high_risk_hook_or_config": lambda d: any(re.search(
        r"(hooks/|config/.*\.(toml|yaml)|AGENTS\.md)", p, re.I)
        for p in d.get("paths_touched", [])),
    # --- observation length family ------------------------------------------------------------
    "observation_oversize": lambda d: d["obs_over_3000"] >= 1,
    "observation_oversize_repeated": lambda d: d["obs_over_3000"] >= 3,
    # --- loop fingerprint family ----------------------------------------------------------------
    "loop_fingerprint_warn": lambda d: 3 <= d["loop_max"] <= 4,
    "loop_fingerprint_hit": lambda d: d["loop_max"] >= 5,
    "loop_fingerprint_hard": lambda d: d["loop_max"] >= 10,
    # --- duty officer partition ---------------------------------------------------------------------
    "consecutive_same_type_failures":
        lambda d: bool(d.get("consecutive_same_type_failures")),
    "retry_regex_no_match": lambda d: bool(d.get("retry_class_no_match")),
    "low_confidence_ruling": lambda d: d.get("prior_ruling_confidence") is not None
        and d["prior_ruling_confidence"] < 0.7,
    "evidence_missing": lambda d: bool(d.get("evidence_missing")),
}


# ---------------------------------------------------------------------------
# Table evaluation (severity-max over hits; rails hardcoded)
# ---------------------------------------------------------------------------
def evaluate_table(d: Mapping[str, Any],
                   table: Mapping[str, Any]) -> tuple[str, list[str], bool, bool]:
    """Evaluate the trigger table over derived signals.

    Returns ``(action, rules_hit, high_risk_hit, duty_hit)``.  The action is
    the raw table verdict *before* any mode logic; the hardcoded high-risk
    rail is applied here (non-overridable)."""
    default_action = table.get("default_action", "send_l2")
    parts = table.get("partitions", {})
    rules = sorted((r for p in parts.values() for r in p.get("rules", [])),
                   key=lambda r: r.get("priority", 10 ** 9))
    rules_hit: list[str] = []
    action: str | None = None
    high_risk_hit = duty_hit = False
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
    if d["high_risk_path_hits"]:
        rules_hit.append("HARDCODED-high-risk-path")
        high_risk_hit = True
    if high_risk_hit:
        action = "direct_l3"
    if action is None:  # off-table: fail-visible default, never silent pass
        rules_hit.append("OFF_TABLE-default")
        action = default_action
    return action, rules_hit, high_risk_hit, duty_hit


# ---------------------------------------------------------------------------
# Sampled verification (deterministic; §2.6.2)
# ---------------------------------------------------------------------------
def sampled_for_verification(packet_id: str, rate: float) -> bool:
    """Deterministic hash sampling: reproducible and auditable.

    ``sha256(packet_id)`` mapped to [0,1) < rate.  The same packet id always
    yields the same decision, so a wave replay reproduces the same K3
    verification set exactly (AC2 of §2.6)."""
    if rate <= 0:
        return False
    digest = hashlib.sha256(packet_id.encode("utf-8")).digest()
    fraction = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return fraction < rate


# ---------------------------------------------------------------------------
# The mode switch — the P0-1 fix itself
# ---------------------------------------------------------------------------
def apply_routing_mode(action: str, packet_id: str, run_id: str, attempt: int,
                       paths: LoopPaths, policy: OrchestrationPolicy,
                       router: AgentRouter | None = None,
                       rules_hit: list[str] | None = None) -> tuple[str, str, dict[str, Any]]:
    """Apply the three-mode switch to a raw table verdict.

    Returns ``(effective_action, mode, side_effects)``.  This function
    replaces the shipped lines 290-291; the raw action is always preserved
    for calibration logging by the caller.
    """
    rules_hit = rules_hit or []
    router = router or AgentRouter(paths, policy)
    mode_enum, _ = router.effective_mode()
    mode = mode_enum.value
    side_effects: dict[str, Any] = {}

    if mode == "cold_start":
        # Legacy behavior, preserved for rollback: everything reaches Sol.
        if action not in ("direct_l3", "direct_l4"):
            action = "direct_l3"
        return action, mode, side_effects

    if mode == "shadow":
        # Executed as cold_start; the as-if-layered action feeds the
        # calibration corpus via the router's shadow log.
        append_ndjson(paths.router_dir / "shadow_log.ndjsonl",
                      {"ts": utc_now(), "packet_id": packet_id,
                       "raw_action": action, "rules_hit": rules_hit,
                       "would_execute": action,
                       "executed_as": "direct_l3"
                       if action not in ("direct_l3", "direct_l4") else action})
        if action not in ("direct_l3", "direct_l4"):
            action = "direct_l3"
        return action, mode, side_effects

    # ---- layered: the table verdict STANDS ------------------------------------
    # Sampled verification: a deterministic slice of healthy passes becomes
    # send_l2 — K3's verification floor, demand-backed (§2.6.2).
    if action == "pass" and sampled_for_verification(
            packet_id, policy.verify_sample_rate()):
        rules_hit.append("SAMPLED_VERIFICATION")
        action = "send_l2"
        side_effects["sampled"] = True

    if action == "send_l2":
        # PROPER send_l2: append a real L2 request record for the consumer.
        key = idem_key("l2req", packet_id, run_id, str(attempt))
        now = utc_now()
        record = {"ts": now, "created_ts": now,
                  "reason": (rules_hit[-1] if rules_hit else "send_l2"),
                  "packet_id": packet_id, "run_id": run_id,
                  "attempt": attempt, "idem_key": key,
                  "rules_hit": rules_hit[-8:]}
        if not _l2_record_exists(paths, key):
            append_ndjson(paths.l2_pending, record)
            side_effects["l2_record"] = key
        else:
            side_effects["l2_record_duplicate_suppressed"] = key
    # pass / annotated_pass -> mechanical acceptance + serial merge queue
    # (zero Sol events); spawn_duty_officer -> duty path (enforce=true);
    # direct_l3 -> only explicit high-risk + off-table + L2 escalations.
    return action, mode, side_effects


def _l2_record_exists(paths: LoopPaths, key: str) -> bool:
    """Idempotency: re-runs must not duplicate L2 queue records (AC2)."""
    pending = paths.l2_pending
    if not pending.exists():
        return False
    try:
        with pending.open(encoding="utf-8") as handle:
            return any(key in line for line in handle)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# L3 cap (preserved rail)
# ---------------------------------------------------------------------------
def load_l3_cap(ladder_path: Path) -> tuple[int, str]:
    """levels.L3.per_packet_call_cap / on_cap_exceeded from the ladder yaml;
    missing file/keys fall back to spec defaults (cap=2 -> L4)."""
    cap, target = 2, "L4"
    try:
        with ladder_path.open(encoding="utf-8") as handle:
            doc = yaml.safe_load(handle) if yaml else {}
        l3 = (doc or {}).get("levels", {}).get("L3", {})
        cap = int(l3.get("per_packet_call_cap", cap))
        target = str(l3.get("on_cap_exceeded", target))
    except (OSError, ValueError, AttributeError):
        pass  # defaults above; never silently disable the cap
    return cap, target


def bump_l3_counter(counters_path: Path, pid: str, cap: int) -> tuple[int, bool]:
    """Per-packet L3 counter.  Counter I/O failure counts as exceeded —
    fail toward L4 (human), never toward unlimited L3."""
    try:
        counters = read_json(counters_path, {}) or {}
        count = int(counters.get(pid, 0)) + 1
        counters[pid] = count
        atomic_write_json(counters_path, counters)
    except (OSError, ValueError, TypeError):
        return cap + 1, True
    return count, count > cap


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="L1 trigger evaluator v2 (three-mode routing)")
    ap.add_argument("--signals", required=True, help="signals JSON path")
    ap.add_argument("--triggers", default=None,
                    help="triggers.yaml path (default: config/triggers.yaml)")
    ap.add_argument("--ladder", default=None,
                    help="escalation_ladder.yaml path")
    ap.add_argument("--counters", default=None,
                    help="L3 per-packet counter JSON (default: next to --log)")
    ap.add_argument("--log", default=None,
                    help="escalation log path (default: data/escalation_log.jsonl)")
    ap.add_argument("--run-id", default="manual")
    ap.add_argument("--attempt", type=int, default=0)
    args = ap.parse_args(argv)

    paths = LoopPaths.resolve()
    try:
        policy = OrchestrationPolicy.load(paths)
    except PolicyError as exc:
        print(f"usage error: policy: {exc}", file=sys.stderr)
        return 2
    try:
        raw = json.loads(Path(args.signals).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"usage error: signals: {exc}", file=sys.stderr)
        return 2

    triggers_path = Path(args.triggers) if args.triggers \
        else paths.config / "triggers.yaml"
    try:
        with triggers_path.open(encoding="utf-8") as handle:
            table = yaml.safe_load(handle) if yaml else json.load(handle)
    except OSError:
        print(f"warn: {triggers_path} missing — minimal fail-visible fallback "
              f"(everything send_l2)", file=sys.stderr)
        table = {"default_action": "send_l2", "partitions": {}}

    d = derive(raw)
    pid = raw.get("packet_id", "?")
    action, rules_hit, high_risk, duty_hit = evaluate_table(d, table)
    raw_action = action

    action, mode, side_effects = apply_routing_mode(
        action, pid, args.run_id, args.attempt, paths, policy,
        rules_hit=rules_hit)

    # L3 per-packet call cap (preserved rail): over-cap => direct_l4.
    l3_calls = None
    if action == "direct_l3":
        ladder_path = Path(args.ladder) if args.ladder \
            else paths.config / "escalation_ladder.yaml"
        cap, cap_target = load_l3_cap(ladder_path)
        log_path = Path(args.log) if args.log \
            else paths.data / "escalation_log.jsonl"
        counters_path = Path(args.counters) if args.counters \
            else log_path.parent / "l3_counters.json"
        l3_calls, exceeded = bump_l3_counter(counters_path, pid, cap)
        if exceeded:
            rules_hit.append(f"L3_CAP_EXCEEDED->{cap_target}")
            action = "direct_l4"  # escalation only — never a release path

    out = {"packet_id": pid, "action": action, "raw_action": raw_action,
           "mode": mode, "rules_hit": rules_hit, "duty_officer_hit": duty_hit,
           "high_risk": high_risk, "l3_calls": l3_calls,
           "side_effects": side_effects,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    log_path = Path(args.log) if args.log else paths.data / "escalation_log.jsonl"
    try:  # calibration data accumulates in every mode
        append_ndjson(log_path, out)
    except OSError as exc:
        print(f"warn: cannot append escalation log: {exc}", file=sys.stderr)
    print(json.dumps({k: out[k] for k in
                      ("packet_id", "action", "raw_action", "mode",
                       "rules_hit", "duty_officer_hit")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
