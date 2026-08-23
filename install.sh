#!/usr/bin/env bash
# ============================================================================
# install.sh — Codex LOOP Orchestra installer (Linux / WSL)
# ----------------------------------------------------------------------------
# Purpose : Install the codex-loop-open-source environment package:
#           ① check Python 3.11+, Node 22+, Git, and Codex CLI (missing -> PRINT install commands,
#              never silently installs anything global);
#           ② copy agents/*.toml into $CODEX_HOME/agents/ (default ~/.codex/,
#              CODEX_HOME respected; identical files skipped, user-modified
#              files backed up before replacement);
#           ③ merge config.toml.example keys into the user config.toml —
#              existing user keys are NEVER overwritten; a diff is printed;
#           ④ initialize the control-root data skeleton and activate managed
#              global hooks with a verified backup/restore ledger;
#           ⑤ auto-run harness/smoke_gate.sh (skip with --skip-smoke).
# Input   : ./install.sh [--repo <path>] [--skip-smoke]
#           env CODEX_HOME (default ~/.codex), env CODEX_BIN (default codex)
# Output  : installed agent TOMLs, merged config.toml, control data skeleton,
#           managed hooks, smoke-gate verdict. Exit 0 ok / 1 prerequisite or
#           smoke failure.
# Idempotency: re-running produces no duplicate keys, no duplicate hook
#           entries, no destructive overwrites; every step detects prior state.
# ============================================================================
set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
CODEX_BIN="${CODEX_BIN:-codex}"
TARGET_REPO="$PWD"
SKIP_SMOKE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) TARGET_REPO="$(cd "$2" && pwd)"; shift 2 ;;
    --skip-smoke) SKIP_SMOKE=1; shift ;;
    -h|--help)
      sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown argument: $1 (see --help)" >&2; exit 1 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s ==\n' "$*"; }
atomic_copy() { # $1 = source, $2 = destination
  local src="$1" dst="$2" tmp="$2.tmp.$$"
  if ! cp "$src" "$tmp"; then
    rm -f -- "$tmp"
    return 1
  fi
  if ! mv -f "$tmp" "$dst"; then
    rm -f -- "$tmp"
    return 1
  fi
}

# ----------------------------------------------------------------------------
# ① Prerequisites. Missing -> prompt, never install.
# ----------------------------------------------------------------------------
step "1/5 Prerequisite check (Python 3.11+, Node 22+, Git, Codex CLI)"
MISSING=0

if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'; then
  say "OK    $(python3 --version) (>= 3.11)"
else
  say "MISSING Python 3.11+ with tomllib. Install it before continuing."
  MISSING=1
fi

if command -v git >/dev/null 2>&1; then
  GIT_V="$(git --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  GIT_MAJOR="${GIT_V%%.*}"
  GIT_REST="${GIT_V#*.}"; GIT_MINOR="${GIT_REST%%.*}"
  if [[ "$GIT_MAJOR" -gt 2 || ( "$GIT_MAJOR" -eq 2 && "$GIT_MINOR" -ge 40 ) ]]; then
    say "OK    git $GIT_V (>= 2.40)"
  else
    say "MISSING git $GIT_V is < 2.40. Upgrade Git before continuing."
    MISSING=1
  fi
else
  say "MISSING git. Install Git 2.40+ before continuing."
  MISSING=1
fi

if command -v node >/dev/null 2>&1; then
  NODE_V="$(node --version)"
  NODE_MAJOR="${NODE_V#v}"; NODE_MAJOR="${NODE_MAJOR%%.*}"
  if [[ "$NODE_MAJOR" -ge 22 ]]; then
    say "OK    node $NODE_V (>= 22)"
  else
    say "WARN  node $NODE_V is < 22. Node 22 LTS is the pinned toolchain (VERSIONS.lock)."
    say "      Install (choose one, run yourself — this script will not):"
    say "        nvm install 22 && nvm use 22"
    say "        # or download https://nodejs.org/dist/v22.23.2/node-v22.23.2-linux-x64.tar.xz"
    MISSING=1
  fi
else
  say "MISSING node. Install Node 22 LTS yourself (this script will not):"
  say "        nvm install 22   # or see https://nodejs.org/en/download"
  MISSING=1
fi

