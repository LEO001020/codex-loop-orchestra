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
# Lines   : 84
# ============================================================================
import argparse
import json
import re
import sys

LEGAL_CLASSES = {"retryable", "fixable", "terminal"}
ALLOWED_KEYS = {"class", "evidence", "confidence", "progress_ledger_delta"}
LINE_PTR_RE = re.compile(r"\d+")  # evidence must carry line numbers


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
    print(json.dumps({"gate": "VALID", "class": ruling["class"],
                      "confidence": ruling["confidence"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
