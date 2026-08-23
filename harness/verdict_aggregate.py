#!/usr/bin/env python3
# ============================================================================
# verdict_aggregate.py — L2/L2.5 verdict aggregation + position randomization
# ----------------------------------------------------------------------------
# Purpose : Collects L2 verifier verdicts over N<=3 candidates (L2.5
#           multi-candidate mode) and aggregates. Candidate presentation
#           order is RANDOMIZED before each verifier pass to defeat position
#           bias (a recognized judge-bias family alongside framing bias);
#           this script both emits a shuffled order (--emit-order) and
#           de-shuffles the returned verdicts. Aggregation is strictest-wins:
#           escalate_l3 > escalate_l2_5 > redo > pass.
#           HARDCODED power semantics (S5): L2 output can only block or
#           escalate — an aggregate "pass" ONLY exempts the packet from Sol
#           per-packet review and forwards it to the serial merge queue;
#           it never releases anything (mechanical acceptance + release gate
#           human review still follow).
# Input   : --verdicts JSON: {"candidates":[{"candidate_id","diff_path",
#              "verdicts":[{"verifier","verdict":"pass|redo|escalate_l2_5|
#              escalate_l3","score":0-1}]}]}
#           --emit-order N : instead emit a randomized presentation order
#              for N candidates (one shuffled index list, JSON) and exit
# Output  : stdout JSON {"aggregate_verdict","best_candidate","ranking"}
#           exit 0 = aggregate computed, 2 = usage error
# Lines   : 83
# ============================================================================
import argparse
import json
import random
import sys

ORDER = ["pass", "redo", "escalate_l2_5", "escalate_l3"]  # strictness asc
RANK = {v: i for i, v in enumerate(ORDER)}


def main():
    ap = argparse.ArgumentParser(description="L2 verdict aggregator")
    ap.add_argument("--verdicts", help="collected verdicts JSON path")
    ap.add_argument("--emit-order", type=int, default=None,
                    help="emit randomized presentation order for N candidates")
    args = ap.parse_args()

    if args.emit_order is not None:  # pre-pass: shuffle before showing L2
        idx = list(range(args.emit_order))
        random.SystemRandom().shuffle(idx)  # non-deterministic by design
        print(json.dumps({"presentation_order": idx}))
        return 0

    if not args.verdicts:
        print("usage error: --verdicts or --emit-order required", file=sys.stderr)
        return 2
    try:
        data = json.load(open(args.verdicts, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("usage error: %s" % exc, file=sys.stderr)
        return 2

    ranking, worst = [], "pass"
    for cand in data.get("candidates", []):
        vs = [v for v in cand.get("verdicts", [])
              if v.get("verdict") in RANK]
        if not vs:  # a candidate nobody judged cannot pass — escalate
            strict, score = "escalate_l3", 0.0
        else:
            strict = max((v["verdict"] for v in vs), key=lambda v: RANK[v])
            score = sum(float(v.get("score", 0)) for v in vs) / len(vs)
        ranking.append({"candidate_id": cand.get("candidate_id", "?"),
                        "diff_path": cand.get("diff_path"),
                        "verdict": strict, "mean_score": round(score, 4)})
        if RANK[strict] > RANK[worst]:
            worst = strict  # strictest-wins across candidates
    # best candidate: least-strict verdict first, then highest mean score
    ranking.sort(key=lambda r: (RANK[r["verdict"]], -r["mean_score"]))
    best = ranking[0] if ranking else None
    print(json.dumps({
        "aggregate_verdict": worst,
        "note": "L2 'pass' only exempts Sol per-packet review; "
                "never a release (S5)",
        "best_candidate": best, "ranking": ranking}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
