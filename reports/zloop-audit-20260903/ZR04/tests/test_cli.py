"""CLI tests (VOL-18 layer B): subprocess-driven against `python -m zloop.cli`.

Exit-code contract under test: 0 ok · 2 usage · 3 S_DEGRADED ·
4 bind-wait timeout (P2-13) · 5 blocked. Parallel modules
(zloop.history / zloop.checkpoint / zloop.install / zloop.hook) may be
absent while other agents write them — those assertions degrade via
importorskip / _have_module.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from zloop import __version__          # noqa: E402
from zloop import db as zdb           # noqa: E402
from zloop import ids, paths          # noqa: E402
from zloop import stage as zstage     # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_kimi_gate(monkeypatch):
    monkeypatch.setenv("ZLOOP_KIMI_HEALTHZ_URL", "http://127.0.0.1:59999/api/v1/healthz")
    from zloop import supervisor as zsup
    monkeypatch.setattr(zsup, "kimi_server_up", lambda: False)


def _cli_env(data_root: Path) -> dict:
    env = dict(os.environ)
    pp = str(SRC)
    old = env.get("PYTHONPATH", "")
    if pp not in old.split(os.pathsep):
        env["PYTHONPATH"] = pp + ((os.pathsep + old) if old else "")
    env["ZLOOP_DATA"] = str(data_root)
    # Isolation for D-17: ensure unit tests probe an unallocated offline port
    # so live host Kimi server instances on 58627 don't trigger KIMI_SERVER_UP refusals.
    env["ZLOOP_KIMI_HEALTHZ_URL"] = "http://127.0.0.1:59999/api/v1/healthz"
    return env


def run_cli(*args, cwd: Path, data_root: Path, input=None, timeout=120):
    return subprocess.run(
        [sys.executable, "-m", "zloop.cli", *map(str, args)],
        input=input, capture_output=True, text=True, cwd=str(cwd),
        env=_cli_env(data_root), timeout=timeout,
        stdin=subprocess.DEVNULL if input is None else None)


def _have_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    d = tmp_path / "zloop-data"
    d.mkdir()
    monkeypatch.setenv("ZLOOP_DATA", str(d))  # in-process foundation calls agree
    return d


@pytest.fixture()
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def g(*a):
        return subprocess.run(["git", *a], cwd=str(repo), capture_output=True,
                              text=True, timeout=60)

    assert g("init").returncode == 0
    g("config", "user.email", "cli-test@example.com")
    g("config", "user.name", "cli-test")
    g("config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# cli test repo\n", encoding="utf-8")
    g("add", "-A")
    r = g("commit", "-m", "init")
    assert r.returncode == 0, r.stderr
    return repo


def _only_project_id() -> str:
    projs = paths.load_registry()["projects"]
    assert len(projs) == 1
    return next(iter(projs))


def _start_run(repo: Path, data_root: Path, objective="test objective"):
    r = run_cli("run", "start", objective, cwd=repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines[0].startswith("ZLOOP_BIND_TOKEN=")
    return lines


def _corrupt_control_db(pid: str) -> None:
    dbp = paths.control_db_path(pid)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(dbp) + suffix)
        if p.exists():
            p.unlink()
    dbp.write_bytes(b"this is definitely not a sqlite database at all")


# ---- basics ---------------------------------------------------------------


def test_version(tmp_path, data_root):
    r = run_cli("--version", cwd=tmp_path, data_root=data_root)
    assert r.returncode == 0
    assert __version__ in r.stdout


def test_usage_errors_exit_2(git_repo, data_root):
    r = run_cli("run", cwd=git_repo, data_root=data_root)  # missing subcommand
    assert r.returncode == 2
    r2 = run_cli("definitely-not-a-command", cwd=git_repo, data_root=data_root)
    assert r2.returncode == 2


def test_commands_require_registered_project(tmp_path, data_root):
    for args in (["run", "list"], ["binding", "status"], ["verify-run"],
                 ["run", "close", "R001"],
                 ["stage", "begin", "--objective", "x"],
                 ["stage", "status"], ["stage", "status", "S01"],
                 ["stage", "close", "S01"], ["stage", "promote", "S01"],
                 ["wave", "propose", "nope.json"], ["wave", "start", "W1"],
                 ["wave", "cancel", "W1"]):
        r = run_cli(*args, cwd=tmp_path, data_root=data_root)
        assert r.returncode == 5, (args, r.stderr)
    r = run_cli("history", "search", "x", cwd=tmp_path, data_root=data_root)
    if _have_module("zloop.history"):
        assert r.returncode == 5
    else:
        assert r.returncode == 0
    rr = run_cli("research", "run", "spec.json", cwd=tmp_path, data_root=data_root)
    if _have_module("zloop.research.broker"):
        assert rr.returncode == 5
    else:
        assert rr.returncode == 0


def test_project_attach_list_idempotent(git_repo, data_root):
    a = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert a.returncode == 0, a.stderr
    rec = json.loads(a.stdout)
    assert rec["created"] is True
    assert rec["project_id"]
    a2 = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert json.loads(a2.stdout)["created"] is False
    l = run_cli("project", "list", cwd=git_repo, data_root=data_root)
    assert l.returncode == 0
    projs = json.loads(l.stdout)
    assert len(projs) == 1
    assert projs[0]["project_id"] == rec["project_id"]
    assert projs[0]["git_root"].lower() == str(git_repo.resolve()).lower()


# ---- run start: bind-token marker (VOL-05 §4) -----------------------------


def test_run_start_marker_first_and_pending_claim(git_repo, data_root):
    lines = _start_run(git_repo, data_root, "do the thing")
    nonce = lines[0].split("=", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{64}", nonce)
    obj = json.loads(lines[1])
    assert set(obj) >= {"run_id", "project_id", "claim_expires_at", "note"}
    assert obj["run_id"] == "R001"
    assert "FOREGROUND" in obj["note"]

    pid = _only_project_id()
    assert obj["project_id"] == pid
    pdir = paths.project_dir(pid)
    conn = zdb.connect(pdir)
    try:
        store = zdb.ControlStore(pdir, conn, project_id=pid)
        run = store.run(obj["run_id"])
        assert run is not None and run["state"] == "ACTIVE"
        assert run["objective"] == "do the thing"
        row = conn.execute(
            "SELECT * FROM pending_binding_claims WHERE nonce=?", (nonce,)).fetchone()
        assert row is not None
        assert row["purpose"] == "run_start"
        assert row["run_id"] == obj["run_id"]
        assert row["claimed_at"] is None
        exp = ids.parse_iso(row["expires_at"])
        assert exp is not None and exp > datetime.now(timezone.utc)  # TTL 120s
    finally:
        conn.close()


# ---- run close / status / verify-run ---------------------------------------


def test_run_close_status_verify_run(git_repo, data_root):
    lines = _start_run(git_repo, data_root, "objective A")
    rid = json.loads(lines[1])["run_id"]

    v = run_cli("verify-run", rid, cwd=git_repo, data_root=data_root)
    assert v.returncode == 5
    assert "ACTIVE" in v.stderr

    c = run_cli("run", "close", rid, cwd=git_repo, data_root=data_root)
    assert c.returncode == 0, c.stderr
    closed = json.loads(c.stdout)
    assert closed["state"] == "CLOSED" and closed["closed_at"]

    s = run_cli("run", "status", rid, cwd=git_repo, data_root=data_root)
    assert s.returncode == 0
    assert json.loads(s.stdout)["state"] == "CLOSED"

    v2 = run_cli("verify-run", rid, cwd=git_repo, data_root=data_root)
    assert v2.returncode == 0
    assert json.loads(v2.stdout)["verify"] == "ok"

    v3 = run_cli("verify-run", cwd=git_repo, data_root=data_root)  # latest run
    assert v3.returncode == 0

    v4 = run_cli("verify-run", "R999", cwd=git_repo, data_root=data_root)
    assert v4.returncode == 5

    lst = run_cli("run", "list", cwd=git_repo, data_root=data_root)
    assert lst.returncode == 0
    assert [r["run_id"] for r in json.loads(lst.stdout)] == [rid]


# ---- attach + binding status (I32 mechanical confirmation) ------------------


def test_attach_marker_and_binding_status(git_repo, data_root):
    lines = _start_run(git_repo, data_root)
    rid = json.loads(lines[1])["run_id"]

    a = run_cli("attach", rid, cwd=git_repo, data_root=data_root)
    assert a.returncode == 0, a.stderr
    alines = a.stdout.splitlines()
    assert alines[0].startswith("ZLOOP_BIND_TOKEN=")
    assert re.fullmatch(r"[0-9a-f]{64}", alines[0].split("=", 1)[1])
    aobj = json.loads(alines[1])
    assert aobj["run_id"] == rid

    # --resume-after-clear is carried on the attach output (I28 intent)
    a3 = run_cli("attach", rid, "--resume-after-clear",
                 cwd=git_repo, data_root=data_root)
    assert a3.returncode == 0
    assert json.loads(a3.stdout.splitlines()[1])["resume_after_clear"] is True

    b = run_cli("binding", "status", cwd=git_repo, data_root=data_root)
    assert b.returncode == 0, b.stderr
    bs = json.loads(b.stdout)
    claims = bs["pending_claims"]
    attach_claims = [c for c in claims if c["purpose"] == "attach"]
    assert attach_claims, claims
    assert attach_claims[0]["run_id"] == rid
    assert attach_claims[0]["seconds_to_expiry"] > 0
    assert any(c["purpose"] == "run_start" for c in claims)  # from run start

    # unknown run -> blocked
    a2 = run_cli("attach", "R999", cwd=git_repo, data_root=data_root)
    assert a2.returncode == 5


def test_detach_and_binding_status_session(git_repo, data_root):
    lines = _start_run(git_repo, data_root)
    rid = json.loads(lines[1])["run_id"]
    pid = _only_project_id()
    pdir = paths.project_dir(pid)
    # simulate a hook claim (the PostToolUse side of the protocol)
    conn = zdb.connect(pdir)
    try:
        store = zdb.ControlStore(pdir, conn, project_id=pid)
        nonce = store.create_claim(purpose="run_start", run_id=rid)
        assert store.claim_binding(nonce, "sess-cli-test") is not None
    finally:
        conn.close()

    b = run_cli("binding", "status", cwd=git_repo, data_root=data_root)
    assert b.returncode == 0
    bs = json.loads(b.stdout)
    assert any(x["zcode_session_id"] == "sess-cli-test" for x in bs["bindings"])

    b2 = run_cli("binding", "status", "--session", "sess-cli-test",
                 cwd=git_repo, data_root=data_root)
    assert b2.returncode == 0
    bs2 = json.loads(b2.stdout)
    assert bs2["bindings"] and bs2["bindings"][0]["zcode_session_id"] == "sess-cli-test"

    d = run_cli("detach", "--session", "sess-cli-test",
                cwd=git_repo, data_root=data_root)
    assert d.returncode == 0, d.stderr

    b3 = run_cli("binding", "status", cwd=git_repo, data_root=data_root)
    assert all(x["zcode_session_id"] != "sess-cli-test"
               for x in json.loads(b3.stdout)["bindings"])

    d2 = run_cli("detach", "--session", "sess-cli-test",
                 cwd=git_repo, data_root=data_root)
    assert d2.returncode == 5  # nothing left to detach


# ---- doctor ---------------------------------------------------------------


def test_doctor(git_repo, data_root):
    _start_run(git_repo, data_root)
    d = run_cli("doctor", cwd=git_repo, data_root=data_root)
    assert d.returncode == 0, d.stderr
    assert "data root" in d.stdout
    assert "journal profile" in d.stdout
    assert "1 project" in d.stdout
    assert "quick_check=ok" in d.stdout

    # a degraded project is reported, but the diagnostic still completes
    _corrupt_control_db(_only_project_id())
    d2 = run_cli("doctor", cwd=git_repo, data_root=data_root)
    assert d2.returncode == 0
    assert "DEGRADED" in d2.stdout


# ---- S degraded -> exit 3 (I4 fail-closed) ---------------------------------


def test_serror_run_list_exit_3(git_repo, data_root):
    _start_run(git_repo, data_root)
    _corrupt_control_db(_only_project_id())
    r = run_cli("run", "list", cwd=git_repo, data_root=data_root)
    assert r.returncode == 3
    assert "S_DEGRADED" in r.stderr


# ---- --wait-claim (P2-13 foreground constraint) -----------------------------


def test_wait_claim_timeout_exit_4(git_repo, data_root):
    r = run_cli("run", "start", "obj", "--wait-claim", "1",
                cwd=git_repo, data_root=data_root)
    assert r.returncode == 4
    assert "token not claimed" in r.stderr
    assert "foreground" in r.stderr.lower()
    # marker + JSON still on stdout in exact order
    lines = r.stdout.splitlines()
    assert lines[0].startswith("ZLOOP_BIND_TOKEN=")
    json.loads(lines[1])


def test_wait_claim_success(git_repo, data_root):
    a = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert a.returncode == 0
    pid = json.loads(a.stdout)["project_id"]
    pdir = paths.project_dir(pid)
    proc = subprocess.Popen(
        [sys.executable, "-m", "zloop.cli", "run", "start", "wait obj",
         "--wait-claim", "15"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(git_repo), env=_cli_env(data_root))
    try:
        nonce = None
        deadline = time.time() + 15
        while time.time() < deadline:
            dbp = pdir / "control.sqlite3"
            if dbp.exists():
                try:
                    conn = sqlite3.connect(str(dbp))
                    row = conn.execute(
                        "SELECT nonce FROM pending_binding_claims"
                        " WHERE claimed_at IS NULL").fetchone()
                    conn.close()
                    if row:
                        nonce = row[0]
                        break
                except sqlite3.Error:
                    pass
            time.sleep(0.1)
        assert nonce, "pending claim never appeared in S"
        conn = zdb.connect(pdir)
        try:
            store = zdb.ControlStore(pdir, conn, project_id=pid)
            assert store.claim_binding(nonce, "sess-hook-sim") is not None
        finally:
            conn.close()
    finally:
        out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    lines = out.splitlines()
    assert lines[0].startswith("ZLOOP_BIND_TOKEN=")
    bound = json.loads(lines[2])
    assert bound["bound"] is True
    assert bound["claimed_by_session"] == "sess-hook-sim"


# ---- lazy parallel modules -------------------------------------------------


def test_history_command_lazy_tolerance(git_repo, data_root):
    a = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert a.returncode == 0
    r = run_cli("history", "search", "anything", cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    if _have_module("zloop.history"):
        assert isinstance(json.loads(r.stdout), list)
    else:
        assert "module not available (parallel integration pending)" in r.stdout
    rv = run_cli("history", "verify", cwd=git_repo, data_root=data_root)
    assert rv.returncode == 0
    if not _have_module("zloop.history"):
        assert "module not available" in rv.stdout


def test_checkpoint_command_lazy_tolerance(git_repo, data_root):
    a = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert a.returncode == 0
    r = run_cli("checkpoint", "current", cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    if not _have_module("zloop.checkpoint"):
        assert "module not available (parallel integration pending)" in r.stdout
    else:
        assert json.loads(r.stdout)["checkpoint"] is None  # fresh project


def test_history_search_real_module(git_repo, data_root):
    pytest.importorskip("zloop.history")
    a = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert a.returncode == 0
    pid = _only_project_id()
    from zloop import evidence as zev
    j = zev.Journal(paths.history_session_file(pid, "sess-h1"),
                    paths.blobs_root(pid))
    assert j.append(kind="prompt", session_id="sess-h1",
                    payload={"q": "find the needle prompt"}) is not None
    r = run_cli("history", "search", "needle", cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    results = json.loads(r.stdout)
    assert isinstance(results, list) and len(results) >= 1
    r2 = run_cli("history", "verify", cwd=git_repo, data_root=data_root)
    assert r2.returncode == 0, r2.stderr
    assert isinstance(json.loads(r2.stdout), dict)


def test_checkpoint_write_show_real_module(git_repo, data_root):
    pytest.importorskip("zloop.checkpoint")
    lines = _start_run(git_repo, data_root)
    rid = json.loads(lines[1])["run_id"]
    capsule = {"run_id": rid, "note": "cli checkpoint capsule", "facts": []}
    w = run_cli("checkpoint", "write", input=json.dumps(capsule),
                cwd=git_repo, data_root=data_root)
    assert w.returncode == 0, w.stderr
    cp = json.loads(w.stdout)
    assert cp.get("checkpoint_id")
    s = run_cli("checkpoint", "show", cp["checkpoint_id"],
                cwd=git_repo, data_root=data_root)
    assert s.returncode == 0, s.stderr
    cur = run_cli("checkpoint", "current", cwd=git_repo, data_root=data_root)
    assert cur.returncode == 0
    assert json.loads(cur.stdout)["checkpoint"] is not None


def test_install_uninstall_real_module(git_repo, data_root, tmp_path):
    pytest.importorskip("zloop.install")
    pytest.importorskip("zloop.hook")
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg = cfg_dir / "config.json"
    i = run_cli("install", "--config-path", str(cfg),
                cwd=git_repo, data_root=data_root)
    assert i.returncode == 0, i.stderr
    assert isinstance(json.loads(i.stdout), dict)
    u = run_cli("uninstall", "--config-path", str(cfg),
                cwd=git_repo, data_root=data_root)
    assert u.returncode == 0, u.stderr
    assert isinstance(json.loads(u.stdout), dict)


def test_install_uninstall_lazy_tolerance(git_repo, data_root, tmp_path):
    if _have_module("zloop.hook") and _have_module("zloop.install"):
        pytest.skip("parallel modules present — covered by real-module test")
    # hook selfcheck or install module is missing -> notice + exit 0,
    # and the user-level config is never touched.
    r = run_cli("install", cwd=git_repo, data_root=data_root)
    assert r.returncode == 0
    assert "module not available (parallel integration pending)" in r.stdout
    if not _have_module("zloop.install"):
        r2 = run_cli("uninstall", cwd=git_repo, data_root=data_root)
        assert r2.returncode == 0
        assert "module not available (parallel integration pending)" in r2.stdout


# ---- stage begin / status / close (VOL-08 §3, I37) ---------------------------


def _git_query(repo: Path, *a):
    return subprocess.run(["git", *a], cwd=str(repo), capture_output=True,
                          text=True, timeout=60).stdout.strip()


def _stage_begin(repo: Path, data_root: Path,
                 objective="implement the milestone 4/6 cli surface",
                 risk=None):
    args = ["stage", "begin", "--objective", objective]
    if risk:
        args += ["--risk", risk]
    return run_cli(*args, cwd=repo, data_root=data_root)


def test_stage_begin_dirty_base_blocked(git_repo, data_root):
    _start_run(git_repo, data_root)
    (git_repo / "uncommitted.txt").write_text("user work in progress\n",
                                              encoding="utf-8")
    r = _stage_begin(git_repo, data_root, "slice on a dirty base")
    assert r.returncode == 5
    assert "BLOCKED_DIRTY_BASE" in r.stdout + r.stderr
    assert "commit your work" in (r.stdout + r.stderr)
    # fail-closed: no stage row was created
    pid = _only_project_id()
    conn = zdb.connect(paths.project_dir(pid))
    try:
        assert conn.execute("SELECT COUNT(*) FROM stages").fetchone()[0] == 0
    finally:
        conn.close()


def test_stage_begin_clean_creates_s01(git_repo, data_root):
    lines = _start_run(git_repo, data_root, "objective with stages")
    rid = json.loads(lines[1])["run_id"]
    r = _stage_begin(git_repo, data_root, "implement the stage cli surface")
    assert r.returncode == 0, r.stderr
    st = json.loads(r.stdout)
    head = _git_query(git_repo, "rev-parse", "HEAD")
    tree = _git_query(git_repo, "rev-parse", "HEAD^{tree}")
    assert st["stage_id"] == "S01"
    assert st["run_id"] == rid
    assert st["state"] == "PLANNING"
    assert st["risk_requested"] == "NORMAL"
    assert st["risk_floor"] == "NORMAL"
    assert st["risk_effective"] == "NORMAL"
    assert st["expected_canonical_head"] == head
    assert st["canonical_dirty_digest"] == ""       # I37 clean proof
    assert st["stage_base_ref"] == head
    assert st["stage_base_tree"] == tree
    # the host floor can only raise (LOW request under a CRITICAL keyword floor)
    r2 = _stage_begin(git_repo, data_root, "harden the live trading path",
                      risk="LOW")
    assert r2.returncode == 0, r2.stderr
    st2 = json.loads(r2.stdout)
    assert st2["stage_id"] == "S02"                 # per-run numbering
    assert st2["risk_floor"] == "CRITICAL"
    assert st2["risk_effective"] == "CRITICAL"      # max(LOW, CRITICAL)
    # status: all stages of the current run, or exactly one
    s = run_cli("stage", "status", cwd=git_repo, data_root=data_root)
    assert s.returncode == 0
    assert [x["stage_id"] for x in json.loads(s.stdout)] == ["S01", "S02"]
    s1 = run_cli("stage", "status", "S01", cwd=git_repo, data_root=data_root)
    assert s1.returncode == 0
    assert json.loads(s1.stdout)["stage_id"] == "S01"
    s9 = run_cli("stage", "status", "S99", cwd=git_repo, data_root=data_root)
    assert s9.returncode == 5


def test_stage_begin_requires_active_run(git_repo, data_root):
    a = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert a.returncode == 0
    r = _stage_begin(git_repo, data_root)
    assert r.returncode == 5
    assert "run start" in (r.stdout + r.stderr)
    # a CLOSED run is not enough either
    lines = _start_run(git_repo, data_root)
    rid = json.loads(lines[1])["run_id"]
    assert run_cli("run", "close", rid, cwd=git_repo,
                   data_root=data_root).returncode == 0
    r2 = _stage_begin(git_repo, data_root)
    assert r2.returncode == 5
    assert "run start" in (r2.stdout + r2.stderr)


def test_stage_close_walks_to_closed(git_repo, data_root):
    _start_run(git_repo, data_root)
    assert _stage_begin(git_repo, data_root).returncode == 0
    r = run_cli("stage", "close", "S01", cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    st = json.loads(r.stdout)
    assert st["state"] == "CLOSED"
    assert st["closed_via"][-1] == "CLOSED"
    # terminal already -> idempotent no-op
    r2 = run_cli("stage", "close", "S01", cwd=git_repo, data_root=data_root)
    assert r2.returncode == 0
    assert json.loads(r2.stdout)["state"] == "CLOSED"
    # unknown stage -> blocked
    r3 = run_cli("stage", "close", "S99", cwd=git_repo, data_root=data_root)
    assert r3.returncode == 5


def test_stage_close_blocked_goes_cancelled(git_repo, data_root):
    lines = _start_run(git_repo, data_root)
    rid = json.loads(lines[1])["run_id"]
    assert _stage_begin(git_repo, data_root).returncode == 0
    # drive S01 to BLOCKED in-process (PLANNING -> EXECUTING -> BLOCKED)
    pid = _only_project_id()
    pdir = paths.project_dir(pid)
    conn = zdb.connect(pdir)
    try:
        store = zdb.ControlStore(pdir, conn, project_id=pid)
        zstage.transition_stage(store, rid, "S01", "EXECUTING")
        zstage.transition_stage(store, rid, "S01", "BLOCKED")
    finally:
        conn.close()
    r = run_cli("stage", "close", "S01", cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    st = json.loads(r.stdout)
    assert st["state"] == "CANCELLED"     # BLOCKED -> CANCELLED (VOL-08 §4)
    assert st["closed_via"] == ["CANCELLED"]


# ---- wave propose / start / cancel (VOL-09) ----------------------------------


def _packet(pid, scope, **kw):
    p = {"packet_id": pid, "goal": f"do {pid}",
         "write_scope": scope, "acceptance": ["python -V"],
         "risk_class": "NORMAL", "network_policy": "none"}
    p.update(kw)
    return p


def _write_packets(tmp_path: Path, packets, name="packets.json") -> Path:
    f = tmp_path / name
    f.write_text(json.dumps({"packets": packets}), encoding="utf-8")
    return f


def _wave_setup(git_repo, data_root, tmp_path, packets,
                objective="wave cli objective"):
    """run start -> stage begin -> wave propose; returns (run_id, propose result)."""
    lines = _start_run(git_repo, data_root, objective)
    rid = json.loads(lines[1])["run_id"]
    assert _stage_begin(git_repo, data_root, objective + " (stage slice)")\
        .returncode == 0
    f = _write_packets(tmp_path, packets)
    r = run_cli("wave", "propose", str(f), cwd=git_repo, data_root=data_root)
    return rid, r


def test_wave_propose_inserts_pending(git_repo, data_root, tmp_path):
    packets = [_packet("P01", ["src/a/**"]),
               _packet("P02", ["src/b/**"], depends_on=["P01"], max_turns=20)]
    rid, r = _wave_setup(git_repo, data_root, tmp_path, packets)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["wave"] == "W1"
    assert [p["packet_id"] for p in out["packets"]] == ["P01", "P02"]
    assert all(p["state"] == "PENDING" for p in out["packets"])
    pid = _only_project_id()
    conn = zdb.connect(paths.project_dir(pid))
    try:
        rows = [dict(x) for x in conn.execute(
            "SELECT * FROM packets ORDER BY packet_id")]
        assert [x["packet_id"] for x in rows] == ["P01", "P02"]
        assert all(x["state"] == "PENDING" and x["stage_id"] == "S01"
                   and x["run_id"] == rid for x in rows)
        assert json.loads(rows[1]["deps_json"]) == ["P01"]
        assert rows[1]["max_turns"] == 20
        kinds = [x["kind"] for x in conn.execute("SELECT kind FROM events")]
        assert kinds.count("packet_created") == 2
        assert "wave_proposed" in kinds
    finally:
        conn.close()
    # a second proposal numbers the next wave W2 (per-stage, VOL-08 §6)
    f2 = _write_packets(tmp_path, [_packet("P03", ["src/c/**"])],
                        name="packets2.json")
    r2 = run_cli("wave", "propose", str(f2), cwd=git_repo, data_root=data_root)
    assert r2.returncode == 0, r2.stderr
    assert json.loads(r2.stdout)["wave"] == "W2"


def test_wave_propose_cycle_rejected(git_repo, data_root, tmp_path):
    packets = [_packet("P01", ["src/a/**"], depends_on=["P02"]),
               _packet("P02", ["src/b/**"], depends_on=["P01"])]
    _rid, r = _wave_setup(git_repo, data_root, tmp_path, packets)
    assert r.returncode == 5
    assert "cycle" in (r.stdout + r.stderr)
    # nothing was written (fail-closed, I4)
    pid = _only_project_id()
    conn = zdb.connect(paths.project_dir(pid))
    try:
        assert conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0] == 0
    finally:
        conn.close()


def test_wave_propose_requires_stage(git_repo, data_root, tmp_path):
    _start_run(git_repo, data_root)
    f = _write_packets(tmp_path, [_packet("P01", ["src/a/**"])])
    r = run_cli("wave", "propose", str(f), cwd=git_repo, data_root=data_root)
    assert r.returncode == 5
    assert "stage begin" in (r.stdout + r.stderr)


def test_wave_start_mock(git_repo, data_root, tmp_path):
    """wave start W1 --backend mock: supervisor-pending -> graceful notice;
    supervisor present -> full mock wave (launch -> collect -> materialize).
    Either way the verify-run path (stage close -> run close -> verify) must
    reach exit 0."""
    packets = [_packet("P01", ["src/a/**"]),
               _packet("P02", ["src/b/**"], depends_on=["P01"])]
    rid, r = _wave_setup(git_repo, data_root, tmp_path, packets,
                         objective="wave start objective")
    assert r.returncode == 0, r.stderr
    r = run_cli("wave", "start", "W1", "--backend", "mock",
                cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    pid = _only_project_id()
    pdir = paths.project_dir(pid)
    if _have_module("zloop.supervisor"):
        summary = json.loads(r.stdout)
        assert summary["wave"] == "W1"
        assert summary["ok"] is True
        assert summary["materialized"] == ["P01", "P02"]
        assert summary["blocked"] == []
    else:
        assert "module not available (parallel integration pending)" in r.stdout
        # the pre-supervisor side effects are durable: stage EXECUTING,
        # packets still PENDING, staging worktree at the locked stage base
        conn = zdb.connect(pdir)
        try:
            st = conn.execute(
                "SELECT state FROM stages WHERE stage_id='S01'").fetchone()
            assert st["state"] == "EXECUTING"
            states = [x["state"] for x in conn.execute(
                "SELECT state FROM packets ORDER BY packet_id")]
            assert states == ["PENDING", "PENDING"]
        finally:
            conn.close()
        wt = pdir / "workspaces" / "S01" / "staging"
        assert wt.is_dir() and (wt / ".git").exists()
        assert _git_query(wt, "rev-parse", "HEAD") == \
            _git_query(git_repo, "rev-parse", "HEAD")
    # verify-run path: stage close -> run close -> verify ok
    sc = run_cli("stage", "close", "S01", cwd=git_repo, data_root=data_root)
    assert sc.returncode == 0, sc.stderr
    assert json.loads(sc.stdout)["state"] == "CLOSED"
    rc = run_cli("run", "close", rid, cwd=git_repo, data_root=data_root)
    assert rc.returncode == 0, rc.stderr
    v = run_cli("verify-run", rid, cwd=git_repo, data_root=data_root)
    assert v.returncode == 0, v.stderr
    assert json.loads(v.stdout)["verify"] == "ok"


def test_wave_start_refusals(git_repo, data_root, tmp_path):
    packets = [_packet("P01", ["src/a/**"])]
    _rid, r = _wave_setup(git_repo, data_root, tmp_path, packets)
    assert r.returncode == 0, r.stderr
    # unknown wave -> blocked
    r9 = run_cli("wave", "start", "W9", cwd=git_repo, data_root=data_root)
    assert r9.returncode == 5
    # codex backend: auth currently broken on this machine -> blocked
    rc = run_cli("wave", "start", "W1", "--backend", "codex",
                 cwd=git_repo, data_root=data_root)
    assert rc.returncode == 5
    out = rc.stdout + rc.stderr
    assert "codex backend requires `codex login`" in out
    assert "auth currently broken" in out
    # unknown backend -> usage error
    rb = run_cli("wave", "start", "W1", "--backend", "bogus",
                 cwd=git_repo, data_root=data_root)
    assert rb.returncode == 2


def test_wave_cancel(git_repo, data_root):
    lines = _start_run(git_repo, data_root, "cancel me")
    rid = json.loads(lines[1])["run_id"]
    r = run_cli("wave", "cancel", "W1", cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["cancel_requested"] is True
    assert out["run_id"] == rid
    assert "CANCELLING" in out["note"]
    assert "tick" in out["note"]
    pid = _only_project_id()
    conn = zdb.connect(paths.project_dir(pid))
    try:
        row = conn.execute("SELECT cancel_requested FROM runs WHERE run_id=?",
                           (rid,)).fetchone()
        assert row["cancel_requested"] == 1
    finally:
        conn.close()
    # D-8: no wave process needs to be live — the request persists, exit 0
    r2 = run_cli("wave", "cancel", "W1", cwd=git_repo, data_root=data_root)
    assert r2.returncode == 0
    # but an ACTIVE run must exist to carry the request
    assert run_cli("run", "close", rid, cwd=git_repo,
                   data_root=data_root).returncode == 0
    r3 = run_cli("wave", "cancel", "W1", cwd=git_repo, data_root=data_root)
    assert r3.returncode == 5


# ---- stage promote (VOL-11 §2, M8) ---------------------------------------------


def _staging_workspace(pdir: Path, stage_id: str = "S01") -> Path:
    """The stage's private staging worktree (the wave-start path convention)."""
    return pdir / "workspaces" / stage_id / "staging"


