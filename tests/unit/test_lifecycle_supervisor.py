import json
import importlib.util
import os
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path

import pytest


PKG = Path(__file__).resolve().parents[2]
SUPERVISOR = PKG / "harness" / "lifecycle_supervisor.py"


def load_supervisor_module():
    spec = importlib.util.spec_from_file_location("lifecycle_supervisor_under_test", SUPERVISOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def invoke(tmp_path, packet, code, timeout=5.0, grace=0.2, run_id=None,
           attempt=0, parent_session_id=None, report=None, publish_report=None):
    data = tmp_path / "data"
    report = report or data / "reports" / packet / "report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    run_id = run_id or "%s-run-%d" % (packet, attempt)
    cmd = [sys.executable, str(SUPERVISOR), "--data-dir", str(data),
           "--packet", packet, "--run-id", run_id, "--attempt", str(attempt),
           "--task-name", "测试任务 " + packet,
           "--model", "test/explicit-model",
           "--cwd", str(tmp_path), "--stdout", str(report.parent / "events.jsonl"),
           "--stderr", str(report.parent / "stderr.log"), "--report", str(report),
           "--timeout", str(timeout), "--grace", str(grace)]
    if publish_report:
        cmd += ["--publish-report", str(publish_report)]
    if parent_session_id:
        cmd += ["--parent-session-id", parent_session_id]
    cmd += ["--", sys.executable, "-c", code]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15), data, report


def events(data):
    path = data / "events.ndjson"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def roster(data, packet):
    doc = json.loads((data / "lifecycle" / "exec_roster.json").read_text(encoding="utf-8"))
    return doc["jobs"][packet]


def test_normal_exit_with_report_emits_stop(tmp_path):
    report = tmp_path / "data" / "reports" / "ok" / "report.json"
    code = "from pathlib import Path; Path(%r).write_text('{}', encoding='utf-8')" % str(report)
    result, data, _ = invoke(tmp_path, "ok", code)
    assert result.returncode == 0, result.stderr
    assert roster(data, "ok")["state"] == "completed"
    assert [x["event"] for x in events(data)].count("subagent_stop") == 1


def test_nonzero_exit_is_visible_and_stderr_is_retained(tmp_path):
    result, data, _ = invoke(tmp_path, "bad", "import sys; print('boom', file=sys.stderr); sys.exit(7)")
    assert result.returncode == 7
    item = roster(data, "bad")
    assert item["state"] == "failed" and item["exit_code"] == 7
    failed = [x for x in events(data) if x["event"] == "exec_failed"]
    assert len(failed) == 1 and "boom" in failed[0]["detail"]["stderr_tail"]


def test_timeout_kills_descendant_process_tree(tmp_path):
    marker = tmp_path / "grandchild-survived.txt"
    grandchild = "import time; from pathlib import Path; time.sleep(1); Path(%r).write_text('bad')" % str(marker)
    child = ("import subprocess,sys,time; "
             "subprocess.Popen([sys.executable,'-c',%r]); time.sleep(30)" % grandchild)
    result, data, _ = invoke(tmp_path, "hang", child, timeout=0.25, grace=0.1)
    assert result.returncode == 124
    assert roster(data, "hang")["state"] == "timed_out"
    assert [x["event"] for x in events(data)].count("timeout") == 1
    time.sleep(1.2)
    assert not marker.exists(), "grandchild escaped the supervisor process boundary"


def test_retry_generation_emits_terminal_event_for_each_attempt(tmp_path):
    first, data, _ = invoke(tmp_path, "retry", "import sys; sys.exit(3)",
                            run_id="retry-a0", attempt=0)
    second, _, _ = invoke(tmp_path, "retry", "import sys; sys.exit(4)",
                          run_id="retry-a1", attempt=1)
    assert first.returncode == 3 and second.returncode == 4
    failed = [x for x in events(data) if x["event"] == "exec_failed"]
    assert [(x["run_id"], x["attempt"]) for x in failed] == [
        ("retry-a0", 0), ("retry-a1", 1)]


