#!/usr/bin/env python3
# ============================================================================
# usage_reconcile.py — weekly usage reconciliation (fail-visible hedge #4)
# Purpose : reconcile expected agent spawns (dispatched events written by
#           harness/dispatch.py) against actual SubagentStart metering hook
#           lines (hooks/subagent_start_meter.sh) and the E0-annotated
#           sessions. Detects:
#             - missing metering  (spawn expected, no hook event)
#             - duplicate metering (same agent/turn metered twice)
#             - routing anomalies  (role running on the wrong model tier —
#               the 25x input-price differential between gpt-5.6 ($5/MTok)
#               and the L1/L2/Sol route split makes the model field a
#               NON-FORGEABLE routing health signal: a mis-routed worker
#               shows up in real usage at 25x cost, spec §10 S1/S4)
#             - reviewer-bucket drift (reviewer tokens in sessions without a
#               reviewer SubagentStart, or >1 reviewer spawn per wave)
#             - Sol token-share budget breach (P1-5: optional --rollout-dir
#               invokes metering/model_token_share.py so the weekly
#               reconciliation carries the share_total/share_effective/
#               share_output windows; WARNING > 20% and BLOCK > 25% become
#               reconciliation discrepancies)
# Input   : --events data/events.ndjson  --sessions annotated.jsonl (from
#           e0_annotate.py), optional --rollout-dir ~/.codex/sessions.
#           Malformed lines counted and skipped.
# Output  : --output reconciliation_report.json (discrepancies flagged,
#           token_share block embedded when --rollout-dir is given)
# Exit    : 0 clean / 1 discrepancies found (fail-visible) / 2 usage error
# Lines   : ~160
# Token ownership: zero token — pure offline disk pipeline.
# ============================================================================
import argparse, json, sys, tomllib
from pathlib import Path
from collections import Counter

# Expected model per role (must agree with config/roles.yaml quadruple table).
EXECUTION_ROLES = ("worker", "executor", "scout", "duty_officer")
K3_ROLES = ("plan_expander", "verifier", "reviewer")


def expected_routes(policy_path=None):
    path = Path(policy_path) if policy_path else (
        Path(__file__).resolve().parents[1] / "config" / "orchestration_policy_v2.toml")
    with path.open("rb") as handle:
        models = tomllib.load(handle).get("models", {})
    required = ("sol_model", "v4_model", "v4_reasoning", "k3_model", "k3_reasoning")
    missing = [key for key in required if not isinstance(models.get(key), str)]
    if missing:
        raise ValueError("active model policy missing: " + ", ".join(missing))
    role_model = {role: models["v4_model"] for role in EXECUTION_ROLES}
    role_model.update({role: models["k3_model"] for role in K3_ROLES})
    role_effort = {role: models["v4_reasoning"] for role in EXECUTION_ROLES}
    role_effort.update({role: models["k3_reasoning"] for role in K3_ROLES})
    return role_model, role_effort, models["sol_model"]


def load_ndjson(path, skipped):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        rows.append(rec)
                    else:
                        skipped[path] = skipped.get(path, 0) + 1
                except (json.JSONDecodeError, ValueError):
                    skipped[path] = skipped.get(path, 0) + 1
    except OSError as e:
        print(f"usage_reconcile: cannot read {path}: {e}", file=sys.stderr)
    return rows


