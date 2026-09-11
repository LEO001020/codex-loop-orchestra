#!/usr/bin/env bash
# ============================================================================
# mock_spawn.sh — Simulates spawning an Executor subagent (B-level mock)
# Purpose : Stand-in for a real `codex` subagent. Depending on the active
#           scenario it lands a report file in data/reports/<pid>/, appends
#           the corresponding hook event to data/events.ndjson, and (when a
#           worktree is given) commits a change there — exactly the artifacts
#           the deterministic harness consumes. No LLM, fully deterministic.
# Input   : $1 = packet_id ; $2 = worktree dir (optional; needed for commits)
#           env LOOP_ROOT (required) — harness data plane root
#           env MOCK_SCENARIO overrides the scenario_control.sh state file
#           env MOCK_CONFLICT_FILE (default shared.txt) for merge_conflict
# Scenarios (see scenario_control.sh):
#           normal         commit inside authorized path, report done, exit 0
#           fail           report failed + exec_failed event, exit 1
#           timeout        timeout event, NO report file, exit 1
#           path_violation commit OUTSIDE authorized paths, report done, exit 0
#           messy_failure  report failed w/ unclassifiable gibberish error,
#                          exec_failed event, exit 1
#           merge_conflict commit pid-specific content into the shared file,
#                          report done, exit 0 (conflict emerges at merge)
# Output  : report.json + events.ndjson lines; exit code per scenario.
# Lines   : ~95
# ============================================================================
set -euo pipefail

PID="${1:?packet_id required}"
WT="${2:-}"
[ -n "${LOOP_ROOT:-}" ] || { echo "mock_spawn: LOOP_ROOT not set" >&2; exit 2; }
DATA="$LOOP_ROOT/data"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${MOCK_CODEX_STATE:-$SCRIPT_DIR/.state}/scenario"
SCEN="${MOCK_SCENARIO:-$(cat "$STATE_FILE" 2>/dev/null || echo normal)}"
CONFLICT_FILE="${MOCK_CONFLICT_FILE:-shared.txt}"

event() {  # event <event_name> <detail-json>
  printf '{"ts":%s,"packet_id":"%s","event":"%s","detail":%s}\n' \
    "$(date +%s)" "$PID" "$1" "${2:-{\}}" >> "$DATA/events.ndjson"
}

report() {  # report <status> <extra-json-fields (no braces)>
  mkdir -p "$DATA/reports/$PID"
  local extra="${2:-}"
  [ -n "$extra" ] && extra=",$extra"
  printf '{"packet_id":"%s","status":"%s","summary":"mock executor (%s)","diff_stat":"mock"%s}\n' \
    "$PID" "$1" "$SCEN" "$extra" > "$DATA/reports/$PID/report.json"
}

first_authorized_path() {  # reads the packet's first authorized_paths entry
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["authorized_paths"][0])' \
    "$DATA/packets/$PID.json"
}

commit_in_worktree() {  # commit_in_worktree <relative_file> <content-line>
  [ -n "$WT" ] || return 0
  mkdir -p "$WT/$(dirname "$1")"
  printf '%s\n' "$2" >> "$WT/$1"
  git -C "$WT" add -A
  git -C "$WT" -c user.name=mock -c user.email=mock@test.local \
      commit -q -m "mock($SCEN): $PID touches $1"
}

case "$SCEN" in
  normal)
    if [ -n "$WT" ]; then
      AP="$(first_authorized_path)"
      case "$AP" in
        */) TARGET="${AP}mock_change_${PID}.py" ;;
        *)  TARGET="$AP" ;;
      esac
      commit_in_worktree "$TARGET" "# mock change by $PID"
    fi
    report done
    event subagent_stop '{"mock":true,"scenario":"normal"}'
    exit 0
    ;;
  fail)
    report failed '"error":"exit code 1: acceptance command failed (mock)"'
    event exec_failed '{"mock":true,"scenario":"fail"}'
    exit 1
    ;;
  timeout)
    # No report file: simulates job_max_runtime_seconds kill mid-flight.
    event timeout '{"mock":true,"scenario":"timeout"}'
    exit 1
    ;;
  path_violation)
    commit_in_worktree "src/zone_forbidden/evil_${PID}.py" "# unauthorized write by $PID"
    report done
    event subagent_stop '{"mock":true,"scenario":"path_violation"}'
    exit 0
    ;;
  messy_failure)
    report failed '"error":"zorblatt quux discombobulated 0xDEADBEEF wibble (unclassifiable)"'
    event exec_failed '{"mock":true,"scenario":"messy_failure"}'
    exit 1
    ;;
  merge_conflict)
    if [ -n "$WT" ]; then
      # Overwrite (not append) the same line region so two packets conflict.
      printf 'edited-by-%s\nline2\nline3\n' "$PID" > "$WT/$CONFLICT_FILE"
      git -C "$WT" add -A
      git -C "$WT" -c user.name=mock -c user.email=mock@test.local \
          commit -q -m "mock(merge_conflict): $PID rewrites $CONFLICT_FILE"
    fi
    report done
    event subagent_stop '{"mock":true,"scenario":"merge_conflict"}'
    exit 0
    ;;
  *)
    echo "mock_spawn: unknown scenario '$SCEN'" >&2
    exit 2
    ;;
esac
