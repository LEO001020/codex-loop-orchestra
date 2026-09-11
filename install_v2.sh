#!/usr/bin/env bash
# Manifest-driven Codex LOOP v2 deployer.  This is the only package-to-package
# copy entry; install.sh remains the single CODEX_HOME/project-hook merger.
set -euo pipefail

SRC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${LOOP_ROOT:-$SRC_ROOT}"
DRY_RUN=0
SKIP_USER_CONFIG=0
SKIP_SMOKE=0

usage() {
  cat <<'EOF'
usage: ./install_v2.sh [--target PATH] [--dry-run] [--skip-user-config]
                       [--with-smoke | --skip-smoke]

Deploy every file in config/managed_files_v2.txt with per-file backups, then
delegate user configuration and project-hook merging to install.sh.  Dry-run
validates the complete manifest and prints changes without writing anything.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-user-config) SKIP_USER_CONFIG=1; shift ;;
    --with-smoke) SKIP_SMOKE=0; shift ;;
    --skip-smoke) SKIP_SMOKE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

PY="$(command -v python3 || command -v python || true)"
[[ -n "$PY" ]] || { echo 'python >=3.11 is required' >&2; exit 1; }
"$PY" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("python >=3.11 required, found " + sys.version.split()[0])
PY

TARGET="$("$PY" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$TARGET")"
MANIFEST="$SRC_ROOT/config/managed_files_v2.txt"
[[ -f "$MANIFEST" ]] || { echo "managed manifest missing: $MANIFEST" >&2; exit 1; }

mapfile -t FILES < <(sed -e 's/\r$//' -e '/^[[:space:]]*$/d' -e '/^[[:space:]]*#/d' "$MANIFEST")
[[ ${#FILES[@]} -gt 0 ]] || { echo 'managed manifest is empty' >&2; exit 1; }

missing=0
for rel in "${FILES[@]}"; do
  if [[ "$rel" = /* || "$rel" == *".."* ]]; then
    echo "unsafe managed path: $rel" >&2; missing=1
  elif [[ ! -f "$SRC_ROOT/$rel" ]]; then
    echo "managed source missing: $rel" >&2; missing=1
  fi
done
[[ $missing -eq 0 ]] || exit 1

# PID makes two same-second installer processes use distinct recovery roots.
# (The script is single-process, so one run has one coherent backup set.)
stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
backup="$TARGET/backup-v2-$stamp"
changed=0
journal="$TARGET/.install-v2-journal-$stamp"
rollback_active=0
install_lock="$TARGET/.install-v2.lock"
install_lock_dir="$TARGET/.install-v2.lock.d"
lock_owned=0

cleanup_tmp() {
  find "$TARGET" -type f -name "*.tmp.$$" -delete 2>/dev/null || true
}

rollback_package() {
  local rc="${1:-$?}"
  cleanup_tmp
  if [[ $rollback_active -eq 1 && -f "$journal" ]]; then
    while IFS=$'\t' read -r existed rel; do
      [[ -n "$rel" ]] || continue
      if [[ "$existed" == 1 ]]; then
        mkdir -p "$TARGET/$(dirname "$rel")"
        cp -p "$backup/$rel" "$TARGET/$rel"
      else
        rm -f -- "$TARGET/$rel"
      fi
    done < "$journal"
    echo "install_v2.sh: package copy rolled back after failure" >&2
  fi
  rm -f -- "$journal"
  if [[ $lock_owned -eq 1 ]]; then
    rm -f -- "$install_lock"
    rmdir "$install_lock_dir" 2>/dev/null || true
    lock_owned=0
  fi
  exit "$rc"
}

release_install_lock() {
  if [[ $lock_owned -eq 1 ]]; then
    rmdir "$install_lock_dir" 2>/dev/null || true
    lock_owned=0
  fi
}
printf 'source : %s\ntarget : %s\nfiles  : %d\n' "$SRC_ROOT" "$TARGET" "${#FILES[@]}"
[[ $DRY_RUN -eq 1 ]] && echo 'mode   : DRY RUN (zero writes)'

if [[ $DRY_RUN -eq 0 ]]; then
  mkdir -p "$TARGET"
  # mkdir is the portable cross-process O_EXCL boundary on Windows-mounted
  # WSL paths and native POSIX filesystems.  Never wait behind another writer:
  # a second installer fails visibly instead of interleaving backups/rollback.
  if ! mkdir "$install_lock_dir" 2>/dev/null; then
    echo "another install_v2 writer owns target lock: $install_lock_dir" >&2
    exit 75
  fi
  lock_owned=1
  printf '%s\n' "pid=$$ started=$stamp" > "$install_lock"
  : > "$journal"
  rollback_active=1
  trap 'rollback_package $?' ERR
  trap 'rollback_package 130' INT
  trap 'rollback_package 143' TERM
  trap 'rollback_package 129' HUP
fi

for rel in "${FILES[@]}"; do
  src="$SRC_ROOT/$rel"; dst="$TARGET/$rel"
  if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
    continue
  fi
  changed=$((changed + 1))
  if [[ $DRY_RUN -eq 1 ]]; then
    [[ -f "$dst" ]] && echo "would backup+replace: $rel" || echo "would install: $rel"
    continue
  fi
  if [[ -f "$dst" ]]; then
    mkdir -p "$backup/$(dirname "$rel")"
    cp -p "$dst" "$backup/$rel"
    printf '1\t%s\n' "$rel" >> "$journal"
  else
    printf '0\t%s\n' "$rel" >> "$journal"
  fi
  mkdir -p "$(dirname "$dst")"
  tmp="$dst.tmp.$$"
  cp -p "$src" "$tmp"
  mv -f "$tmp" "$dst"
done

echo "changed: $changed"
if [[ $DRY_RUN -eq 1 ]]; then
  echo 'DRY RUN complete; no files or configuration were written.'
  exit 0
fi
[[ -d "$backup" ]] && echo "backup: $backup"

"$PY" - "$TARGET" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
for sub in ("harness", "metering", "hooks"):
    sys.path.insert(0, str(root / sub))
from orchestration_common import LoopPaths, OrchestrationPolicy, RefillPolicy
paths = LoopPaths.resolve(root)
policy = OrchestrationPolicy.load(paths)
refill = RefillPolicy.load(paths)
if policy.routing_mode() not in {"cold_start", "shadow", "layered"}:
    raise SystemExit("invalid routing mode: " + policy.routing_mode())
targets = (refill.target_total(), refill.v4_target(), refill.k3_target())
if targets[0] <= 0 or targets[1] < 0 or targets[2] < 0 \
        or targets[1] + targets[2] != targets[0]:
    raise SystemExit("invalid refill target relationship: %r" % (targets,))
import refill_consumer_v2, parent_manifest_importer  # noqa: F401
import headless_wave, lifecycle_supervisor  # noqa: F401
print("v2 validation OK: mode=%s target=%d/%d/%d" %
      ((policy.routing_mode(),) + targets))
PY

# Package bytes and imports are coherent.  User config/hook installation is a
# separate transaction with its own per-file backups and atomic replacements.
rollback_active=0
trap - ERR INT TERM HUP
rm -f -- "$journal"
cleanup_tmp
rm -f -- "$install_lock"
release_install_lock

if [[ $SKIP_USER_CONFIG -eq 0 ]]; then
  args=(--repo "$TARGET")
  [[ $SKIP_SMOKE -eq 1 ]] && args+=(--skip-smoke)
  "$TARGET/install.sh" "${args[@]}"
fi

echo 'install_v2.sh: SUCCESS'
