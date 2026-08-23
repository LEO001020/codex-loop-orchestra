#!/usr/bin/env bash
# ============================================================================
# worktree_pool.sh — Worktree allocation + serial merge (spec §3.3 t16/t17)
# Purpose : Write-parallel physical isolation: one packet = one worktree = one
#           branch off a frozen base SHA. Serial merge under a single lockfile
#           (flock): each merge rebases the next packet onto the advancing
#           integration branch; conflict -> abort + MERGE_CONFLICT event.
# Input   : subcommand [allocate <pid> | release <pid> | merge <pid> |
#           merge-queue <pid>... | status]; env LOOP_ROOT, LOOP_REPO,
#           LOOP_BASE_BRANCH (default main).
# Output  : allocate prints worktree path (last line); merge appends
#           merged/merge_conflict events to events.ndjson. Exit 0 ok,
#           1 error, 3 merge conflict. Zero-token, deterministic.
# Lines   : ~150 (including comments)
# ============================================================================
set -euo pipefail

LOOP_ROOT="${LOOP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
if [ -z "${LOOP_REPO:-}" ]; then
  if git -C "$LOOP_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    LOOP_REPO="$LOOP_ROOT"
  else
    LOOP_REPO="$LOOP_ROOT/repo"
  fi
fi
BASE_BRANCH="${LOOP_BASE_BRANCH:-main}"            # frozen base for the wave
INTEG_BRANCH="${LOOP_INTEG_BRANCH:-loop-integration}"
WT_DIR="${LOOP_WT_DIR:-$LOOP_ROOT/worktrees}"      # pool location
DATA="$LOOP_ROOT/data"
LOCK="$DATA/.merge.lock"                           # single point of mutual exclusion
mkdir -p "$WT_DIR" "$DATA"

event() {  # event <pid> <event> <detail-json>
  printf '{"ts":%s,"packet_id":"%s","event":"%s","detail":%s}\n' \
    "$(date +%s)" "$1" "$2" "${3:-{\}}" >> "$DATA/events.ndjson"
}

die() { echo "worktree_pool: $*" >&2; exit 1; }

need_repo() {
  git -C "$LOOP_REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 || \
    die "no git repo at LOOP_REPO=$LOOP_REPO"
}

# --- allocate <packet_id> ---------------------------------------------------
# Creates worktrees/<pid> on branch packet/<pid> off the frozen base SHA.
# Branch exclusivity is a free git safety rail: the same branch cannot be
# checked out in two worktrees (one packet, one branch, one worktree).
allocate() {
  local pid="$1"; need_repo
  local wt="$WT_DIR/$pid" br="packet/$pid"
  if [ -d "$wt" ]; then                 # idempotent re-allocate
    echo "$wt"; return 0
  fi
  local base_sha
  base_sha="$(git -C "$LOOP_REPO" rev-parse "$BASE_BRANCH")"
  git -C "$LOOP_REPO" worktree add --lock --reason "packet $pid" \
      -b "$br" "$wt" "$base_sha" >&2
  event "$pid" "worktree_allocated" "{\"path\":\"$wt\",\"branch\":\"$br\",\"base\":\"$base_sha\"}"
  echo "$wt"                            # last line = path (consumed by dispatch.py)
}

# --- release <packet_id> ----------------------------------------------------
release() {
  local pid="$1"; need_repo
  local wt="$WT_DIR/$pid"
  [ -d "$wt" ] || { echo "no worktree for $pid" >&2; return 0; }
  git -C "$LOOP_REPO" worktree unlock "$wt" 2>/dev/null || true
  git -C "$LOOP_REPO" worktree remove --force "$wt"
  git -C "$LOOP_REPO" worktree prune
  event "$pid" "worktree_released" "{}"
}

# --- merge <packet_id> ------------------------------------------------------
# Serial merge under flock: rebase packet branch onto the integration branch,
# then fast-forward integration. On rebase conflict: abort cleanly, emit
# merge_conflict (ACCEPTED -> MERGE_CONFLICT, transition 17), exit 3.
# The queue caller stops on conflict — never merge onto a broken baseline.
merge_one() {
  local pid="$1"; need_repo
  local br="packet/$pid" wt="$WT_DIR/$pid"
  [ -d "$wt" ] || die "no worktree for $pid"
  # ensure integration branch exists (starts at base)
  git -C "$LOOP_REPO" show-ref --verify --quiet "refs/heads/$INTEG_BRANCH" || \
    git -C "$LOOP_REPO" branch "$INTEG_BRANCH" "$BASE_BRANCH"
  # rebase the packet branch (in its own worktree — own index, no lock clash)
  if ! git -C "$wt" rebase "$INTEG_BRANCH" >&2; then
    git -C "$wt" rebase --abort >&2 || true
    event "$pid" "merge_conflict" "{\"onto\":\"$INTEG_BRANCH\"}"
    echo "MERGE_CONFLICT $pid" >&2
    return 3
  fi
  # fast-forward integration to the rebased packet branch (linear history)
  git -C "$LOOP_REPO" fetch . "$br:$INTEG_BRANCH" >&2 || {
    event "$pid" "merge_conflict" "{\"stage\":\"ff\"}"; return 3; }
  event "$pid" "merged" "{\"into\":\"$INTEG_BRANCH\"}"
  echo "MERGED $pid -> $INTEG_BRANCH"
}

# --- merge with lockfile ------------------------------------------------------
merge() {
  local pid="$1"
  (
    flock -w 600 9 || die "could not obtain merge lock within 600s"
    merge_one "$pid"
  ) 9>>"$LOCK"
}

# --- merge-queue <pid>... -----------------------------------------------------
# Merges ACCEPTED packets one at a time; each successful merge advances the
# integration branch so the NEXT packet rebases onto it (merge-queue model:
# A vs main, B vs main+A, C vs main+A+B). Stops the queue on first conflict.
merge_queue() {
  local pid rc=0
  (
    flock -w 600 9 || die "could not obtain merge lock within 600s"
    for pid in "$@"; do
      if ! merge_one "$pid"; then
        rc=3
        echo "queue stopped at $pid (conflict escalates to SOL_ADJUDICATE)" >&2
        break
      fi
    done
    exit "$rc"
  ) 9>>"$LOCK"
}

status() {
  need_repo
  git -C "$LOOP_REPO" worktree list --porcelain
}

cmd="${1:-}"; shift || true
case "$cmd" in
  allocate)     allocate "${1:?packet_id required}" ;;
  release)      release  "${1:?packet_id required}" ;;
  merge)        merge    "${1:?packet_id required}" ;;
  merge-queue)  merge_queue "$@" ;;
  status)       status ;;
  *) die "usage: worktree_pool.sh {allocate|release|merge|merge-queue|status} <packet_id>..." ;;
esac
