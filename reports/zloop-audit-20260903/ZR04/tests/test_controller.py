"""Controller token tests (D-8/D-20): death-proof takeover semantics.

``takeover_controller`` refuses while the recorded owner is mechanically
ALIVE (pid present + start match), allows when it is mechanically DEAD
(pid absent, or start mismatch = PID reuse), fails closed when the
owner's identity is ambiguous (no recorded start — I43), and CAS-guards
the probe/claim race via ``expected_old``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import db as zdb     # noqa: E402
from zloop import ids as zids   # noqa: E402


@pytest.fixture()
def store(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ZLOOP_DATA", str(tmp_path / "zloop-data"))
    sdir = tmp_path / "S"
    conn = zdb.connect(sdir, create=True)
    yield zdb.ControlStore(sdir, conn, project_id="testproj")
    conn.close()


def _run(store) -> str:
    return store.create_run("objective")


@pytest.fixture()
def dead_owner():
    """A real process that dies on exit: its pid + REAL start time are the
    mechanical identity a dead controller would leave behind in S."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        start = zdb.process_start_time(proc.pid)
        assert start, "probe of a live spawned process must succeed"
        proc.kill()
        proc.wait(timeout=15)
        assert proc.poll() is not None
    except Exception:
        proc.kill()
        proc.wait()
        raise
    return proc.pid, start


# ---- identity probes ----------------------------------------------------------


def test_process_identity_probes():
    start = zdb.process_start_time(os.getpid())
    assert isinstance(start, str) and start  # real ISO 8601 creation time
    assert zdb.process_start_time(999999999) is None  # absent pid: failure
    assert zdb.owner_alive(os.getpid(), None) is None  # no recorded start
    assert zdb.owner_alive(os.getpid(), start) is True  # live + matching
    # same pid, wrong start: the recorded owner cannot be this process
    assert zdb.owner_alive(os.getpid(), "2001-01-01T00:00:00.0000000+00:00") \
        is False
    # absent pid with a recorded start: mechanically dead
    assert zdb.owner_alive(999999999, start) is False


# ---- (a) dead owner: takeover succeeds ----------------------------------------


def test_dead_owner_takeover_succeeds(store, dead_owner):
    run_id = _run(store)
    old_pid, old_start = dead_owner
    old = zids.new_nonce()
    assert store.claim_controller(run_id, nonce=old, pid=old_pid,
                                  pid_start=old_start)
    new = zids.new_nonce()
    res = store.takeover_controller(run_id, nonce=new, pid=os.getpid(),
                                    pid_start=zdb.process_start_time(os.getpid()),
                                    expected_old=old)
    assert res == {"ok": True, "nonce": new, "takeover": True}
    ctrl = store.controller(run_id)
    assert ctrl["controller_nonce"] == new
    assert ctrl["controller_pid"] == os.getpid()


# ---- (b) live self: second takeover refused -----------------------------------


def test_live_owner_takeover_refused(store):
    run_id = _run(store)
    old = zids.new_nonce()
    own_start = zdb.process_start_time(os.getpid())
    assert own_start
    assert store.claim_controller(run_id, nonce=old, pid=os.getpid(),
                                  pid_start=own_start)
    res = store.takeover_controller(run_id, nonce=zids.new_nonce(),
                                    pid=os.getpid(), pid_start=own_start,
                                    expected_old=old)
    assert res == {"ok": False, "reason": "owner_alive"}
    # the live owner keeps the token (I5: never wrestle)
    assert store.controller(run_id)["controller_nonce"] == old


# ---- (c) PID reuse: live pid + wrong start = the old owner is dead ------------


def test_pid_reuse_live_pid_wrong_start_treated_as_dead(store):
    run_id = _run(store)
    old = zids.new_nonce()
    # a LIVE pid (ours) carrying a fabricated creation time: the recorded
    # owner cannot be this process — the PID was reused after it died
    assert store.claim_controller(run_id, nonce=old, pid=os.getpid(),
                                  pid_start="2001-01-01T00:00:00.0000000+00:00")
    new = zids.new_nonce()
    res = store.takeover_controller(run_id, nonce=new, pid=os.getpid(),
                                    expected_old=old)
    assert res["ok"] is True and res["takeover"] is True
    assert store.controller(run_id)["controller_nonce"] == new


# ---- (d) ambiguous (pid_start None recorded): refused -------------------------


def test_ambiguous_owner_fails_closed(store):
    run_id = _run(store)
    old = zids.new_nonce()
    assert store.claim_controller(run_id, nonce=old, pid=os.getpid(),
                                  pid_start=None)
    res = store.takeover_controller(run_id, nonce=zids.new_nonce(),
                                    pid=os.getpid(), expected_old=old)
    assert res == {"ok": False, "reason": "ambiguous_fail_closed"}  # I43
    assert store.controller(run_id)["controller_nonce"] == old


# ---- guards -------------------------------------------------------------------


def test_takeover_of_free_run_is_a_fresh_claim(store):
    run_id = _run(store)
    nonce = zids.new_nonce()
    res = store.takeover_controller(run_id, nonce=nonce, pid=os.getpid(),
                                    pid_start=None, expected_old="stale")
    assert res == {"ok": True, "nonce": nonce, "takeover": False}
    assert store.controller(run_id)["controller_nonce"] == nonce


def test_takeover_unknown_run_fails(store):
    res = store.takeover_controller("R999", nonce=zids.new_nonce(),
                                    pid=os.getpid(), expected_old="x")
    assert res == {"ok": False, "reason": "unknown_run"}


def test_takeover_with_stale_expected_old_cas_fails(store, dead_owner):
    run_id = _run(store)
    old_pid, old_start = dead_owner
    old = zids.new_nonce()
    assert store.claim_controller(run_id, nonce=old, pid=old_pid,
                                  pid_start=old_start)
    # the owner is dead, but our knowledge of the token is stale: the CAS
    # must fail instead of taking over against a nonce we never saw
    res = store.takeover_controller(run_id, nonce=zids.new_nonce(),
                                    pid=os.getpid(), expected_old="not-old")
    assert res == {"ok": False, "reason": "cas_failed"}
    assert store.controller(run_id)["controller_nonce"] == old
