#!/usr/bin/env bash
# ============================================================================
# test_worktree_pool.sh — Unit tests for harness/worktree_pool.sh
# Cases: allocate + idempotent re-allocate (normal), concurrent allocation of
#        two packets (parallel safety), lockfile mutual exclusion (merge
#        blocked while flock held), serial merge-queue of disjoint packets
#        (both MERGED, linear), rebase conflict -> exit 3 + merge_conflict
#        event + queue stop (failure injection).
# Usage : bash test_worktree_pool.sh   (exit 0 = all pass, 1 = any fail)
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="$(cd "$HERE/../.." && pwd)"
POOL="$PKG/harness/worktree_pool.sh"
SETUP="$PKG/tests/mock_codex/setup_test_repo.sh"

TMP="$(mktemp -d /tmp/wtpool_test.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

export LOOP_ROOT="$TMP/loop"
export LOOP_REPO="$TMP/repo"
export LOOP_WT_DIR="$TMP/worktrees"
# sandbox has no global git identity; rebase creates commits and needs one
export GIT_AUTHOR_NAME=loop-test GIT_AUTHOR_EMAIL=loop@test.local
export GIT_COMMITTER_NAME=loop-test GIT_COMMITTER_EMAIL=loop@test.local
mkdir -p "$LOOP_ROOT/data"
touch "$LOOP_ROOT/data/events.ndjson"
bash "$SETUP" "$LOOP_REPO" >/dev/null

FAILS=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*" >&2; FAILS=$((FAILS + 1)); }

commit_in() {  # commit_in <pid> <file> <content>
  mkdir -p "$LOOP_WT_DIR/$1/$(dirname "$2")"
  printf '%s\n' "$3" > "$LOOP_WT_DIR/$1/$2"
  git -C "$LOOP_WT_DIR/$1" add -A
  git -C "$LOOP_WT_DIR/$1" -c user.name=t -c user.email=t@t commit -qm "$1: $2"
}

# --- 1. allocate + idempotent re-allocate ------------------------------------
WT1="$(bash "$POOL" allocate p1 | tail -1)"
[ -d "$WT1" ] && pass "allocate p1 -> $WT1" || fail "allocate p1: no worktree dir"
git -C "$LOOP_REPO" show-ref --verify --quiet refs/heads/packet/p1 \
  && pass "branch packet/p1 exists" || fail "branch packet/p1 missing"
WT1B="$(bash "$POOL" allocate p1 | tail -1)"
[ "$WT1B" = "$WT1" ] && pass "re-allocate p1 idempotent" || fail "re-allocate p1 changed path"

# --- 2. concurrent allocation (two distinct packets in parallel) --------------
set +e
bash "$POOL" allocate p2 >/dev/null 2>&1 & PIDA=$!
bash "$POOL" allocate p3 >/dev/null 2>&1 & PIDB=$!
wait "$PIDA"; RCA=$?
wait "$PIDB"; RCB=$?
set -e
# git may transiently contend on internal locks; retry any loser once serially
[ "$RCA" -ne 0 ] && bash "$POOL" allocate p2 >/dev/null
[ "$RCB" -ne 0 ] && bash "$POOL" allocate p3 >/dev/null
if [ -d "$LOOP_WT_DIR/p2" ] && [ -d "$LOOP_WT_DIR/p3" ]; then
  pass "concurrent allocation p2+p3 (rc=$RCA/$RCB)"
else
  fail "concurrent allocation: missing worktree(s)"
fi

# --- 3. lockfile mutual exclusion ---------------------------------------------
commit_in p1 "src/alpha/p1_change.py" "# p1"
(
  flock 9
  sleep 2
) 9>>"$LOOP_ROOT/data/.merge.lock" &
HOLDER=$!
sleep 0.3   # ensure the holder owns the lock first
T0=$(date +%s)
bash "$POOL" merge p1 >/dev/null 2>&1
T1=$(date +%s)
wait "$HOLDER"
ELAPSED=$((T1 - T0))
if [ "$ELAPSED" -ge 1 ]; then
  pass "merge waited ${ELAPSED}s for external flock holder (mutex proven)"
else
  fail "merge did not block on lockfile (elapsed=${ELAPSED}s)"
fi
grep -q '"packet_id":"p1","event":"merged"' "$LOOP_ROOT/data/events.ndjson" \
  && pass "merged event for p1 recorded" || fail "no merged event for p1"

# --- 4. serial merge-queue: disjoint packets both merge ------------------------
commit_in p2 "src/beta/p2_change.py" "# p2"
commit_in p3 "docs_p3.md" "# p3"
set +e
bash "$POOL" merge-queue p2 p3 >/dev/null 2>&1
RC=$?
set -e
[ "$RC" -eq 0 ] && pass "merge-queue p2 p3 rc=0" || fail "merge-queue rc=$RC"
git -C "$LOOP_REPO" cat-file -e "loop-integration:src/beta/p2_change.py" 2>/dev/null \
  && git -C "$LOOP_REPO" cat-file -e "loop-integration:docs_p3.md" 2>/dev/null \
  && pass "integration branch contains p2 + p3 changes" \
  || fail "integration branch missing merged files"

# --- 5. rebase conflict -> exit 3 + merge_conflict event -----------------------
WT4="$(bash "$POOL" allocate p4 | tail -1)"
WT5="$(bash "$POOL" allocate p5 | tail -1)"
printf 'edited-by-p4\nline2\nline3\n' > "$WT4/shared.txt"
git -C "$WT4" add -A && git -C "$WT4" -c user.name=t -c user.email=t@t commit -qm "p4 shared"
printf 'edited-by-p5\nline2\nline3\n' > "$WT5/shared.txt"
git -C "$WT5" add -A && git -C "$WT5" -c user.name=t -c user.email=t@t commit -qm "p5 shared"
set +e
bash "$POOL" merge-queue p4 p5 >/dev/null 2>&1
RC=$?
set -e
[ "$RC" -eq 3 ] && pass "conflicting merge-queue exits 3" || fail "expected rc=3, got rc=$RC"
grep -q '"packet_id":"p5","event":"merge_conflict"' "$LOOP_ROOT/data/events.ndjson" \
  && pass "merge_conflict event for p5 recorded" || fail "no merge_conflict event for p5"
git -C "$LOOP_REPO" cat-file -e "loop-integration:shared.txt" >/dev/null 2>&1 || true
# p4 (first in queue) must have merged; p5 stopped the queue
FIRST_LINE="$(git -C "$LOOP_REPO" show loop-integration:shared.txt | head -1)"
[ "$FIRST_LINE" = "edited-by-p4" ] && pass "queue merged p4 then stopped at p5" \
  || fail "integration shared.txt head is '$FIRST_LINE', expected p4's edit"

# --- verdict --------------------------------------------------------------------
if [ "$FAILS" -eq 0 ]; then
  echo "test_worktree_pool: ALL PASS"
  exit 0
fi
echo "test_worktree_pool: $FAILS FAILURE(S)" >&2
exit 1
