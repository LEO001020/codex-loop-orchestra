#!/usr/bin/env python3
# ============================================================================
# duty_gate.py — Duty officer ruling JSON whitelist gate (S6 fence)
# ----------------------------------------------------------------------------
# Purpose : Validates a Duty Officer ruling before any script acts on it.
#           The ruling is an ENUM JSON, never free text — the officer's
#           words are input, never authorization. Whitelist: class in
#           {retryable, fixable, terminal}; evidence must be a non-empty
#           list of report line-number pointers (must contain digits, e.g.
#           "report.md:41" or "line 12"); confidence float >= theta.
#           fix_hint (H-01 hedge): whitelisted CONSTRAINED string — legal
#           ONLY when class=fixable AND length <= 200 chars, otherwise the
#           whole ruling is rejected. A valid enforced fixable ruling is
#           persisted to data/duty_rulings/<pid>.json so dispatch.py can
#           splice the hint into the re-dispatch handle line (t12 wiring).
#           The hint stays data, never authorization — gate exit codes alone
#           route, and the gate can still only reject or record.
#           Anything off-whitelist (unknown keys, free text, low confidence,
#           unparsable input) routes to DEAD_LETTER (exit 1) — fail-visible.
#           F2 cold start (--enforce false): a valid ruling is only RECORDED
#           (exit 3); the packet still takes the original dead-letter path.
# Input   : ruling JSON on stdin or --ruling FILE:
#           {"class":"retryable|fixable|terminal","evidence":["...:<line>"],
#            "confidence":0-1,"progress_ledger_delta":{}}
#           --theta confidence floor (default 0.7); --enforce true|false
# Output  : stdout one-line JSON gate result
#           exit 0 = valid+enforced, 3 = valid+record-only (F2),
#           1 = off-whitelist -> DEAD_LETTER, 2 = usage error
# Lines   : ~110
# ============================================================================
import argparse
import json
import os
import re
import sys

ROOT = os.environ.get("LOOP_ROOT",
                      os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
LEGAL_CLASSES = {"retryable", "fixable", "terminal"}
ALLOWED_KEYS = {"class", "evidence", "confidence", "progress_ledger_delta",
                "fix_hint"}  # fix_hint: constrained — fixable-only, <=200 chars
FIX_HINT_MAX = 200
LINE_PTR_RE = re.compile(r"\d+")  # evidence must carry line numbers
# Packet-id -> filename safety: 1-96 ASCII letters/digits/._- ONLY.  Any
# separator (/, \, Unicode), "..", or absolute path is rejected so a pid can
# never escape data/duty_rulings/.  Fail-visible: SystemExit(1) before write.
PACKET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")


def safe_pid_filename(pid):
    if not isinstance(pid, str) or not PACKET_ID_RE.fullmatch(pid):
        sys.stderr.write("invalid packet id %r: allowed 1-96 ASCII "
                         "letters/digits/._- only; refusing write\n" % (pid,))
        raise SystemExit(1)
    return pid


def persist_fix_hint(ruling):
    """Best-effort t12 wiring: store the VALIDATED fixable ruling so
    dispatch.py can splice fix_hint into the previous-attempt handle line.
    Persistence failure only drops the hint (stderr warn) — it can never
    turn a rejection into a pass or vice versa."""
    pid = (ruling.get("progress_ledger_delta") or {}).get("packet_id")
    if not pid or ruling.get("class") != "fixable" or "fix_hint" not in ruling:
        return
    safe_pid_filename(pid)  # invalid pid: fail-visible, never written
    try:
        os.makedirs(os.path.join(DATA, "duty_rulings"), exist_ok=True)
        path = os.path.join(DATA, "duty_rulings", "%s.json" % pid)
        json.dump({"class": ruling["class"], "fix_hint": ruling["fix_hint"],
                   "confidence": ruling["confidence"]},
                  open(path, "w", encoding="utf-8"), indent=1)
    except OSError as exc:
        print("warn: cannot persist fix_hint ruling: %s" % exc, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="duty officer ruling gate")
    ap.add_argument("--ruling", default=None, help="ruling JSON file")
    ap.add_argument("--theta", type=float, default=0.7)
    ap.add_argument("--enforce", default="false", choices=["true", "false"])
    args = ap.parse_args()
    raw = (open(args.ruling, encoding="utf-8").read()
           if args.ruling else sys.stdin.read())
    reasons = []
    try:
        ruling = json.loads(raw)
        if not isinstance(ruling, dict):
            raise ValueError("ruling is not a JSON object")
    except (ValueError, json.JSONDecodeError):
        ruling = {}
        reasons.append("FREE_TEXT_REJECTED: ruling is not valid JSON object")

    if ruling:
        extra = set(ruling) - ALLOWED_KEYS
        if extra:  # off-whitelist keys are a channel for smuggled authority
            reasons.append("OFF_WHITELIST_KEYS: %s" % sorted(extra))
        if ruling.get("class") not in LEGAL_CLASSES:
            reasons.append("ILLEGAL_CLASS: %r" % ruling.get("class"))
        ev = ruling.get("evidence")
        if not isinstance(ev, list) or not ev:
            reasons.append("EVIDENCE_MISSING: non-empty list required")
        elif not all(isinstance(e, str) and LINE_PTR_RE.search(e) for e in ev):
            reasons.append("EVIDENCE_NO_LINE_NUMBERS: every item needs a "
                           "report line pointer")
        if "fix_hint" in ruling:  # constrained string: fixable-only, bounded
            hint = ruling["fix_hint"]
            if ruling.get("class") != "fixable":
                reasons.append("FIX_HINT_CLASS_ILLEGAL: fix_hint is only "
                               "legal when class=fixable (got %r)"
                               % ruling.get("class"))
            if not isinstance(hint, str) or not hint.strip():
                reasons.append("FIX_HINT_NOT_STRING: non-empty string required")
            elif len(hint) > FIX_HINT_MAX:
                reasons.append("FIX_HINT_TOO_LONG: %d > %d chars"
                               % (len(hint), FIX_HINT_MAX))
        conf = ruling.get("confidence")
        if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            reasons.append("CONFIDENCE_ILLEGAL: %r" % conf)
        elif conf < args.theta:
            reasons.append("CONFIDENCE_BELOW_THETA: %.3f < %.3f"
                           % (conf, args.theta))

    if reasons:
        print(json.dumps({"gate": "DEAD_LETTER", "reasons": reasons}))
        return 1  # off-whitelist => dead-letter, never silent
    if args.enforce == "false":  # F2 cold start: record, do not route
        print(json.dumps({"gate": "RECORDED_NOT_ENFORCED",
                          "class": ruling["class"],
                          "confidence": ruling["confidence"]}))
        return 3
    persist_fix_hint(ruling)  # t12 wiring: hint handle for re-dispatch (H-01)
    print(json.dumps({"gate": "VALID", "class": ruling["class"],
                      "confidence": ruling["confidence"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
