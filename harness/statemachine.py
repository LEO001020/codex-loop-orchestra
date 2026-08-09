#!/usr/bin/env python3
# ============================================================================
# statemachine.py — Orchestration state machine (spec §3.3, all 23 transitions)
# Purpose : Consume data/events.ndjson (fast path) + reports/ (second truth
#           source), drive packet lifecycle deterministically. No LLM calls.
#           Off-table events -> DEAD_LETTER + Sol wake summary (fail-visible).
# Input   : events.ndjson lines {"ts","packet_id","event","detail"?}; report
#           files data/reports/<pid>/report.json; config/config.toml toggles.
# Output  : data/progress_ledger.json (state per packet), Sol wake summaries
#           under data/sol_wake/, appended audit lines to events.ndjson.
#           Exit 0 = ok, 1 = I/O error, 2 = dead-letters produced this step.
# Lines   : ~150 (excluding this header)
# ============================================================================
import argparse, json, os, re, sys, time

ROOT = os.environ.get("LOOP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
E = lambda *p: os.path.join(DATA, *p)
TERMINAL = {"MERGED", "DEAD_LETTER", "SOL_ADJUDICATE", "DONE"}

# Transition table: (from_state, event) -> (to_state, transition_number).
# This is the complete authorized set (delegation semantics, spec S3);
# anything not listed is off-table and goes to DEAD_LETTER, fail-visible.
T = {
    ("NONE",           "planned"):            ("PLANNED",        1),
    ("PLANNED",        "dag_assert_pass"):    ("DISPATCHABLE",   2),
    ("DISPATCHABLE",   "dispatched"):         ("RUNNING",        3),
    ("RUNNING",        "subagent_stop"):      ("REPORTED",       4),   # gated on report file below
    ("RUNNING",        "timeout"):            ("TIMED_OUT",      5),
    ("RUNNING",        "exec_failed"):        ("FAILED",         6),
    ("REPORTED",       "acceptance_pass"):    ("ACCEPTED",       7),
    ("REPORTED",       "acceptance_fail"):    ("FAILED",         8),
    ("FAILED",         "retry_dispatch"):     ("RUNNING",        9),
    ("FAILED",         "duty_review"):        ("DUTY_REVIEW",   10),
    ("DUTY_REVIEW",    "duty_retryable"):     ("RUNNING",       11),   # gated on duty_officer.enforce
    ("DUTY_REVIEW",    "duty_fixable"):       ("RUNNING",       12),   # gated on duty_officer.enforce
    ("DUTY_REVIEW",    "duty_terminal"):      ("DEAD_LETTER",   13),
    ("TIMED_OUT",      "budget_exhausted"):   ("DEAD_LETTER",   14),
    ("FAILED",         "budget_exhausted"):   ("DEAD_LETTER",   15),
    ("ACCEPTED",       "merged"):             ("MERGED",        16),
    ("ACCEPTED",       "merge_conflict"):     ("MERGE_CONFLICT",17),
    ("MERGED",         "wave_complete"):      ("WAVE_DONE",     18),
    ("DEAD_LETTER",    "dead_letter_summary"):("SOL_ADJUDICATE",19),
    ("MERGE_CONFLICT", "conflict_pointer"):   ("SOL_ADJUDICATE",20),
    ("WAVE_DONE",      "wave_summary"):       ("SOL_ADJUDICATE",21),
    ("SOL_ADJUDICATE", "sol_replan"):         ("PLANNED",       22),
    ("SOL_ADJUDICATE", "release_merge"):      ("DONE",          23),
}

def read_toggle(section, key, default=False):
    """Minimal deterministic TOML scan for '[section] key = true|false'."""
    cfg = os.path.join(ROOT, "config", "config.toml")
    if not os.path.exists(cfg):
        cfg = os.path.join(ROOT, "config", "config.toml.example")
    cur, pat = None, re.compile(r"^\s*%s\s*=\s*(true|false)" % re.escape(key))
    try:
        for ln in open(cfg, encoding="utf-8"):
            s = ln.split("#", 1)[0].strip()
            if s.startswith("["):
                cur = s.strip("[]").strip()
            elif cur == section:
                m = pat.match(s)
                if m:
                    return m.group(1) == "true"
    except OSError:
        pass
    return default

def load_ledger():
    try:
        return json.load(open(E("progress_ledger.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {"packets": {}, "waves": []}

def save_ledger(led):
    tmp = E("progress_ledger.json.tmp")
    json.dump(led, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, E("progress_ledger.json"))  # atomic: files are the truth

def append(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, separators=(",", ":")) + "\n")

def sol_wake(pid, reason, detail):
    """Fail-visible: synthesize a bounded Sol wake summary on disk (spec S3/S5)."""
    os.makedirs(E("sol_wake"), exist_ok=True)
    path = E("sol_wake", "%d_%s.md" % (int(time.time()), pid))
    body = ("# SOL WAKE — %s\npacket: %s\nreason: %s\ndetail: %s\n"
            "report: data/reports/%s/report.json\ndead_letter: data/dead_letters/%s.json\n"
            % (reason, pid, reason, json.dumps(detail)[:800], pid, pid))
    open(path, "w", encoding="utf-8").write(body)
    append(E("escalation_log.jsonl"), {"ts": time.time(), "packet_id": pid,
                                       "level": "SOL_WAKE", "reason": reason, "summary_path": path})
    return path

def to_dead_letter(led, pid, reason, detail):
    p = led["packets"].setdefault(pid, {"state": "NONE", "history": [], "attempts": 0})
    p["state"] = "DEAD_LETTER"
    p["history"].append({"ts": time.time(), "to": "DEAD_LETTER", "via": reason})
    dl = {"packet_id": pid, "reason": reason, "detail": detail,
          "prior_history": p["history"][-10:], "ts": time.time()}
    json.dump(dl, open(E("dead_letters", "%s.json" % pid), "w", encoding="utf-8"), indent=1)
    sol_wake(pid, reason, detail)

# Audit-only events emitted by harness scripts (worktree_pool.sh) that are
# NOT state transitions. Without this skip list the harness dead-letters its
# own allocation bookkeeping (integration bug found by Phase7 golden tests).
INFO_EVENTS = {"worktree_allocated", "worktree_released"}

def apply_event(led, ev, enforce_duty):
    pid, name = ev.get("packet_id", "?"), ev.get("event", "?")
    if name in INFO_EVENTS:  # audit trail only — no state change, never DLQ
        return None
    p = led["packets"].setdefault(pid, {"state": "NONE", "history": [], "attempts": 0})
    key = (p["state"], name)
    if key not in T:  # off-table -> DEAD_LETTER + Sol wake, never silent (spec §3.3 safety)
        to_dead_letter(led, pid, "off_table_event", {"event": name, "from_state": p["state"]})
        return "DEAD_LETTER"
    to_state, num = T[key]
    if num == 4 and not os.path.exists(E("reports", pid, "report.json")):
        # Hook said stop but report file missing: hook is fast path only; the
        # report file is the second truth source. Treat as missing-item (§7.2 ③).
        to_dead_letter(led, pid, "report_missing_on_stop", {"event": name})
        return "DEAD_LETTER"
    if num in (11, 12) and not enforce_duty:
        # F2 cold start: duty officer only records, never routes (duty_officer.enforce=false).
        append(E("escalation_log.jsonl"), {"ts": time.time(), "packet_id": pid,
               "level": "DUTY_RECORD_ONLY", "ruling": name, "routed": False})
        to_dead_letter(led, pid, "duty_ruling_recorded_not_routed", {"ruling": name})
        return "DEAD_LETTER"
    p["state"] = to_state
    p["history"].append({"ts": time.time(), "to": to_state, "via": name, "t": num})
    if num == 9:
        p["attempts"] = p.get("attempts", 0) + 1
    return to_state

def reconcile(led):
    """Fallback path (§7.2 ②): hook event lost but report file landed -> REPORTED."""
    for pid, p in led["packets"].items():
        if p["state"] == "RUNNING" and os.path.exists(E("reports", pid, "report.json")):
            p["state"] = "REPORTED"
            p["history"].append({"ts": time.time(), "to": "REPORTED", "via": "report_file_fallback", "t": 4})

def wave_check(led):
    """Transition 18 precondition: all packets terminal + missing-item check."""
    states = [p["state"] for p in led["packets"].values()]
    all_term = states and all(s in TERMINAL or s == "WAVE_DONE" for s in states)
    rc = os.system("bash %s >/dev/null 2>&1" % os.path.join(ROOT, "harness", "missing_check.sh"))
    return all_term and rc == 0

def main():
    ap = argparse.ArgumentParser(description="LOOP-F2 deterministic state machine")
    ap.add_argument("cmd", choices=["step", "reconcile", "wave-check", "state"])
    ap.add_argument("--packet", help="packet id for 'state'")
    args = ap.parse_args()
    led = load_ledger()
    enforce_duty = read_toggle("duty_officer", "enforce", False)
    dead = 0
    if args.cmd == "state":
        p = led["packets"].get(args.packet or "", {"state": "NONE"})
        print(p["state"]); return 0
    if args.cmd in ("step", "reconcile"):
        cursor_f = E(".sm_cursor")
        off = int(open(cursor_f).read().strip() or 0) if os.path.exists(cursor_f) else 0
        with open(E("events.ndjson"), encoding="utf-8") as f:
            f.seek(off)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    ev = {"packet_id": "malformed", "event": "unparseable", "raw": line[:200]}
                if apply_event(led, ev, enforce_duty) == "DEAD_LETTER":
                    dead += 1
            open(cursor_f, "w").write(str(f.tell()))
        reconcile(led)
        save_ledger(led)
        print(json.dumps({p: v["state"] for p, v in led["packets"].items()}))
        return 2 if dead else 0
    if args.cmd == "wave-check":
        ok = wave_check(led)
        print("WAVE_DONE_READY" if ok else "WAVE_INCOMPLETE")
        return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
