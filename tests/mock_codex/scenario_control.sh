#!/usr/bin/env bash
# ============================================================================
# scenario_control.sh — Controls mock_codex behavior for B-level tests
# Purpose : Set/reset the scenario the mock codex layer (mock_spawn.sh,
#           mock_codex_exec.sh) will act out on its next invocation.
# Input   : $1 = set|reset|get ; $2 = scenario name for `set`
#           Scenarios: normal | fail | timeout | path_violation |
#                      messy_failure | merge_conflict | breach
#           env MOCK_CODEX_STATE = state dir (default <script_dir>/.state)
# Output  : state file $MOCK_CODEX_STATE/scenario ; `get` prints scenario.
#           Exit 0 ok, 2 usage error.
# Lines   : ~35
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${MOCK_CODEX_STATE:-$SCRIPT_DIR/.state}"
STATE_FILE="$STATE_DIR/scenario"
VALID="normal fail timeout path_violation messy_failure merge_conflict breach"

cmd="${1:-}"
case "$cmd" in
  set)
    scen="${2:-}"
    ok=0
    for v in $VALID; do [ "$v" = "$scen" ] && ok=1; done
    if [ "$ok" -ne 1 ]; then
      echo "scenario_control: unknown scenario '$scen' (valid: $VALID)" >&2
      exit 2
    fi
    mkdir -p "$STATE_DIR"
    printf '%s\n' "$scen" > "$STATE_FILE"
    echo "scenario=$scen"
    ;;
  reset)
    mkdir -p "$STATE_DIR"
    printf 'normal\n' > "$STATE_FILE"
    echo "scenario=normal"
    ;;
  get)
    if [ -f "$STATE_FILE" ]; then cat "$STATE_FILE"; else echo normal; fi
    ;;
  *)
    echo "usage: scenario_control.sh {set <scenario>|reset|get}" >&2
    exit 2
    ;;
esac
