#!/usr/bin/env bash
# ============================================================================
# test_missing_check.sh — Unit tests for harness/missing_check.sh
# Cases: all reports present (rc 0), some missing (rc 1 + pids listed),
#        zero-byte report counts as missing (boundary), empty manifest
#        (rc 0, 0/0), dag.json excluded from the manifest.
# Usage : bash test_missing_check.sh   (exit 0 = all pass, 1 = any fail)
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="$(cd "$HERE/../.." && pwd)"
CHECK="$PKG/harness/missing_check.sh"

TMP="$(mktemp -d /tmp/misscheck_test.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
export LOOP_ROOT="$TMP/loop"
mkdir -p "$LOOP_ROOT/data/packets" "$LOOP_ROOT/data/reports"

FAILS=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*" >&2; FAILS=$((FAILS + 1)); }

run_check() { set +e; OUT="$(bash "$CHECK" 2>&1)"; RC=$?; set -e; }

# --- 1. empty manifest -> OK 0/0 ----------------------------------------------
run_check
[ "$RC" -eq 0 ] && echo "$OUT" | grep -q "OK 0/0" \
  && pass "empty manifest -> rc 0, OK 0/0" || fail "empty manifest: rc=$RC out=$OUT"

# --- 2. two packets, no reports -> rc 1, both listed -----------------------------
echo '{"packet_id":"p1"}' > "$LOOP_ROOT/data/packets/p1.json"
echo '{"packet_id":"p2"}' > "$LOOP_ROOT/data/packets/p2.json"
echo '{"edges":[],"waves":[]}' > "$LOOP_ROOT/data/packets/dag.json"   # excluded
run_check
if [ "$RC" -eq 1 ] && echo "$OUT" | grep -q "MISSING 2/2" \
   && echo "$OUT" | grep -q "^p1$" && echo "$OUT" | grep -q "^p2$"; then
  pass "no reports -> rc 1, MISSING 2/2, both pids listed"
else
  fail "no reports: rc=$RC out=$OUT"
fi

# --- 3. zero-byte report still counts as missing (boundary) ----------------------
mkdir -p "$LOOP_ROOT/data/reports/p1"
: > "$LOOP_ROOT/data/reports/p1/report.json"
run_check
[ "$RC" -eq 1 ] && echo "$OUT" | grep -q "^p1$" \
  && pass "zero-byte report treated as missing" || fail "zero-byte report: rc=$RC out=$OUT"

# --- 4. one real report -> only p2 missing ----------------------------------------
echo '{"packet_id":"p1","status":"done"}' > "$LOOP_ROOT/data/reports/p1/report.json"
run_check
if [ "$RC" -eq 1 ] && echo "$OUT" | grep -q "MISSING 1/2" \
   && echo "$OUT" | grep -q "^p2$" && ! echo "$OUT" | grep -q "^p1$"; then
  pass "one report present -> MISSING 1/2, only p2 listed"
else
  fail "one report: rc=$RC out=$OUT"
fi

# --- 5. all present -> rc 0 (dag.json never demanded a report) --------------------
mkdir -p "$LOOP_ROOT/data/reports/p2"
echo '{"packet_id":"p2","status":"done"}' > "$LOOP_ROOT/data/reports/p2/report.json"
run_check
[ "$RC" -eq 0 ] && echo "$OUT" | grep -q "OK 2/2" \
  && pass "all present -> rc 0, OK 2/2 (dag.json excluded)" || fail "all present: rc=$RC out=$OUT"

# --- verdict ------------------------------------------------------------------------
if [ "$FAILS" -eq 0 ]; then
  echo "test_missing_check: ALL PASS"
  exit 0
fi
echo "test_missing_check: $FAILS FAILURE(S)" >&2
exit 1
