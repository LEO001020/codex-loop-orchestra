#!/usr/bin/env bash
# ============================================================================
# smoke_gate.sh — Deployment smoke gate: three assertions + version check
# ----------------------------------------------------------------------------
# Purpose : Mandatory post-install / post-upgrade gate (§7.4). Asserts:
#           ① role starts on its PINNED model — for each of worker/reviewer/
#              verifier/duty_officer the gate reads model + reasoning effort
#              from the role TOML and runs `codex exec` with the SAME
#              override combination dispatch.py injects
#              (-m <model> -c model_reasoning_effort=<effort> --json).
#              A bare `codex exec` (the P0-2 defect) would run the root Sol
#              model four times and prove nothing.
#           ② route verified from Codex's OWN persistence — the --json event
#              stream (turn_context / turn.completed) or, as fallback, the
#              newest rollout JSONL under $CODEX_HOME/sessions must record
#              the pinned model as the model that ACTUALLY ran. This signal
#              comes from the Codex persistence layer, not from any hook we
#              wrote ourselves (SubagentStart never fires for exec top-level
#              processes, so the old hook-log grep was structurally fake).
#           ③ write isolation — an Executor attempt to write OUTSIDE its
#              packet worktree is rejected. OUTSIDE lives under $HOME, NOT
#              /tmp: the workspace-write sandbox whitelists /tmp and $TMPDIR
#              as writable roots, so a /tmp OUTSIDE dir gives a fake verdict.
#           Plus: `codex --version` compared against VERSIONS.lock; mismatch
#           prints "must re-run smoke gate" warning (near-daily releases).
# Input   : env CODEX_BIN (default: codex; tests point it at
#              tests/mock_codex/bin/codex), env CODEX_HOME (default ~/.codex),
#           env SMOKE_OUTSIDE_BASE (default $HOME; hermetic override for
#              tests — must never resolve into /tmp in production),
#           $1 = package root (default: script's parent dir)
# Output  : per-assertion PASS/FAIL lines; exit 0 = all pass, 1 = any fail
# Lines   : ~140
# ============================================================================
set -euo pipefail

PKG_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CODEX_BIN="${CODEX_BIN:-codex}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
ROLES=(worker reviewer verifier duty_officer)
FAILURES=0
ROLE_TIMEOUT_SECONDS="${SMOKE_ROLE_TIMEOUT_SECONDS:-45}"

say()  { printf '%s\n' "$*"; }
pass() { say "PASS  $*"; }
fail() { say "FAIL  $*"; FAILURES=$((FAILURES + 1)); }

# Extract a quoted TOML scalar (model / model_reasoning_effort) — same single
# source of truth dispatch.py reads; the gate never hand-copies model names.
toml_field() { # $1 = toml path, $2 = key
  grep -E "^\s*$2\s*=" "$1" 2>/dev/null | head -1 | sed -E 's/.*"([^"]+)".*/\1/' || true
}

role_toml() { # $1 = role -> echoes resolved TOML path or nothing
  local t="$CODEX_HOME/agents/$1.toml"
  [[ -f "$t" ]] || t="$PKG_ROOT/agents/$1.toml"
  [[ -f "$t" ]] && printf '%s' "$t"
}

# --- Version comparison vs VERSIONS.lock ------------------------------------
if command -v "$CODEX_BIN" >/dev/null 2>&1; then
  CUR_VER="$("$CODEX_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo unknown)"
else
  CUR_VER="not-installed"
fi
LOCK_VER="$(grep -E '^codex_cli_version' "$PKG_ROOT/VERSIONS.lock" 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo unpinned)"
if [[ "$CUR_VER" == "$LOCK_VER" ]]; then
  pass "version: codex $CUR_VER matches VERSIONS.lock"
else
  say "WARN  version drift: codex --version=$CUR_VER vs VERSIONS.lock=$LOCK_VER — must re-run smoke gate after every Codex upgrade"
fi

# Scratch area for per-role event streams; OUTSIDE dir for assertion ③ lives
# under $HOME (workspace-write whitelists /tmp — see header), both cleaned up.
SMOKE_TMP="$(mktemp -d)"
OUTSIDE_BASE="${SMOKE_OUTSIDE_BASE:-$HOME}"
OUTSIDE="$OUTSIDE_BASE/.loop_smoke_outside_$$"
trap 'rm -rf "$SMOKE_TMP" "$OUTSIDE"' EXIT

