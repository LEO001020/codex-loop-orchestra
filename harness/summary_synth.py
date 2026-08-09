#!/usr/bin/env python3
# ============================================================================
# summary_synth.py — Wave finale / dead-letter summary synthesis (<=500 tok)
# ----------------------------------------------------------------------------
# Purpose : Synthesizes the single summary injected into Sol at
#           WAVE_DONE / DEAD_LETTER adjudication (axiom 5: bytes stay on
#           disk, Sol gets "path + <=500 token structured summary").
#           Structure: results table + dead-letter list (with duty officer
#           attribution) + conflict pointers + REPORT INDEX + READ/UNREAD
#           checklist (recoverable compression: delete content, keep paths).
#           Budget enforcement: token estimate = ceil(chars/4); if over
#           budget the free-prose fields are truncated FIRST and pointer
#           lines (paths) are preserved verbatim — pointer integrity is
#           never sacrificed; if pointers alone exceed budget, per-row notes
#           drop to bare paths and a "+N more, see index file" tail row is
#           used, keeping the full index recoverable on disk.
# Input   : --wave wave results JSON:
#             {"wave_id","packets":[{"packet_id","state","report_path",
#               "note"}],"dead_letters":[{"packet_id","report_path",
#               "duty_attribution","note"}],"conflicts":[{"packet_id",
#               "pointer"}]}
#           --budget-tokens (default 500); --out summary file (also stdout)
# Output  : structured plaintext summary <=500 tokens, exit 0; 2 = usage
# Lines   : 127
# ============================================================================
import argparse
import json
import math
import sys


def tokens(text):
    return math.ceil(len(text) / 4)  # conservative chars/4 estimate


def clip(text, max_chars):
    if max_chars <= 0:
        return ""
    text = str(text or "")
    return text if len(text) <= max_chars else text[:max_chars - 1] + "…"


def build(wave, note_chars, row_cap):
    """Render summary with per-note char cap + row cap (rows over the cap
    collapse to an aggregate tail line; full lists stay on disk)."""
    L = []
    packets = wave.get("packets", [])
    deads = wave.get("dead_letters", [])
    conflicts = wave.get("conflicts", [])
    L.append("WAVE %s FINALE — %d packets, %d dead-letter, %d conflicts"
             % (wave.get("wave_id", "?"), len(packets), len(deads),
                len(conflicts)))
    L.append("== RESULTS ==")
    for p in packets[:row_cap]:
        L.append(("%s %s %s" % (p.get("packet_id", "?"), p.get("state", "?"),
                                clip(p.get("note"), note_chars))).rstrip())
    if len(packets) > row_cap:
        states = {}
        for p in packets[row_cap:]:
            states[p.get("state", "?")] = states.get(p.get("state", "?"), 0) + 1
        L.append("+%d more (%s) — full list: data/reports/INDEX.json"
                 % (len(packets) - row_cap,
                    " ".join("%s=%d" % kv for kv in sorted(states.items()))))
    if deads:
        L.append("== DEAD LETTERS (duty officer attribution) ==")
        for d in deads[:row_cap]:
            L.append("%s [%s] %s -> %s"
                     % (d.get("packet_id", "?"),
                        clip(d.get("duty_attribution", "unattributed"),
                             max(note_chars, 16)),
                        clip(d.get("note"), note_chars),
                        d.get("report_path", "?")))
        if len(deads) > row_cap:
            L.append("+%d more dead letters — data/dead_letters/"
                     % (len(deads) - row_cap))
    if conflicts:
        L.append("== CONFLICT POINTERS ==")
        for c in conflicts:
            L.append("%s -> %s" % (c.get("packet_id", "?"),
                                   c.get("pointer", "?")))
    # Report index + read/unread checklist: pointer lines, never truncated.
    L.append("== REPORT INDEX (read/unread checklist) ==")
    rows = [(p.get("packet_id", "?"), p.get("report_path", "?"),
             "READ" if p.get("read") else "UNREAD")
            for p in packets + deads]
    shown = rows[:row_cap]
    for pid, path, flag in shown:
        L.append("[%s] %s %s" % (flag, pid, path))
    if len(rows) > len(shown):
        L.append("[UNREAD] +%d more — full index: data/reports/INDEX.json"
                 % (len(rows) - len(shown)))
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description="wave summary synthesizer")
    ap.add_argument("--wave", required=True, help="wave results JSON")
    ap.add_argument("--budget-tokens", type=int, default=500)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    try:
        wave = json.load(open(args.wave, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("usage error: %s" % exc, file=sys.stderr)
        sys.exit(2)

    # Degrade prose before pointers: shrink note width first, then cap rows.
    # Rows beyond the cap collapse to aggregate tail lines that always point
    # at the on-disk index — pointer integrity is preserved at every rung.
    text = None
    for note_chars, row_cap in ((120, 10**9), (60, 10**9), (24, 10**9),
                                (0, 10**9), (0, 40), (0, 20), (0, 8),
                                (0, 3), (0, 1)):
        text = build(wave, note_chars, row_cap)
        if tokens(text) <= args.budget_tokens:
            break
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    sys.stdout.write(text)
    print("-- synthesized %d est. tokens (budget %d)"
          % (tokens(text), args.budget_tokens), file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
