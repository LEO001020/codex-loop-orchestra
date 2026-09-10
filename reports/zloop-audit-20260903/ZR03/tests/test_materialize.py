"""Materialization tests (VOL-10 §1-§4): host re-apply onto the CURRENT
staging snapshot + host-run acceptance (I38: worker green is not evidence).
Real git repos via subprocess; ControlStore on a tmp ZLOOP_DATA."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import db as zdb            # noqa: E402
from zloop import materialize as zmat  # noqa: E402
from zloop import stage as zstage      # noqa: E402
from zloop import wave as zwave        # noqa: E402
from zloop import workspace as zw      # noqa: E402


def git(*args: str, cwd: Path) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd),
                       capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr}"
    return p.stdout


@pytest.fixture()
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ZLOOP_DATA", str(data))
    conn = zdb.connect(data, create=True)
    yield zdb.ControlStore(data, conn, project_id="mat-test")
    conn.close()


@pytest.fixture()
def canon(tmp_path: Path) -> Path:
    root = tmp_path / "canon"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "T", cwd=root)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / "del.py").write_text("deleted soon\n", encoding="utf-8")
    (root / "src" / "old.py").write_text("renamed soon\n", encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "init", cwd=root)
    return root


@pytest.fixture()
def worktrees(canon: Path, tmp_path: Path):
    """(worker workspace, staging worktree) both at the canonical HEAD."""
    worker = tmp_path / "ws" / "worker"
    worker.parent.mkdir(parents=True, exist_ok=True)
    r = zw.create_worktree(canon, worker)
    assert r["ok"], r
    staging = tmp_path / "ws" / "staging"
    git("worktree", "add", "-q", "-b", "zloop-staging", str(staging), "HEAD",
        cwd=canon)
    return worker, staging


def _packet_row(store, run_id, stage_id):
    row = store.conn.execute(
        "SELECT * FROM packets WHERE run_id=? AND stage_id=?",
        (run_id, stage_id)).fetchone()
    return dict(row)


def _reported_packet(store, canon: Path, *, acceptance):
    """Run one wave so packet P01 lands in REPORTED via the I6 fence."""
    run_id = store.create_run("objective")
    head = git("rev-parse", "HEAD", cwd=canon).strip()
    st = zstage.create_stage(store, run_id, "implement a thing", "NORMAL",
                             expected_head=head, dirty_digest="",
                             stage_base_ref="refs/zloop/R/base",
                             stage_base_tree="tree0")
    zstage.transition_stage(store, run_id, st["stage_id"], "EXECUTING")
    packet = {"packet_id": "P01", "goal": "change src",
              "write_scope": ["src/**"], "acceptance": list(acceptance),
              "risk_class": "NORMAL", "network_policy": "none"}
    summary = zwave.run_wave(store, run_id, st["stage_id"], 1, [packet],
                             zwave.MockBackend())
    assert summary["reported"] == 1
    return run_id, st["stage_id"], packet

# ---- run_host_acceptance -----------------------------------------------------

def test_run_host_acceptance_rc_and_output(tmp_path: Path):
    v = zmat.run_host_acceptance(tmp_path, ['python -c "exit(0)"'])
    assert v["ok"] is True
    assert v["results"][0]["rc"] == 0

    v2 = zmat.run_host_acceptance(tmp_path, ['python -c "exit(1)"'])
    assert v2["ok"] is False
    assert v2["results"][0]["rc"] == 1

    v3 = zmat.run_host_acceptance(
        tmp_path, ['python -c "import sys; print(\'hi\');'
                   ' sys.stderr.write(\'bad\'); sys.exit(3)"'])
    assert v3["ok"] is False
    assert v3["results"][0]["rc"] == 3
    assert "hi" in v3["results"][0]["stdout"]
    assert "bad" in v3["results"][0]["stderr"]

    # one failing command in a sequence fails the whole acceptance
    v4 = zmat.run_host_acceptance(
        tmp_path, ['python -c "exit(0)"', 'python -c "exit(2)"'])
    assert v4["ok"] is False and len(v4["results"]) == 2


def test_run_host_acceptance_timeout(tmp_path: Path):
    v = zmat.run_host_acceptance(
        tmp_path, ['python -c "import time; time.sleep(10)"'], timeout_s=2)
    assert v["ok"] is False
    assert v["results"][0]["timeout"] is True
    assert v["results"][0]["rc"] != 0


# ---- materialize_packet (VOL-10 §1-§2) ---------------------------------------

def test_materialize_scope_violation(store, canon, worktrees):
    worker, staging = worktrees
    run_id, sid, packet = _reported_packet(
        store, canon, acceptance=['python -c "exit(0)"'])
    # worker changed an in-scope AND an out-of-scope path
    (worker / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    (worker / "README.md").write_text("worker touched me\n", encoding="utf-8")

    res = zmat.materialize_packet(
        store, run_id, sid, "P01", git_root=canon, staging_ws=staging,
        workspace=worker, write_scope=packet["write_scope"],
        acceptance=packet["acceptance"])
    assert res["ok"] is False
    assert res["reason"] == "scope_violation"
    assert res["violations"] == ["README.md"]
    # fail-closed: packet NOT transitioned, staging snapshot untouched
    assert _packet_row(store, run_id, sid)["state"] == "REPORTED"
    assert git("status", "--porcelain", cwd=staging) == ""
    assert zstage.get_stage(store, run_id, sid)["current_snapshot"] is None
    kinds = [r["kind"] for r in store.conn.execute(
        "SELECT kind FROM events ORDER BY seq")]
    assert "materialization_scope_violation" in kinds


def test_materialize_success_commit_and_trailers(store, canon, worktrees):
    worker, staging = worktrees
    base = git("rev-parse", "HEAD", cwd=canon).strip()
    run_id, sid, packet = _reported_packet(
        store, canon, acceptance=['python -c "exit(0)"'])
    # worker final FS: modify + add + delete + rename, all inside src/**
    (worker / "src" / "a.py").write_text("x = 42\n", encoding="utf-8")
    (worker / "src" / "new.py").write_text("brand new\n", encoding="utf-8")
    (worker / "src" / "del.py").unlink()
    git("mv", "src/old.py", "src/renamed.py", cwd=worker)

    res = zmat.materialize_packet(
        store, run_id, sid, "P01", git_root=canon, staging_ws=staging,
        workspace=worker, write_scope=packet["write_scope"],
        acceptance=packet["acceptance"])
    assert res["ok"] is True, res
    sha = res["commit"]
    assert zmat.staging_commit_sha(staging) == sha

    # packet MATERIALIZED + snapshot pointer advanced (same S transaction)
    assert _packet_row(store, run_id, sid)["state"] == "MATERIALIZED"
    assert zstage.get_stage(store, run_id, sid)["current_snapshot"] == sha
    kinds = [r["kind"] for r in store.conn.execute(
        "SELECT kind FROM events ORDER BY seq")]
    assert "packet_materialized" in kinds

    # the candidate carries the worker's full delta
    assert (staging / "src" / "a.py").read_text(encoding="utf-8") == "x = 42\n"
    assert (staging / "src" / "new.py").read_text(encoding="utf-8") == "brand new\n"
    assert not (staging / "src" / "del.py").exists()
    assert (staging / "src" / "renamed.py").exists()
    assert not (staging / "src" / "old.py").exists()

    # host commit provenance: author + ZLoop trailers, canonical untouched
    body = git("log", "-1", "--format=%B", cwd=staging)
    for trailer in (f"ZLoop-Run: {run_id}", f"ZLoop-Stage: {sid}",
                    "ZLoop-Packet: P01", "ZLoop-Packet-Revision: 1"):
        assert trailer in body
    assert git("log", "-1", "--format=%an %ae", cwd=staging).strip() == \
        "zloop zloop@localhost"
    assert git("rev-parse", "HEAD", cwd=canon).strip() == base
    assert git("status", "--porcelain", cwd=canon) == ""


def test_materialize_second_packet_lands_on_current_snapshot(
        store, canon, worktrees):
    worker, staging = worktrees
    run_id, sid, _p = _reported_packet(
        store, canon, acceptance=['python -c "exit(0)"'])
    # P01 materializes first
    (worker / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    res1 = zmat.materialize_packet(
        store, run_id, sid, "P01", git_root=canon, staging_ws=staging,
        workspace=worker, write_scope=["src/**"],
        acceptance=['python -c "exit(0)"'])
    assert res1["ok"] is True

    # P02 (fresh REPORTED packet, fresh worker delta) must re-apply on top of
    # P01's commit — the CURRENT stage snapshot, not the original base
    packet2 = {"packet_id": "P02", "goal": "more src",
               "write_scope": ["src/**"], "risk_class": "NORMAL",
               "network_policy": "none",
               "acceptance": ['python -c "exit(0)"']}
    with store.mutation():
        store.conn.execute(
            "INSERT INTO packets(run_id, stage_id, stage_revision, packet_id,"
            " packet_revision, goal, write_scope_json, acceptance_json,"
            " risk_class, network_policy, state)"
            " VALUES (?,?,1,'P02',1,?,?,?,?,?,'REPORTED')",
            (run_id, sid, packet2["goal"],
             json.dumps(packet2["write_scope"]),
             json.dumps(packet2["acceptance"]),
             packet2["risk_class"], packet2["network_policy"]))
    worker2 = worker.parent / "worker2"
    r = zw.create_worktree(canon, worker2)
    assert r["ok"], r
    (worker2 / "src" / "b.py").write_text("second packet\n", encoding="utf-8")
    res2 = zmat.materialize_packet(
        store, run_id, sid, "P02", git_root=canon, staging_ws=staging,
        workspace=worker2, write_scope=["src/**"],
        acceptance=['python -c "exit(0)"'])
    assert res2["ok"] is True
    # res2's candidate is a child of res1's candidate (same staging branch)
    assert git("rev-parse", f"{res2['commit']}^", cwd=staging).strip() == \
        res1["commit"]
    assert (staging / "src" / "a.py").exists()          # P01's delta kept
    assert (staging / "src" / "b.py").exists()          # P02's delta applied
    rows = {r["packet_id"]: r["state"] for r in store.conn.execute(
        "SELECT packet_id, state FROM packets WHERE run_id=?", (run_id,))}
    assert rows == {"P01": "MATERIALIZED", "P02": "MATERIALIZED"}


def test_materialize_acceptance_failure_no_transition(store, canon, worktrees):
    worker, staging = worktrees
    run_id, sid, _p = _reported_packet(
        store, canon, acceptance=['python -c "exit(1)"'])
    (worker / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")

    res = zmat.materialize_packet(
        store, run_id, sid, "P01", git_root=canon, staging_ws=staging,
        workspace=worker, write_scope=["src/**"],
        acceptance=['python -c "exit(1)"'])
    assert res["ok"] is False
    assert res["reason"] == "acceptance_failed"
    # the candidate commit is LEFT on the staging branch (evidence) ...
    assert zmat.staging_commit_sha(staging) == res["commit"]
    assert (staging / "src" / "a.py").read_text(encoding="utf-8") == "x = 2\n"
    # ... but the packet is NOT transitioned (I38: worker/host-green on the
    # candidate is what counts, and it failed)
    assert _packet_row(store, run_id, sid)["state"] == "REPORTED"
    assert zstage.get_stage(store, run_id, sid)["current_snapshot"] is None
    kinds = [r["kind"] for r in store.conn.execute(
        "SELECT kind FROM events ORDER BY seq")]
    assert "materialization_failed" in kinds
    assert "packet_materialized" not in kinds
    # retry after fixing acceptance re-runs on the same staging branch
    res2 = zmat.materialize_packet(
        store, run_id, sid, "P01", git_root=canon, staging_ws=staging,
        workspace=worker, write_scope=["src/**"],
        acceptance=['python -c "exit(0)"'])
    assert res2["ok"] is True
    assert _packet_row(store, run_id, sid)["state"] == "MATERIALIZED"


def test_materialize_requires_reported_packet(store, canon, worktrees):
    worker, staging = worktrees
    run_id, sid, _p = _reported_packet(
        store, canon, acceptance=['python -c "exit(0)"'])
    # not REPORTED (still RUNNING): refused before any FS/git work
    with store.mutation():
        store.conn.execute(
            "UPDATE packets SET state='RUNNING' WHERE run_id=? AND stage_id=?",
            (run_id, sid))
    res = zmat.materialize_packet(
        store, run_id, sid, "P01", git_root=canon, staging_ws=staging,
        workspace=worker, write_scope=["src/**"],
        acceptance=['python -c "exit(0)"'])
    assert res == {"ok": False, "reason": "not_reported",
                  "detail": "packet state is RUNNING, not REPORTED"}
    assert git("status", "--porcelain", cwd=staging) == ""
    # unknown packet: same fail-closed shape
    res2 = zmat.materialize_packet(
        store, run_id, sid, "P99", git_root=canon, staging_ws=staging,
        workspace=worker, write_scope=["src/**"],
        acceptance=['python -c "exit(0)"'])
    assert res2["ok"] is False and res2["reason"] == "not_reported"
