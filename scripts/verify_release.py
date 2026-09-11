#!/usr/bin/env python3
"""Safely inspect, extract, and verify Codex LOOP Orchestra release archives."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

FORBIDDEN = {".git", ".codex", ".opencodex", "data", "reports", "state",
             "sessions", "logs", "backups", "credentials", "secrets"}


def validate_name(name: str, expected_root: str) -> PurePosixPath:
    if "\\" in name:
        raise ValueError(f"backslash archive path: {name}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe archive path: {name}")
    if not path.parts or path.parts[0] != expected_root:
        raise ValueError(f"unexpected archive root: {name}")
    if FORBIDDEN.intersection(path.parts):
        raise ValueError(f"forbidden release path: {name}")
    return path


def safe_destination(base: Path, path: PurePosixPath) -> Path:
    destination = (base / Path(*path.parts)).resolve()
    if not destination.is_relative_to(base.resolve()):
        raise ValueError(f"path escapes extraction root: {path}")
    return destination


def extract_zip(archive_path: Path, destination: Path, expected_root: str) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        seen: set[str] = set()
        for item in archive.infolist():
            path = validate_name(item.filename, expected_root)
            if item.filename in seen:
                raise ValueError(f"duplicate archive path: {item.filename}")
            seen.add(item.filename)
            target = safe_destination(destination, path)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def extract_tar(archive_path: Path, destination: Path, expected_root: str) -> None:
    with tarfile.open(archive_path) as archive:
        seen: set[str] = set()
        for item in archive.getmembers():
            path = validate_name(item.name, expected_root)
            if item.name in seen:
                raise ValueError(f"duplicate archive path: {item.name}")
            seen.add(item.name)
            if not (item.isfile() or item.isdir()):
                raise ValueError(f"non-regular archive entry: {item.name}")
            target = safe_destination(destination, path)
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            source = archive.extractfile(item)
            if source is None:
                raise ValueError(f"unreadable archive entry: {item.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            value.update(chunk)
    return value.hexdigest()


def verify_tree(root: Path) -> None:
    for required in ("README.md", "README.zh-CN.md", "LICENSE", "AGENT_INSTALL.md",
                     "install.sh", "uninstall.sh", "SHA256SUMS", "FILELIST.txt"):
        if not (root / required).is_file():
            raise ValueError(f"required release file missing: {required}")
    rows: dict[str, str] = {}
    for line in (root / "SHA256SUMS").read_text(encoding="utf-8-sig").splitlines():
        expected, rel = line.split("  ", 1)
        validate_name(f"release/{rel}", "release")
        if rel in rows:
            raise ValueError(f"duplicate checksum row: {rel}")
        rows[rel] = expected
    listed = {
        line.strip() for line in (root / "FILELIST.txt").read_text(
            encoding="utf-8-sig").splitlines() if line.strip()
    }
    if set(rows) != listed - {"SHA256SUMS"}:
        raise ValueError("FILELIST and SHA256SUMS boundaries differ")
    for rel, expected in rows.items():
        path = root / rel
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"checksum verification failed: {rel}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--expected-root", required=True)
    args = parser.parse_args()
    for archive in args.archives:
        with tempfile.TemporaryDirectory(prefix="codex-loop-release-") as temporary:
            destination = Path(temporary)
            if archive.name.endswith(".zip"):
                extract_zip(archive, destination, args.expected_root)
            elif archive.name.endswith(".tar.gz"):
                extract_tar(archive, destination, args.expected_root)
            else:
                raise SystemExit(f"unsupported archive: {archive}")
            verify_tree(destination / args.expected_root)
        print(f"verified: {archive}")


if __name__ == "__main__":
    main()