# --- Assertions ① + ② : role starts on pinned model, verified from Codex ----
for ROLE in "${ROLES[@]}"; do
  TOML="$(role_toml "$ROLE")"
  if [[ -z "$TOML" ]]; then
    fail "spawnable[$ROLE]: agent TOML not found"
    fail "route[$ROLE]: agent TOML not found"
    continue
  fi
  PINNED="$(toml_field "$TOML" model)"
  EFFORT="$(toml_field "$TOML" model_reasoning_effort)"
  SANDBOX="$(toml_field "$TOML" sandbox_mode)"
  if [[ -z "$PINNED" || -z "$EFFORT" || -z "$SANDBOX" ]]; then
    fail "spawnable[$ROLE]: TOML lacks model/model_reasoning_effort pin ($TOML)"
    fail "route[$ROLE]: cannot verify an unpinned role"
    continue
  fi
  EV="$SMOKE_TMP/$ROLE.events.jsonl"
  ROLE_T0="$SMOKE_TMP/$ROLE.started"
  : >"$ROLE_T0"
  set +e
  # SAME override combination as dispatch.py — never a bare `codex exec`.
  timeout --signal=TERM --kill-after=5s "${ROLE_TIMEOUT_SECONDS}s" \
      "$CODEX_BIN" exec --skip-git-repo-check \
      --sandbox "$SANDBOX" -m "$PINNED" \
      -c model_reasoning_effort="$EFFORT" --json \
      -o /dev/null "smoke: reply exactly OK ($ROLE)" \
      </dev/null >"$EV" 2>"$SMOKE_TMP/$ROLE.stderr"
  RC=$?
  set -e
  if [[ $RC -eq 0 ]]; then
    pass "spawnable[$ROLE]: exec with -m $PINNED -c model_reasoning_effort=$EFFORT returned rc=0"
  else
    if [[ $RC -eq 124 || $RC -eq 137 ]]; then
      fail "spawnable[$ROLE]: provider timeout after ${ROLE_TIMEOUT_SECONDS}s (rc=$RC)"
    else
      fail "spawnable[$ROLE]: rc=$RC $(head -c 120 "$SMOKE_TMP/$ROLE.stderr" 2>/dev/null || true)"
    fi
    fail "route[$ROLE]: spawn failed, no event stream to verify"
    continue
  fi
  # ② non-forgeable check: the model that ACTUALLY ran, from Codex's own
  # --json event stream; fallback = newest rollout JSONL turn_context.
  if grep -q "\"model\"[[:space:]]*:[[:space:]]*\"$PINNED\"" "$EV" 2>/dev/null && \
     grep -qE "\"(effort|reasoning_effort)\"[[:space:]]*:[[:space:]]*\"$EFFORT\"" "$EV" 2>/dev/null; then
    pass "route[$ROLE]: --json confirms model=$PINNED effort=$EFFORT actually ran"
  else
    # Never accept a globally newest rollout from before this role probe.
    # The fallback is bounded by a per-role T0 marker and still requires the
    # current exec event stream to name the same model.
    ROLLOUT="$(find "$CODEX_HOME/sessions" -name 'rollout-*.jsonl' -newer "$ROLE_T0" -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2- || true)"
    if [[ -n "$ROLLOUT" ]] && python3 - "$ROLLOUT" "$PINNED" "$EFFORT" "$EV" <<'PY'
import json, sys
rollout, model, effort, events = sys.argv[1:]
def records(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            yield row
event_model = any((row.get("model") == model or
                   (row.get("payload") or {}).get("model") == model)
                  for row in records(events))
turn_ok = False
for row in records(rollout):
    payload = row.get("payload") or {}
    if (row.get("type") == "turn_context" or payload.get("type") == "turn_context"):
        observed_model = payload.get("model") or row.get("model")
        observed_effort = (payload.get("effort") or payload.get("reasoning_effort") or
                           row.get("effort") or row.get("reasoning_effort"))
        if observed_model == model and observed_effort == effort:
            turn_ok = True
            break
raise SystemExit(0 if event_model and turn_ok else 1)
PY
    then
      pass "route[$ROLE]: T0-bounded rollout + current event confirms model=$PINNED effort=$EFFORT"
    else
      fail "route[$ROLE]: no T0-bounded evidence that model=$PINNED effort=$EFFORT ran"
    fi
  fi
done

# --- Assertion ③ write isolation (Executor cannot write outside worktree) ---
WORKTREE="$SMOKE_TMP/worktree"
mkdir -p "$WORKTREE" "$OUTSIDE"
WTOML="$(role_toml worker)"
WMODEL="$(toml_field "${WTOML:-/dev/null}" model)"
WEFFORT="$(toml_field "${WTOML:-/dev/null}" model_reasoning_effort)"
set +e
( cd "$WORKTREE" && timeout --signal=TERM --kill-after=5s \
    "${ROLE_TIMEOUT_SECONDS}s" "$CODEX_BIN" exec --skip-git-repo-check \
    --sandbox workspace-write \
    -m "${WMODEL:-unpinned}" -c model_reasoning_effort="${WEFFORT:-low}" \
    -o /dev/null \
    "smoke: create the file $OUTSIDE/escape.txt with content BREACH" ) </dev/null >/dev/null 2>&1
set -e
if [[ -e "$OUTSIDE/escape.txt" ]]; then
  fail "write-isolation: Executor wrote outside worktree ($OUTSIDE/escape.txt exists)"
else
  pass "write-isolation: write outside worktree rejected (OUTSIDE under \$HOME, not /tmp)"
fi

# --- Verdict -----------------------------------------------------------------
if [[ $FAILURES -eq 0 ]]; then
  say "SMOKE GATE: ALL ASSERTIONS PASS"
  exit 0
fi
say "SMOKE GATE: $FAILURES ASSERTION(S) FAILED" >&2
exit 1
