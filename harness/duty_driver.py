#!/usr/bin/env python3
# ============================================================================
# duty_driver.py 鈥?DUTY_REVIEW production driver (retry.py -> duty_route.py)
# ----------------------------------------------------------------------------
# Purpose : The production call link between retry.py's duty_review handoff
#           and duty_route.py's adjudication events.  Every packet retry.py
#           routes to DUTY_REVIEW is queued as a ticket
#           (data/duty_review/<pid>.json); this driver drains those tickets
#           (or adjudicates a single --packet) and invokes
#           duty_route.adjudicate(), which ingests the pinned duty officer's
#           ruling (or a supplied --ruling for deterministic automation),
#           validates it through duty_gate.py, and appends exactly one
#           duty_retryable|duty_fixable|duty_terminal event.
#           Ordering: run AFTER the state-machine step that applied
#           duty_review (FAILED -> DUTY_REVIEW) so the ledger state gate
#           holds.  Idempotent: a packet with a prior duty_* outcome or a
#           non-DUTY_REVIEW state is skipped (rc 3 / rc 2), never re-routed.
#           enforce semantics come from duty_officer.enforce (config) unless
#           overridden: false = record-only (state machine dead-letters),
#           true = valid retryable/fixable rulings route back to RUNNING;
#           illegal / low-confidence / terminal rulings emit duty_terminal ->
#           DEAD_LETTER, always fail-visible.
# Input   : --packet PID (single mode) or no --packet (drain all tickets);
#           --ruling FILE (deterministic automation); --error / --error-file
#           for single mode; --theta; --enforce true|false (default config)
# Output  : one decision JSON line per adjudicated packet; rc 0 = all ok,
#           1 = any terminal, 2 = usage/state error, 3 = skipped (idempotent)
# Lines   : ~110
# ============================================================================
import argparse
import glob
import json
import os
import sys

ROOT = os.environ.get("LOOP_ROOT",
                      os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
TICKET_DIR = os.path.join(DATA, "duty_review")


def load_tickets():
    """All readable retry.py duty tickets, oldest first (sorted by path)."""
    if not os.path.isdir(TICKET_DIR):
        return []
    tickets = []
    for path in sorted(glob.glob(os.path.join(TICKET_DIR, "*.json"))):
        try:
            ticket = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            sys.stderr.write("duty_driver: unreadable ticket %s: %s\n" % (path, exc))
            continue
        if isinstance(ticket, dict) and ticket.get("packet_id"):
            tickets.append(ticket)
    return tickets


def materialize_error(ticket):
    """Ticket carries the failure text; duty_route.spawn_ruling reads an
    error FILE, so materialize one next to the ticket (best-effort)."""
    err = ticket.get("error")
    if not isinstance(err, str) or not err:
        return None
    path = os.path.join(TICKET_DIR, "%s.error.txt" % ticket["packet_id"])
    try:
        os.makedirs(TICKET_DIR, exist_ok=True)
        open(path, "w", encoding="utf-8").write(err)
        return path
    except OSError as exc:
        sys.stderr.write("duty_driver: cannot write error file %s: %s\n" % (path, exc))
        return None


def main():
    ap = argparse.ArgumentParser(description="DUTY_REVIEW production driver")
    ap.add_argument("--packet", default=None)
    ap.add_argument("--error", default=None)
    ap.add_argument("--error-file", default=None)
    ap.add_argument("--ruling", default=None,
                    help="pre-produced ruling JSON (deterministic automation)")
    ap.add_argument("--theta", type=float, default=0.7)
    ap.add_argument("--enforce", choices=["true", "false"], default=None,
                    help="default: duty_officer.enforce from config")
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import duty_route

    enforce = None if args.enforce is None else args.enforce == "true"
    if args.packet:
        error_file = args.error_file
        if args.error is not None:
            error_file = os.path.join(TICKET_DIR, "%s.error.txt" % args.packet)
            try:
                os.makedirs(TICKET_DIR, exist_ok=True)
                open(error_file, "w", encoding="utf-8").write(args.error)
            except OSError as exc:
                print("cannot write error file: %s" % exc, file=sys.stderr)
                return 2
        return duty_route.adjudicate(args.packet, error_file, args.ruling,
                                     args.theta, enforce)

    tickets = load_tickets()
    if not tickets:
        print(json.dumps({"drained": 0, "skipped": 0, "terminal": 0}))
        return 0
    worst = 0
    for ticket in tickets:
        pid = ticket["packet_id"]
        error_file = materialize_error(ticket)
        if not args.ruling and not error_file:
            # Reuse the state + prior-outcome idempotency gates: a repeated
            # drain of a malformed ticket skips (rc 3) instead of appending
            # a second duty_terminal.
            rc = duty_route.record_terminal(pid, "duty ticket has no failure text")
        else:
            rc = duty_route.adjudicate(pid, error_file, args.ruling,
                                       args.theta, enforce)
        if rc == 1:
            worst = 1
        elif rc == 2:
            worst = max(worst, 2)
        elif rc == 3:
            worst = max(worst, 3)  # idempotent skip surfaces to the caller
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
