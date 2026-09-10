#!/usr/bin/env python3
# ============================================================================
# run_view.py — Current Run View (derived observation, NOT a control system)
# Purpose : Deterministic, zero-LLM projection of the canonical LOOP state
#           (data/progress_ledger.json, joined with the exec roster) so any
#           agent (Root/Worker/Reviewer) can see what first-class work is
#           active right now, and where durable evidence lives. The ledger
#           remains the ONLY execution authority; this module is pure read.
# Input   : data/progress_ledger.json, data/packets/<pid>.json (fallback
#           fields), data/lifecycle/exec_roster.json (liveness overlay),
#           data/dead_letters/<pid>.json (terminal reason).
# Output  : compact text view / JSON (stdout). Injected once at task
#           boundaries (worker spawn prompt, SubagentStart) — never per turn.
# Failure : fail-open. Any unreadable input degrades to a minimal view; the
#           caller (dispatch/hook) must never break because of this module.
# Lines   : ~230 (including comments)
# ============================================================================
import argparse
import datetime
import json
import os
import sys

VIEW_SCHEMA = "codex-loop-run-view/v1"

# Terminal in both statemachine v1 and v2. SOL_ADJUDICATE is v1-terminal but
# semantically "waiting for Root" — shown in its own section, not buried.
TERMINAL_STATES = {"MERGED", "DONE", "DEAD_LETTER"}
AWAITING_ROOT_STATES = {"SOL_ADJUDICATE", "WAVE_DONE", "WAVE_DONE_READY",
                        "MERGE_CONFLICT"}