def test_late_old_generation_cannot_clobber_newer_run(tmp_path):
    module = load_supervisor_module()
    store = module.Store(tmp_path / "data")
    store.update("packet", run_id="old", attempt=0, state="starting", started_at=1.0)
    store.update("packet", run_id="new", attempt=1, state="starting", started_at=2.0)
    row = store.update("packet", run_id="old", attempt=0,
                       state="completed", exit_code=0)
    assert row["run_id"] == "new"
    assert row["state"] == "starting"
    assert row.get("exit_code") is None


def test_new_generation_clears_prior_terminal_diagnostics(tmp_path):
    module = load_supervisor_module()
    store = module.Store(tmp_path / "data")
    store.update("packet", run_id="old", attempt=0, state="starting", started_at=1.0)
    store.update("packet", run_id="old", attempt=0, state="failed", exit_code=7,
                 failure={"why": "old"}, stop_reason="old_stop",
                 published_report="old.json")
    row = store.update("packet", run_id="new", attempt=1,
                       state="starting", started_at=2.0)
    assert row["run_id"] == "new" and row["state"] == "starting"
    assert not ({"exit_code", "failure", "stop_reason", "published_report"} & row.keys())


def test_new_generation_retains_prior_terminal_evidence_in_history(tmp_path):
    module = load_supervisor_module()
    store = module.Store(tmp_path / "data")
    store.update("packet", run_id="old", attempt=0,
                 state="starting", started_at=1.0)
    store.update("packet", run_id="old", attempt=0,
                 state="completed", exit_code=0,
                 published_report="old-report.json")
    row = store.update("packet", run_id="new", attempt=1,
                       state="starting", started_at=2.0)

    old_terminal = [item for item in row["history"]
                    if item.get("run_id") == "old"
                    and item.get("state") == "completed"]
    assert len(old_terminal) == 1
    assert old_terminal[0]["exit_code"] == 0
    assert old_terminal[0]["published_report"] == "old-report.json"


