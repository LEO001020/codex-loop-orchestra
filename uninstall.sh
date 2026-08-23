#!/usr/bin/env bash
# Restore the Codex configuration captured before LOOP activation.
set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --repo) shift 2 ;; # accepted for compatibility; global mode is repo-independent
    -h|--help)
      echo "Usage: ./uninstall.sh [--dry-run]"
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ $DRY_RUN -eq 1 ]]; then
  echo "DRY-RUN would restore AGENTS.md, hooks.json, and requirements.toml from the verified activation backup."
  echo "DRY-RUN would remove unchanged LOOP agent TOMLs or restore their newest install-time backup."
  exit 0
fi

python3 "$PKG_ROOT/harness/global_desktop_mode.py" restore \
  --root "$PKG_ROOT" --codex-home "$CODEX_HOME"

for source in "$PKG_ROOT"/agents/*.toml; do
  name="$(basename "$source")"
  target="$CODEX_HOME/agents/$name"
  [[ -f "$target" ]] || continue
  if ! cmp -s "$source" "$target"; then
    echo "KEEP  $target (modified after installation)"
    continue
  fi
  backup="$(find "$CODEX_HOME/agents" -maxdepth 1 -type f -name "$name.bak.*" -print 2>/dev/null | sort | tail -1 || true)"
  if [[ -n "$backup" ]]; then
    cp -f "$backup" "$target"
    echo "RESTORED $target from $backup"
  else
    rm -f -- "$target"
    echo "REMOVED unchanged LOOP agent $target"
  fi
done

echo "Uninstall complete. Existing config.toml keys are intentionally preserved."
echo "Runtime data remains under $PKG_ROOT/data and may be removed manually after review."
