#!/usr/bin/env python3
"""Compare the managed Windows/WSL overlay and attest exact agreement."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def compare(windows_root: Path, wsl_root: Path) -> dict:
    manifest_path = windows_root / "config" / "managed_files_v2.txt"
    files = [line.strip() for line in manifest_path.read_text(
        encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    windows_lines: list[str] = []
    wsl_lines: list[str] = []
    mismatches: list[str] = []
    for rel in files:
        win = windows_root / rel
        wsl = wsl_root / rel
        if not win.is_file() or not wsl.is_file():
            mismatches.append(rel + ":missing")
            continue
        win_hash, wsl_hash = sha(win), sha(wsl)
        windows_lines.append(rel + "=" + win_hash)
        wsl_lines.append(rel + "=" + wsl_hash)
        if win_hash != wsl_hash:
            mismatches.append(rel)
    win_manifest = hashlib.sha256("\n".join(windows_lines).encode()).hexdigest()
    wsl_manifest = hashlib.sha256("\n".join(wsl_lines).encode()).hexdigest()
    policy_hash = sha(windows_root / "config" / "orchestration_policy_v2.toml")
    result = {
        "schema": "codex-loop-dual-plane-hash/v2", "ts": time.time(),
        "status": "PASS" if not mismatches and win_manifest == wsl_manifest else "FAIL",
        "managed_files": len(files),
        "windows_manifest_sha256": win_manifest,
        "wsl_manifest_sha256": wsl_manifest,
        "policy_sha256": policy_hash,
        "mismatches": mismatches,
    }
    for root in (windows_root, wsl_root):
        atomic_json(root / "data" / "governor" / "dual_plane_hash.json", result)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="attest Windows/WSL managed overlay")
    ap.add_argument("--windows-root", type=Path, required=True)
    ap.add_argument("--wsl-root", type=Path, required=True)
    args = ap.parse_args()
    result = compare(args.windows_root.resolve(), args.wsl_root.resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