def test_atomic_json_retries_transient_windows_permission_error(tmp_path,
                                                                monkeypatch):
    module = load_supervisor_module()
    target = tmp_path / "roster.json"
    real_replace = module.os.replace
    calls = {"count": 0}

    def flaky_replace(source, destination):
        calls["count"] += 1
        if calls["count"] <= 3:
            raise PermissionError(5, "transient target hold", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", flaky_replace)
    module.atomic_json(target, {"ok": True}, replace_timeout_s=1.0)
    assert calls["count"] == 4
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert not list(tmp_path.glob("roster.json.*.tmp"))


def test_atomic_json_fails_visible_after_bounded_replace_timeout(tmp_path,
                                                                 monkeypatch):
    module = load_supervisor_module()
    target = tmp_path / "roster.json"

    def blocked_replace(_source, destination):
        raise PermissionError(5, "persistent target hold", str(destination))

    monkeypatch.setattr(module.os, "replace", blocked_replace)
    with pytest.raises(module.LifecycleError, match="remained blocked"):
        module.atomic_json(target, {"ok": False}, replace_timeout_s=0.01)
    assert not target.exists()
    assert not list(tmp_path.glob("roster.json.*.tmp"))


def test_locked_fails_visible_after_bounded_contention(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows msvcrt contention contract")
    module = load_supervisor_module()
    import msvcrt

    def always_busy(_fd, mode, _size):
        if mode == msvcrt.LK_NBLCK:
            raise OSError(36, "resource deadlock avoided")

    monkeypatch.setattr(msvcrt, "locking", always_busy)
    with pytest.raises(module.LifecycleError, match="lock remained busy"):
        with module.locked(tmp_path / "busy.lock", timeout_s=0.01):
            pass


def test_store_multiprocess_updates_preserve_all_jobs(tmp_path):
    """Exercise the real file lock + replace protocol across 16 processes."""
    data = tmp_path / "data"
    code = (
        "import importlib.util,pathlib,sys;"
        "spec=importlib.util.spec_from_file_location('ls',sys.argv[1]);"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "s=m.Store(pathlib.Path(sys.argv[2]));pid=sys.argv[3];"
        "[s.update(pid,run_id=pid+'-run',attempt=0,state='running',"
        "heartbeat_at=i) for i in range(20)]"
    )
    processes = [subprocess.Popen(
        [sys.executable, "-c", code, str(SUPERVISOR), str(data), f"p{i}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(16)]
    failures = []
    for proc in processes:
        stdout, stderr = proc.communicate(timeout=30)
        if proc.returncode != 0:
            failures.append((proc.returncode, stdout, stderr))
    assert not failures
    doc = json.loads((data / "lifecycle" / "exec_roster.json").read_text(
        encoding="utf-8"))
    assert set(doc["jobs"]) == {f"p{i}" for i in range(16)}
    assert all(row["state"] == "running" for row in doc["jobs"].values())
    assert not list((data / "lifecycle").glob("exec_roster.json.*.tmp"))


def test_post_spawn_supervisor_error_is_never_misclassified_spawn_failed(
        tmp_path, monkeypatch):
    module = load_supervisor_module()
    updates = []

    class FakeStore:
        def __init__(self, _data_dir):
            pass

        def update(self, _packet, **fields):
            updates.append(dict(fields))
            return dict(fields)

        def append_event_once(self, _packet, _run_id, _attempt, event, _detail):
            if event == "exec_spawned":
                raise RuntimeError("injected post-spawn infrastructure failure")
            return True

    class FakeBoundary:
        pid = 4242

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    boundary = FakeBoundary()
    monkeypatch.setattr(module, "Store", FakeStore)
    monkeypatch.setattr(module, "spawn_boundary", lambda *_args: boundary)
    monkeypatch.setattr(module, "proc_start_ticks", lambda _pid: 1)
    args = Namespace(
        data_dir=tmp_path / "data", command=["--", "fake-worker"],
        packet="packet", run_id="packet-run", attempt=0,
        task_name="post-spawn-classification", role="worker",
        model="test/model", plane="Windows CLI", cwd=tmp_path,
        parent_session_id=None, stdout=tmp_path / "stdout.log",
        stderr=tmp_path / "stderr.log", report=tmp_path / "report.json",
        publish_report=None, timeout=30.0, grace=0.1,
        l2_idem_key=None, l2_revision=None)
    assert module.run(args) == 1
    assert boundary.closed is True
    states = [row.get("state") for row in updates]
    assert states[:2] == ["starting", "running"]
    assert states[-1] == "failed"
    assert "spawn_failed" not in states
    assert updates[-1]["failure"]["phase"] == "post_spawn"


def test_adhoc_terminal_uses_packet_only_refill(tmp_path, monkeypatch):
    module = load_supervisor_module()
    calls = []
    import refill_consumer_v2
    monkeypatch.setattr(refill_consumer_v2, "schedule_run",
                        lambda root, *, source: calls.append((root, source)) or
                        {"status": "scheduled"})
    monkeypatch.setattr(module, "schedule_epilogue",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("adhoc terminal must not run full epilogue")))
    report = tmp_path / "data" / "reports" / "adhoc-test" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}", encoding="utf-8")
    args = Namespace(
        data_dir=tmp_path / "data", command=["--", sys.executable, "-c", "pass"],
        packet="adhoc-test", run_id="adhoc-test-run", attempt=0,
        task_name="adhoc", role="worker", model="test/model", plane="Windows CLI",
        cwd=tmp_path, parent_session_id=None, stdout=tmp_path / "stdout.log",
        stderr=tmp_path / "stderr.log", report=report, publish_report=None,
        timeout=30.0, grace=0.1, l2_idem_key=None, l2_revision=None)
    assert module.run(args) == 0
    assert calls == [(tmp_path, "adhoc_lifecycle_supervisor")]


def test_schedule_epilogue_coalesces_without_losing_request(tmp_path, monkeypatch):
    module = load_supervisor_module()
    orchestration = tmp_path / "data" / "orchestration"
    orchestration.mkdir(parents=True)
    (orchestration / "terminal_epilogue.pid").write_text(
        json.dumps({"pid": 1234, "proc_start_ticks": 77}), encoding="utf-8")
    monkeypatch.setattr(module, "runner_generation_alive",
                        lambda pid, ticks: (pid, ticks) == (1234, 77))
    result = module.schedule_epilogue(tmp_path, "second-edge")
    requests = json.loads((orchestration / "terminal_requests.json").read_text())
    assert result["status"] == "coalesced"
    assert requests["requested_seq"] == 1
    assert requests["consumed_seq"] == 0


def test_stale_cancel_generation_does_not_cancel_retry(tmp_path):
    data = tmp_path / "data"
    cancel = data / "lifecycle" / "cancel" / "fresh.json"
    cancel.parent.mkdir(parents=True)
    cancel.write_text(json.dumps({"reason": "parent_stop", "run_id": "old", "attempt": 0}))
    report = data / "reports" / "fresh" / "report.json"
    code = "from pathlib import Path; Path(%r).write_text('{}')" % str(report)
    result, _, _ = invoke(tmp_path, "fresh", code, run_id="new", attempt=1)
    assert result.returncode == 0, result.stderr


def test_current_cancel_is_consumed_and_emits_exec_failed(tmp_path):
    data = tmp_path / "data"
    cancel = data / "lifecycle" / "cancel" / "cancelled.json"
    cancel.parent.mkdir(parents=True)
    cancel.write_text(json.dumps({"reason": "parent_stop", "run_id": "current", "attempt": 2}))
    result, _, _ = invoke(tmp_path, "cancelled", "import time; time.sleep(30)",
                          timeout=30, run_id="current", attempt=2)
    assert result.returncode == 124
    assert not cancel.exists()
    failed = [x for x in events(data) if x["event"] == "exec_failed"]
    assert failed[-1]["run_id"] == "current" and failed[-1]["detail"]["cancelled"] is True


def test_worktree_report_is_atomically_published_to_root(tmp_path):
    local = tmp_path / "worktree" / "data" / "reports" / "copy" / "local.json"
    local.parent.mkdir(parents=True)
    published = tmp_path / "data" / "reports" / "copy" / "report.json"
    code = "from pathlib import Path; Path(%r).write_text('{\"status\":\"done\"}')" % str(local)
    result, _, _ = invoke(tmp_path, "copy", code, report=local,
                          publish_report=published)
    assert result.returncode == 0, result.stderr
    assert json.loads(published.read_text())["status"] == "done"


def test_plain_codex_final_is_wrapped_as_machine_report(tmp_path):
    local = tmp_path / "worktree" / "data" / "reports" / "plain" / "local.txt"
    local.parent.mkdir(parents=True)
    published = tmp_path / "data" / "reports" / "plain" / "report.json"
    code = "from pathlib import Path; Path(%r).write_text('plain result')" % str(local)
    result, _, _ = invoke(tmp_path, "plain", code, report=local,
                          publish_report=published, run_id="plain-run")
    assert result.returncode == 0, result.stderr
    value = json.loads(published.read_text(encoding="utf-8"))
    assert value == {"packet_id": "plain", "status": "done",
                     "run_id": "plain-run", "summary": "plain result"}


def test_timeout_kills_sigterm_immune_group_member_after_leader_exits(tmp_path):
    if os.name == "nt":
        return
    marker = tmp_path / "immune-survived.txt"
    grandchild = ("import signal,time; from pathlib import Path; "
                  "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(1); "
                  "Path(%r).write_text('bad')" % str(marker))
    child = ("import subprocess,sys,time; "
             "subprocess.Popen([sys.executable,'-c',%r]); time.sleep(30)" % grandchild)
    result, _, _ = invoke(tmp_path, "immune", child, timeout=0.25, grace=0.1)
    assert result.returncode == 124
    time.sleep(1.2)
    assert not marker.exists()


if os.name == "nt":
    SPEC = importlib.util.spec_from_file_location("lifecycle_supervisor_win_test", SUPERVISOR)
    WIN_MOD = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(WIN_MOD)

    class FakeKernel32:
        def __init__(self, fail=()):
            self.fail = set(fail)
            self.calls = []

        def _record(self, name, *args):
            self.calls.append((name, args))

        def CreateJobObjectW(self, *args):
            self._record("CreateJobObjectW", *args)
            return 0 if "CreateJobObjectW" in self.fail else 11

        def SetInformationJobObject(self, *args):
            self._record("SetInformationJobObject", *args)
            return "SetInformationJobObject" not in self.fail

        def CreateProcessW(self, *args):
            self._record("CreateProcessW", *args)
            if "CreateProcessW" in self.fail:
                return False
            info = args[-1]._obj
            info.hProcess, info.hThread, info.dwProcessId, info.dwThreadId = 21, 22, 4242, 4243
            return True

        def AssignProcessToJobObject(self, *args):
            self._record("AssignProcessToJobObject", *args)
            return "AssignProcessToJobObject" not in self.fail

        def ResumeThread(self, *args):
            self._record("ResumeThread", *args)
            return 0xFFFFFFFF if "ResumeThread" in self.fail else 1

        def TerminateProcess(self, *args):
            self._record("TerminateProcess", *args)
            return "TerminateProcess" not in self.fail

        def CloseHandle(self, *args):
            self._record("CloseHandle", *args)
            return True

    def make_windows_boundary(tmp_path, fake):
        return WIN_MOD.WindowsBoundary(
            [sys.executable, "-c", "pass"], tmp_path,
            tmp_path / "stdout.log", tmp_path / "stderr.log",
            _kernel32=fake,
            _win_error_fn=lambda action: WIN_MOD.LifecycleError(action))

    def test_windows_assign_failure_terminates_suspended_child_before_close(tmp_path):
        fake = FakeKernel32({"AssignProcessToJobObject"})
        with pytest.raises(WIN_MOD.LifecycleError, match="AssignProcessToJobObject"):
            make_windows_boundary(tmp_path, fake)
        names = [name for name, _ in fake.calls]
        assert names.index("TerminateProcess") < names.index("CloseHandle")
        closed = [args[0] for name, args in fake.calls if name == "CloseHandle"]
        assert closed == [21, 22, 11]

    def test_windows_terminate_fallback_failure_is_combined_and_still_closes(tmp_path):
        fake = FakeKernel32({"AssignProcessToJobObject", "TerminateProcess"})
        with pytest.raises(WIN_MOD.LifecycleError) as caught:
            make_windows_boundary(tmp_path, fake)
        assert "AssignProcessToJobObject" in str(caught.value)
        assert "TerminateProcess" in str(caught.value)
        closed = [args[0] for name, args in fake.calls if name == "CloseHandle"]
        assert closed == [21, 22, 11]

    def test_windows_set_job_failure_closes_job_without_child(tmp_path):
        fake = FakeKernel32({"SetInformationJobObject"})
        with pytest.raises(WIN_MOD.LifecycleError, match="SetInformationJobObject"):
            make_windows_boundary(tmp_path, fake)
        assert [name for name, _ in fake.calls] == [
            "CreateJobObjectW", "SetInformationJobObject", "CloseHandle"]
        assert fake.calls[-1][1] == (11,)
