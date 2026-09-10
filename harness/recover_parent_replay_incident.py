#!/usr/bin/env python3
"""Recover one imported parent manifest from a historical-event replay."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import time
from pathlib import Path

from orchestration_common import LoopPaths, atomic_write_json, file_lock, read_json


@contextlib.contextmanager
def retry_lock(path: Path, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while True:
        manager = file_lock(path)
        try:
            manager.__enter__()
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    try:
        yield
    finally:
        manager.__exit__(None, None, None)


def valid_report(path: Path, packet_id: str) -> bool:
    value = read_json(path, None)
    return isinstance(value, dict) and value.get("packet_id") == packet_id


def recover(root: Path, manifest_id: str) -> dict[str, object]:
    paths = LoopPaths.resolve(root)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    evidence = paths.data / "repairs" / ("parent-replay-incident-" + stamp)
    evidence.mkdir(parents=True, exist_ok=False)
    quarantine = evidence / "quarantine"
    (quarantine / "dead_letters").mkdir(parents=True)
    (quarantine / "sol_wake").mkdir(parents=True)
    selected: list[str] = []
    states: dict[str, str] = {}
    # The outer lock excludes already-scheduled full epilogues.  The events
    # lock makes cursor=EOF a true cut: later terminal edges append after it.
    with retry_lock(paths.data / "orchestration" / ".epilogue.lock"):
        with retry_lock(paths.events_lock):
            with retry_lock(paths.data / "progress_ledger.lock"):
                before = paths.ledger.read_bytes()
                ledger = json.loads(before.decode("utf-8"))
                roster = read_json(paths.data / "lifecycle" / "exec_roster.json",
                                   {"jobs": {}}) or {"jobs": {}}
                jobs = roster.get("jobs") if isinstance(roster, dict) else {}
                jobs = jobs if isinstance(jobs, dict) else {}
                event_cursor = paths.events.stat().st_size if paths.events.exists() else 0
                for packet_id, entry in (ledger.get("packets") or {}).items():
                    if entry.get("manifest_id") != manifest_id:
                        continue
                    selected.append(packet_id)
                    entry.setdefault("history", [])
                    entry.setdefault("attempts", 0)
                    job = jobs.get(packet_id) if isinstance(jobs.get(packet_id), dict) else {}
                    run_id = job.get("run_id")
                    job_state = str(job.get("state") or "")
                    report = paths.data / "reports" / packet_id / "report.json"
                    if job_state in {"starting", "running"} and run_id:
                        state = "RUNNING"
                    elif job_state == "completed" and valid_report(report, packet_id):
                        state = "REPORTED"
                    elif job_state in {"spawn_failed"}:
                        state = "DISPATCHABLE"
                    else:
                        state = "FAILED"
                    entry["state"] = state
                    if run_id:
                        entry["current_run_id"] = str(run_id)
                    else:
                        entry.pop("current_run_id", None)
                    entry["attempts"] = int(job.get("attempt", entry["attempts"]) or 0)
                    entry["history"].append({
                        "ts": time.time(), "to": state,
                        "via": "recover_historical_replay_incident",
                        "run_id": run_id, "job_state": job_state,
                        "event_cursor": event_cursor,
                    })
                    states[packet_id] = state
                if not selected:
                    raise RuntimeError("manifest has no ledger entries: " + manifest_id)
                ledger["event_cursor"] = event_cursor
                (evidence / "progress_ledger.before.json").write_bytes(before)
                atomic_write_json(paths.ledger, ledger)
                (evidence / "progress_ledger.after.json").write_bytes(
                    paths.ledger.read_bytes())

    moved_dead: list[str] = []
    moved_wake: list[str] = []
    selected_set = set(selected)
    for path in (paths.data / "dead_letters").glob("parent-*.json"):
        value = read_json(path, None)
        if (path.stem in selected_set and isinstance(value, dict)
                and value.get("reason") == "off_table_event"
                and path.stat().st_mtime >= 1786567400):
            target = quarantine / "dead_letters" / path.name
            os.replace(path, target)
            moved_dead.append(path.name)
    for path in (paths.data / "sol_wake").glob("*.md"):
        if (path.stat().st_mtime >= 1786567400
                and any(packet_id in path.name for packet_id in selected_set)):
            target = quarantine / "sol_wake" / path.name
            os.replace(path, target)
            moved_wake.append(path.name)
    manifest = {
        "schema": "codex-loop-parent-replay-recovery/v1",
        "ts": time.time(), "pid": os.getpid(), "manifest_id": manifest_id,
        "selected": selected, "states": states,
        "quarantined_dead_letters": moved_dead,
        "quarantined_sol_wakes": moved_wake,
        "evidence_dir": str(evidence),
    }
    atomic_write_json(evidence / "repair_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest-id", required=True)
    args = parser.parse_args()
    print(json.dumps(recover(args.root, args.manifest_id),
                     ensure_ascii=False, indent=2))
