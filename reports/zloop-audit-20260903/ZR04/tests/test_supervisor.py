"""Supervisor tests (M6): controller ownership (D-8/D-20), the kimi-server
gate (D-17), DAG launch order, materialization, mid-run cancel (VOL-09
§8), I6 fencing, worker failure."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# zloop.materialize (M7) is developed in parallel; the supervisor imports it
# lazily and these tests exercise it through its documented interface.
pytest.importorskip("zloop.materialize")

from zloop import db as zdb             # noqa: E402
from zloop import ids as zids          # noqa: E402
from zloop import materialize as zmat  # noqa: E402
from zloop import stage as zstage      # noqa: E402
from zloop import supervisor as zsup   # noqa: E402
from zloop import wave as zwave        # noqa: E402
from zloop import workspace as zws     # noqa: E402


def git(*args: str, cwd: Path) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd),
                       capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr}"
    return p.stdout


@pytest.fixture(autouse=True)
def _isolate_kimi_server_gate_for_unit_tests(monkeypatch):
    """Ensure supervisor unit tests don't fail due to a live Kimi server on 58627."""
    monkeypatch.setenv("ZLOOP_KIMI_HEALTHZ_URL", "http://127.0.0.1:59999/api/v1/healthz")
    monkeypatch.setattr(zsup, "kimi_server_up", lambda: False)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    """Temp ZLOOP_DATA + a clean git repo + a private staging worktree."""
    monkeypatch.setenv("ZLOOP_DATA", str(tmp_path / "zloop-data"))
    monkeypatch.setenv("ZLOOP_KIMI_HEALTHZ_URL", "http://127.0.0.1:59999/api/v1/healthz")
    root = tmp_path / "repo"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "T", cwd=root)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "init", cwd=root)
    head = git("rev-parse", "HEAD", cwd=root).strip()
    tree = git("rev-parse", "HEAD^{tree}", cwd=root).strip()
    base_ref = "refs/zloop/R001/S01/base"
    git("update-ref", base_ref, head, cwd=root)
    staging = tmp_path / "staging"
    r = zws.create_worktree(root, staging)
    assert r["ok"], r
    return SimpleNamespace(git_root=root, staging_ws=staging,
                           workspaces_root=tmp_path / "workspaces",
                           head=head, tree=tree, base_ref=base_ref)


