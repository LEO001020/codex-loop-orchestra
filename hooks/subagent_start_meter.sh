#!/usr/bin/env sh
# ============================================================================
# subagent_start_meter.sh — SubagentStart metering hook
# Purpose : append one single-line JSON record per subagent spawn to
#           data/events.ndjson (fail-visible routing health observation;
#           the 25x price differential between tiers makes `model` a
#           non-forgeable routing signal)
# Input   : SubagentStart hook payload as one JSON object on stdin
#           (fields used: model, cwd, agent_type, agent_id, turn_id,
#            session_id, permission_mode)
# Output  : one NDJSON line appended to $LOOP_DATA_DIR/events.ndjson
#           {"event":"SubagentStart","ts_utc":...,"model":...,"cwd":...,
#            "agent_role":...,"agent_id":...,"turn_id":...,"session_id":...,
#            "permission_mode":...}
# Safety  : FAIL-OPEN — this hook is pure observation (spec §10 S4). It must
#           NEVER block a spawn: every failure path exits 0. SubagentStart
#           cannot block spawns at the platform level either; events.ndjson
#           is only the fast path — report files are the second truth source.
# Lines   : ~55
# ============================================================================

# Everything is wrapped so that any error still results in exit 0.
{
  # Data directory: repo-local data/ by default, overridable for tests.
  DATA_DIR="${LOOP_DATA_DIR:-data}"
  OUT_FILE="${DATA_DIR}/events.ndjson"

  # Read the entire stdin payload (single JSON object).
  PAYLOAD="$(cat 2>/dev/null || true)"

  # UTC timestamp, ISO-8601 with seconds.
  TS_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"

  mkdir -p "$DATA_DIR" 2>/dev/null || true

  LINE=""
  if command -v jq >/dev/null 2>&1 && [ -n "$PAYLOAD" ]; then
    # Preferred path: jq guarantees valid, single-line, properly escaped JSON.
    LINE="$(printf '%s' "$PAYLOAD" | jq -c --arg ts "$TS_UTC" '{
      event: "SubagentStart",
      ts_utc: $ts,
      model: (.model // "unknown"),
      cwd: (.cwd // "unknown"),
      agent_role: (.agent_type // "unknown"),
      agent_id: (.agent_id // null),
      turn_id: (.turn_id // null),
      session_id: (.session_id // null),
      permission_mode: (.permission_mode // null)
    }' 2>/dev/null || true)"
  fi

  if [ -z "$LINE" ]; then
    # Degraded path (no jq, empty payload, or malformed JSON): emit a minimal
    # well-formed line so the metering stream never silently gaps. Raw payload
    # is NOT embedded (unescaped JSON-in-JSON would corrupt the NDJSON stream).
    LINE="$(printf '{"event":"SubagentStart","ts_utc":"%s","model":"unknown","cwd":"unknown","agent_role":"unknown","degraded":true}' "$TS_UTC")"
  fi

  printf '%s\n' "$LINE" >> "$OUT_FILE" 2>/dev/null || true
} 2>/dev/null

# Fail-open invariant: unconditional success so the spawn is never impeded
# and the hook is never marked failed for transient metering issues.
exit 0
