#!/usr/bin/env python3
# ============================================================================
# statemachine.py — Orchestration state machine (spec §3.3, all 23 transitions)
# Purpose : Consume data/events.ndjson (fast path) + reports/ (second truth
#           source), drive packet lifecycle deterministically. No LLM calls.
#           Off-table events -> DEAD_LETTER + Sol wake summary (fail-visible).
#           Watchdog (H-02): each step, RUNNING packets whose dispatch spawn
#           ts (data/spawn_times.json) is older than [agents]
#           job_max_runtime_seconds get a 'timeout' event emitted — the
#           production producer for transition 5 (RUNNING->TIMED_OUT).
#           Replan cap (H-04): transition 22 (SOL_ADJUDICATE->PLANNED) is
#           counted per packet in data/replan_counters.json; over 2 replans
#           (or any counter I/O failure — fail toward human, same direction
#           as the L3 cap) the transition is REFUSED and a direct_l4 summary
#           is queued under data/l4_queue/ for the human release gate.
# Input   : events.ndjson lines {"ts","packet_id","event","detail"?}; report
#           files data/reports/<pid>/report.json; config/config.toml toggles;
#           data/spawn_times.json (watchdog input written by dispatch.py).
# Output  : data/progress_ledger.json (state per packet), Sol wake summaries
#           under data/sol_wake/, direct_l4 summaries under data/l4_queue/,
#           appended audit lines to events.ndjson.
#           Exit 0 = ok, 1 = I/O error, 2 = dead-letters produced this step.
# Lines   : ~220 (excluding this header)
# ============================================================================
import argparse, hashlib, json, os, re, sys, time
from loop_config import config_bool, config_int
from lifecycle_supervisor import locked
from pathlib import Path

ROOT = os.environ.get("LOOP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
E = lambda *p: os.path.join(DATA, *p)
TERMINAL = {"MERGED", "DEAD_LETTER", "SOL_ADJUDICATE", "DONE"}
SUPERVISOR_HEARTBEAT_STALE_SECONDS = 5.0
# Packet-id -> filename safety: 1-96 ASCII letters/digits/._- ONLY.  Any
# separator (/, \, Unicode), "..", or absolute path is rejected so a pid can
# never escape data/dead_letters/, data/sol_wake/, or data/l4_queue/.
# Fail-visible: SystemExit(1) before any write.
PACKET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")


def safe_pid_filename(pid):
    if not isinstance(pid, str) or not PACKET_ID_RE.fullmatch(pid):
        sys.stderr.write("invalid packet id %r: allowed 1-96 ASCII "
                         "letters/digits/._- only; refusing write\n" % (pid,))
        raise SystemExit(1)
    return pid

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
    ("FAILED",         "retry_dispatch"):     ("DISPATCHABLE",   9),
    ("TIMED_OUT",      "retry_dispatch"):     ("DISPATCHABLE",  37),
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
    # Release-review route (packets flagged release_review=True): the
    # reviewer's result ALWAYS returns to SOL_ADJUDICATE -- success (verdict
    # checked) or failure (exec_failed) -- never a direct release. Exit from
    # SOL_ADJUDICATE stays human/Sol-triggered via t22/t23 only.
    ("REPORTED",       "review_verdict_pass"):("SOL_ADJUDICATE",24),
    ("REPORTED",       "review_verdict_fail"):("SOL_ADJUDICATE",25),
}

def read_toggle(section, key, default=False):
    return config_bool(section, key, default)

def read_int_key(section, key, default):
    return config_int(section, key, default)

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
    lock_path = (Path(DATA) / "lifecycle" / ".events.lock"
                 if os.path.normcase(path) == os.path.normcase(E("events.ndjson"))
                 else Path(path + ".lock"))
    with locked(lock_path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, separators=(",", ":")) + "\n")

def sol_wake(pid, reason, detail):
    """Fail-visible: synthesize a bounded Sol wake summary on disk (spec S3/S5)."""
    safe_pid_filename(pid)  # invalid pid: fail-visible, never written
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
    safe_pid_filename(pid)  # invalid pid: fail-visible, never written
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
INFO_EVENTS = {
    "worktree_allocated", "worktree_released",
    "SubagentStart", "SubagentStartRecovered", "SubagentStop",
    "exec_spawned", "exec_supervisor_started", "exec_exited", "exec_killed", "spawn_lost",
    "parent_stop", "close_requested", "lifecycle_reconciled",
    "dispatch_dry_run", "sol_budget_blocked",
}

REPLAN_CAP = 2  # H-04: max SOL_ADJUDICATE->PLANNED loops per packet (t22)
TIMEOUT_RETRY_MAX = 1  # t37: only the first timeout may re-dispatch

