#!/usr/bin/env bash
# ============================================================================
# install.sh — Codex-LOOP-Build-F2 one-click installer (idempotent, spec §8)
# ----------------------------------------------------------------------------
# Purpose : Install the codex-loop-s-f2 environment package:
#           ① check Node 22+ and Codex CLI (missing -> PRINT install commands,
#              never silently installs anything global);
#           ② copy agents/*.toml into $CODEX_HOME/agents/ (default ~/.codex/,
#              CODEX_HOME respected; identical files skipped, user-modified
#              files backed up before replacement);
#           ③ merge config.toml.example keys into the user config.toml —
#              existing user keys are NEVER overwritten; a diff is printed;
#           ④ initialize the data/ directory skeleton and mount the metering
#              hook (.codex/hooks.json + hook script) in the target git repo;
#           ⑤ auto-run harness/smoke_gate.sh (skip with --skip-smoke).
# Input   : ./install.sh [--repo <path>] [--skip-smoke]
#           env CODEX_HOME (default ~/.codex), env CODEX_BIN (default codex)
# Output  : installed agent TOMLs, merged config.toml, repo data/ skeleton,
#           hook mount, smoke-gate verdict. Exit 0 ok / 1 prerequisite or
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
# ① Prerequisites: Node 22+ and Codex CLI. Missing -> prompt, never install.
# ----------------------------------------------------------------------------
step "1/5 Prerequisite check (Node 22+, Codex CLI)"
MISSING=0

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
  "$PKG_ROOT/hooks/hooks.json.example" "$TARGET_REPO/.codex/hooks.json" <<'PYEOF'
import json, os, sys, tomllib
example, user_config, hook_template, user_hooks = sys.argv[1:]
with open(example, "rb") as handle:
    tomllib.load(handle)
if os.path.exists(user_config):
    with open(user_config, "rb") as handle:
        tomllib.load(handle)
json.load(open(hook_template, encoding="utf-8"))
if os.path.exists(user_hooks):
    json.load(open(user_hooks, encoding="utf-8"))
print("OK    config/hook inputs parse before user-state writes")
PYEOF

# ----------------------------------------------------------------------------
# ② Agent TOMLs -> $CODEX_HOME/agents/
# ----------------------------------------------------------------------------
step "2/5 Agent TOMLs -> $CODEX_HOME/agents/"
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
multiline_key_missing = False  # does the multiline body belong to a NEW key?
with open(example_path, encoding="utf-8") as f:
    for raw in f:
        line = raw.rstrip("\n")
        if in_multiline:
            # Continuation lines belong to the key that OPENED the multiline
            # value.  Appending them to the last missing key unconditionally
            # injected garbage under an unrelated key (or under one the user
            # already had).  Handle both TOML multiline quote styles.
            if multiline_key_missing and missing.get(section):
                missing[section][-1][1].append(line)
            if line.count('"""') >= 1 or line.count("'''") >= 1:
                in_multiline = False
            continue
        m = sec_re.match(line)
        if m:
            section = m.group(1)
            continue
        m = key_re.match(line)
        if m and not line.lstrip().startswith("#"):
            key = m.group(1)
            multiline_key_missing = not has_key(user, section, key)
            if multiline_key_missing:
                missing.setdefault(section, []).append([key, [line]])
            if line.count('"""') == 1 or line.count("'''") == 1:
                in_multiline = True

with open(user_path, encoding="utf-8") as f:
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
        appended += ["", f"# merged from codex-loop-s-f2 config.toml.example", header] + lines

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
tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=os.path.dirname(user_path))
tmp.write(rendered)
tmp.close()
os.replace(tmp.name, user_path)
PYEOF
fi

