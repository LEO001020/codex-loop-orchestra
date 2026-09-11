#!/usr/bin/env python3
"""Generate SHA256SUMS for exactly the managed public-release boundary."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(root: Path, manifest: Path) -> list[str]:
    path = manifest if manifest.is_absolute() else root / manifest
    entries: list[str] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        rel = raw.strip()
        if not rel or rel.startswith("#"):
            continue
        pure = PurePosixPath(rel.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts:
            raise SystemExit(f"unsafe manifest path: {rel}")
        normalized = pure.as_posix()
        if normalized in entries:
            raise SystemExit(f"duplicate manifest path: {normalized}")
        entries.append(normalized)
    return entries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--output", default="SHA256SUMS")
    parser.add_argument("--manifest", default="config/managed_files_v2.txt")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output_rel = PurePosixPath(args.output.replace("\\", "/")).as_posix()
    entries = load_manifest(root, Path(args.manifest))
    if output_rel not in entries:
        raise SystemExit(f"output must be listed in managed manifest: {output_rel}")

    rows: list[str] = []
    for rel in entries:
        if rel == output_rel:
            continue
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"managed file missing: {rel}")
        rows.append(f"{sha256_file(path)}  {rel}")

    output = root / output_rel
    output.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    print(f"SHA256SUMS written: {output} ({len(rows)} entries)")


if __name__ == "__main__":
    main()
