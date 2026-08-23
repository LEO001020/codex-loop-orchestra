#!/usr/bin/env python3
"""Generate FILELIST.txt from the canonical managed-file manifest."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--manifest", default="config/managed_files_v2.txt")
    parser.add_argument("--output", default="FILELIST.txt")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    manifest = root / args.manifest
    entries: list[str] = []
    for raw in manifest.read_text(encoding="utf-8-sig").splitlines():
        rel = raw.strip()
        if not rel or rel.startswith("#"):
            continue
        path = PurePosixPath(rel.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe manifest path: {rel}")
        entries.append(path.as_posix())
    if len(entries) != len(set(entries)):
        raise SystemExit("managed manifest contains duplicate paths")
    output = root / args.output
    if output.relative_to(root).as_posix() not in entries:
        raise SystemExit(f"output must be listed in managed manifest: {args.output}")
    generated = {args.output, "SHA256SUMS"}
    missing = [rel for rel in entries if rel not in generated and not (root / rel).is_file()]
    if missing:
        raise SystemExit("managed files missing: " + ", ".join(missing))
    output.write_text("\n".join(entries) + "\n", encoding="utf-8", newline="\n")
    print(f"FILELIST written: {output} ({len(entries)} entries)")


if __name__ == "__main__":
    main()
