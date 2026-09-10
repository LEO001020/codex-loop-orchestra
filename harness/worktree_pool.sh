#!/usr/bin/env bash
# ============================================================================
# worktree_pool.sh — Worktree allocation + serial merge (spec §3.3 t16/t17)
# Purpose : Write-parallel physical isolation: one packet = one worktree = one
#           branch off a frozen base SHA. Serial merge under a single lockfile
#           (flock): each merge rebases the next packet onto the advancing
#           integration branch; conflict -> abort + MERGE_CONFLICT event.
# Input   : subcommand [allocate <pid> | release <pid> [--save-patch] |
#           merge <pid> | merge-queue <pid>... | status]; env LOOP_ROOT,
#           LOOP_REPO, LOOP_BASE_BRANCH (default main).
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
    "$(date +%s)" "$(printf '%s' "$1" | json_escape)" \
    "$(printf '%s' "$2" | json_escape)" "${3:-{\}}" >> "$DATA/events.ndjson"
}

die() { echo "worktree_pool: $*" >&2; exit 1; }

# JSON-escape a string for safe embedding in event details: Windows paths
# contain backslashes ("E:\x" is an invalid \U escape in JSON) and pids may
# contain quotes; an unescaped detail writes a MALFORMED event line that the
# statemachine dead-letters as a bogus "malformed" packet.
json_escape() {  # stdin -> stdout, escapes \ and "
  sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

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
  local wt_esc br_esc
  wt_esc="$(printf '%s' "$wt" | json_escape)"
  br_esc="$(printf '%s' "$br" | json_escape)"
  event "$pid" "worktree_allocated" "{\"path\":\"$wt_esc\",\"branch\":\"$br_esc\",\"base\":\"$base_sha\"}"
  echo "$wt"                            # last line = path (consumed by dispatch.py)
}

# --- release <packet_id> [--save-patch] --------------------------------------
# Destructive boundary (strong recovery edge): a dirty worktree (tracked or
# untracked changes) with no recoverable representation is never silently
# deleted. Without --save-patch the release REFUSES and records
# worktree_dirty_preserved (keep + record = lowest-friction recovery).
# With --save-patch the exact recoverable delta (tracked diff + untracked
# file content) is archived under data/reports/<pid>/ before removal.
release() {
  local pid="$1"; shift || true
  need_repo
  local save_patch=0
  [ "${1:-}" = "--save-patch" ] && save_patch=1
  local wt="$WT_DIR/$pid"
  [ -d "$wt" ] || { echo "no worktree for $pid" >&2; return 0; }
  local dirty
  if ! dirty="$(git -C "$wt" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"; then
    # A live worker holding index.lock makes status fail: treat as unknown
    # dirty and refuse (fail-visible, never delete on an unknown state).
    event "$pid" "worktree_dirty_preserved" "{\"path\":\"$(printf '%s' "$wt" | json_escape)\",\"dirty_entries\":-1,\"why\":\"status_failed\"}"
    echo "worktree_pool: cannot verify $wt cleanliness (index locked?); release refused." >&2
    return 4
  fi
  if [ "$dirty" -gt 0 ]; then
    if [ "$save_patch" = 1 ]; then
      local arch="$DATA/reports/$pid/worktree-dirty-$(date +%Y%m%d-%H%M%S)"
      mkdir -p "$arch"
      git -C "$wt" diff HEAD > "$arch/tracked.patch" || true
      local n_untracked
      n_untracked="$(git -C "$wt" ls-files --others --exclude-standard | wc -l | tr -d ' ')"
      local f rel
      git -C "$wt" ls-files --others --exclude-standard | while IFS= read -r f; do
        rel="$arch/untracked/$f"
        mkdir -p "$(dirname "$rel")"
        cp -p "$wt/$f" "$rel"
      done
      event "$pid" "worktree_dirty_archived" \
        "{\"path\":\"$arch\",\"dirty_entries\":$dirty,\"untracked\":$n_untracked}"
      echo "dirty worktree archived: $arch (tracked.patch + $n_untracked untracked files)" >&2
    else
      event "$pid" "worktree_dirty_preserved" \
        "{\"path\":\"$wt\",\"dirty_entries\":$dirty}"
      echo "worktree_pool: $wt is DIRTY ($dirty entries); release refused." >&2
      echo "no silent loss: re-run with --save-patch to archive tracked.patch +" >&2
      echo "untracked content under data/reports/$pid/ before removal." >&2
      return 4
    fi
  fi
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
  # A dirty packet worktree (agent still finishing) would fail the rebase
  # for a reason that is NOT a content conflict; report it distinctly and
  # never silently conflate it with MERGE_CONFLICT.
  if [ -n "$(git -C "$wt" status --porcelain 2>/dev/null)" ]; then
    event "$pid" "merge_refused_dirty" "{\"wt\":\"$(printf '%s' "$wt" | json_escape)\"}"
    echo "worktree_pool: $wt is dirty; commit or release --save-patch before merging" >&2
    return 4
  fi
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
  # Record the exact integration commit: historical evidence must be able to
  # point at the Git state a merge actually produced.
  local merged_sha
  merged_sha="$(git -C "$LOOP_REPO" rev-parse "$INTEG_BRANCH")"
  event "$pid" "merged" "{\"into\":\"$INTEG_BRANCH\",\"sha\":\"$merged_sha\"}"
  echo "MERGED $pid -> $INTEG_BRANCH ($merged_sha)"
}

# --- merge lock ---------------------------------------------------------------
# Portable single merge mutex: flock where available (WSL/Linux); Git Bash
# has no flock, so fall back to an atomic mkdir spin-lock with the same
# 600s deadline (mkdir is atomic on every platform).
merge_lock_acquire() {
  local deadline=$(( $(date +%s) + 600 ))
  if command -v flock >/dev/null 2>&1; then
    flock -w 600 9 || die "could not obtain merge lock within 600s"
  else
    until mkdir "$LOCK.lockdir" 2>/dev/null; do
      [ "$(date +%s)" -ge "$deadline" ] && die "could not obtain merge lock within 600s"
      sleep 0.2
    done
  fi
}
merge_lock_release() {
  if ! command -v flock >/dev/null 2>&1; then
    rmdir "$LOCK.lockdir" 2>/dev/null || true
  fi
}

merge() {
  local pid="$1" rc=0
  (
    merge_lock_acquire
    merge_one "$pid" || rc=$?
    merge_lock_release
    exit "$rc"
  ) 9>>"$LOCK"
}

# --- merge-queue <pid>... -----------------------------------------------------
# Merges ACCEPTED packets one at a time; each successful merge advances the
# integration branch so the NEXT packet rebases onto it (merge-queue model:
# A vs main, B vs main+A, C vs main+A+B). Stops the queue on first conflict.
merge_queue() {
  local pid rc=0
  (
    merge_lock_acquire
    for pid in "$@"; do
      if ! merge_one "$pid"; then
        rc=3
        echo "queue stopped at $pid (conflict escalates to SOL_ADJUDICATE)" >&2
        break
      fi
    done
    merge_lock_release
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
  release)      release  "${1:?packet_id required}" "${2:-}" ;;
  merge)        merge    "${1:?packet_id required}" ;;
  merge-queue)  merge_queue "$@" ;;
  status)       status ;;
  *) die "usage: worktree_pool.sh {allocate|release|merge|merge-queue|status} <packet_id>..." ;;
esac