# ----------------------------------------------------------------------------
# ④ data/ skeleton + hook mounting in the target git repo
# ----------------------------------------------------------------------------
step "4/5 data/ skeleton + hook mount in target repo: $TARGET_REPO"
if ! git -C "$TARGET_REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  say "WARN  $TARGET_REPO is not a git repo — worktree pool / serial merge need git. Skeleton created anyway."
fi
DATA="$TARGET_REPO/data"
mkdir -p "$DATA/packets" "$DATA/reports" "$DATA/dead_letters"
touch "$DATA/events.ndjson" "$DATA/escalation_log.jsonl" "$DATA/lessons.jsonl" "$DATA/.merge.lock"
[[ -f "$DATA/progress_ledger.json" ]] || printf '{"packets": {}, "waves": []}\n' > "$DATA/progress_ledger.json"
say "OK    data/ skeleton at $DATA (packets/ reports/ dead_letters/ + 4 truth files)"

HOOK_DIR="$TARGET_REPO/hooks"
HOOK_JSON="$TARGET_REPO/.codex/hooks.json"
if command -v cygpath >/dev/null 2>&1; then
  HOOK_DIR_WINDOWS="$(cygpath -w "$HOOK_DIR")"
elif command -v wslpath >/dev/null 2>&1; then
  HOOK_DIR_WINDOWS="$(wslpath -w "$HOOK_DIR")"
else
  HOOK_DIR_WINDOWS="$HOOK_DIR"
fi
HOOK_DIR_WINDOWS_JSON="${HOOK_DIR_WINDOWS//\\/\\\\}"
mkdir -p "$(dirname "$HOOK_JSON")"
python3 - "$PKG_ROOT/hooks/hooks.json.example" "$HOOK_JSON" \
  "$HOOK_DIR" "$HOOK_DIR_WINDOWS" <<'PYEOF'
import json, os, shutil, sys, tempfile, time
template_path, target_path, hook_dir, hook_dir_windows = sys.argv[1:]
template = open(template_path, encoding="utf-8").read()
template = template.replace("<HOOK_DIR>", hook_dir.replace("\\", "/"))
template = template.replace("<HOOK_DIR_WINDOWS>", hook_dir_windows.replace("\\", "\\\\"))
desired = json.loads(template)["hooks"]
try:
    current = json.load(open(target_path, encoding="utf-8"))
except FileNotFoundError:
    current = {"hooks": {}}
hooks = current.setdefault("hooks", {})
# Remove every historical LOOP-managed hook before adding the canonical set.
# This deliberately includes the old Windows-only metering helpers: otherwise
# a Windows-generated hooks.json copied into WSL keeps PowerShell/E:\ paths and
# runs alongside the package-local lifecycle hook.
managed = ("subagent_lifecycle.py", "leaf_agent_spawn_gate.py", "sol_tool_gate.py",
           "sol_tool_gate_router.py", "subagent_start_meter",
           "reconcile_subagent_metering")
for event, entries in desired.items():
    kept = []
    for entry in hooks.get(event, []):
        commands = " ".join(str(h.get("command", "")) + " " +
                            str(h.get("commandWindows", ""))
                            for h in entry.get("hooks", []))
        if not any(name in commands for name in managed):
            kept.append(entry)
    hooks[event] = kept + entries
rendered = json.dumps(current, ensure_ascii=False, indent=2) + "\n"
if os.path.exists(target_path) and open(target_path, encoding="utf-8").read() == rendered:
    print("SKIP  hooks.json already contains canonical LOOP hooks")
    raise SystemExit(0)
if os.path.exists(target_path):
    backup = target_path + ".bak." + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "." + str(time.time_ns())
    shutil.copy2(target_path, backup)
    print("BACKUP hooks.json -> %s" % backup)
fd, tmp = tempfile.mkstemp(prefix="hooks.json.", dir=os.path.dirname(target_path))
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    handle.write(rendered)
os.replace(tmp, target_path)
print("MERGED canonical router + lifecycle hooks -> %s" % target_path)
PYEOF
say "NOTE  Hooks require trust: run /hooks in the Codex TUI once, or pass"
say "      --dangerously-bypass-hook-trust for non-interactive codex exec runs."

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
