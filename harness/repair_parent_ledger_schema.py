#!/usr/bin/env python3
"""One-shot, evidence-preserving repair for imported parent ledger entries."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from orchestration_common import LoopPaths, atomic_write_json, file_lock, read_json


def repair(root: Path, manifest_id: str) -> dict[str, object]:
    paths = LoopPaths.resolve(root)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    evidence = paths.data / "repairs" / ("parent-ledger-schema-" + stamp)
    evidence.mkdir(parents=True, exist_ok=False)
    with file_lock(paths.data / "progress_ledger.lock"):
        before_bytes = paths.ledger.read_bytes()
        ledger = json.loads(before_bytes.decode("utf-8"))
        roster = read_json(paths.data / "lifecycle" / "exec_roster.json",
                           {"jobs": {}}) or {"jobs": {}}
        jobs = roster.get("jobs") if isinstance(roster, dict) else {}
        jobs = jobs if isinstance(jobs, dict) else {}
        selected: list[str] = []
        promoted: list[str] = []
        for packet_id, entry in (ledger.get("packets") or {}).items():
            if entry.get("manifest_id") != manifest_id:
                continue
            selected.append(packet_id)
            entry.setdefault("history", [])
            entry.setdefault("attempts", 0)
            job = jobs.get(packet_id)
            if not isinstance(job, dict) or job.get("state") not in {
                    "starting", "running"} or not job.get("run_id"):
                continue
            entry["state"] = "RUNNING"
            entry["attempts"] = int(job.get("attempt", entry["attempts"]) or 0)
            entry["current_run_id"] = str(job["run_id"])
            entry["history"].append({
                "ts": time.time(), "to": "RUNNING",
                "via": "repair_live_roster_generation",
                "run_id": entry["current_run_id"],
                "attempt": entry["attempts"],
            })
            promoted.append(packet_id)
        if not selected:
            raise RuntimeError("manifest has no ledger entries: " + manifest_id)
        (evidence / "progress_ledger.before.json").write_bytes(before_bytes)
        atomic_write_json(paths.ledger, ledger)
        (evidence / "progress_ledger.after.json").write_bytes(
            paths.ledger.read_bytes())
        manifest = {
            "schema": "codex-loop-parent-ledger-repair/v1",
            "ts": time.time(), "pid": os.getpid(),
            "manifest_id": manifest_id, "selected": selected,
            "promoted_from_live_roster": promoted,
            "ledger": str(paths.ledger), "evidence_dir": str(evidence),
        }
        atomic_write_json(evidence / "repair_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest-id", required=True)
    args = parser.parse_args()
    print(json.dumps(repair(args.root, args.manifest_id),
                     ensure_ascii=False, indent=2))
