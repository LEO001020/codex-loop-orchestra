#!/usr/bin/env python3
"""Wrap successful legacy plain-text parent reports and repair their ledger state."""
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
    evidence = paths.data / "repairs" / ("plain-parent-reports-" + stamp)
    evidence.mkdir(parents=True, exist_ok=False)
    reports_before = evidence / "reports.before"
    reports_before.mkdir()
    with file_lock(paths.data / "orchestration" / ".terminal_packet.lock"):
        with file_lock(paths.data / "progress_ledger.lock"):
            before = paths.ledger.read_bytes()
            ledger = json.loads(before.decode("utf-8"))
            roster = read_json(paths.data / "lifecycle" / "exec_roster.json",
                               {"jobs": {}}) or {"jobs": {}}
            jobs = roster.get("jobs") if isinstance(roster, dict) else {}
            jobs = jobs if isinstance(jobs, dict) else {}
            repaired: list[str] = []
            for packet_id, entry in (ledger.get("packets") or {}).items():
                if entry.get("manifest_id") != manifest_id:
                    continue
                job = jobs.get(packet_id)
                if not isinstance(job, dict) or job.get("state") != "completed" \
                        or int(job.get("exit_code", 1) or 0) != 0:
                    continue
                report = paths.data / "reports" / packet_id / "report.json"
                if not report.is_file():
                    continue
                raw = report.read_bytes()
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    value = None
                if isinstance(value, dict) and value.get("packet_id") == packet_id:
                    continue
                (reports_before / (packet_id + ".bin")).write_bytes(raw)
                wrapped = {"packet_id": packet_id, "status": "done",
                           "run_id": job.get("run_id"),
                           "summary": raw.decode("utf-8", errors="replace")}
                atomic_write_json(report, wrapped)
                entry.setdefault("history", [])
                entry.setdefault("attempts", int(job.get("attempt", 0) or 0))
                entry["state"] = "REPORTED"
                entry["current_run_id"] = str(job.get("run_id"))
                entry["history"].append({"ts": time.time(), "to": "REPORTED",
                                         "via": "repair_plain_parent_report",
                                         "run_id": job.get("run_id")})
                repaired.append(packet_id)
            (evidence / "progress_ledger.before.json").write_bytes(before)
            atomic_write_json(paths.ledger, ledger)
            (evidence / "progress_ledger.after.json").write_bytes(
                paths.ledger.read_bytes())
    manifest = {"schema": "codex-loop-plain-parent-repair/v1",
                "ts": time.time(), "pid": os.getpid(),
                "manifest_id": manifest_id, "repaired": repaired,
                "evidence_dir": str(evidence)}
    atomic_write_json(evidence / "repair_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--manifest-id", required=True)
    ns = ap.parse_args()
    print(json.dumps(repair(ns.root, ns.manifest_id), ensure_ascii=False, indent=2))