if command -v "$CODEX_BIN" >/dev/null 2>&1; then
  CODEX_V="$("$CODEX_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo unknown)"
  LOCK_V="$(grep -E '^codex_cli_version' "$PKG_ROOT/VERSIONS.lock" 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo unpinned)"
  say "OK    codex $CODEX_V (VERSIONS.lock pins $LOCK_V)"
  if [[ "$CODEX_V" != "$LOCK_V" ]]; then
    say "WARN  version drift vs VERSIONS.lock — smoke gate MUST be re-run after every Codex upgrade (§7.4)."
  fi
else
  say "MISSING codex CLI. Install yourself (choose one, this script will not):"
  say "        npm install -g @openai/codex"
  say "        curl -fsSL https://chatgpt.com/codex/install.sh | sh"
  MISSING=1
fi

if [[ "$MISSING" -eq 1 ]]; then
  say ""
  say "ABORT: prerequisites missing (printed above). Re-run install.sh after installing them."
  exit 1
fi

# Fail before the first user-state write when existing or template inputs are
# malformed.  This keeps agent/config/hook installation simple and avoids a
# half-applied user transaction caused by discovering bad JSON/TOML late.
python3 - "$PKG_ROOT/config/config.toml.example" "$CODEX_HOME/config.toml" \
  "$PKG_ROOT/config/global_requirements.toml" "$CODEX_HOME/hooks.json" <<'PYEOF'
import json, os, sys, tomllib
example, user_config, requirements, user_hooks = sys.argv[1:]
with open(example, "rb") as handle:
    tomllib.load(handle)
if os.path.exists(user_config):
    with open(user_config, "rb") as handle:
        tomllib.load(handle)
with open(requirements, "rb") as handle:
    tomllib.load(handle)
if os.path.exists(user_hooks):
    with open(user_hooks, encoding="utf-8-sig") as handle:
        hooks = json.load(handle)
    if not isinstance(hooks, dict):
        raise SystemExit("hooks.json must contain one JSON object")
print("OK    config and managed-requirements inputs parse before user-state writes")
PYEOF

# ----------------------------------------------------------------------------
# ② Agent TOMLs -> $CODEX_HOME/agents/
# ----------------------------------------------------------------------------
step "2/5 Agent TOMLs -> $CODEX_HOME/agents/"
# Create the immutable restore ledger before the compatibility shell steps
# below. They then observe identical files/keys and become no-ops, while the
# familiar per-file output remains available to existing operators.
python3 "$PKG_ROOT/harness/install_user_config.py" install \
  --root "$PKG_ROOT" --codex-home "$CODEX_HOME"
