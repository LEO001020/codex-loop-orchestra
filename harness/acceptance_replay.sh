#!/usr/bin/env bash
# ============================================================================
# acceptance_replay.sh — L0 mechanical acceptance: acceptance command replay
# ----------------------------------------------------------------------------
# Purpose : Replays the acceptance commands declared in a packet's
#           "acceptance" field. Two modes:
#             freeze  — run acceptance commands on the CLEAN tree BEFORE any
#                       change lands, and write a frozen oracle JSON
#                       (baseline test count + command list + tree SHA).
#                       The oracle is frozen FIRST so the candidate can never
#                       redefine what "passing" means.
#             replay  — re-run the same commands inside the candidate worktree
#                       and record the measured test count for diffvalidator.
#           Constraint entries of the form "min_test_count>=N" are NOT shell
#           commands; they are parsed out and checked by diffvalidator.py.
# Input   : $1 = mode (freeze|replay)
#           $2 = packet JSON path
#           $3 = tree to run in (clean tree for freeze, worktree for replay)
#           $4 = oracle JSON path (written by freeze, read by replay)
# Output  : freeze : oracle JSON at $4; replay: replay result JSON at
#           <oracle>.replay.json {"test_count":N,"commands_passed":bool}
#           stdout: per-command PASS/FAIL lines + summary
#           exit 0 = all acceptance commands pass, 1 = any fail, 2 = usage
# Lines   : 121
# ============================================================================
set -euo pipefail

MODE="${1:-}"; PACKET="${2:-}"; TREE="${3:-}"; ORACLE="${4:-}"
if [[ -z "$MODE" || -z "$PACKET" || -z "$TREE" || -z "$ORACLE" ]]; then
  echo "usage: acceptance_replay.sh <freeze|replay> <packet.json> <tree_dir> <oracle.json>" >&2
  exit 2
fi
[[ -f "$PACKET" ]] || { echo "packet not found: $PACKET" >&2; exit 2; }
[[ -d "$TREE"   ]] || { echo "tree dir not found: $TREE" >&2; exit 2; }

# In replay mode the oracle must already exist (frozen first — hard order).
if [[ "$MODE" == "replay" && ! -f "$ORACLE" ]]; then
  echo "REPLAY_WITHOUT_ORACLE: oracle $ORACLE missing — freeze must run first" >&2
  exit 2
fi

# Extract executable acceptance commands (excluding min_test_count pseudo-cmd)
mapfile -t COMMANDS < <(python3 - "$PACKET" <<'PYEOF'
import json, re, sys
pkt = json.load(open(sys.argv[1]))
for item in pkt.get("acceptance", []):
    if not re.match(r"^min_test_count\s*>=\s*\d+$", str(item).strip()):
        print(item)
PYEOF
)

PACKET_ID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("packet_id","?"))' "$PACKET")"
LOG_DIR="$(dirname "$ORACLE")"
mkdir -p "$LOG_DIR"

# --- run all acceptance commands in the target tree, capture test count ----
ALL_PASS=1
TEST_COUNT=0
CMD_RESULTS="[]"
for CMD in "${COMMANDS[@]}"; do
  [[ -z "$CMD" ]] && continue
  set +e
  OUT="$(cd "$TREE" && bash -c "$CMD" 2>&1)"
  RC=$?
  set -e
  if [[ $RC -eq 0 ]]; then
    echo "PASS  [$PACKET_ID] $CMD"
  else
    echo "FAIL  [$PACKET_ID] rc=$RC $CMD"
    ALL_PASS=0
  fi
  # Best-effort mechanical test-count extraction (pytest "N passed",
  # unittest "Ran N tests", generic "N tests"). Count of PASSING tests only.
  N="$(printf '%s\n' "$OUT" | grep -oE '[0-9]+ passed' | tail -1 | grep -oE '^[0-9]+' || true)"
  if [[ -z "$N" ]]; then
    N="$(printf '%s\n' "$OUT" | grep -oE 'Ran [0-9]+ tests?' | tail -1 | grep -oE '[0-9]+' || true)"
  fi
  if [[ -n "$N" && $RC -eq 0 ]]; then
    TEST_COUNT=$((TEST_COUNT + N))
  fi
  # keep last 50 lines of each command log on disk (data plane, not context)
  printf '%s\n' "$OUT" | tail -50 > "$LOG_DIR/${PACKET_ID}.$(echo "$CMD" | tr -c 'A-Za-z0-9' '_' | cut -c1-40).log"
  CMD_RESULTS="$(python3 -c '
import json,sys
r=json.loads(sys.argv[1]); r.append({"cmd":sys.argv[2],"rc":int(sys.argv[3])})
print(json.dumps(r))' "$CMD_RESULTS" "$CMD" "$RC")"
done

TREE_SHA="$(cd "$TREE" && git rev-parse HEAD 2>/dev/null || echo "no-git")"

if [[ "$MODE" == "freeze" ]]; then
  # Oracle frozen first: refuse to overwrite an existing oracle (immutability)
  if [[ -f "$ORACLE" ]]; then
    echo "ORACLE_ALREADY_FROZEN: refusing to overwrite $ORACLE" >&2
    exit 2
  fi
  python3 -c '
import json,sys
json.dump({"packet_id":sys.argv[1],"test_count":int(sys.argv[2]),
           "tree_sha":sys.argv[3],"commands":json.loads(sys.argv[4]),
           "frozen":True},open(sys.argv[5],"w"),indent=1)' \
    "$PACKET_ID" "$TEST_COUNT" "$TREE_SHA" "$CMD_RESULTS" "$ORACLE"
  echo "ORACLE_FROZEN [$PACKET_ID] test_count=$TEST_COUNT sha=$TREE_SHA -> $ORACLE"
else
  python3 -c '
import json,sys
json.dump({"packet_id":sys.argv[1],"test_count":int(sys.argv[2]),
           "tree_sha":sys.argv[3],"commands":json.loads(sys.argv[4]),
           "commands_passed":bool(int(sys.argv[5]))},
          open(sys.argv[6],"w"),indent=1)' \
    "$PACKET_ID" "$TEST_COUNT" "$TREE_SHA" "$CMD_RESULTS" "$ALL_PASS" "${ORACLE}.replay.json"
  echo "REPLAY_DONE [$PACKET_ID] test_count=$TEST_COUNT all_pass=$ALL_PASS -> ${ORACLE}.replay.json"
fi

if [[ $ALL_PASS -eq 1 ]]; then
  echo "SUMMARY [$PACKET_ID] mode=$MODE all acceptance commands passed"
  exit 0
else
  echo "SUMMARY [$PACKET_ID] mode=$MODE acceptance FAILED" >&2
  exit 1
fi
