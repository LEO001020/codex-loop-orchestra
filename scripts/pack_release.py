#!/usr/bin/env python3
"""
pack_release.py -- Reproducible release packager
=================================================
Usage:
    python pack_release.py --source <SRC_DIR> --output <OUT_DIR> \
                           --allowlist <ALLOWLIST_FILE> --name <RELEASE_NAME>

Features
--------
* Allowlist filtering: only paths matching patterns in allowlist are included
* Reproducible timestamps: all file mtimes normalised to SOURCE_DATE_EPOCH
  (env var, default 2026-08-20T00:00:00Z = 1755648000)
* Deterministic ordering: entries sorted lexicographically inside archives
* SHA256SUMS generated for every produced archive
* ZIP and tar.gz produced in one pass
* Symlinks that escape the source root are silently skipped (no traversal)
* Never writes outside --output directory
"""

import argparse
import fnmatch
import gzip
import hashlib
import os
import re
import stat
import sys
import tarfile
import time
import zipfile
from pathlib import Path

DEFAULT_SOURCE_DATE_EPOCH = 1755648000  # 2026-08-20 00:00:00 UTC


def normalized_mode(path):
    """Return cross-platform archive permissions from content, not host mode."""
    with open(path, "rb") as handle:
        executable = handle.read(2) == b"#!"
    return 0o755 if executable else 0o644


def validate_release_name(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) or value in {".", ".."}:
        raise SystemExit("ERROR: --name must be a safe archive basename")
    return value


def load_allowlist(path):
    patterns = []
    if not Path(path).exists():
        return patterns
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def is_allowed(rel_path, patterns):
    if not patterns:
        return True
    rel_posix = rel_path.replace("\\", "/")
    for pat in patterns:
        if fnmatch.fnmatch(rel_posix, pat):
            return True
        for part in Path(rel_posix).parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def is_safe_symlink(link_path, source_root):
    try:
        target = link_path.resolve()
        return target.is_relative_to(source_root.resolve())
    except Exception:
        return False


def collect_files(source, allowlist):
    SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "data", "reports",
                 "logs", "state", "sessions", ".venv", "venv", "node_modules",
                 "dist", "build"}
    collected = []
    for root, dirs, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        dirs[:] = [
            d for d in sorted(dirs)
            if d not in SKIP_DIRS
            and not d.startswith("backup")
            and (not (root_path / d).is_symlink()
                 or is_safe_symlink(root_path / d, source))
        ]
        for fname in sorted(files):
            fpath = root_path / fname
            if fpath.is_symlink() and not is_safe_symlink(fpath, source):
                print(f"  [SKIP symlink-escapes-root] {fpath}", file=sys.stderr)
                continue
            rel = str(fpath.relative_to(source))
            if is_allowed(rel, allowlist):
                collected.append(fpath)
    return collected


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_zip(files, source, out_path, epoch, archive_root):
    zt = time.gmtime(epoch)[:6]
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fpath in files:
            rel = fpath.relative_to(source).as_posix()
            info = zipfile.ZipInfo(
                filename=f"{archive_root}/{rel}", date_time=zt,
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = normalized_mode(fpath) << 16
            with open(fpath, "rb") as fp:
                zf.writestr(info, fp.read())
    print(f"  ZIP  -> {out_path}")


def build_targz(files, source, out_path, epoch, archive_root):
    with open(out_path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tf:
                for fpath in files:
                    rel = fpath.relative_to(source).as_posix()
                    info = tf.gettarinfo(
                        str(fpath), arcname=f"{archive_root}/{rel}",
                    )
                    info.mtime = epoch
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mode = normalized_mode(fpath)
                    with open(fpath, "rb") as fp:
                        tf.addfile(info, fp)
    print(f"  TGZ  -> {out_path}")


def write_checksums(archives, out_dir):
    lines = []
    for a in archives:
        digest = sha256_file(a)
        lines.append(f"{digest}  {Path(a).name}")
    sums_path = Path(out_dir) / "SHA256SUMS"
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  SUMS -> {sums_path}")
    return sums_path


def main():
    parser = argparse.ArgumentParser(description="Reproducible release packager")
    parser.add_argument("--source",    required=True)
    parser.add_argument("--output",    required=True)
    parser.add_argument("--allowlist", default="allowlist.txt")
    parser.add_argument("--name",      default="release")
    parser.add_argument("--epoch",     type=int, default=None)
    args = parser.parse_args()

    source  = Path(args.source).resolve()
    out_dir = Path(args.output).resolve()

    if not source.is_dir():
        sys.exit(f"ERROR: source '{source}' is not a directory")

    epoch = (args.epoch if args.epoch is not None else
             int(os.environ.get("SOURCE_DATE_EPOCH", DEFAULT_SOURCE_DATE_EPOCH)))
    release_name = validate_release_name(args.name)
    print(f"SOURCE_DATE_EPOCH = {epoch}")

    out_dir.mkdir(parents=True, exist_ok=True)
    patterns = load_allowlist(args.allowlist)
    print(f"Allowlist patterns ({len(patterns)}): {patterns or '(none)'}")

    files = collect_files(source, patterns)
    manifest = source / "config" / "managed_files_v2.txt"
    if manifest.is_file():
        managed = {
            line.strip() for line in manifest.read_text(
                encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        collected = {path.relative_to(source).as_posix() for path in files}
        if collected != managed:
            missing = sorted(managed - collected)
            extra = sorted(collected - managed)
            sys.exit(
                "ERROR: archive boundary differs from managed manifest; "
                f"missing={missing}; extra={extra}"
            )
    print(f"Collected {len(files)} files")

    zip_path = out_dir / f"{release_name}.zip"
    tgz_path = out_dir / f"{release_name}.tar.gz"

    build_zip(files, source, zip_path, epoch, release_name)
    build_targz(files, source, tgz_path, epoch, release_name)
    sums_path = write_checksums([zip_path, tgz_path], out_dir)

    print("\nDone. Artifacts:")
    for p in [zip_path, tgz_path, sums_path]:
        print(f"  {p}")


if __name__ == "__main__":
    main()