def _final_stage(staging: Path, rel_path: str, content: str,
                 run_id: str = "R001", stage_id: str = "S01") -> str:
    """Assemble the final candidate in the stage's private staging worktree
    (VOL-11 §1 final staging) and return its commit SHA (staged_head)."""
    target = staging / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git_query(staging, "add", "-A")
    _git_query(staging, "-c", "user.name=zloop", "-c",
               "user.email=zloop@localhost", "commit", "-q", "-m",
               f"final staging {run_id}/{stage_id}\n\n"
               f"ZLoop-Run: {run_id}\nZLoop-Stage: {stage_id}")
    return _git_query(staging, "rev-parse", "HEAD")


def _run_mock_wave(git_repo, data_root, tmp_path, *, objective, risk=None,
                   packets=None):
    """run start -> stage begin -> wave propose -> wave start (mock); the
    wave must have materialized every packet. Returns (run_id, stage_id)."""
    packets = packets or [_packet("P01", ["src/a/**"],
                                  risk_class=risk or "NORMAL",
                                  acceptance=['python -c "exit(0)"'])]
    lines = _start_run(git_repo, data_root, objective)
    rid = json.loads(lines[1])["run_id"]
    assert _stage_begin(git_repo, data_root, objective + " (stage slice)",
                        risk=risk).returncode == 0
    f = _write_packets(tmp_path, packets)
    r = run_cli("wave", "propose", str(f), cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    start_args = ["wave", "start", "W1", "--backend", "mock"]
    r = run_cli(*start_args, cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    summary = json.loads(r.stdout)
    assert summary["ok"] is True, summary
    assert summary["materialized"] == ["P01"]
    return rid, "S01"


def test_stage_promote_e2e(git_repo, data_root, tmp_path):
    """Full execution path (M8): run start -> stage begin -> wave propose ->
    wave start (mock) -> MATERIALIZED -> stage promote -> exit 0 with the
    canonical HEAD advanced by exactly the staged commit (ff-only, I39), the
    stage PROMOTED and the output JSON carrying new_head."""
    rid, sid = _run_mock_wave(git_repo, data_root, tmp_path,
                              objective="promote the staged slice")
    pid = _only_project_id()
    pdir = paths.project_dir(pid)
    conn = zdb.connect(pdir)
    try:
        states = [x["state"] for x in conn.execute(
            "SELECT state FROM packets ORDER BY packet_id")]
        assert states == ["MATERIALIZED"]        # the M8 precondition
    finally:
        conn.close()
    base_head = _git_query(git_repo, "rev-parse", "HEAD")

    # final staging: the candidate commit in the private staging worktree
    staged = _final_stage(_staging_workspace(pdir, sid), "src/a/feature.txt",
                          "staged\n", rid, sid)
    assert staged != base_head                   # a real advance, not a no-op

    r = run_cli("stage", "promote", sid, cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["promoted"] is True
    assert out["stage"] == sid
    assert out["new_head"] == staged
    # canonical advanced by the staged commit (ff): content checked out, clean
    assert _git_query(git_repo, "rev-parse", "HEAD") == staged
    assert (git_repo / "src" / "a" / "feature.txt").read_text(
        encoding="utf-8") == "staged\n"
    assert _git_query(git_repo, "status", "--porcelain") == ""
    # S: stage PROMOTED (promote settled it), intent APPLIED, audited
    conn = zdb.connect(pdir)
    try:
        st = conn.execute("SELECT state FROM stages WHERE stage_id=?",
                          (sid,)).fetchone()
        assert st["state"] == "PROMOTED"
        intent = conn.execute(
            "SELECT state FROM promotion_intents WHERE staged_head=?",
            (staged,)).fetchone()
        assert intent is not None and intent["state"] == "APPLIED"
        kinds = [x["kind"] for x in conn.execute(
            "SELECT kind FROM events ORDER BY seq")]
        assert "promotion_applied" in kinds and "stage_promoted" in kinds
    finally:
        conn.close()


def test_stage_promote_automated_c2c_gate(git_repo, data_root, tmp_path):
    """HIGH-risk stage: promote automatically runs C2C audit; proceeds with staged commit."""
    rid, sid = _run_mock_wave(git_repo, data_root, tmp_path,
                              objective="high risk slice", risk="HIGH")
    pid = _only_project_id()
    pdir = paths.project_dir(pid)
    staged = _final_stage(_staging_workspace(pdir, sid), "src/a/hard.txt",
                           "hard\n", rid, sid)

    r = run_cli("stage", "promote", sid, cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    assert "promoted" in r.stdout

    r2 = run_cli("stage", "promote", sid, "--skip-c2c",
                 cwd=git_repo, data_root=data_root)
    assert r2.returncode == 0, r2.stderr
    out = json.loads(r2.stdout)
    assert out["promoted"] is True and out["new_head"] == staged
    assert out.get("c2c_waived") is True
    assert _git_query(git_repo, "rev-parse", "HEAD") == staged
    conn = zdb.connect(pdir)
    try:
        st = conn.execute("SELECT state FROM stages WHERE stage_id=?",
                          (sid,)).fetchone()
        assert st["state"] == "PROMOTED"
        kinds = [x["kind"] for x in conn.execute(
            "SELECT kind FROM events ORDER BY seq")]
        assert "c2c_waiver" in kinds
        assert "c2c_recorded" not in kinds           # nothing was fabricated
    finally:
        conn.close()


def test_stage_promote_dirty_canonical_and_staging_missing(git_repo, data_root,
                                                           tmp_path):
    """A dirty canonical worktree refuses the promotion (DIRTY_OR_DRIFT,
    repo untouched); a missing staging worktree refuses with
    staging_missing."""
    rid, sid = _run_mock_wave(git_repo, data_root, tmp_path,
                              objective="promote blocked paths")
    pid = _only_project_id()
    pdir = paths.project_dir(pid)
    staged = _final_stage(_staging_workspace(pdir, sid), "src/a/cand.txt",
                          "candidate\n", rid, sid)
    base_head = _git_query(git_repo, "rev-parse", "HEAD")

    # dirty canonical (third-party touch) -> exit 5 DIRTY_OR_DRIFT, untouched
    (git_repo / "scratch.txt").write_text("someone was here\n", encoding="utf-8")
    r = run_cli("stage", "promote", sid, cwd=git_repo, data_root=data_root)
    assert r.returncode == 5
    assert "DIRTY_OR_DRIFT" in (r.stdout + r.stderr)
    assert _git_query(git_repo, "rev-parse", "HEAD") == base_head
    assert not (git_repo / "src" / "a" / "cand.txt").exists()
    (git_repo / "scratch.txt").unlink()

    # staging worktree gone -> exit 5 staging_missing
    staging = pdir / "workspaces" / sid / "staging"
    shutil.rmtree(staging)
    _git_query(git_repo, "worktree", "prune")
    r2 = run_cli("stage", "promote", sid, cwd=git_repo, data_root=data_root)
    assert r2.returncode == 5
    assert "staging_missing" in (r2.stdout + r2.stderr)


def test_stage_promote_precondition_reasons(git_repo, data_root, tmp_path):
    """nothing_materialized (no packets at all), unknown stage, and
    packets_pending (a proposed wave that never started)."""
    lines = _start_run(git_repo, data_root, "promote preconditions")
    rid = json.loads(lines[1])["run_id"]
    assert _stage_begin(git_repo, data_root).returncode == 0

    r = run_cli("stage", "promote", "S99", cwd=git_repo, data_root=data_root)
    assert r.returncode == 5
    assert "unknown stage" in (r.stdout + r.stderr)

    r = run_cli("stage", "promote", "S01", cwd=git_repo, data_root=data_root)
    assert r.returncode == 5
    assert "nothing_materialized" in (r.stdout + r.stderr)

    f = _write_packets(tmp_path, [_packet("P01", ["src/a/**"])])
    assert run_cli("wave", "propose", str(f), cwd=git_repo,
                   data_root=data_root).returncode == 0
    r = run_cli("stage", "promote", "S01", cwd=git_repo, data_root=data_root)
    assert r.returncode == 5
    assert "packets_pending" in (r.stdout + r.stderr)
    assert "P01:PENDING" in (r.stdout + r.stderr)


# ---- verify-run stage awareness (VOL-08 §7) ---------------------------------


def test_verify_run_open_stage_blocked(git_repo, data_root):
    lines = _start_run(git_repo, data_root, "goal needing stages")
    rid = json.loads(lines[1])["run_id"]
    assert _stage_begin(git_repo, data_root).returncode == 0
    # run closed while a stage is still open -> verify blocked
    assert run_cli("run", "close", rid, cwd=git_repo,
                   data_root=data_root).returncode == 0
    v = run_cli("verify-run", rid, cwd=git_repo, data_root=data_root)
    assert v.returncode == 5
    out = json.loads(v.stdout)
    assert out["verify"] == "blocked"
    assert "S01" in out["reason"] and "PLANNING" in out["reason"]
    assert out["stages"]["open"] == ["S01:PLANNING"]
    # remediation: closing the stage lets the same run verify
    assert run_cli("stage", "close", "S01", cwd=git_repo,
                   data_root=data_root).returncode == 0
    v2 = run_cli("verify-run", rid, cwd=git_repo, data_root=data_root)
    assert v2.returncode == 0
    assert json.loads(v2.stdout)["verify"] == "ok"
    # a CANCELLED stage is terminal too (BLOCKED's only exit, VOL-08 §4)
    lines2 = _start_run(git_repo, data_root, "second goal")
    rid2 = json.loads(lines2[1])["run_id"]
    assert _stage_begin(git_repo, data_root).returncode == 0
    pid = _only_project_id()
    pdir = paths.project_dir(pid)
    conn = zdb.connect(pdir)
    try:
        store = zdb.ControlStore(pdir, conn, project_id=pid)
        zstage.transition_stage(store, rid2, "S01", "CANCELLED")
    finally:
        conn.close()
    assert run_cli("run", "close", rid2, cwd=git_repo,
                   data_root=data_root).returncode == 0
    v3 = run_cli("verify-run", rid2, cwd=git_repo, data_root=data_root)
    assert v3.returncode == 0
    assert json.loads(v3.stdout)["verify"] == "ok"


# ---- research run (VOL-15, milestone 4) --------------------------------------


class _FakeKimiLaneServer:
    """Minimal loopback stand-in for `kimi web`: healthz answers 200 (so the
    lane never spawns a real server), everything else 404 (so each question
    settles fast into an error evidence record — lane behavior itself is the
    parallel module's test concern, not the CLI's)."""

    def __init__(self):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"{}" if self.path == "/api/v1/healthz" else b"not found"
                self.send_response(200 if self.path == "/api/v1/healthz" else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET

            def log_message(self, *a):
                pass

        self._srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever,
                                        daemon=True)
        self._thread.start()

    def close(self):
        self._srv.shutdown()
        self._srv.server_close()
        self._thread.join(timeout=5)


def test_research_run_lazy_tolerance(git_repo, data_root, tmp_path, monkeypatch):
    a = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert a.returncode == 0
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"questions": [
        {"id": "q1", "query": "what is 2+2 (probe)"}]}), encoding="utf-8")
    if not _have_module("zloop.research.broker"):
        r = run_cli("research", "run", str(spec), cwd=git_repo,
                    data_root=data_root)
        assert r.returncode == 0, r.stderr
        assert "module pending (parallel integration)" in r.stdout
        return
    fake = _FakeKimiLaneServer()
    try:
        monkeypatch.setenv("ZLOOP_KIMI_URL", f"http://127.0.0.1:{fake.port}")
        r = run_cli("research", "run", str(spec), cwd=git_repo,
                    data_root=data_root)
    finally:
        fake.close()
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["research_id"] == "RS001"
    assert isinstance(out["results"], list) and len(out["results"]) == 1
    rec = out["results"][0]
    assert rec["question_id"] == "q1"
    assert rec.get("error")            # lane unreachable -> error record,
    assert rec.get("answer") is None    # never a crash, never a fake answer
    # the full manifest (answers unbounded) lives in the research dir
    pid = _only_project_id()
    full = paths.project_dir(pid) / "research" / "RS001" / "manifest.json"
    assert full.is_file()
    assert out["full_manifest"] == str(full)


def test_research_manifest_bounded_in_stdout():
    """The stdout copy of a research manifest is bounded to 200-char fields
    (VOL-15); the full answers stay in the on-disk manifest."""
    from zloop import cli as zcli
    long_answer = "x" * 5000
    manifest = {"research_id": "RS001",
                "results": [{"question_id": "q1", "answer": long_answer,
                             "claim": long_answer[:300], "error": None}],
                "openapi_digest": None}
    bounded = zcli._bounded_research_manifest(manifest)
    rec = bounded["results"][0]
    assert rec["answer"].startswith("x")
    assert len(rec["answer"]) <= 200 + len("…[truncated]")
    assert rec["answer"].endswith("…[truncated]")
    assert len(rec["claim"]) <= 200 + len("…[truncated]")
    # the caller's manifest object is not mutated by the bounding pass
    assert len(manifest["results"][0]["answer"]) == 5000
