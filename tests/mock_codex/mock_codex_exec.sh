#!/usr/bin/env bash
# ============================================================================
# mock_codex_exec.sh — Simulates the `codex` CLI for B-level acceptance
# Purpose : Drop-in fake for `codex exec ...` (and `codex --version`). Parses
#           the flags the harness actually uses (--json, --skip-git-repo-check,
#           --output-schema FILE, --sandbox MODE, -o/--output-last-message
#           FILE) and the trailing prompt. If the prompt names a report path
#           (data/reports/<pid>/report.json) it lands that report and appends
#           a subagent_stop event under $LOOP_ROOT — closing the loop for
#           dispatch.py single-spawn tests without any real Codex.
# Input   : codex-style argv; env LOOP_ROOT (optional, enables report landing)
#           scenario via scenario_control.sh state file / MOCK_SCENARIO:
#             normal  -> canned success output, exit 0
#             fail    -> error text on stderr, exit 1
#             breach  -> additionally obeys "create the file <path>" prompts
#                        (simulates a sandbox escape for smoke-gate testing)
#           any other scenario behaves like normal for the CLI surface.
# Output  : canned last message / JSONL / schema-shaped JSON; exit 0 or 1.
# Lines   : ~90
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${MOCK_CODEX_STATE:-$SCRIPT_DIR/.state}/scenario"
SCEN="${MOCK_SCENARIO:-$(cat "$STATE_FILE" 2>/dev/null || echo normal)}"

# --- codex --version ---------------------------------------------------------
if [ "${1:-}" = "--version" ]; then
  echo "codex-cli 0.0.0-mock"
  exit 0
fi

# --- parse `codex exec [flags] <prompt>` --------------------------------------
JSON_MODE=0
OUT_FILE=""
SCHEMA_FILE=""
PROMPT=""
SEEN_EXEC=0
while [ $# -gt 0 ]; do
  case "$1" in
    exec) SEEN_EXEC=1 ;;
    --json) JSON_MODE=1 ;;
    --skip-git-repo-check) : ;;                # accepted, no-op
    --sandbox) shift ;;                        # accepted, value ignored
    --output-schema) shift; SCHEMA_FILE="${1:-}" ;;
    -o|--output-last-message) shift; OUT_FILE="${1:-}" ;;
    --*) : ;;                                  # unknown flags tolerated
    *) PROMPT="$1" ;;                          # last positional wins
  esac
  shift
done

if [ "$SEEN_EXEC" -ne 1 ]; then
  echo "mock codex: only 'exec' and '--version' are simulated" >&2
  exit 2
fi

# --- scenario: fail ------------------------------------------------------------
if [ "$SCEN" = "fail" ]; then
  echo "mock codex: simulated spawn failure (scenario=fail)" >&2
  exit 1
fi

# --- scenario: breach — deliberately violate write isolation -------------------
if [ "$SCEN" = "breach" ]; then
  ESCAPE="$(printf '%s' "$PROMPT" | grep -oE 'create the file [^ ]+' | sed 's/^create the file //' || true)"
  if [ -n "$ESCAPE" ]; then
    mkdir -p "$(dirname "$ESCAPE")"
    echo "BREACH" > "$ESCAPE"
  fi
fi

# --- report landing: prompt names data/reports/<pid>/report.json ---------------
RPT_REL="$(printf '%s' "$PROMPT" | grep -oE 'data/reports/[A-Za-z0-9_.-]+/report\.json' | head -1 || true)"
if [ -n "$RPT_REL" ] && [ -n "${LOOP_ROOT:-}" ]; then
  PID_FROM_PROMPT="$(basename "$(dirname "$RPT_REL")")"
  mkdir -p "$LOOP_ROOT/$(dirname "$RPT_REL")"
  printf '{"packet_id":"%s","status":"done","summary":"mock codex exec ok","diff_stat":"mock"}\n' \
    "$PID_FROM_PROMPT" > "$LOOP_ROOT/$RPT_REL"
  printf '{"ts":%s,"packet_id":"%s","event":"subagent_stop","detail":{"mock":true,"via":"mock_codex_exec"}}\n' \
    "$(date +%s)" "$PID_FROM_PROMPT" >> "$LOOP_ROOT/data/events.ndjson"
fi

# --- outputs -------------------------------------------------------------------
LAST_MSG="OK (mock codex, scenario=$SCEN)"
if [ -n "$OUT_FILE" ] && [ "$OUT_FILE" != "/dev/null" ]; then
  printf '%s\n' "$LAST_MSG" > "$OUT_FILE"
elif [ -n "$OUT_FILE" ]; then
  : # /dev/null — discard
fi

if [ -n "$SCHEMA_FILE" ]; then
  # Canned JSON conforming to dispatch.py's output_schema shape.
  printf '{"status":"done","summary":"mock codex exec ok","report_path":"%s"}\n' "${RPT_REL:-none}"
elif [ "$JSON_MODE" -eq 1 ]; then
  printf '{"type":"agent_message","message":"%s"}\n' "$LAST_MSG"
  printf '{"type":"task_complete","last_agent_message":"%s"}\n' "$LAST_MSG"
else
  printf '%s\n' "$LAST_MSG"
fi
exit 0
