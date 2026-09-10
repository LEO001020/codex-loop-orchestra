#!/usr/bin/env python3
"""Recover canonical ledger entries polluted by transport-only events.

This one-shot repair is deliberately narrow and recoverable.  It backs up the
entire ledger, removes only ``adhoc-*`` entries whose sole history row is an
``off_table_event`` and which have no canonical packet file, then records a
manifest containing every removed row and related artifact path.  Artifact
files are retained in place; nothing is deleted.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

try:
    from orchestration_common import atomic_write_json, file_lock
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from orchestration_common import atomic_write_json, file_lock


def qualifies(root: Path, packet_id: str, item: Any) -> bool:
    transport_id = (packet_id.startswith("adhoc-")
                    or packet_id == "lifecycle-gate-probe")
    if not transport_id or not isinstance(item, dict):
        return False
    if item.get("state") != "DEAD_LETTER":
        return False
    history = item.get("history")
    if not isinstance(history, list) or not history:
        return False
    if any(not isinstance(row, dict) or row.get("via") != "off_table_event"
           for row in history):
        return False
    return not (root / "data" / "packets" / f"{packet_id}.json").exists()


def run(root: Path, *, apply: bool) -> dict[str, Any]:
    root = root.resolve()
    ledger_path = root / "data" / "progress_ledger.json"
    with file_lock(root / "data" / "progress_ledger.lock"):
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        packets = ledger.get("packets")
        if not isinstance(packets, dict):
            raise RuntimeError("canonical ledger has no packets object")
        selected = {pid: item for pid, item in packets.items()
                    if qualifies(root, pid, item)}
        artifacts: dict[str, list[str]] = {}
        for pid in selected:
            paths = []
            dead = root / "data" / "dead_letters" / f"{pid}.json"
            if dead.exists():
                paths.append(str(dead))
            wake = root / "data" / "sol_wake"
            if wake.exists():
                paths.extend(str(path) for path in wake.glob(f"*_{pid}.md"))
            artifacts[pid] = sorted(paths)
        result: dict[str, Any] = {
            "schema": "codex-loop-transport-deadletter-repair/v1",
            "ts": time.time(), "root": str(root), "apply": apply,
            "selected_count": len(selected), "selected": selected,
            "retained_artifacts": artifacts,
        }
        if not apply:
            return result
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = root / "data" / "repairs" / f"transport-deadletters-{stamp}"
        backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(ledger_path, backup / "progress_ledger.before.json")
        for pid in selected:
            del packets[pid]
        atomic_write_json(ledger_path, ledger)
        result["backup_dir"] = str(backup)
        result["remaining_packets"] = len(packets)
        atomic_write_json(backup / "repair_manifest.json", result)
        shutil.copy2(ledger_path, backup / "progress_ledger.after.json")
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.root, apply=args.apply), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
