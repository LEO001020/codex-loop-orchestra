#!/usr/bin/env python3
# ============================================================================
# oscillation_report.py — Oscillation metric aggregator (GPT critique metric)
# ----------------------------------------------------------------------------
# Purpose : Makes "does each intervention ADD information or merely
#           re-interpret existing information?" measurable (critique
#           L90-117 / audit Q4). Zero-token OFFLINE script — pure sorting,
#           set comparison and counting, no LLM. Per packet it orders the
#           intervention sequence by ts (L1 evaluations from
#           escalation_log.jsonl, retry decisions + duty rulings from
#           events.ndjson, L3/L4 adjudications) and computes:
#           (1) signature change rate — is the failure signature
#               (retry_class / rules_hit set) AFTER an intervention different
#               from the one BEFORE it? different = information gain;
#               identical = pure re-interpretation (oscillation evidence);
#           (2) state revisit count — repetitions of (state, via) pairs in
#               the ledger history; >= 2 repeats = oscillation candidate.
#           Optionally joins metering/e0_annotate.py's summary (T5 retry /
#           T6 duty_review / T10 adjudication buckets) to attribute the
#           oscillation cost in Sol cost-equivalent units.
# Input   : --escalation-log data/escalation_log.jsonl
#           --events         data/events.ndjson
#           --ledger         data/progress_ledger.json
#           --e0-summary     e0_annotate summary JSON (optional join)
#           --out            report JSON path (default: stdout)
# Output  : JSON {per_packet:{pid:{interventions, info_gain_interventions,
#           signature_change_rate, revisit_pairs, oscillation_candidate}},
#           totals:{interventions, info_gain, info_gain_ratio},
#           revisit_histogram, e0_cost_join}. Exit 0 always assembled;
#           empty/missing inputs degrade to an empty (all-zero) report.
# Lines   : ~110 (header included)
# ============================================================================
import argparse, json, sys


def read_jsonl(path):
    rows = []
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue  # malformed line: skipped here, dead-lettered upstream
    except (OSError, TypeError):
        pass
    return rows


def read_json(path):
    try:
        return json.load(open(path, encoding="utf-8")) if path else None
    except (OSError, ValueError):
        return None


def interventions_for(pid, esc, events):
    """Ts-ordered intervention list; each carries a deterministic failure
    signature: L1 eval -> frozenset(rules_hit); retry/duty -> retry class."""
    seq = []
    for r in esc:  # L1 evaluations + duty records + L3/L4 escalation entries
        if r.get("packet_id") != pid:
            continue
        kind = ("l1_eval" if "rules_hit" in r else
                "duty_ruling" if r.get("level") == "DUTY_RECORD_ONLY" else
                "l3_l4" if r.get("level") in ("SOL_WAKE", "DIRECT_L4") else None)
        if kind:
            sig = (frozenset(r.get("rules_hit", [])) if kind == "l1_eval"
                   else frozenset([str(r.get("ruling") or r.get("reason") or "")]))
            seq.append((float(r.get("ts", 0) or 0), kind, sig))
    for e in events:  # retry decisions (t9) + duty reviews (t10)
        if e.get("packet_id") != pid or e.get("event") not in ("retry_dispatch", "duty_review"):
            continue
        det = e.get("detail") or {}
        sig = frozenset([str(det.get("class") or det.get("why") or "unclassified")])
        seq.append((float(e.get("ts", 0) or 0), e["event"], sig))
    return sorted(seq, key=lambda x: x[0])


def main():
    ap = argparse.ArgumentParser(description="oscillation metric aggregator (zero token)")
    ap.add_argument("--escalation-log", default="data/escalation_log.jsonl")
    ap.add_argument("--events", default="data/events.ndjson")
    ap.add_argument("--ledger", default="data/progress_ledger.json")
    ap.add_argument("--e0-summary", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    esc, events = read_jsonl(a.escalation_log), read_jsonl(a.events)
    led = read_json(a.ledger) or {"packets": {}}
    pids = sorted(set(led.get("packets", {}))
                  | {r.get("packet_id") for r in esc if r.get("packet_id")}
                  | {e.get("packet_id") for e in events if e.get("packet_id")})
    per_packet, hist = {}, {}
    tot_iv = tot_gain = 0
    for pid in pids:
        seq = interventions_for(pid, esc, events)
        # (1) signature change rate: compare each intervention's signature to
        # the previous one — changed = the intervention altered the system
        # trajectory (information gain); unchanged = pure re-interpretation.
        gain = sum(1 for i in range(1, len(seq)) if seq[i][2] != seq[i - 1][2])
        comparisons = max(len(seq) - 1, 0)
        # (2) state revisit count: repeated (state, via) pairs in history.
        pairs = {}
        for h in led.get("packets", {}).get(pid, {}).get("history", []):
            k = "%s|%s" % (h.get("to"), h.get("via"))
            pairs[k] = pairs.get(k, 0) + 1
        revisits = {k: c for k, c in pairs.items() if c >= 2}
        for c in revisits.values():
            hist[str(c)] = hist.get(str(c), 0) + 1
        per_packet[pid] = {
            "interventions": len(seq),
            "info_gain_interventions": gain,
            "signature_change_rate": round(gain / comparisons, 3) if comparisons else None,
            "revisit_pairs": revisits,
            "oscillation_candidate": bool(revisits) or
            (comparisons > 0 and gain < comparisons),
        }
        tot_iv += comparisons
        tot_gain += gain
    # e0 join: attribute T5 (retry) / T6 (duty) / T10 (adjudication) buckets
    # to Sol cost-equivalent — where oscillation actually bills.
    e0 = read_json(a.e0_summary) or {}
    e0_join = {b: (e0.get("per_bucket", {}).get(b) or {}).get("cost_equivalent", 0.0)
               for b in ("T5", "T6", "T10")}
    report = {"per_packet": per_packet,
              "totals": {"interventions": tot_iv, "info_gain": tot_gain,
                         "info_gain_ratio": round(tot_gain / tot_iv, 3) if tot_iv else None},
              "revisit_histogram": hist,
              "e0_cost_join": e0_join,
              "note": "signature unchanged after an intervention = pure "
                      "re-interpretation (oscillation); zero-token offline metric"}
    out = json.dumps(report, indent=1)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(out + "\n")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
