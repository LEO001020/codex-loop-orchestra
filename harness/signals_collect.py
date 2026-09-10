#!/usr/bin/env python3
# ============================================================================
# signals_collect.py — Deterministic L0-output -> L1 signals JSON assembler
# ----------------------------------------------------------------------------
# Purpose : Closes gap H-05 (L1 input completeness relied on caller
#           convention, not code). Folds the L0 mechanical outputs into the
#           signals JSON that harness/trigger_eval.py consumes. Pure
#           if/else + regex + counting — ZERO LLM, zero tokens. Missing or
#           unreadable inputs degrade SAFELY: the field falls back to its
#           conservative default and a *_missing marker is set, the script
#           never crashes and never fabricates green signals.
# Input   : --packet            data/packets/<pid>.json (4-field packet)
#           --diffvalidator-out file capturing diffvalidator.py stdout
#                               ("PASS packet=.. files=N +A/-R hunks=H
#                                 tests=X->Y" or "FAIL [REASON] ...")
#           --replay            acceptance_replay <oracle>.replay.json
#           --events            data/events.ndjson (retry/duty history)
#           --ledger            data/progress_ledger.json (attempts)
#           --diff              unified diff file (paths_touched, optional)
#           --out               output signals JSON path (default: stdout)
# Output  : one signals JSON object with trigger_eval-schema keys
#           (exit_codes, retry_count, run_level_budget, diff_lines,
#            deleted_lines, diff_budget, path_boundary_attempts,
#            test_count_before/after, min_test_count, paths_touched,
#            observation_lengths, command_history, consecutive_same_type_
#            failures, retry_class_no_match) PLUS audit-alias fields
#           (exit_code_sequence, diff_line_count, path_violation_attempted,
#            test_count_delta, high_risk_path_hit, observation_length,
#            loop_fingerprint). Exit 0 = assembled, 2 = usage error.
# Lines   : ~110 (header included)
# ============================================================================
import argparse, json, os, re, sys

STATS_RE = re.compile(r"\+(\d+)/-(\d+)")
TESTS_RE = re.compile(r"tests=(\d+|None)->(\d+|None)")
MIN_TEST_RE = re.compile(r"^min_test_count\s*>=\s*(\d+)$")
DIFF_BUDGET_RE = re.compile(r"diff\s*(?:≤|<=)\s*(\d+)\s*lines", re.IGNORECASE)
DIFF_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.M)
HIGH_RISK_PATH_RE = re.compile(  # same set trigger_eval.py hardcodes
    r"(migrations?/|schema/|\.sql$|\.env|secrets|credentials|\.aws/|\.ssh/|"
    r"auth\.json|\.github/workflows/|\.gitlab-ci|Jenkinsfile|\.circleci/|"
    r"hooks/|AGENTS\.md)", re.IGNORECASE)


def read_json(path):
    """None on any failure — caller degrades to conservative defaults."""
    try:
        return json.load(open(path, encoding="utf-8")) if path and os.path.exists(path) else None
    except (OSError, ValueError):
        return None


def read_text(path):
    try:
        return open(path, encoding="utf-8", errors="replace").read() \
            if path and os.path.exists(path) else None
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description="L0 -> L1 signals assembler (zero token)")
    ap.add_argument("--packet", required=True)
    ap.add_argument("--diffvalidator-out", default=None)
    ap.add_argument("--replay", default=None)
    ap.add_argument("--events", default=None)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--diff", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    pkt = read_json(a.packet)
    if pkt is None:
        print("usage error: packet JSON unreadable: %s" % a.packet, file=sys.stderr)
        return 2
    pid = pkt.get("packet_id", "?")
    sig = {"packet_id": pid}

    # --- packet constraints: diff budget + min test count (regex, count) ----
    cons = [str(c) for c in pkt.get("constraints", [])]
    sig["diff_budget"] = next((int(m.group(1)) for c in cons
                               for m in [DIFF_BUDGET_RE.search(c)] if m), None)
    sig["min_test_count"] = next((int(m.group(1)) for c in cons
                                  for m in [MIN_TEST_RE.match(c.strip())] if m), None)

    # --- diffvalidator stdout: lines, deletions, tests, boundary verdict ----
    dv = read_text(a.diffvalidator_out)
    sig["diffvalidator_missing"] = dv is None
    added = removed = 0
    tc_before = tc_after = None
    if dv is not None:
        m = STATS_RE.search(dv)
        added, removed = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        t = TESTS_RE.search(dv)
        if t:
            tc_before = None if t.group(1) == "None" else int(t.group(1))
            tc_after = None if t.group(2) == "None" else int(t.group(2))
        sig["path_boundary_attempts"] = 1 if "PATH_BOUNDARY" in dv else 0
    else:  # safe degrade: no L0 verdict -> no boundary claim, zero-size diff
        sig["path_boundary_attempts"] = 0
    sig["diff_lines"] = added + removed
    sig["deleted_lines"] = removed

    # --- acceptance replay JSON: exit codes + test count + commands ---------
    rp = read_json(a.replay) or {}
    sig["replay_missing"] = not rp
    cmds = [c for c in rp.get("commands", []) if isinstance(c, dict)]
    sig["exit_codes"] = [int(c.get("rc", 1)) for c in cmds]
    sig["command_history"] = [str(c.get("cmd", "")) for c in cmds]
    if tc_after is None and rp.get("test_count") is not None:
        tc_after = int(rp["test_count"])
    sig["test_count_before"], sig["test_count_after"] = tc_before, tc_after

    # --- ledger + events: retry count, duty-partition flags -----------------
    led = read_json(a.ledger) or {"packets": {}}
    pk = led.get("packets", {}).get(pid, {})
    sig["retry_count"] = int(pk.get("attempts", 0) or 0)
    sig["run_level_budget"] = 6  # spec run-level retry budget (retry_classes.yaml)
    same_class = no_match = False
    ev_text = read_text(a.events) or ""
    for line in ev_text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue  # malformed event line: statemachine dead-letters it, not us
        if ev.get("packet_id") != pid or ev.get("event") != "duty_review":
            continue
        why = (ev.get("detail") or {}).get("why", "")
        same_class = same_class or why == "2_consecutive_same_class"
        no_match = no_match or why == "regex_no_match"
    sig["consecutive_same_type_failures"] = same_class
    sig["retry_class_no_match"] = no_match

    # --- unified diff (optional): touched paths for the high-risk regex -----
    diff_text = read_text(a.diff) or ""
    sig["paths_touched"] = sorted({p for m in DIFF_HEADER_RE.finditer(diff_text)
                                   for p in (m.group(1), m.group(2))})

    # --- observation lengths: byte sizes of the L0 artifacts consumed -------
    sig["observation_lengths"] = [len(x) for x in (dv, json.dumps(rp) if rp else None,
                                                   diff_text or None) if x]

    # --- audit-alias fields (same facts, audit-report naming) ---------------
    counts = {}
    for c in sig["command_history"]:
        counts[c] = counts.get(c, 0) + 1
    sig.update(exit_code_sequence=sig["exit_codes"],
               diff_line_count=sig["diff_lines"],
               path_violation_attempted=sig["path_boundary_attempts"] > 0,
               test_count_delta=(tc_after - tc_before)
               if tc_before is not None and tc_after is not None else None,
               high_risk_path_hit=any(HIGH_RISK_PATH_RE.search(p)
                                      for p in sig["paths_touched"]),
               observation_length=max(sig["observation_lengths"] or [0]),
               loop_fingerprint=max(counts.values()) if counts else 0)
    out = json.dumps(sig, indent=1)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(out + "\n")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