def main():
    ap = argparse.ArgumentParser(description="weekly usage reconciliation")
    ap.add_argument("--events", required=True, help="data/events.ndjson")
    ap.add_argument("--sessions", required=True, help="annotated.jsonl from e0_annotate.py")
    ap.add_argument("--output", required=True, help="reconciliation report JSON")
    ap.add_argument("--rollout-dir", default=None,
                    help="Codex sessions dir (~/.codex/sessions); enables the "
                         "Sol token-share section via model_token_share.py")
    ap.add_argument("--policy", default=None)
    a = ap.parse_args()
    try:
        role_model, role_effort, sol_model = expected_routes(a.policy)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        print(f"usage_reconcile: active route policy unreadable: {exc}", file=sys.stderr)
        return 2
    skipped = {}
    events = load_ndjson(a.events, skipped)
    turns = load_ndjson(a.sessions, skipped)

    dispatched = [e for e in events if e.get("event") == "dispatched"]
    starts = [e for e in events
              if e.get("event") in ("SubagentStart", "SubagentStartRecovered")]
    disc = []

    # 1) missing metering: expected spawns vs UNIQUE hook events (count-level,
    #    since SubagentStart carries no packet_id — hook is pure observation).
    #    Unique key = (agent_id, turn_id) so duplicates cannot mask a gap.
    uniq_starts = {(s.get("agent_id"), s.get("turn_id"), s.get("session_id")) for s in starts}
    if len(uniq_starts) < len(dispatched):
        disc.append({"type": "missing_metering", "severity": "warn",
                     "expected_spawns": len(dispatched), "hook_events": len(uniq_starts),
                     "missing": len(dispatched) - len(uniq_starts),
                     "note": "spawn without SubagentStart line — hook is fail-open; "
                             "verify via report-file second truth source (§3.3)"})

    # 2) duplicate metering: identical (agent_id, turn_id) metered twice.
    keys = Counter((s.get("agent_id"), s.get("turn_id")) for s in starts
                   if s.get("agent_id") is not None)
    for k, c in keys.items():
        if c > 1:
            disc.append({"type": "duplicate_metering", "severity": "warn",
                         "agent_id": k[0], "turn_id": k[1], "count": c})

    # 3) routing anomalies: role vs pinned model tier (25x differential).
    seen_anom = set()
    for s in starts:
        role, model = (s.get("agent_role") or "unknown").lower(), s.get("model") or "unknown"
        exp = role_model.get(role)
        akey = (role, model, s.get("agent_id"))
        if exp and model not in ("unknown",) and model != exp and akey not in seen_anom:
            seen_anom.add(akey)
            # Reviewer is now pinned to K3 max.  A reviewer observed on Sol is
            # a current routing failure, not the historical pre-migration
            # reviewer attribution case handled by model_token_share.py.
            sev = "critical" if model == sol_model else "warn"
            disc.append({"type": "routing_anomaly", "severity": sev, "agent_role": role,
                         "model_observed": model, "model_expected": exp,
                         "note": "25x price differential — non-forgeable in real usage"})
        effort = s.get("effort")
        expected_effort = role_effort.get(role)
        effort_key = (role, "effort", effort, s.get("agent_id"))
        if (expected_effort and effort and effort != expected_effort
                and effort_key not in seen_anom):
            seen_anom.add(effort_key)
            disc.append({"type": "routing_anomaly", "severity": "warn",
                         "agent_role": role, "effort_observed": effort,
                         "effort_expected": expected_effort,
                         "note": "runtime reasoning effort differs from role pin"})
        if s.get("degraded"):
            disc.append({"type": "degraded_meter_line", "severity": "info",
                         "ts_utc": s.get("ts_utc"), "note": "hook fell back to minimal line"})

    # 4) reviewer-bucket drift vs annotated sessions (patch caliber 2 check).
    rev_turns = [t for t in turns if t.get("meter_bucket") == "reviewer"]
    rev_starts = [s for s in starts if (s.get("agent_role") or "").lower() == "reviewer"]
    if rev_turns and not rev_starts:
        disc.append({"type": "reviewer_unmetered", "severity": "critical",
                     "reviewer_turns": len(rev_turns),
                     "note": "reviewer tokens in sessions but no reviewer SubagentStart"})
    if len(rev_starts) > 1:
        disc.append({"type": "reviewer_multi_spawn", "severity": "warn",
                     "count": len(rev_starts), "note": "release-gate reviewer is single-spawn per wave"})
    free_cost = sum(t.get("cost_equivalent", 0) for t in turns if t.get("meter_bucket") != "sol")
    if free_cost:
        disc.append({"type": "free_side_billed", "severity": "critical", "amount": free_cost,
                     "note": "§2 Ruling 2 violated: free side must carry zero cost"})

    # 5) Sol token-share budget (P1-5): reuse model_token_share.py offline.
    token_share = None
    if a.rollout_dir:
        import os, tempfile
        import model_token_share
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            ts_out = tf.name
        rc = model_token_share.main(["--sessions-dir", a.rollout_dir,
                                     "--events", a.events, "--output", ts_out])
        try:
            token_share = json.load(open(ts_out, encoding="utf-8"))
        except (OSError, ValueError):
            token_share = {"error": "aggregator produced no report", "rc": rc}
            disc.append({"type": "token_share_unavailable", "severity": "warn",
                         "rollout_dir": a.rollout_dir, "rc": rc})
        finally:
            try:
                os.unlink(ts_out)
            except OSError:
                pass
        for wname, w in (token_share or {}).get("windows", {}).items():
            if w.get("status") == "WARNING":
                disc.append({"type": "sol_share_warning", "severity": "warn",
                             "window": wname, "share_effective": w["share_effective"],
                             "note": "Sol share_effective exceeds 20% budget"})
            elif w.get("status") == "BLOCK":
                disc.append({"type": "sol_share_block", "severity": "critical",
                             "window": wname, "share_effective": w["share_effective"],
                             "note": "Sol share_effective exceeds 25% hard cap — "
                                     "refuse new non-planning/adjudication Sol work"})

    report = {"events_file": a.events, "sessions_file": a.sessions,
              "token_share": token_share,
              "expected_spawns": len(dispatched), "subagent_start_events": len(starts),
              "annotated_turns": len(turns), "reviewer_spawns": len(rev_starts),
              "skipped_lines": skipped, "discrepancies": disc,
              "status": "clean" if not disc else "discrepancies_found"}
    with open(a.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(f"usage_reconcile: {report['status']} — {len(disc)} discrepancy(ies) -> {a.output}")
    return 0 if not disc else 1


if __name__ == "__main__":
    sys.exit(main())
