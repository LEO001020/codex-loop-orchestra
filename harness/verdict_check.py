#!/usr/bin/env python3
# ============================================================================
# verdict_check.py — Release-gate ruling algebraic closure check
# ----------------------------------------------------------------------------
# Purpose : Verifies internal consistency of the Reviewer's verdict JSON.
#           Algebraic closure rules (a verdict is a function of its findings,
#           not free prose): (1) verdict must be a legal enum; (2) if any
#           finding has severity "blocking", verdict cannot be APPROVED;
#           (3) APPROVED with zero findings recorded is legal; REJECTED /
#           CHANGES_REQUESTED with zero findings is inconsistent (a negative
#           verdict must cite evidence); (4) every finding needs severity +
#           a file/line pointer. The reviewer's word alone never releases:
#           this check gates the verdict before Sol final review + human merge.
# Input   : --verdict <reviewer verdict JSON>:
#           {"verdict":"APPROVED|APPROVED_WITH_NOTES|CHANGES_REQUIRED|
#                       CHANGES_REQUESTED|REJECTED",
#            "findings":[{"severity":"blocking|major|minor|info",
#                         "pointer":"file:line","note":"..."}]}
#           Enum matches agents/reviewer.toml (APPROVED_WITH_NOTES is a
#           pass-class verdict; CHANGES_REQUIRED/CHANGES_REQUESTED are
#           blocking-class synonyms — the legacy spelling stays legal so no
#           previously-valid verdict is silently reclassified).
# Output  : stdout one-line result; stderr itemized inconsistencies
#           exit 0 = closure holds, 1 = inconsistent verdict, 2 = usage error
# Lines   : 85
# ============================================================================
import argparse
import json
import sys

# Full enum from agents/reviewer.toml plus the legacy CHANGES_REQUESTED
# spelling (kept legal: dropping it could only reject, never release, but
# the reviewer instructions and this gate must agree on one closed set).
LEGAL_VERDICTS = {"APPROVED", "APPROVED_WITH_NOTES", "CHANGES_REQUIRED",
                  "CHANGES_REQUESTED", "REJECTED"}
LEGAL_SEVERITIES = {"blocking", "major", "minor", "info"}
# Pass-class: may coexist only with non-blocking findings.
PASS_VERDICTS = {"APPROVED", "APPROVED_WITH_NOTES"}
# Blocking-class: must cite at least one finding as evidence.
NEGATIVE_VERDICTS = {"CHANGES_REQUIRED", "CHANGES_REQUESTED", "REJECTED"}


def main():
    ap = argparse.ArgumentParser(description="verdict algebraic closure check")
    ap.add_argument("--verdict", required=True, help="reviewer verdict JSON")
    ap.add_argument("--dispatch-record", default=None,
                    help="optional controlled release-review dispatch record "
                         "(data/release_review/w<N>.json); when given the "
                         "report's provenance must match it exactly")
    args = ap.parse_args()
    try:
        data = json.load(open(args.verdict, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("usage error: cannot read verdict: %s" % exc, file=sys.stderr)
        sys.exit(2)

    errors = []
    verdict = data.get("verdict")
    findings = data.get("findings", [])
    # Provenance against the controlled dispatch record (release-review
    # route): the report must echo run_id/model/effort/wave of the record.
    if args.dispatch_record:
        try:
            rec = json.load(open(args.dispatch_record, encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print("usage error: cannot read dispatch record: %s" % exc,
                  file=sys.stderr)
            sys.exit(2)
        if not isinstance(rec, dict):
            errors.append("DISPATCH_RECORD_INVALID: record must be a JSON object")
            rec = {}
        prov = data.get("provenance")
        if not isinstance(prov, dict):
            errors.append("PROVENANCE_MISSING: report must carry a provenance "
                          "object when a dispatch record is supplied")
        else:
            for field in ("run_id", "model", "effort", "wave"):
                if prov.get(field) != rec.get(field):
                    errors.append("PROVENANCE_MISMATCH: %s %r != record %r"
                                  % (field, prov.get(field), rec.get(field)))
    if verdict not in LEGAL_VERDICTS:
        errors.append("ILLEGAL_VERDICT: %r not in %s" % (verdict, sorted(LEGAL_VERDICTS)))
    if not isinstance(findings, list):
        errors.append("FINDINGS_NOT_LIST: findings must be a JSON array")
        findings = []
    for i, f in enumerate(findings):
        if not isinstance(f, dict) or f.get("severity") not in LEGAL_SEVERITIES:
            errors.append("FINDING_%d: missing/illegal severity" % i)
        if isinstance(f, dict) and not str(f.get("pointer", "")).strip():
            errors.append("FINDING_%d: missing file:line pointer" % i)
    blocking = [f for f in findings
                if isinstance(f, dict) and f.get("severity") == "blocking"]
    if blocking and verdict in PASS_VERDICTS:
        errors.append("CLOSURE_VIOLATION: %d blocking finding(s) present — "
                      "verdict cannot be %s" % (len(blocking), verdict))
    if verdict in NEGATIVE_VERDICTS and not findings:
        errors.append("CLOSURE_VIOLATION: negative verdict %s with zero "
                      "findings (no evidence cited)" % verdict)
    if verdict == "APPROVED_WITH_NOTES" and not findings:
        errors.append("CLOSURE_VIOLATION: APPROVED_WITH_NOTES with zero "
                      "findings (the notes must be cited as findings)")

    if errors:
        for e in errors:
            print(e, file=sys.stderr)  # fail-visible
        print("FAIL verdict=%s findings=%d blocking=%d errors=%d"
              % (verdict, len(findings), len(blocking), len(errors)))
        sys.exit(1)
    print("PASS verdict=%s findings=%d blocking=%d"
          % (verdict, len(findings), len(blocking)))
    sys.exit(0)


if __name__ == "__main__":
    main()