mkdir -p "$CODEX_HOME/agents"
for SRC in "$PKG_ROOT"/agents/*.toml; do
  BASE="$(basename "$SRC")"
  DST="$CODEX_HOME/agents/$BASE"
  if [[ -f "$DST" ]] && cmp -s "$SRC" "$DST"; then
    say "SKIP  $BASE (identical already installed)"
  elif [[ -f "$DST" ]]; then
    BAK="$DST.bak.$(date -u +%Y%m%dT%H%M%SZ)-$$"
    cp "$DST" "$BAK"
    atomic_copy "$SRC" "$DST"
    say "REPLACED $BASE (previous version backed up: $BAK)"
  else
    atomic_copy "$SRC" "$DST"
    say "INSTALLED $BASE"
  fi
done

# ----------------------------------------------------------------------------
# ③ Merge config.toml.example keys into $CODEX_HOME/config.toml
#    (existing keys never overwritten; diff printed for user confirmation)
# ----------------------------------------------------------------------------
step "3/5 Config merge -> $CODEX_HOME/config.toml"
USER_CFG="$CODEX_HOME/config.toml"
EXAMPLE="$PKG_ROOT/config/config.toml.example"
if [[ ! -f "$USER_CFG" ]]; then
  TMP_CFG="$USER_CFG.tmp.$$"
  atomic_copy "$EXAMPLE" "$TMP_CFG"
  if ! python3 -c 'import sys,tomllib; tomllib.load(open(sys.argv[1], "rb"))' "$TMP_CFG"; then
    rm -f -- "$TMP_CFG"
    exit 1
  fi
  mv -f "$TMP_CFG" "$USER_CFG"
  say "CREATED $USER_CFG from config.toml.example (no prior user config)"
else
  python3 - "$EXAMPLE" "$USER_CFG" <<'PYEOF'
import re, sys, difflib, tempfile, os, shutil, time
try:
    import tomllib
except ImportError:  # pragma: no cover
    sys.exit("python3 with tomllib (3.11+) required for config merge")

example_path, user_path = sys.argv[1], sys.argv[2]
with open(user_path, "rb") as f:
    user = tomllib.load(f)

def has_key(cfg, section, key):
    node = cfg
    if section:
        for part in section.split("."):
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
    return isinstance(node, dict) and key in node

# Walk the example line-by-line; collect uncommented keys the user lacks.
sec_re = re.compile(r"^\s*\[+([A-Za-z0-9_.\-]+)\]+\s*$")
key_re = re.compile(r"^\s*([A-Za-z0-9_\-]+)\s*=")
missing = {}   # section -> [raw key lines]
section = ""
in_multiline = False
with open(example_path) as f:
    for raw in f:
        line = raw.rstrip("\n")
        if in_multiline:
            missing.setdefault(section, [])
            if section in missing and missing[section] and missing[section][-1][0]:
                missing[section][-1][1].append(line)
            if '"""' in line:
                in_multiline = False
            continue
        m = sec_re.match(line)
        if m:
            section = m.group(1)
            continue
        m = key_re.match(line)
        if m and not line.lstrip().startswith("#"):
            key = m.group(1)
            if not has_key(user, section, key):
                missing.setdefault(section, []).append([key, [line]])
            if line.count('"""') == 1:
                in_multiline = True

with open(user_path) as f:
    user_lines = f.read().splitlines()

new_lines = list(user_lines)
appended = []
for section, entries in missing.items():
    if not entries:
        continue
    header = f"[{section}]"
    lines = [l for _, block in entries for l in block]
    idx = next((i for i, l in enumerate(new_lines) if l.strip() == header), None)
    if idx is not None:
        new_lines[idx+1:idx+1] = lines           # insert just after existing header
    else:
        appended += ["", f"# merged from codex-loop-open-source config.toml.example", header] + lines

if appended:
    new_lines += appended

if new_lines == user_lines:
    print("SKIP  config.toml already contains every key from config.toml.example")
    sys.exit(0)

diff = difflib.unified_diff(user_lines, new_lines,
                            fromfile="config.toml (before)",
                            tofile="config.toml (after merge)", lineterm="")
print("Merged the following NEW keys (existing keys untouched) — diff:")
for d in diff:
    print(d)
rendered = "\n".join(new_lines) + "\n"
# Validate the exact bytes before preserving/replacing the user's config.
tomllib.loads(rendered)
backup = user_path + ".bak." + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "." + str(time.time_ns())
shutil.copy2(user_path, backup)
print("BACKUP config.toml -> %s" % backup)
tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=os.path.dirname(user_path))
tmp.write(rendered)
tmp.close()
os.replace(tmp.name, user_path)
PYEOF
fi

# ----------------------------------------------------------------------------
# ④ Activate one global LOOP control root
# ----------------------------------------------------------------------------
step "4/5 Activate global LOOP mode"
if ! git -C "$TARGET_REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  say "WARN  $TARGET_REPO is not a git repo — worktree isolation and merge features need git."
fi
DATA="$PKG_ROOT/data"
mkdir -p "$DATA/packets" "$DATA/reports" "$DATA/dead_letters"
touch "$DATA/events.ndjson" "$DATA/escalation_log.jsonl" "$DATA/lessons.jsonl" "$DATA/.merge.lock"
[[ -f "$DATA/progress_ledger.json" ]] || printf '{"packets": {}, "waves": []}\n' > "$DATA/progress_ledger.json"
say "OK    control data skeleton at $DATA"
python3 "$PKG_ROOT/harness/global_desktop_mode.py" activate \
  --root "$PKG_ROOT" --codex-home "$CODEX_HOME"
say "OK    managed requirements, active agreement, spawn gates, and lifecycle hooks installed"
say "NOTE  Fully restart Codex Desktop/CLI and start a new task after activation."

# ----------------------------------------------------------------------------
# ⑤ Smoke gate (mandatory after install and after every Codex upgrade)
# ----------------------------------------------------------------------------
step "5/5 Smoke gate"
if [[ "$SKIP_SMOKE" -eq 1 ]]; then
  say "SKIPPED (--skip-smoke). Run it before first use: $PKG_ROOT/harness/smoke_gate.sh"
  exit 0
fi
if "$PKG_ROOT/harness/smoke_gate.sh" "$PKG_ROOT"; then
  say ""
  say "INSTALL COMPLETE — smoke gate green. See README.md for the 5-minute deployment path."
else
  say ""
  say "INSTALL FINISHED WITH SMOKE-GATE FAILURES — fix the FAIL lines above before first use." >&2
  exit 1
fi
