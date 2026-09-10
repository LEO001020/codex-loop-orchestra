#!/usr/bin/env python3
"""Plan or safely apply stale Desktop spawn-edge reconciliation.

Default operation is strictly read-only.  ``--apply`` is an explicit offline
maintenance action: it refuses while Codex Desktop processes are present,
backs up the WAL database through SQLite's backup API, verifies the expected
schema, and performs only the idempotent transition ``open -> closed`` for
children whose shadow roster has strong terminal evidence.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Callable


EXPECTED_EDGE_COLUMNS = {"parent_thread_id", "child_thread_id", "status"}
STRONG_TERMINAL = {"terminal"}


class ReconcileError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def state_digest(path: Path) -> str:
    """Hash the complete durable SQLite state, including an active WAL."""
    digest = hashlib.sha256()
    for candidate in (path, Path(str(path) + "-wal")):
        digest.update(candidate.name.encode("utf-8"))
        if not candidate.exists():
            digest.update(b"\0missing\0")
            continue
        digest.update(str(candidate.stat().st_size).encode("ascii") + b"\0")
        with candidate.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)


def edge_schema(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(thread_spawn_edges)").fetchall()
    return {str(row[1]) for row in rows}


def terminal_agents(roster_path: Path) -> dict[str, dict[str, Any]]:
    try:
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReconcileError("native roster unreadable: %s" % exc) from exc
    agents = roster.get("agents", {})
    if not isinstance(agents, dict):
        raise ReconcileError("native roster agents must be an object")
    return {str(agent_id): item for agent_id, item in agents.items()
            if isinstance(item, dict) and item.get("status") in STRONG_TERMINAL and
            str(item.get("terminal_reason", "")).startswith(("SubagentStop", "rollout_")) and
            (item.get("host_close_confirmed_at") or
             str(item.get("terminal_reason", "")).startswith("rollout_thread_closed"))}


def snapshot(state_db: Path) -> dict[str, Any]:
    with connect_readonly(state_db) as conn:
        columns = edge_schema(conn)
        if columns != EXPECTED_EDGE_COLUMNS:
            raise ReconcileError("unexpected thread_spawn_edges schema: %s" % sorted(columns))
        rows = conn.execute(
            "SELECT parent_thread_id, child_thread_id, status FROM thread_spawn_edges ORDER BY child_thread_id"
        ).fetchall()
    return {"db_sha256": sha256(state_db), "db_state_digest": state_digest(state_db),
            "schema_columns": sorted(columns),
            "edge_count": len(rows),
            "edges": [{"parent_thread_id": row[0], "child_thread_id": row[1], "status": row[2]}
                      for row in rows]}


def plan(state_db: Path, roster_path: Path) -> dict[str, Any]:
    snap = snapshot(state_db)
    terminal = terminal_agents(roster_path)
    candidates = []
    for edge in snap["edges"]:
        child = str(edge["child_thread_id"])
        if edge["status"] == "open" and child in terminal:
            item = terminal[child]
            candidates.append({**edge, "task_name": item.get("task_name"),
                               "terminal_reason": item.get("terminal_reason")})
    return {"schema": "codex-loop-desktop-edge-plan/v1", "generated_at": time.time(),
            "state_db": str(state_db.resolve()), "roster": str(roster_path.resolve()),
            "db_sha256": snap["db_sha256"], "db_state_digest": snap["db_state_digest"],
            "roster_sha256": sha256(roster_path), "edge_count": snap["edge_count"],
            "candidates": candidates,
            "candidate_count": len(candidates), "mode": "dry-run"}


def desktop_running() -> bool:
    names = {"chatgpt.exe", "codex.exe", "codex-code-mode-host.exe",
             "chatgpt", "codex", "codex-code-mode-host"}
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                                text=True, timeout=10, check=False)
        if result.returncode != 0:
            # Unknown liveness must fail closed for a write operation.
            return True
        for row in csv.reader(result.stdout.splitlines()):
            if row and row[0].strip().lower() in names:
                return True
        return False
    result = subprocess.run(["ps", "-eo", "comm="], capture_output=True, text=True,
                            timeout=10, check=False)
    return result.returncode != 0 or any(Path(line.strip()).name.lower() in names
                                         for line in result.stdout.splitlines())


def verify_backup(target: Path, expected_edge_count: int) -> dict[str, Any]:
    with sqlite3.connect(str(target)) as conn:
        columns = edge_schema(conn)
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        backup_edges = conn.execute("SELECT COUNT(*) FROM thread_spawn_edges").fetchone()[0]
    if columns != EXPECTED_EDGE_COLUMNS:
        raise ReconcileError("backup schema verification failed")
    if quick_check != "ok" or backup_edges != expected_edge_count:
        raise ReconcileError("backup integrity/row-count verification failed")
    return {"quick_check": quick_check, "edge_count": backup_edges}


def sqlite_backup(state_db: Path, backup_dir: Path,
                  expected_edge_count: int | None = None) -> dict[str, Any]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / ("state_5-%d.sqlite" % int(time.time() * 1000))
    source = connect_readonly(state_db)
    destination = sqlite3.connect(str(target))
    try:
        source_edges = source.execute("SELECT COUNT(*) FROM thread_spawn_edges").fetchone()[0]
        source.backup(destination)
    finally:
        destination.close(); source.close()
    if expected_edge_count is not None and source_edges != expected_edge_count:
        raise ReconcileError("source row count changed after dry-run snapshot")
    verified = verify_backup(target, source_edges)
    return {"path": str(target.resolve()), "sha256": sha256(target),
            "bytes": target.stat().st_size, **verified}


def apply_plan(plan_doc: dict[str, Any], backup_dir: Path,
               is_desktop_running: Callable[[], bool] = desktop_running) -> dict[str, Any]:
    if is_desktop_running():
        raise ReconcileError("Codex Desktop is running or liveness is unknown; refusing state DB write")
    state_db = Path(plan_doc["state_db"])
    roster_path = Path(plan_doc["roster"])
    if state_digest(state_db) != plan_doc.get("db_state_digest"):
        raise ReconcileError("state DB changed after dry-run snapshot")
    if sha256(roster_path) != plan_doc.get("roster_sha256"):
        raise ReconcileError("native roster changed after dry-run snapshot")
    terminal = terminal_agents(roster_path)
    for item in plan_doc.get("candidates", []):
        if str(item.get("child_thread_id")) not in terminal:
            raise ReconcileError("candidate lost strong terminal/host-close evidence")
    backup = sqlite_backup(state_db, backup_dir, int(plan_doc.get("edge_count", -1)))
    if is_desktop_running():
        raise ReconcileError("Codex Desktop started during backup; refusing state DB write")
    if state_digest(state_db) != plan_doc.get("db_state_digest"):
        raise ReconcileError("state DB changed during backup")
    changed = 0
    conn = sqlite3.connect(str(state_db), timeout=5.0, isolation_level=None)
    try:
        if edge_schema(conn) != EXPECTED_EDGE_COLUMNS:
            raise ReconcileError("schema changed before apply")
        conn.execute("BEGIN IMMEDIATE")
        for item in plan_doc.get("candidates", []):
            cursor = conn.execute(
                "UPDATE thread_spawn_edges SET status='closed' "
                "WHERE child_thread_id=? AND status='open'",
                (item["child_thread_id"],))
            changed += cursor.rowcount
        conn.execute("COMMIT")
    except BaseException:
        try: conn.execute("ROLLBACK")
        except sqlite3.Error: pass
        raise
    finally:
        conn.close()
    return {**plan_doc, "mode": "applied", "changed": changed, "backup": backup,
            "applied_at": time.time(), "post_db_sha256": sha256(state_db),
            "post_db_state_digest": state_digest(state_db)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Dry-run/offline stale Desktop edge reconciliation")
    ap.add_argument("--state-db", type=Path, required=True)
    ap.add_argument("--roster", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup-dir", type=Path)
    args = ap.parse_args()
    doc = plan(args.state_db, args.roster)
    if args.apply:
        if not args.backup_dir:
            raise ReconcileError("--backup-dir is required with --apply")
        doc = apply_plan(doc, args.backup_dir)
    rendered = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
