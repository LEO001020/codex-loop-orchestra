#!/usr/bin/env python3
# ============================================================================
# run_evidence.py — Run Evidence Entry (stable index over existing evidence)
# Purpose : One stable programmatic entry point that links a logical LOOP run
#           to the durable evidence it already produced: canonical ledger
#           history, per-packet reports (incl. archived prior attempts), exec
#           roster rows, dead letters, duty tickets, sol wakes, Root decision
#           records. It is a DERIVED INDEX — never a second task authority;
#           it can be rebuilt at any time from the files it points to.
# Input   : data/progress_ledger.json, data/reports/**, data/lifecycle/
#           exec_roster.json, data/dead_letters/, data/duty_review/,
#           data/sol_wake/, data/decisions/decisions.ndjsonl.
# Output  : data/evidence/index.json (atomic write) + stdout summary.
#           `record-decision` appends a Root external decision (with
#           evidence refs) to data/decisions/decisions.ndjsonl — durable
#           provenance for decisions that exist only in the Root transcript.
# Failure : fail-open per evidence source; a missing/unreadable source is
#           skipped, never fatal. Zero LLM calls.
# Lines   : ~300 (including comments)
# ============================================================================
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

EVIDENCE_SCHEMA = "codex-loop-run-evidence/v1"
DECISIONS_RELPATH = os.path.join("decisions", "decisions.ndjsonl")
HISTORY_TAIL = 8
ROSTER_ROWS_CAP = 10


