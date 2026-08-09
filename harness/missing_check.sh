#!/usr/bin/env bash
# ============================================================================
# missing_check.sh — Report-file vs packet-manifest reconciliation (spec §3.3)
# Purpose : Second truth source fallback: hooks are fail-open fast path only;
#           this check counts data/reports/<pid>/report.json against the
#           packet manifest (data/packets/*.json) and lists missing items.
# Input   : data/packets/*.json (manifest), data/reports/<pid>/report.json.
# Output  : stdout "OK n/n" or "MISSING k/n" + one missing pid per line.
#           Exit 0 = all present, 1 = missing items (fail-visible).
# Lines   : ~30
# ============================================================================
set -euo pipefail

LOOP_ROOT="${LOOP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PACKETS="$LOOP_ROOT/data/packets"
REPORTS="$LOOP_ROOT/data/reports"

total=0 missing=0 missing_list=""
shopt -s nullglob
for pj in "$PACKETS"/*.json; do
  base="$(basename "$pj" .json)"
  [ "$base" = "dag" ] && continue          # dag.json is not a packet
  total=$((total + 1))
  if [ ! -s "$REPORTS/$base/report.json" ]; then
    missing=$((missing + 1))
    missing_list="$missing_list$base"$'\n'
  fi
done

if [ "$missing" -eq 0 ]; then
  echo "OK $total/$total reports present"
  exit 0
else
  echo "MISSING $missing/$total reports absent:"
  printf '%s' "$missing_list"
  exit 1
fi