def _utc(ts):
    return datetime.datetime.fromtimestamp(
        ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short(pid, width=20):
    pid = str(pid)
    return pid if len(pid) <= width else pid[:width] + "…"


def load_ledger(data_dir):
    """Fail-open canonical state read: corrupt/missing ledger → empty run."""
    path = os.path.join(data_dir, "progress_ledger.json")
    try:
        with open(path, encoding="utf-8") as handle:
            led = json.load(handle)
    except (OSError, ValueError):
        return {"packets": {}, "event_cursor": None}
    if not isinstance(led, dict):
        return {"packets": {}, "event_cursor": None}
    led.setdefault("packets", {})
    return led


def load_roster(data_dir):
    """Fail-open liveness overlay from the exec roster (packet-keyed rows)."""
    path = os.path.join(data_dir, "lifecycle", "exec_roster.json")
    try:
        with open(path, encoding="utf-8") as handle:
            roster = json.load(handle)
    except (OSError, ValueError):
        return {}
    jobs = roster.get("jobs", roster) if isinstance(roster, dict) else {}
    live = {}
    if isinstance(jobs, dict):
        for _key, row in jobs.items():
            if not isinstance(row, dict):
                continue
            pid = row.get("packet_id")
            if not pid:
                continue
            prev = live.get(pid)
            # newest started_at wins
            if prev is None or float(row.get("started_at") or 0) >= float(
                    prev.get("started_at") or 0):
                live[pid] = row
    return live


def _packet_goal(data_dir, pid, entry):
    goal = entry.get("goal")
    if goal:
        return str(goal)
    try:
        with open(os.path.join(data_dir, "packets", "%s.json" % pid),
                  encoding="utf-8") as handle:
            return str(json.load(handle).get("goal") or "")
    except (OSError, ValueError):
        return ""


def _packet_task(data_dir, pid, entry):
    task = entry.get("task_name")
    if task:
        return str(task)
    goal = _packet_goal(data_dir, pid, entry)
    return goal[:60]


def _last_ts(entry):
    hist = entry.get("history") or []
    return float(hist[-1].get("ts") or 0) if hist else 0.0


def snapshot(root, data_dir=None, exclude=None):
    """Pure read projection of canonical state → view document.

    ``exclude`` omits packet ids from the ACTIVE section only (a worker
    receiving this view already carries its own packet in the prompt; the
    view exists to show the rest of the concurrent world).
    """
    root = os.path.abspath(root)
    data_dir = data_dir or os.path.join(root, "data")
    led = load_ledger(data_dir)
    live = load_roster(data_dir)
    packets = led.get("packets") or {}
    counts = {}
    active, awaiting, terminal = [], [], []
    for pid, entry in packets.items():
        if not isinstance(entry, dict):
            continue
        state = str(entry.get("state") or "NONE")
        counts[state] = counts.get(state, 0) + 1
        if exclude and pid in exclude:
            continue
        hist = entry.get("history") or []
        base = {
            "packet_id": pid,
            "state": state,
            "attempt": entry.get("attempts", 0),
            "role": entry.get("role") or "",
            "task": _packet_task(data_dir, pid, entry)[:70],
            "scope": entry.get("authorized_paths") or [],
            "worktree": entry.get("cwd") or "",
            "run_id": entry.get("current_run_id") or "",
            "manifest_id": entry.get("manifest_id") or "",
            "last_ts": _last_ts(entry),
            "live": (live.get(pid, {}).get("state") or "") if pid in live else "",
        }
        if state in TERMINAL_STATES:
            node = dict(base)
            if state == "DEAD_LETTER":
                try:
                    with open(os.path.join(data_dir, "dead_letters",
                                           "%s.json" % pid),
                              encoding="utf-8") as handle:
                        node["dead_letter_reason"] = str(
                            json.load(handle).get("reason") or "")
                except (OSError, ValueError):
                    node["dead_letter_reason"] = ""
            report = os.path.join(data_dir, "reports", pid, "report.json")
            node["report"] = ("data/reports/%s/report.json" % pid
                              if os.path.exists(report) else "")
            terminal.append(node)
        elif state in AWAITING_ROOT_STATES:
            awaiting.append(base)
        else:
            active.append(base)

    def recent(node):
        return node.get("last_ts") or 0.0

    active.sort(key=lambda n: (n["state"] != "RUNNING", -recent(n)))
    awaiting.sort(key=lambda n: -recent(n))
    terminal.sort(key=lambda n: -recent(n))
    return {
        "schema": VIEW_SCHEMA,
        "run": root,
        "event_cursor": led.get("event_cursor"),
        "generated_at": _utc(datetime.datetime.now(
            datetime.timezone.utc).timestamp()),
        "counts": counts,
        "active": active,
        "awaiting_root": awaiting,
        "terminal_recent": terminal,
    }


def render_view(root, max_active=30, max_awaiting=8, max_terminal=8,
                data_dir=None, exclude=None):
    """Compact bounded text view of the current run (present tense)."""
    doc = snapshot(root, data_dir, exclude=exclude)
    cursor = doc["event_cursor"]
    cursor_s = str(cursor) if cursor is not None else "n/a"
    lines = [
        "RUN CONTEXT (derived observation; the ledger stays the only "
        "execution authority)",
        "run: %s" % doc["run"],
        "state_cursor: %s    generated_at: %s" % (cursor_s, doc["generated_at"]),
    ]
    counts = doc["counts"]
    lines.append("counts: " + (" ".join(
        "%s=%d" % (k.lower(), v) for k, v in sorted(counts.items()))
        or "no packets"))

    def emit_group(title, items, limit, long_form):
        shown = items[:limit]
        lines.append("%s (%d%s)" % (title, len(items),
                                    "" if len(items) <= limit
                                    else ", showing %d" % len(shown)))
        for node in shown:
            lines.extend(long_form(node))
        if len(items) > len(shown):
            lines.append("  … +%d more — python harness/run_view.py" %
                         (len(items) - len(shown)))
        if not items:
            lines.append("  (none)")

    def active_rows(node):
        head = "  %s %s a%s %s" % (_short(node["packet_id"]), node["state"],
                                   node["attempt"],
                                   ("live:" + node["live"]) if node["live"]
                                   else node["role"])
        line = head + (" task: %s" % node["task"] if node["task"] else "")
        out = [line]
        detail = []
        if node["scope"]:
            detail.append("scope: %s" % ", ".join(node["scope"][:4]))
        if node["worktree"]:
            detail.append("wt: %s" % node["worktree"])
        if detail:
            out.append("    " + " | ".join(detail))
        return out

    def short_rows(node):
        head = "  %s %s a%s" % (_short(node["packet_id"]), node["state"],
                                node["attempt"])
        if node.get("role"):
            head += " %s" % node["role"]
        if node["task"]:
            head += " task: %s" % node["task"]
        return [head]

    def terminal_rows(node):
        head = "  %s %s a%s" % (_short(node["packet_id"]), node["state"],
                                node["attempt"])
        if node["state"] == "DEAD_LETTER" and node.get("dead_letter_reason"):
            head += " reason: %s -> data/dead_letters/%s.json" % (
                node["dead_letter_reason"], node["packet_id"])
        elif node.get("report"):
            head += " evidence: %s" % node["report"]
        return [head]

    emit_group("ACTIVE WORK", doc["active"], max_active, active_rows)
    emit_group("AWAITING ROOT DECISION", doc["awaiting_root"], max_awaiting,
               short_rows)
    emit_group("RECENT TERMINAL (newest first)", doc["terminal_recent"],
               max_terminal, terminal_rows)
    lines += [
        "EVIDENCE ENTRY: data/evidence/index.json  "
        "(rebuild: python harness/run_evidence.py build)",
        "Historical reports/decisions are evidence from their time; before "
        "relying on an old conclusion, check that packet's later history "
        "(ledger history / evidence index disposition).",
    ]
    return "\n".join(lines)


def worker_context_block(root, max_active=12, data_dir=None, exclude=None):
    """Block appended once to a fresh Worker/Reviewer spawn prompt.

    ``exclude`` names the packet the receiving worker is about to execute:
    its own task is already in the prompt's four-field header, and it is
    not yet someone else's concurrent work.
    """
    return render_view(root, max_active=max_active, max_awaiting=4,
                       max_terminal=4, data_dir=data_dir, exclude=exclude)


def root_pointer_block(root, data_dir=None):
    """Small stable pointer injected at Root SessionStart (not a full board)."""
    doc = snapshot(root, data_dir)
    counts = doc["counts"]
    running = sum(counts.get(s, 0) for s in ("RUNNING", "DISPATCHABLE"))
    return "\n".join([
        "ROOT RUN CONTEXT",
        "run: %s" % doc["run"],
        "canonical state: data/progress_ledger.json (event_cursor: %s)"
        % (doc["event_cursor"] if doc["event_cursor"] is not None else "n/a"),
        "events: data/events.ndjson",
        "current work: %d running/dispatchable, %s packets total — full view: "
        "python harness/run_view.py" % (running, sum(counts.values())),
        "historical evidence: data/evidence/index.json "
        "(python harness/run_evidence.py build|show)",
        "Inspect prior Root decisions, Worker/Reviewer outcomes, tests, "
        "Git/worktree evidence and artifacts programmatically (rg/jq/python) "
        "when relevant.",
        "Historical conclusions may have been superseded; inspect the later "
        "attempt/review/adjudication state before relying on an old "
        "conclusion.",
    ])


def hook_block(root, event):
    """Block for the SessionStart/SubagentStart context hook (fail-open)."""
    if event == "SessionStart":
        return root_pointer_block(root)
    if event == "SubagentStart":
        return worker_context_block(root, max_active=16)
    return ""


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Current Run View — derived projection of canonical LOOP state")
    parser.add_argument("--root", default=os.environ.get(
        "LOOP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--max-active", type=int, default=30)
    args = parser.parse_args(argv)
    if args.json:
        print(json.dumps(snapshot(args.root), ensure_ascii=False, indent=1))
    else:
        print(render_view(args.root, max_active=args.max_active))
    return 0


if __name__ == "__main__":
    sys.exit(main())
