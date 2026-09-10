#!/usr/bin/env python3
"""Atomic routing-mode switch and reversible rollback rehearsal."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(os.environ.get("LOOP_ROOT", Path(__file__).resolve().parents[1])).resolve()
MODE_RE = re.compile(r'(?m)^(mode\s*=\s*")(cold_start|shadow|layered)("\s*(?:#.*)?)$')


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def replace_mode(text: str, mode: str) -> str:
    updated, count = MODE_RE.subn(r"\g<1>%s\g<3>" % mode, text, count=1)
    if count != 1:
        raise ValueError("routing.mode line not uniquely replaceable")
    return updated


def atomic_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def current(path: Path) -> str:
    match = MODE_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        raise ValueError("routing.mode not found")
    return match.group(2)


def set_mode(path: Path, mode: str) -> None:
    atomic_text(path, replace_mode(path.read_text(encoding="utf-8"), mode))


def layered_authorized(path: Path, now: float | None = None) -> bool:
    """Authorize only the exact prospective layered policy bytes."""
    root = path.resolve().parents[1]
    marker_path = root / "data" / "governor" / "layered_authorization.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        age = (time.time() if now is None else now) - float(marker["ts"])
        candidate = replace_mode(path.read_text(encoding="utf-8"), "layered")
        candidate_hash = digest(candidate)
        conditions = marker["conditions"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return (marker.get("status") == "PASS"
            and marker.get("authorized_mode") == "layered"
            and 0 <= age <= 3600
            and marker.get("policy_sha256") == candidate_hash
            and isinstance(conditions, list) and bool(conditions)
            and all(isinstance(item, dict) and bool(item.get("ok"))
                    for item in conditions))


def rehearse(root: Path, path: Path) -> dict:
    original = path.read_text(encoding="utf-8")
    original_mode = current(path)
    before_hash = digest(original)
    try:
        atomic_text(path, replace_mode(original, "cold_start"))
        if current(path) != "cold_start":
            raise RuntimeError("cold_start switch did not persist")
    finally:
        atomic_text(path, original)
    restored = path.read_text(encoding="utf-8")
    if restored != original or current(path) != original_mode:
        raise RuntimeError("rollback rehearsal did not restore exact policy bytes")
    marker = {"schema": "codex-loop-rollback-rehearsal/v2", "status": "PASS",
              "ts": time.time(), "original_mode": original_mode,
              "policy_sha256": before_hash, "restored_sha256": digest(restored)}
    atomic_json(root / "data" / "governor" / "rollback_rehearsal.json", marker)
    return marker


def main() -> int:
    ap = argparse.ArgumentParser(description="atomic LOOP routing-mode control")
    ap.add_argument("command", choices=["get", "set", "rehearse"])
    ap.add_argument("mode", nargs="?", choices=["cold_start", "shadow", "layered"])
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args()
    root = args.root.resolve()
    path = root / "config" / "orchestration_policy_v2.toml"
    if args.command == "get":
        print(current(path))
        return 0
    if args.command == "set":
        if not args.mode:
            ap.error("set requires a mode")
        if args.mode == "layered" and not layered_authorized(path):
            raise ValueError("layered authorization missing, stale, or not "
                             "bound to the prospective policy; run "
                             "harness/layered_gate.py enable first")
        set_mode(path, args.mode)
        print(args.mode)
        return 0
    print(json.dumps(rehearse(root, path), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
