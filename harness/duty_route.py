#!/usr/bin/env python3
"""Production outlet for packets waiting in DUTY_REVIEW.

Spawns the pinned duty_officer in read-only mode (or ingests a supplied ruling
for deterministic automation), validates the ruling through duty_gate.py, and
appends exactly one duty_retryable/duty_fixable/duty_terminal event.  The state
machine remains the sole owner of state transitions.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from dispatch import agent_overrides
from lifecycle_supervisor import locked
from loop_config import config_bool


ROOT = os.environ.get("LOOP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
HARN = os.path.join(ROOT, "harness")
OUTCOMES = {"retryable": "duty_retryable",
            "fixable": "duty_fixable",
            "terminal": "duty_terminal"}


def load_ledger():
    return json.load(open(os.path.join(DATA, "progress_ledger.json"), encoding="utf-8"))


def append_event(pid, event, detail):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "events.ndjson"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": time.time(), "packet_id": pid,
                                 "event": event, "detail": detail},
                                separators=(",", ":")) + "\n")


def prior_outcome(pid):
    path = os.path.join(DATA, "events.ndjson")
    if not os.path.exists(path):
        return None
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("packet_id") == pid and event.get("event") in OUTCOMES.values():
            return event.get("event")
    return None


def extract_ruling(text):
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line.strip())
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("duty officer output contains no JSON object")


def spawn_ruling(pid, error_file):
    overrides, sandbox, model, effort = agent_overrides("duty_officer")
    out_dir = os.path.join(DATA, "duty_rulings")
    os.makedirs(out_dir, exist_ok=True)
    last_message = os.path.join(out_dir, pid + ".last_message.txt")
    events = os.path.join(out_dir, pid + ".events.jsonl")
    error_text = open(error_file, encoding="utf-8", errors="replace").read()[-8000:]
    report = os.path.join(DATA, "reports", pid, "report.json")
    prompt = ("You are the Duty Officer. Classify this DUTY_REVIEW failure. "
              "Return exactly one JSON object with keys class, evidence, "
              "confidence, progress_ledger_delta, and optional fix_hint. "
              "class must be retryable|fixable|terminal; every evidence item "
              "must contain a report line number. packet_id=%s; report=%s; "
              "failure_tail=%s" % (pid, report, error_text))
    cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", sandbox,
           *overrides, "--json", "-o", last_message, prompt]
    with open(events, "wb") as event_stream:
        completed = subprocess.run(cmd, cwd=ROOT, stdout=event_stream,
                                   stderr=subprocess.PIPE)
    if completed.returncode != 0:
        raise RuntimeError("duty officer failed rc=%d" % completed.returncode)
    ruling = extract_ruling(open(last_message, encoding="utf-8", errors="replace").read())
    return ruling, {"model": model, "reasoning_effort": effort,
                    "events_path": events, "last_message_path": last_message}


def gate_ruling(pid, ruling, enforce, theta):
    delta = ruling.get("progress_ledger_delta")
    if not isinstance(delta, dict):
        delta = {}
    delta["packet_id"] = pid
    ruling["progress_ledger_delta"] = delta
    cmd = [sys.executable, os.path.join(HARN, "duty_gate.py"),
           "--theta", str(theta), "--enforce", "true" if enforce else "false"]
    completed = subprocess.run(cmd, input=json.dumps(ruling), text=True,
                               capture_output=True, env=os.environ.copy())
    try:
        gate = json.loads(completed.stdout.strip())
    except ValueError:
        gate = {"gate": "DEAD_LETTER", "reasons": ["gate output invalid"]}
    if completed.returncode in (0, 3) and ruling.get("class") in OUTCOMES:
        event = OUTCOMES[ruling["class"]]
    else:
        event = "duty_terminal"
    return event, gate


def adjudicate(pid, error_file=None, ruling_path=None, theta=0.7, enforce=None):
    """Adjudicate ONE DUTY_REVIEW packet and append exactly one
    duty_retryable/duty_fixable/duty_terminal event (idempotent).
    enforce=None resolves duty_officer.enforce from config (single source of
    truth): false = record-only (the state machine dead-letters), true =
    valid retryable/fixable rulings route back to RUNNING.  Returns exit
    code: 0 routed, 1 terminal, 2 wrong state/usage, 3 already adjudicated.
    This is the shared production entry used by duty_driver.py and the CLI."""
    ledger = load_ledger()
    state = ledger.get("packets", {}).get(pid, {}).get("state")
    if state != "DUTY_REVIEW":
        print("packet %s is %s, not DUTY_REVIEW" % (pid, state), file=sys.stderr)
        return 2
    previous = prior_outcome(pid)
    if previous:
        print("SKIP %s already has %s" % (pid, previous))
        return 3
    if enforce is None:
        enforce = config_bool("duty_officer", "enforce", False)
    try:
        if ruling_path:
            ruling = json.load(open(ruling_path, encoding="utf-8"))
            source = {"ruling_path": os.path.abspath(ruling_path)}
        else:
            if not error_file:
                print("--error-file is required without --ruling", file=sys.stderr)
                return 2
            ruling, source = spawn_ruling(pid, error_file)
        event, gate = gate_ruling(pid, ruling, enforce, theta)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        ruling, source = {}, {"error": str(exc)}
        event, gate = "duty_terminal", {"gate": "DEAD_LETTER",
                                         "reasons": [str(exc)]}
    with locked(Path(DATA) / "lifecycle" / ".events.lock"):
        raced = prior_outcome(pid)   # re-check under the lock: concurrent drain
        if raced:                    # must never double-adjudicate
            print("SKIP %s already has %s" % (pid, raced))
            return 3
        append_event(pid, event, {"class": ruling.get("class"),
                     "confidence": ruling.get("confidence"), "gate": gate,
                     "enforce": enforce, "theta": theta, **source})
    print(json.dumps({"packet_id": pid, "event": event,
                      "gate": gate.get("gate"), "enforce": enforce},
                     separators=(",", ":")))
    return 0 if event != "duty_terminal" else 1


def record_terminal(pid, reason):
    """Fail-visible fallback for unusable duty tickets (missing/empty failure
    text): applies the SAME ledger-state and prior-outcome idempotency gates
    as adjudicate(), then appends exactly one duty_terminal event.  Used by
    duty_driver drain mode; repeated drains skip (rc 3), never duplicate."""
    ledger = load_ledger()
    if ledger.get("packets", {}).get(pid, {}).get("state") != "DUTY_REVIEW":
        print("packet %s is %s, not DUTY_REVIEW"
              % (pid, ledger.get("packets", {}).get(pid, {}).get("state")),
              file=sys.stderr)
        return 2
    previous = prior_outcome(pid)
    if previous:
        print("SKIP %s already has %s" % (pid, previous))
        return 3
    gate = {"gate": "DEAD_LETTER", "reasons": [reason]}
    with locked(Path(DATA) / "lifecycle" / ".events.lock"):
        raced = prior_outcome(pid)   # atomic claim: no duplicate terminal
        if raced:
            print("SKIP %s already has %s" % (pid, raced))
            return 3
        append_event(pid, "duty_terminal", {"class": None, "confidence": None,
                                            "gate": gate, "error": reason})
    print(json.dumps({"packet_id": pid, "event": "duty_terminal",
                      "gate": "DEAD_LETTER"}, separators=(",", ":")))
    return 1


def main():
    parser = argparse.ArgumentParser(description="DUTY_REVIEW production router")
    parser.add_argument("--packet", required=True)
    parser.add_argument("--error-file")
    parser.add_argument("--ruling", help="pre-produced ruling JSON (automation/tests)")
    parser.add_argument("--theta", type=float, default=0.7)
    parser.add_argument("--enforce", choices=["true", "false"])
    args = parser.parse_args()
    enforce = None if args.enforce is None else args.enforce == "true"
    return adjudicate(args.packet, args.error_file, args.ruling,
                      args.theta, enforce)


if __name__ == "__main__":
    raise SystemExit(main())
