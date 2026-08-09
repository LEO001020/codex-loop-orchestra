#!/usr/bin/env python3
# ============================================================================
# sanitize.py — ReviewPacket desensitization (framing-effect defense)
# ----------------------------------------------------------------------------
# Purpose : Drops REVIEW_FORBIDDEN_KEYS from a ReviewPacket before it reaches
#           the Reviewer. Rationale: "verified bug-free" style framing drops
#           reviewer detection by up to -93.5 pp (arXiv 2603.18740); the
#           implementing agent must never author the framing text the
#           reviewer sees. Reviewer input = diff path + task spec only.
# Input   : original packet JSON on stdin (or --in FILE)
# Output  : desensitized packet JSON on stdout (or --out FILE); exit 0
# Lines   : 40
# ============================================================================
import argparse, json, sys

REVIEW_FORBIDDEN_KEYS = {
    "generation_narrative", "generation_process", "author_self_assessment",
    "self_assessment", "prior_verdict", "builder_claims", "self_report",
    "completion_summary", "confidence_claim", "tests_pass_claim",
}


def scrub(node):  # recursive drop, arrays included
    if isinstance(node, dict):
        return {k: scrub(v) for k, v in node.items()
                if k.lower() not in REVIEW_FORBIDDEN_KEYS}
    if isinstance(node, list):
        return [scrub(v) for v in node]
    return node


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ReviewPacket desensitizer")
    ap.add_argument("--in", dest="inp", default=None)
    ap.add_argument("--out", dest="out", default=None)
    a = ap.parse_args()
    src = open(a.inp, encoding="utf-8") if a.inp else sys.stdin
    dst = open(a.out, "w", encoding="utf-8") if a.out else sys.stdout
    json.dump(scrub(json.load(src)), dst, indent=1)
    dst.write("\n")
