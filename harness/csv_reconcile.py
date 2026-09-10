#!/usr/bin/env python3
"""Bridge spawn_agents_on_csv results into generation-aware LOOP events."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from lifecycle_supervisor import Store, publish_report


PACKET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
SUCCESS = {"done", "completed", "ok", "success", "passed"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_stamp(path: Path, batch_csv: Path, results_csv: Path,
                summary: dict[str, int]) -> None:
    result_stat = results_csv.stat()
    doc = {
        "schema": "codex-loop-csv-reconcile-stamp/v1",
        "batch_csv": str(batch_csv.resolve()),
        "batch_sha256": sha256(batch_csv),
        "results_csv": str(results_csv.resolve()),
        "results_sha256": sha256(results_csv),
        "results_mtime_ns": result_stat.st_mtime_ns,
        "results_size": result_stat.st_size,
        "summary": summary,
        "exit": 1 if summary["failed"] else 0,
        "ran_at": time.time(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-%d" % os.getpid())
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(doc, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError as exc:
        raise RuntimeError("CSV unreadable: %s: %s" % (path, exc)) from exc


def run(batch_csv: Path, results_csv: Path, data_dir: Path) -> dict[str, int]:
    batch = rows(batch_csv)
    results = rows(results_csv)
    try:
        ledger = json.loads((data_dir / "progress_ledger.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {"packets": {}}
    by_id: dict[str, dict[str, str]] = {}
    for row in results:
        pid = row.get("packet_id") or row.get("id") or ""
        if pid in by_id:
            raise RuntimeError("duplicate CSV result packet_id: %s" % pid)
        by_id[pid] = row
    expected_ids = {row.get("packet_id") or "" for row in batch}
    extras = sorted(set(by_id) - expected_ids)
    if extras:
        raise RuntimeError("results contain packet ids absent from batch: %s" % extras)
    store = Store(data_dir.resolve())
    summary = {"completed": 0, "failed": 0}
    for expected in batch:
        pid = expected.get("packet_id") or ""
        if not PACKET_ID_RE.fullmatch(pid):
            raise RuntimeError("invalid packet id in batch: %r" % pid)
        run_id = expected.get("run_id") or ""
        try:
            attempt = int(expected.get("attempt", "0"))
        except ValueError as exc:
            raise RuntimeError("invalid attempt for %s" % pid) from exc
        current_attempt = int(ledger.get("packets", {}).get(pid, {}).get("attempts", attempt) or 0)
        if attempt < current_attempt:
            continue
        result = by_id.get(pid)
        status = str((result or {}).get("status") or "").strip().lower()
        if result is not None and status in SUCCESS:
            source = Path(expected.get("local_report") or "")
            worktree = Path(expected.get("worktree") or "")
            destination = data_dir / "reports" / pid / "report.json"
            if not source.is_file():
                status = "missing_report"
            else:
                try:
                    report = json.loads(source.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise RuntimeError("report unreadable for %s: %s" % (pid, exc)) from exc
                if report.get("packet_id") not in (None, pid):
                    raise RuntimeError("report packet_id mismatch for %s" % pid)
                publish_report(source, destination, worktree, data_dir)
                store.append_event_once(pid, run_id, attempt, "subagent_stop",
                    {"source": "csv_reconcile", "status": status})
                summary["completed"] += 1
                continue
        why = "csv_result_missing" if result is None else (
              "missing_report" if status == "missing_report" else "csv_worker_failed")
        store.append_event_once(pid, run_id, attempt, "exec_failed",
            {"source": "csv_reconcile", "why": why,
             "status": status, "summary": str((result or {}).get("summary") or "")[:500]})
        summary["failed"] += 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile spawn_agents_on_csv output")
    parser.add_argument("--batch-csv", type=Path, required=True)
    parser.add_argument("--results-csv", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--stamp", type=Path,
                        help="write an atomic result-fingerprint stamp after complete reconciliation")
    args = parser.parse_args()
    summary = run(args.batch_csv, args.results_csv, args.data_dir)
    if args.stamp:
        write_stamp(args.stamp, args.batch_csv, args.results_csv, summary)
        summary["stamp"] = str(args.stamp.resolve())
    try:
        from orchestration_epilogue import run_epilogue
        run_epilogue(args.data_dir.parent, source="csv_reconcile")
    except Exception as exc:
        summary["epilogue_error"] = "%s: %s" % (type(exc).__name__, exc)
    print(json.dumps(summary, sort_keys=True))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