@pytest.fixture()
def store(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ZLOOP_DATA", str(tmp_path / "zloop-data"))
    sdir = tmp_path / "S"
    conn = zdb.connect(sdir, create=True)
    yield zdb.ControlStore(sdir, conn, project_id="testproj")
    conn.close()


def _executing_stage(store, env, objective="implement supervisor semantics"):
    run_id = store.create_run("objective")
    st = zstage.create_stage(
        store, run_id, objective, "NORMAL",
        expected_head=env.head, dirty_digest="",
        stage_base_ref=env.base_ref, stage_base_tree=env.tree)
    zstage.transition_stage(store, run_id, st["stage_id"], "EXECUTING")
    return run_id, st["stage_id"]


def _packet(pid, scope, **kw):
    p = {"packet_id": pid, "goal": f"do {pid}",
         "write_scope": scope, "acceptance": ["python -V"],
         "risk_class": "NORMAL", "network_policy": "none"}
    p.update(kw)
    return p


def _run(store, env, run_id, sid, packets, backend, wave=1):
    return zsup.run_wave(store, run_id, sid, wave, packets, backend,
                         git_root=env.git_root,
                         staging_ws=env.staging_ws,
                         workspaces_root=env.workspaces_root, poll_s=0.01)


def _rows(store, run_id, stage_id):
    return {r["packet_id"]: dict(r) for r in store.conn.execute(
        "SELECT * FROM packets WHERE run_id=? AND stage_id=?",
        (run_id, stage_id))}


def _events(store, kind):
    return [json.loads(r["detail_json"]) for r in store.conn.execute(
        "SELECT detail_json FROM events WHERE kind=?", (kind,))]


class WorkspaceBackend(zwave.MockBackend):
    """MockBackend whose "worker" first turns the launch workspace into a
    real worktree (workspace content setup is the caller's concern — the
    supervisor only mkdirs the directory), then writes in-scope files there
    during the run (between start and collect)."""

    def __init__(self, git_root, writes=None):
        super().__init__()
        self._git_root = Path(git_root)
        self._writes = writes or {}  # packet_id -> [(relpath, content)]

    def start(self, spec):
        ws = Path(spec["workspace"])
        if ws.is_dir() and not any(ws.iterdir()):
            ws.rmdir()  # let create_worktree own the directory
        r = zws.create_worktree(self._git_root, ws)
        assert r["ok"], r
        for rel, content in self._writes.get(spec["packet_id"], []):
            target = ws / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return super().start(spec)


# ---- happy path -------------------------------------------------------------


def test_happy_path_dep_chain_materializes(store, env):
    run_id, sid = _executing_stage(store, env)
    packets = [_packet("P01", ["src/a/**"]),
               _packet("P02", ["src/b/**"], depends_on=["P01"])]
    backend = WorkspaceBackend(env.git_root, {
        "P01": [("src/a/mod.py", "a = 1\n")],
        "P02": [("src/b/mod.py", "b = 2\n")]})

    res = _run(store, env, run_id, sid, packets, backend)
    assert res["ok"] is True
    assert res["cancelled"] is False
    assert "reason" not in res
    assert res["materialized"] == ["P01", "P02"]
    assert res["blocked"] == []

    rows = _rows(store, run_id, sid)
    assert [rows["P01"]["state"], rows["P02"]["state"]] == \
        ["MATERIALIZED", "MATERIALIZED"]

    # launches reached TERMINAL(completed) and the packets stayed wired
    # to their exact launch until the end
    launches = [dict(r) for r in store.conn.execute(
        "SELECT * FROM launches ORDER BY packet_id")]
    assert len(launches) == 2
    assert all(l["intent_state"] == "TERMINAL" for l in launches)
    assert all(l["terminal_state"] == "completed" for l in launches)
    assert rows["P01"]["active_launch_id"] == launches[0]["launch_id"]

    # DAG order: P02 only started after P01 was MATERIALIZED [I8]
    seq = [(r["kind"], json.loads(r["detail_json"]).get("packet_id"))
           for r in store.conn.execute(
               "SELECT kind, detail_json FROM events ORDER BY seq")]
    p01_mat = next(i for i, (k, pid) in enumerate(seq)
                   if k == "packet_materialized" and pid == "P01")
    p02_run = next(i for i, (k, pid) in enumerate(seq)
                   if k == "packet_running" and pid == "P02")
    assert p02_run > p01_mat

    # per-launch workspaces under <stage>/<packet>/<launch> [I34]
    for pid, sub in (("P01", "a"), ("P02", "b")):
        launches_dir = env.workspaces_root / sid / pid
        matches = list(launches_dir.iterdir())
        assert len(matches) == 1
        assert (matches[0] / "src" / sub / "mod.py").read_text(
            encoding="utf-8")

    # stage stays EXECUTING across waves (VOL-08 §4: no state move)
    assert zstage.get_stage(store, run_id, sid)["state"] == "EXECUTING"

    # controller token released (D-8)
    assert store.controller(run_id)["controller_nonce"] is None

    # staging branch advanced with ZLoop provenance trailers (VOL-10 §4)
    sha = zmat.staging_commit_sha(env.staging_ws)
    assert sha == git("rev-parse", "HEAD", cwd=env.staging_ws).strip()
    assert sha != env.head
    count = int(git("rev-list", "--count", "HEAD",
                    cwd=env.staging_ws).strip())
    assert count >= 3  # initial commit + one materialization per packet
    bodies = git("log", "--format=%B", cwd=env.staging_ws)
    assert bodies.count("ZLoop-Run") >= 2
    assert bodies.count("ZLoop-Packet") >= 2
    assert "P01" in bodies and "P02" in bodies

    kinds = {k for k, _ in seq}
    for k in ("controller_claimed", "wave_started", "packet_reported",
              "packet_materialized", "wave_completed", "controller_released"):
        assert k in kinds


# ---- mid-run cancel (VOL-09 §8, D-8) ----------------------------------------


def test_cancel_mid_run_settles_wave(store, env):
    class CancelOnFirstCollect(WorkspaceBackend):
        """The user cancels while the first worker runs: collect() writes
        runs.cancel_requested=1 (command input, D-8) as its side effect."""

        def __init__(self, git_root, store_, run_id_):
            super().__init__(git_root)
            self._store, self._run_id = store_, run_id_
            self._collects = 0

        def collect(self, handle, *, status="completed"):
            self._collects += 1
            if self._collects == 1:
                self._store.request_cancel(self._run_id)
            return super().collect(handle, status=status)

    run_id, sid = _executing_stage(store, env)
    packets = [_packet("P01", ["src/a/**"]),
               _packet("P02", ["src/b/**"], depends_on=["P01"])]
    res = _run(store, env, run_id, sid, packets,
               CancelOnFirstCollect(env.git_root, store, run_id))
    assert res["ok"] is False and res["reason"] == "cancelled"
    assert res["cancelled"] is True
    assert res["materialized"] == ["P01"]  # finished before the cancel tick
    assert res["cancelled_packets"] == ["P02"]

    rows = _rows(store, run_id, sid)
    assert rows["P01"]["state"] == "MATERIALIZED"
    assert rows["P02"]["state"] == "CANCELLED"
    assert rows["P02"]["active_launch_id"] is None  # never launched
    # stage transitioned per VOL-08 §4 (any -> CANCELLED)
    assert zstage.get_stage(store, run_id, sid)["state"] == "CANCELLED"
    # the remaining packet got its cancellation event
    cancels = _events(store, "packet_cancelled")
    assert [c["packet_id"] for c in cancels] == ["P02"]
    assert cancels[0]["from_state"] == "PENDING"
    # controller released even on the cancel path
    assert store.controller(run_id)["controller_nonce"] is None


# ---- fencing (I6) ------------------------------------------------------------


def test_stale_result_fence_leaves_packet_running(store, env):
    class StaleBackend(zwave.MockBackend):
        def collect(self, handle, *, status="completed"):
            report = super().collect(handle, status=status)
            report["launch_id"] = "L000000000000"  # wrong launch [I6]
            return report

    run_id, sid = _executing_stage(store, env)
    res = _run(store, env, run_id, sid,
               [_packet("P01", ["src/a/**"])], StaleBackend())
    # the fenced-out result can never become a state change: the wave ends
    # stalled with the packet left for the next controller epoch
    assert res["ok"] is False and res["reason"] == "stalled"
    assert res["running"] == ["P01"]
    assert res["materialized"] == []

    rows = _rows(store, run_id, sid)
    assert rows["P01"]["state"] == "RUNNING"  # stays RUNNING [I7]
    assert rows["P01"]["active_launch_id"]    # still bound to the live launch
    launches = [dict(r) for r in store.conn.execute("SELECT * FROM launches")]
    assert len(launches) == 1
    assert launches[0]["intent_state"] == "RUNNING"
    # the rejection is audited with the I6 reason
    rejected = _events(store, "result_rejected")
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "stale_launch"
    assert rejected[0]["packet_id"] == "P01"
    # nothing moved past the fence
    assert _events(store, "packet_reported") == []
    assert _events(store, "packet_materialized") == []
    # controller released
    assert store.controller(run_id)["controller_nonce"] is None


# ---- controller ownership (D-8/D-20, I5) --------------------------------------


def test_controller_busy_live_owner_never_wrestles(store, env):
    run_id, sid = _executing_stage(store, env)
    other = zids.new_nonce()
    own_start = zdb.process_start_time(os.getpid())
    assert own_start  # real process identity (D-20), not a hint
    assert store.claim_controller(run_id, nonce=other, pid=os.getpid(),
                                  pid_start=own_start)
    res = _run(store, env, run_id, sid,
               [_packet("P01", ["src/a/**"])], zwave.MockBackend())
    # the owner is mechanically ALIVE: refusal names it (D-20), and the
    # supervisor never wrestles the token away (I5)
    assert res["ok"] is False and res["reason"] == "owner_alive"
    # nothing touched: no packets, no wave events, no workspaces
    assert _rows(store, run_id, sid) == {}
    assert _events(store, "wave_started") == []
    assert not (env.workspaces_root / sid).exists()
    # the live owner keeps the token
    ctrl = store.controller(run_id)
    assert ctrl["controller_nonce"] == other
    assert ctrl["controller_pid"] == os.getpid()


def test_controller_ambiguous_owner_fails_closed(store, env):
    run_id, sid = _executing_stage(store, env)
    # a claim whose probe-based start could not be recorded (pid_start=None):
    # death can never be proven, so the takeover must fail closed (I43)
    other = zids.new_nonce()
    assert store.claim_controller(run_id, nonce=other, pid=os.getpid(),
                                  pid_start=None)
    res = _run(store, env, run_id, sid,
               [_packet("P01", ["src/a/**"])], zwave.MockBackend())
    assert res["ok"] is False and res["reason"] == "ambiguous_fail_closed"
    assert _rows(store, run_id, sid) == {}
    assert _events(store, "wave_started") == []
    assert store.controller(run_id)["controller_nonce"] == other


def test_dead_owner_takeover_allows_wave(store, env):
    run_id, sid = _executing_stage(store, env)
    old = zids.new_nonce()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        start = zdb.process_start_time(proc.pid)
        assert start
        assert store.claim_controller(run_id, nonce=old, pid=proc.pid,
                                      pid_start=start)
        proc.kill()
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    # the old owner is mechanically dead: run_wave takes over (D-20) and
    # supervises the wave normally
    backend = WorkspaceBackend(env.git_root,
                               {"P01": [("src/a/mod.py", "a = 1\n")]})
    res = _run(store, env, run_id, sid,
               [_packet("P01", ["src/a/**"])], backend)
    assert res["ok"] is True
    assert res["materialized"] == ["P01"]
    assert store.controller(run_id)["controller_nonce"] is None  # released


# ---- kimi-server gate (D-17/P-SEC1) --------------------------------------------


def test_kimi_server_up_blocks_wave_before_claim(store, env, monkeypatch):
    monkeypatch.setattr(zsup, "kimi_server_up", lambda timeout_s=1.0: True)
    run_id, sid = _executing_stage(store, env)
    res = _run(store, env, run_id, sid,
               [_packet("P01", ["src/a/**"])], zwave.MockBackend())
    assert res["ok"] is False and res["reason"] == "KIMI_SERVER_UP"
    assert res["detail"] == ("loopback escalation path open (P-SEC1/D-17): "
                             "stop kimi web before running worker waves")
    # refused before claiming: nothing touched, no controller token taken
    assert _rows(store, run_id, sid) == {}
    assert _events(store, "wave_started") == []
    assert _events(store, "controller_claimed") == []
    assert not (env.workspaces_root / sid).exists()
    assert store.controller(run_id)["controller_nonce"] is None


# ---- validation errors are returned, not raised ------------------------------


def test_validation_errors_returned_nothing_written(store, env):
    run_id, sid = _executing_stage(store, env)
    bad = [_packet("P01", ["src/a/**"]),
           _packet("P02", ["src/b/**"], depends_on=["P01"],
                   risk_class="LOW")]
    res = _run(store, env, run_id, sid, bad, zwave.MockBackend())
    assert res["ok"] is False and res["reason"] == "invalid_wave"
    assert any("floor" in e for e in res["errors"])
    assert _rows(store, run_id, sid) == {}  # fail-closed, nothing written (I4)
    assert not (env.workspaces_root / sid).exists()
    # the failed claim's token is still released
    assert store.controller(run_id)["controller_nonce"] is None


# ---- terminal worker failure --------------------------------------------------


def test_failed_worker_status_fails_packet(store, env):
    class FailingBackend(zwave.MockBackend):
        def collect(self, handle, *, status="completed"):
            return super().collect(handle, status="failed")

    run_id, sid = _executing_stage(store, env)
    res = _run(store, env, run_id, sid,
               [_packet("P01", ["src/a/**"])], FailingBackend())
    assert res["ok"] is True  # the wave completed; the packet's outcome:
    assert res["failed"] == ["P01"]
    rows = _rows(store, run_id, sid)
    assert rows["P01"]["state"] == "FAILED"
    assert rows["P01"]["active_launch_id"] is None
    assert _events(store, "packet_failed")
    assert store.controller(run_id)["controller_nonce"] is None


# ---- materialization rejection (VOL-10 §2 scope violation) -------------------


def test_scope_violation_blocks_packet(store, env):
    # the "worker" writes outside its write_scope: host materialization
    # rejects the delta and the supervisor BLOCKs the packet (VOL-10 §2)
    backend = WorkspaceBackend(env.git_root, {
        "P01": [("src/a/mod.py", "a = 1\n"), ("outside/evil.txt", "x\n")]})
    run_id, sid = _executing_stage(store, env)
    res = _run(store, env, run_id, sid,
               [_packet("P01", ["src/a/**"])], backend)
    assert res["ok"] is True
    assert res["materialized"] == []
    assert res["blocked"] == ["P01"]
    rows = _rows(store, run_id, sid)
    assert rows["P01"]["state"] == "BLOCKED"
    blocked = _events(store, "packet_blocked")
    assert len(blocked) == 1
    assert blocked[0]["reason"] == "scope_violation"
    assert blocked[0]["violations"] == ["outside/evil.txt"]
    # the out-of-scope escape never reached the staging branch
    assert not (env.staging_ws / "outside").exists()
    assert store.controller(run_id)["controller_nonce"] is None