def bump_replan_counter(pid):
    """Per-packet t22 replan counter (same bump pattern as the L3 cap in
    trigger_eval.py). Returns (count_after, exceeded). Counter-file I/O
    failure counts as exceeded — fail toward the human gate (direct_l4),
    never toward unlimited replanning."""
    path = E("replan_counters.json")
    try:
        counters = json.load(open(path, encoding="utf-8")) \
            if os.path.exists(path) else {}
    except (OSError, ValueError):
        return REPLAN_CAP + 1, True  # unreadable state: assume over-cap -> L4
    count = int(counters.get(pid, 0)) + 1
    counters[pid] = count
    try:
        tmp = path + ".tmp"
        json.dump(counters, open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, path)
    except OSError:
        return REPLAN_CAP + 1, True  # cannot persist: assume over-cap -> L4
    return count, count > REPLAN_CAP


def bump_timeout_retry_counter(pid):
    """Independent t37 budget; never reuse the aggregate attempts counter.

    The sidecar is shared with v2 so a routing-mode switch cannot reset the
    timeout budget.  I/O failure follows the existing cap discipline and
    fails toward dead-letter/human review.
    """
    path = E("timeout_retry_counters.json")
    try:
        counters = (json.load(open(path, encoding="utf-8"))
                    if os.path.exists(path) else {})
        count = int(counters.get(pid, 0)) + 1
        counters[pid] = count
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(counters, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except (OSError, ValueError, TypeError):
        return TIMEOUT_RETRY_MAX + 1, True
    return count, count > TIMEOUT_RETRY_MAX

def l4_summary(pid, reason, detail):
    """Queue a bounded direct_l4 summary for the HUMAN release gate —
    escalation only, never a release path (release stays human-triggered)."""
    safe_pid_filename(pid)  # invalid pid: fail-visible, never written
    os.makedirs(E("l4_queue"), exist_ok=True)
    path = E("l4_queue", "%d_%s.md" % (int(time.time()), pid))
    open(path, "w", encoding="utf-8").write(
        "# DIRECT_L4 — %s\npacket: %s\nreason: %s\ndetail: %s\n"
        "report: data/reports/%s/report.json\n"
        % (reason, pid, reason, json.dumps(detail)[:800], pid))
    append(E("escalation_log.jsonl"), {"ts": time.time(), "packet_id": pid,
                                       "level": "DIRECT_L4", "reason": reason,
                                       "summary_path": path})
    return path

def report_is_current(pid):
    report = E("reports", pid, "report.json")
    if not os.path.exists(report):
        return False
    try:
        times = json.load(open(E("spawn_times.json"), encoding="utf-8"))
        stamp = times.get(pid)
        if not isinstance(stamp, dict) or stamp.get("ts") is None:
            return True  # legacy dispatch has no generation timestamp
        return os.path.getmtime(report) >= float(stamp["ts"])
    except (OSError, ValueError, TypeError):
        return True  # preserve legacy second-truth behavior when metadata is absent

def apply_event(led, ev, enforce_duty):
    pid, name = ev.get("packet_id", "?"), ev.get("event", "?")
    if isinstance(pid, str) and pid.startswith(("l2v-", "v2job-")):
        # Synthetic L2 verifier jobs have their own claim/completion state;
        # their lifecycle events must not mutate the source packet state.
        return None
    if name in INFO_EVENTS:  # audit trail only — no state change, never DLQ
        return None
    # Any state-bearing event can eventually derive a filename (dead letter,
    # Sol wake, L4 queue).  Validate before the unknown-packet admission guard
    # so traversal-shaped ids cannot be silently ignored.
    safe_pid_filename(pid)
    if name == "unparseable":
        to_dead_letter(led, pid, "unparseable_event_line",
                       {"raw": ev.get("raw", "")})
        return "DEAD_LETTER"
    if pid not in led["packets"] and name not in {"planned", "skeleton_ready"}:
        # The shared event stream also contains transport/supervisor events
        # whose adhoc run id is not a canonical packet admission.
        return None
    p = led["packets"].setdefault(pid, {"state": "NONE", "history": [], "attempts": 0})
    detail = ev.get("detail") if isinstance(ev.get("detail"), dict) else {}
    event_attempt = ev.get("attempt", detail.get("attempt"))
    try:
        event_attempt = int(event_attempt) if event_attempt is not None else None
    except (TypeError, ValueError):
        event_attempt = None
    current_attempt = int(p.get("attempts", 0) or 0)
    if event_attempt is not None and event_attempt < current_attempt:
        p["history"].append({"ts": time.time(), "to": p["state"],
                             "via": "stale_generation_ignored", "event": name,
                             "event_attempt": event_attempt,
                             "current_attempt": current_attempt})
        return p["state"]
    # The report-file fallback may win a race with the supervisor's terminal
    # event.  Once the same report is present, that late stop is an idempotent
    # confirmation rather than an off-table safety violation.
    if (p["state"] == "REPORTED" and name == "subagent_stop"
            and os.path.exists(E("reports", pid, "report.json"))):
        p["history"].append({"ts": time.time(), "to": "REPORTED",
                             "via": "late_subagent_stop_confirmed",
                             "event_attempt": event_attempt})
        return "REPORTED"
    # A duplicate physical-dispatch audit for the authoritative generation may
    # arrive after the first dispatched event already moved the packet to
    # RUNNING.  Treat that duplicate as confirmation, not an off-table event.
    if p["state"] == "RUNNING" and name == "dispatched" and event_attempt == current_attempt:
        p["history"].append({"ts": time.time(), "to": "RUNNING",
                             "via": "generation_dispatch_confirmed",
                             "event_attempt": event_attempt})
        return "RUNNING"
    # Release-review failure route: a flagged review packet whose worker fails
    # goes straight back to SOL_ADJUDICATE (never FAILED/retry loop, and never
    # any release path). Success continues REPORTED -> review_verdict_pass.
    if p.get("release_review") and p["state"] == "RUNNING" and name == "exec_failed":
        p["state"] = "SOL_ADJUDICATE"
        p["history"].append({"ts": time.time(), "to": "SOL_ADJUDICATE",
                             "via": name, "t": 26,
                             "release_review": True})
        return "SOL_ADJUDICATE"
    key = (p["state"], name)
    if key not in T:  # off-table -> DEAD_LETTER + Sol wake, never silent (spec §3.3 safety)
        to_dead_letter(led, pid, "off_table_event", {"event": name, "from_state": p["state"]})
        return "DEAD_LETTER"
    to_state, num = T[key]
    if num == 4 and not report_is_current(pid):
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
    if num == 22:
        # H-04: plan-layer oscillation cap — count every Sol replan; over-cap
        # (or counter I/O failure) REFUSES the PLANNED transition and queues a
        # direct_l4 summary instead (human intervention, same fail direction
        # as the L3 cap). The packet stays in SOL_ADJUDICATE; its only exit
        # is the human-triggered release_merge (t23).
        count, exceeded = bump_replan_counter(pid)
        if exceeded:
            l4_summary(pid, "replan_cap_exceeded",
                       {"replan_count": count, "cap": REPLAN_CAP})
            p["history"].append({"ts": time.time(), "to": "SOL_ADJUDICATE",
                                 "via": "replan_cap_forced_l4", "t": 22})
            return "SOL_ADJUDICATE"
    if num == 37:
        count, exceeded = bump_timeout_retry_counter(pid)
        if exceeded:
            to_dead_letter(led, pid, "timeout_retry_exhausted",
                           {"timeout_retries": count,
                            "cap": TIMEOUT_RETRY_MAX})
            return "DEAD_LETTER"
    p["state"] = to_state
    p["history"].append({"ts": time.time(), "to": to_state, "via": name, "t": num})
    if num in (9, 37):
        p["attempts"] = p.get("attempts", 0) + 1
    return to_state

def reconcile(led):
    """Fallback path (§7.2 ②): hook event lost but report file landed -> REPORTED."""
    for pid, p in led["packets"].items():
        if p["state"] == "RUNNING" and report_is_current(pid):
            p["state"] = "REPORTED"
            p["history"].append({"ts": time.time(), "to": "REPORTED", "via": "report_file_fallback", "t": 4})

def watchdog(led):
    """H-02: production producer for transition 5. A RUNNING packet whose
    spawn ts (data/spawn_times.json, written by dispatch.py) is STRICTLY
    older than [agents] job_max_runtime_seconds gets a 'timeout' event:
    appended to events.ndjson (audit) and applied in-place (RUNNING ->
    TIMED_OUT). Exactly-at-boundary is NOT over. Returns list of timed-out
    packet ids. Unreadable spawn state degrades to no-op (report/reconcile
    and the platform timeout remain as the outer safety nets)."""
    try:
        times = json.load(open(E("spawn_times.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return []
    try:
        exec_roster = json.load(open(E("lifecycle", "exec_roster.json"), encoding="utf-8"))
    except (OSError, ValueError):
        exec_roster = {"jobs": {}}
    limit = read_int_key("agents", "job_max_runtime_seconds", 1800)
    now, fired = time.time(), []
    for pid, p in led["packets"].items():
        stamp = times.get(pid)
        mode = stamp.get("mode") if isinstance(stamp, dict) else "legacy"
        ts = stamp.get("ts") if isinstance(stamp, dict) else stamp
        try:
            elapsed = now - float(ts) if ts is not None else None
        except (TypeError, ValueError):
            continue
        if p["state"] != "RUNNING" or elapsed is None or elapsed <= limit:
            continue
        job = exec_roster.get("jobs", {}).get(pid, {})
        heartbeat = job.get("heartbeat_at")
        run_id = stamp.get("run_id") if isinstance(stamp, dict) else None
        try:
            heartbeat_fresh = (heartbeat is not None and
                now - float(heartbeat) <= SUPERVISOR_HEARTBEAT_STALE_SECONDS)
        except (TypeError, ValueError):
            heartbeat_fresh = False
        supervisor_owns_generation = (mode == "single" and job.get("run_id") == run_id
            and job.get("state") in ("starting", "running") and heartbeat_fresh)
        if supervisor_owns_generation:
            continue
        append(E("events.ndjson"), {"ts": now, "packet_id": pid, "event": "timeout",
                                    "attempt": stamp.get("attempt") if isinstance(stamp, dict) else None,
                                    "run_id": run_id,
                                    "detail": {"why": "watchdog", "spawn_ts": ts,
                                               "mode": mode, "limit_s": limit}})
        p["state"] = "TIMED_OUT"
        p["history"].append({"ts": now, "to": "TIMED_OUT", "via": "timeout", "t": 5})
        fired.append(pid)
    return fired

def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def csv_reconcile_ok():
    """Every enabled CSV call pack must have a stamp for its current inputs."""
    dispatch_dir = Path(E("dispatch"))
    if not dispatch_dir.is_dir():
        return True
    for call_path in dispatch_dir.glob("batch_w*.call.json"):
        try:
            call = json.loads(call_path.read_text(encoding="utf-8"))
            post = call.get("required_postprocess") or {}
            if not post.get("enabled", True):
                continue
            batch = Path(call["csv_path"])
            results = Path(call["output_csv_path"])
            stamp = json.loads(Path(post["stamp"]).read_text(encoding="utf-8"))
            if (stamp.get("schema") != "codex-loop-csv-reconcile-stamp/v1"
                    or stamp.get("batch_sha256") != file_sha256(batch)
                    or stamp.get("results_sha256") != file_sha256(results)):
                return False
        except (OSError, ValueError, KeyError, TypeError):
            return False
    return True

def wave_check(led):
    """Transition 18 precondition: all packets terminal + missing-item check."""
    states = [p["state"] for p in led["packets"].values()]
    all_term = states and all(s in TERMINAL or s == "WAVE_DONE" for s in states)
    # Reconcile only the packet set represented by this ledger/wave.  The old
    # shell helper scans every manifest under data/packets, so packets from a
    # different wave (or a retained historical manifest) could incorrectly
    # block the current wave.  The in-process check is also shell-free.
    reports_ok = all(
        os.path.isfile(E("reports", pid, "report.json"))
        and os.path.getsize(E("reports", pid, "report.json")) > 0
        for pid in led.get("packets", {})
    )
    return bool(all_term and reports_ok and csv_reconcile_ok())

def main():
    # Layered extends the transition table; cold_start and shadow preserve v1.
    # Launchers keep calling this file, so routing.mode is also the rollback.
    policy_path = os.path.join(ROOT, "config", "orchestration_policy_v2.toml")
    try:
        import tomllib
        with open(policy_path, "rb") as handle:
            routing_mode = str(tomllib.load(handle).get("routing", {}).get(
                "mode", "cold_start"))
    except (OSError, ValueError):
        routing_mode = "cold_start"
    if routing_mode == "layered":
        from statemachine_v2 import main as v2_main
        return v2_main(sys.argv[1:])
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
        ledger_cursor = led.get("event_cursor")
        if isinstance(ledger_cursor, int) and not isinstance(ledger_cursor, bool):
            off = max(0, ledger_cursor)
        else:
            off = int(open(cursor_f).read().strip() or 0) if os.path.exists(cursor_f) else 0
        consumed = off
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
            consumed = f.tell()
        reconcile(led)
        if watchdog(led):  # H-02: watchdog appends audit 'timeout' events —
            # advance the cursor past them (already applied in-place above).
            consumed = os.path.getsize(E("events.ndjson"))
        # State and consumed offset share one atomic ledger commit.  The
        # external cursor remains a compatibility cache for older tooling.
        led["event_cursor"] = consumed
        save_ledger(led)
        open(cursor_f, "w").write(str(consumed))
        try:
            from orchestration_epilogue import run_epilogue
            run_epilogue(ROOT, source="statemachine_v1_%s" % args.cmd)
        except Exception as exc:
            sys.stderr.write("orchestration epilogue failed visibly: %s\n" % exc)
        print(json.dumps({p: v["state"] for p, v in led["packets"].items()}))
        return 2 if dead else 0
    if args.cmd == "wave-check":
        ok = wave_check(led)
        print("WAVE_DONE_READY" if ok else "WAVE_INCOMPLETE")
        return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
