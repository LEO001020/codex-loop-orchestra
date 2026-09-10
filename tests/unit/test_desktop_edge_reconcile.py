import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


PKG = Path(__file__).resolve().parents[2]
SCRIPT = PKG / "harness" / "desktop_edge_reconcile.py"
SPEC = importlib.util.spec_from_file_location("desktop_edge_reconcile", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def fixture(tmp_path):
    db = tmp_path / "state_5.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT NOT NULL, "
                     "child_thread_id TEXT NOT NULL PRIMARY KEY, status TEXT NOT NULL)")
        conn.executemany("INSERT INTO thread_spawn_edges VALUES (?,?,?)", [
            ("p", "terminal-child", "open"), ("p", "running-child", "open"),
            ("p", "closed-child", "closed")])
    roster = tmp_path / "native_roster.json"
    roster.write_text(json.dumps({"agents": {
        "terminal-child": {"status": "terminal", "terminal_reason": "SubagentStop",
                           "host_close_confirmed_at": 123.0,
                           "task_name": "终态任务"},
        "running-child": {"status": "running", "task_name": "在途任务"}}}), encoding="utf-8")
    return db, roster


def statuses(db):
    with sqlite3.connect(db) as conn:
        return dict(conn.execute("SELECT child_thread_id,status FROM thread_spawn_edges"))


def test_default_plan_is_read_only_and_requires_strong_terminal_evidence(tmp_path):
    db, roster = fixture(tmp_path)
    before = db.read_bytes()
    doc = MOD.plan(db, roster)
    assert doc["mode"] == "dry-run" and doc["candidate_count"] == 1
    assert doc["edge_count"] == 3
    assert doc["roster_sha256"] == MOD.sha256(roster)
    assert doc["candidates"][0]["child_thread_id"] == "terminal-child"
    assert db.read_bytes() == before


def test_apply_refuses_while_desktop_is_live(tmp_path):
    db, roster = fixture(tmp_path)
    with pytest.raises(MOD.ReconcileError, match="Desktop is running"):
        MOD.apply_plan(MOD.plan(db, roster), tmp_path / "backup", lambda: True)
    assert statuses(db)["terminal-child"] == "open"


def test_offline_apply_backs_up_and_only_closes_exact_candidates(tmp_path):
    db, roster = fixture(tmp_path)
    result = MOD.apply_plan(MOD.plan(db, roster), tmp_path / "backup", lambda: False)
    assert result["changed"] == 1
    assert Path(result["backup"]["path"]).exists()
    assert result["backup"]["quick_check"] == "ok"
    assert result["backup"]["edge_count"] == 3
    assert statuses(db) == {"terminal-child": "closed", "running-child": "open", "closed-child": "closed"}
    # A fresh plan is empty: open->closed apply is idempotent.
    assert MOD.plan(db, roster)["candidate_count"] == 0


def test_apply_detects_wal_only_change_after_plan(tmp_path):
    db, roster = fixture(tmp_path)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        doc = MOD.plan(db, roster)
        main_hash = MOD.sha256(db)
        conn.execute("INSERT INTO thread_spawn_edges VALUES (?,?,?)", ("p", "new-child", "open"))
        conn.commit()
        assert MOD.sha256(db) == main_hash
        with pytest.raises(MOD.ReconcileError, match="state DB changed"):
            MOD.apply_plan(doc, tmp_path / "backup", lambda: False)
    finally:
        conn.close()


def test_apply_detects_roster_evidence_change_after_plan(tmp_path):
    db, roster = fixture(tmp_path)
    doc = MOD.plan(db, roster)
    parsed = json.loads(roster.read_text(encoding="utf-8"))
    parsed["agents"]["terminal-child"].pop("host_close_confirmed_at")
    roster.write_text(json.dumps(parsed), encoding="utf-8")
    with pytest.raises(MOD.ReconcileError, match="roster changed"):
        MOD.apply_plan(doc, tmp_path / "backup", lambda: False)


def test_apply_detects_main_db_change_after_plan(tmp_path):
    db, roster = fixture(tmp_path)
    doc = MOD.plan(db, roster)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE thread_spawn_edges SET status='closed' WHERE child_thread_id='running-child'")
    with pytest.raises(MOD.ReconcileError, match="state DB changed"):
        MOD.apply_plan(doc, tmp_path / "backup", lambda: False)


def test_apply_refuses_if_desktop_starts_during_backup(tmp_path):
    db, roster = fixture(tmp_path)
    states = iter((False, True))
    with pytest.raises(MOD.ReconcileError, match="started during backup"):
        MOD.apply_plan(MOD.plan(db, roster), tmp_path / "backup", lambda: next(states))
    assert statuses(db)["terminal-child"] == "open"


def test_verify_backup_rejects_wrong_expected_row_count(tmp_path):
    db, _ = fixture(tmp_path)
    backup = MOD.sqlite_backup(db, tmp_path / "backup", expected_edge_count=3)
    with pytest.raises(MOD.ReconcileError, match="row-count"):
        MOD.verify_backup(Path(backup["path"]), 4)
