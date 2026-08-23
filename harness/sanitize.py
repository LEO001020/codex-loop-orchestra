#!/usr/bin/env python3
# ============================================================================
# sanitize.py — ReviewPacket desensitization (framing-effect defense)
# ----------------------------------------------------------------------------
# Purpose : WHITELIST filter (P-12 fix: was a blacklist — off-list keys used
#           to pass through). Only ALLOWED_REVIEW_KEYS survive into the
#           ReviewPacket; every other key is dropped at every nesting level.
#           Rationale: "verified bug-free" style framing drops reviewer
#           detection by up to -93.5 pp (arXiv 2603.18740); the implementing
#           agent must never author the framing text the reviewer sees.
#           Reviewer input = diff path + task spec only. Fail direction:
#           unknown keys are DELETED (never forwarded) — a too-narrow
#           whitelist starves the reviewer visibly, never poisons it.
# Input   : original packet JSON on stdin (or --in FILE)
# Output  : desensitized packet JSON on stdout (or --out FILE); exit 0
# Lines   : ~55
# ============================================================================
import argparse, json, sys

# Task-spec / mechanical-fact keys ONLY. Anything not listed here — including
# the entire former blacklist (self_report, prior_verdict, builder_claims,
# tests_pass_claim, ...) and any future smuggling key — is dropped.
ALLOWED_REVIEW_KEYS = {
    # packet identity + task spec (4-field schema + optional constraints)
    "packet_id", "goal", "authorized_paths", "acceptance", "constraints",
    # artifact handles (paths, not content)
    "diff_path", "report_path", "oracle_path", "artifact_path",
    # mechanical facts from L0 (diff + test measurements)
    "diff", "diff_stat", "test_output", "test_count", "files", "paths_touched",
    "counts", "added", "removed", "lines",
    # multi-candidate scaffolding (L2.5) — structure only
    "candidates", "id", "meta", "wave",
}


def scrub(node):  # recursive whitelist keep, arrays included
    if isinstance(node, dict):
        return {k: scrub(v) for k, v in node.items()
                if k.lower() in ALLOWED_REVIEW_KEYS}
    if isinstance(node, list):
        return [scrub(v) for v in node]
    return node


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ReviewPacket desensitizer (whitelist)")
    ap.add_argument("--in", dest="inp", default=None)
    ap.add_argument("--out", dest="out", default=None)
    a = ap.parse_args()
    src = open(a.inp, encoding="utf-8") if a.inp else sys.stdin
    dst = open(a.out, "w", encoding="utf-8") if a.out else sys.stdout
    json.dump(scrub(json.load(src)), dst, indent=1)
    dst.write("\n")