def _utc(ts):
    return datetime.datetime.fromtimestamp(
        ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _rel(path, root):
    try:
        rel = str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        rel = str(path)
    return rel.replace(os.sep, "/")  # jq/rg-friendly, platform-stable


def load_ledger(data_dir):
    led = _read_json(os.path.join(data_dir, "progress_ledger.json"))
    if not isinstance(led, dict):
        return {"packets": {}, "event_cursor": None}
    led.setdefault("packets", {})
    return led


def load_decisions(data_dir, packet_id=None):
    """All recorded Root decisions, optionally filtered by packet."""
    path = os.path.join(data_dir, DECISIONS_RELPATH)
    rows = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if packet_id is None or row.get("packet_id") == packet_id:
                    rows.append(row)
    except OSError:
        return []
    return rows


def append_decision(root, packet_id, decision, note="", refs=(),
                    actor="root", data_dir=None):
    """Append one durable Root decision row (with evidence refs).

    This is deliberate provenance for decisions that would otherwise exist
    only in the Root model transcript. It drives no state transition.
    """
    data_dir = data_dir or os.path.join(os.path.abspath(root), "data")
    if not packet_id or not decision:
        raise ValueError("record-decision requires --packet and --decision")
    row = {
        "ts": datetime.datetime.now(datetime.timezone.utc).timestamp(),
        "ts_utc": _utc(datetime.datetime.now(datetime.timezone.utc)
                       .timestamp()),
        "packet_id": packet_id,
        "decision": str(decision),
        "note": str(note or "")[:2000],
        "evidence_refs": [str(r) for r in refs],
        "actor": actor,
    }
    path = os.path.join(data_dir, DECISIONS_RELPATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = path + ".lock"
    with open(lock, "a", encoding="utf-8") as guard:
        try:
            import fcntl
            fcntl.flock(guard, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass  # Windows: single-writer Root decisions; best effort lock
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _packet_reports(root, data_dir, pid):
    """Report evidence for one packet (current + archived prior attempts).

    All index paths are relative to the control root so one anchor serves
    every consumer (jq/rg/python from the repo root).
    """
    found = {}
    rdir = os.path.join(data_dir, "reports", pid)
    report = os.path.join(rdir, "report.json")
    if os.path.exists(report):
        found["report"] = _rel(report, root)
    prev_dir = os.path.join(rdir, "previous")
    prior = []
    if os.path.isdir(prev_dir):
        for name in sorted(os.listdir(prev_dir)):
            if name.endswith(".json"):
                prior.append(_rel(os.path.join(prev_dir, name), root))
    if prior:
        found["prior_attempts"] = prior
    hw = os.path.join(data_dir, "reports", "headless-wave", pid)
    if os.path.isdir(hw):
        runs = sorted(os.listdir(hw))[-ROSTER_ROWS_CAP:]
        found["headless_runs"] = [
            _rel(os.path.join(hw, r), root) for r in runs]
    return found


def _roster_rows(data_dir, pid):
    roster = _read_json(os.path.join(data_dir, "lifecycle",
                                     "exec_roster.json"))
    jobs = roster.get("jobs", roster) if isinstance(roster, dict) else {}
    rows = []
    if not isinstance(jobs, dict):
        return rows
    for _key, row in jobs.items():
        if isinstance(row, dict) and row.get("packet_id") == pid:
            rows.append({
                "run_id": row.get("run_id"),
                "state": row.get("state"),
                "attempt": row.get("attempt"),
                "exit_code": row.get("exit_code"),
                "published_report": row.get("published_report"),
                "stderr_path": row.get("stderr_path"),
                "stdout_path": row.get("stdout_path"),
                "started_at": row.get("started_at"),
            })
    rows.sort(key=lambda r: float(r.get("started_at") or 0), reverse=True)
    return rows[:ROSTER_ROWS_CAP]


def build_index(root, data_dir=None):
    """Rebuildable index: packet → state/disposition + its evidence paths."""
    root = os.path.abspath(root)
    data_dir = data_dir or os.path.join(root, "data")
    led = load_ledger(data_dir)
    packets = {}
    for pid, entry in led.get("packets", {}).items():
        if not isinstance(entry, dict):
            continue
        hist = [
            {"ts": h.get("ts"), "to": h.get("to"), "via": h.get("via")}
            for h in (entry.get("history") or [])[-HISTORY_TAIL:]
        ]
        dead = _read_json(os.path.join(data_dir, "dead_letters",
                                       "%s.json" % pid))
        node = {
            "state": entry.get("state"),
            "attempts": entry.get("attempts", 0),
            "role": entry.get("role"),
            "task_name": entry.get("task_name"),
            "manifest_id": entry.get("manifest_id"),
            "parent_session_id": entry.get("parent_session_id"),
            "current_run_id": entry.get("current_run_id"),
            "history_tail": hist,
            "last_transition": hist[-1] if hist else None,
        }
        node.update(_packet_reports(root, data_dir, pid))
        if dead:
            node["dead_letter"] = {
                "path": _rel(os.path.join(data_dir, "dead_letters",
                                          "%s.json" % pid), root),
                "reason": dead.get("reason"),
                "ts": dead.get("ts"),
            }
        duty = _read_json(os.path.join(data_dir, "duty_review",
                                       "%s.json" % pid))
        if duty:
            node["duty_review"] = _rel(os.path.join(data_dir, "duty_review",
                                                    "%s.json" % pid), root)
        wakes = []
        wake_dir = os.path.join(data_dir, "sol_wake")
        if os.path.isdir(wake_dir):
            for name in sorted(os.listdir(wake_dir)):
                if pid in name:
                    wakes.append(_rel(os.path.join(wake_dir, name), root))
        if wakes:
            node["sol_wake"] = wakes
        rows = _roster_rows(data_dir, pid)
        if rows:
            node["exec_runs"] = rows
        decisions = load_decisions(data_dir, pid)
        if decisions:
            node["decisions"] = decisions
        packets[pid] = node
    index = {
        "schema": EVIDENCE_SCHEMA,
        "generated_at": _utc(datetime.datetime.now(
            datetime.timezone.utc).timestamp()),
        "control_root": root,
        "event_cursor": led.get("event_cursor"),
        "canonical_state": _rel(os.path.join(data_dir,
                                             "progress_ledger.json"), root),
        "events": _rel(os.path.join(data_dir, "events.ndjson"), root),
        "counts": {
            "packets": len(packets),
            "decisions": len(load_decisions(data_dir)),
        },
        "how_to_query": {
            "one_packet": "jq '.packets[\"<packet_id>\"]' data/evidence/index.json",
            "packet_events": "jq 'select(.packet_id==\"<packet_id>\")' data/events.ndjson",
            "all_decisions": "cat data/decisions/decisions.ndjsonl",
            "note": "historical evidence is from its time; the ledger "
                    "history_tail above carries each packet's later "
                    "disposition (retried / dead-lettered / merged / …)",
        },
        "packets": packets,
    }
    out_dir = os.path.join(data_dir, "evidence")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "index.json")
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=1)
    os.replace(tmp, out_path)
    return index


def show(root, data_dir=None):
    index_path = os.path.join(data_dir or os.path.join(root, "data"),
                              "evidence", "index.json")
    index = _read_json(index_path)
    if not index:
        return ("no evidence index yet — build it with: "
                "python harness/run_evidence.py build\n"
                "evidence lives under data/: progress_ledger.json, "
                "events.ndjson, reports/, lifecycle/, dead_letters/, "
                "duty_review/, sol_wake/, decisions/")
    counts = index.get("counts", {})
    return "\n".join([
        "RUN EVIDENCE ENTRY",
        "index: %s (schema %s, generated %s)" % (
            _rel(index_path, root), index.get("schema"),
            index.get("generated_at")),
        "packets indexed: %s    decisions recorded: %s" % (
            counts.get("packets", 0), counts.get("decisions", 0)),
        "query one packet: " + index.get("how_to_query", {}).get(
            "one_packet", ""),
        "query events:    " + index.get("how_to_query", {}).get(
            "packet_events", ""),
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run Evidence Entry — stable index over existing "
                    "durable evidence")
    parser.add_argument("command", nargs="?", default="show",
                        choices=("build", "show", "record-decision"))
    parser.add_argument("--root", default=os.environ.get(
        "LOOP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    parser.add_argument("--packet", help="packet id (record-decision)")
    parser.add_argument("--decision", help="accept|reject|requeue|replan|"
                        "integrate|hold|… (record-decision)")
    parser.add_argument("--note", default="")
    parser.add_argument("--ref", action="append", default=[],
                        help="evidence ref (path); repeatable")
    args = parser.parse_args(argv)
    data_dir = os.path.join(args.root, "data")
    if args.command == "build":
        index = build_index(args.root, data_dir)
        print(show(args.root, data_dir))
        return 0
    if args.command == "record-decision":
        row = append_decision(args.root, args.packet, args.decision,
                              note=args.note, refs=args.ref)
        print(json.dumps(row, ensure_ascii=False))
        return 0
    print(show(args.root, data_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
