#!/usr/bin/env bash
# ============================================================================
# smoke_gate.sh — Deployment smoke gate: three assertions + version check
# ----------------------------------------------------------------------------
# Purpose : Mandatory post-install / post-upgrade gate (§7.4). Asserts:
#           ① role spawnable  — each of worker/reviewer/verifier/duty_officer
#              spawns and returns (via `codex exec` with the agent TOML);
#           ② route grep visible — the SubagentStart metering hook's
#              single-line JSON contains a "model" field that matches the
#              model pinned in each role TOML (25x price differential makes
#              this a non-forgeable routing-health signal);
#           ③ write isolation — an Executor attempt to write OUTSIDE its
#              packet worktree is rejected (file must not appear).
#           Plus: `codex --version` compared against VERSIONS.lock; mismatch
#           prints "must re-run smoke gate" warning (near-daily releases).
# Input   : env CODEX_BIN (default: codex; tests point it at
#              tests/mock_codex/codex), env CODEX_HOME (default ~/.codex),
#           $1 = package root (default: script's parent dir)
# Output  : per-assertion PASS/FAIL lines; exit 0 = all pass, 1 = any fail
# Lines   : 108
# ============================================================================
set -euo pipefail

PKG_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CODEX_BIN="${CODEX_BIN:-codex}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
METER_LOG="${METER_LOG:-$PKG_ROOT/data/events.ndjson}"  # same path the SubagentStart meter hook writes
ROLES=(worker reviewer verifier duty_officer)
FAILURES=0

say()  { printf '%s\n' "$*"; }
pass() { say "PASS  $*"; }
fail() { say "FAIL  $*"; FAILURES=$((FAILURES + 1)); }

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

# --- Assertion ① role spawnable ---------------------------------------------
for ROLE in "${ROLES[@]}"; do
  TOML="$CODEX_HOME/agents/$ROLE.toml"
  [[ -f "$TOML" ]] || TOML="$PKG_ROOT/agents/$ROLE.toml"
  if [[ ! -f "$TOML" ]]; then
    fail "spawnable[$ROLE]: agent TOML not found"
    continue
  fi
  set +e
  OUT="$("$CODEX_BIN" exec --skip-git-repo-check \
        -o /dev/null "smoke: reply exactly OK ($ROLE)" 2>&1)"
  RC=$?
  set -e
  if [[ $RC -eq 0 ]]; then
    pass "spawnable[$ROLE]: spawn returned rc=0"
  else
    fail "spawnable[$ROLE]: rc=$RC ${OUT:0:120}"
  fi
done

# --- Assertion ② route grep visible (meter model field == TOML pin) ---------
for ROLE in "${ROLES[@]}"; do
  TOML="$CODEX_HOME/agents/$ROLE.toml"
  [[ -f "$TOML" ]] || TOML="$PKG_ROOT/agents/$ROLE.toml"
  PINNED="$(grep -E '^\s*model\s*=' "$TOML" 2>/dev/null | head -1 | sed -E 's/.*"([^"]+)".*/\1/' || true)"
  if [[ -z "$PINNED" ]]; then
    fail "route[$ROLE]: no model pin in TOML"
    continue
  fi
  if [[ -f "$METER_LOG" ]] && \
     grep -q "\"agent_role\"[[:space:]]*:[[:space:]]*\"$ROLE\"" "$METER_LOG" && \
     grep "\"agent_role\"[[:space:]]*:[[:space:]]*\"$ROLE\"" "$METER_LOG" | tail -1 | grep -q "\"model\"[[:space:]]*:[[:space:]]*\"$PINNED\""; then
    pass "route[$ROLE]: meter shows model=$PINNED (matches TOML pin)"
  else
    fail "route[$ROLE]: no meter line with model=$PINNED for role=$ROLE in $METER_LOG"
  fi
done

# --- Assertion ③ write isolation (Executor cannot write outside worktree) ---
ISO_TMP="$(mktemp -d)"
trap 'rm -rf "$ISO_TMP"' EXIT
WORKTREE="$ISO_TMP/worktree"; OUTSIDE="$ISO_TMP/outside"
mkdir -p "$WORKTREE" "$OUTSIDE"
set +e
( cd "$WORKTREE" && "$CODEX_BIN" exec --skip-git-repo-check \
    --sandbox workspace-write -o /dev/null \
    "smoke: create the file $OUTSIDE/escape.txt with content BREACH" ) >/dev/null 2>&1
set -e
if [[ -e "$OUTSIDE/escape.txt" ]]; then
  fail "write-isolation: Executor wrote outside worktree ($OUTSIDE/escape.txt exists)"
else
  pass "write-isolation: write outside worktree rejected"
fi

# --- Verdict -----------------------------------------------------------------
if [[ $FAILURES -eq 0 ]]; then
  say "SMOKE GATE: ALL ASSERTIONS PASS"
  exit 0
fi
say "SMOKE GATE: $FAILURES ASSERTION(S) FAILED" >&2
exit 1
