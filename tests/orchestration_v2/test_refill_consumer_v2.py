from __future__ import annotations

import json

import pytest

from tests.orchestration_v2.conftest import make_root, write_packet
from orchestration_common import LoopPaths
import refill_consumer_v2
from refill_consumer_v2 import (build_manifest, drain_requested, run_once,
                                schedule_followup_if_debt,
                                schedule_retry_if_debt, schedule_run, select_tasks)
from refill_controller_v2 import RefillControllerV2


def _ledger(root, packets):
    path = root / "data" / "progress_ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"packets": packets}), encoding="utf-8")


def test_empty_ledger_never_emits_manifest(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    _ledger(root, {})
    ctl = RefillControllerV2(paths)
    ctl.queue_sync_ledger()
    state = ctl.recompute(emit=False)
    assert state["deficit"]["total"] == 0
    assert build_manifest(paths, state) is None


def test_manifest_contains_only_real_bounded_packets(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    for pid in ("w1", "w2", "k1", "terminal"):
        write_packet(root, pid)
    _ledger(root, {
        "w1": {"state": "DISPATCHABLE", "role": "worker"},
        "w2": {"state": "DISPATCHABLE", "role": "worker"},
        "k1": {"state": "L2_VERIFY"},
        "terminal": {"state": "MERGED", "role": "worker"},
    })
    state = {"deficit": {"total": 2, "v4": 1, "k3": 1},
             "policy_version": "test"}
    tasks = select_tasks(paths, state)
    assert [(x["packet_id"], x["role"]) for x in tasks] == [
        ("w1", "worker")]
    manifest = json.loads(build_manifest(paths, state).read_text(encoding="utf-8"))
    assert len(manifest["tasks"]) == 1
    assert all("prompt" not in task and "cwd" not in task
               for task in manifest["tasks"])


def test_missing_packet_file_fails_visible(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    _ledger(root, {"missing": {"state": "DISPATCHABLE", "role": "worker"}})
    state = {"deficit": {"total": 1, "v4": 1, "k3": 0}}
    try:
        select_tasks(paths, state)
    except RuntimeError as exc:
        assert "packet file missing" in str(exc)
    else:
        raise AssertionError("missing packet was silently converted to a task")


def test_dispatchable_without_explicit_role_fails_visible(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    write_packet(root, "p1")
    _ledger(root, {"p1": {"state": "DISPATCHABLE"}})
    state = {"deficit": {"total": 1, "v4": 1, "k3": 0}}
    try:
        select_tasks(paths, state)
    except RuntimeError as exc:
        assert "explicit LOOP role" in str(exc)
    else:
        raise AssertionError("roleless packet inherited a model")


def test_pool_hint_role_conflict_fails_visible(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    write_packet(root, "p1")
    _ledger(root, {"p1": {"state": "DISPATCHABLE", "role": "verifier",
                           "pool_hint": "v4"}})
    state = {"deficit": {"total": 1, "v4": 1, "k3": 0}}
    try:
        select_tasks(paths, state)
    except RuntimeError as exc:
        assert "pool/role conflict" in str(exc)
    else:
        raise AssertionError("pool/role conflict was accepted")


def test_release_review_is_not_ordinary_refill_work(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    write_packet(root, "rr-wave1")
    _ledger(root, {"rr-wave1": {"state": "DISPATCHABLE", "role": "reviewer",
                                 "release_review": True}})
    state = {"deficit": {"total": 1, "v4": 0, "k3": 1}}
    assert select_tasks(paths, state) == []


def test_parent_provenance_propagates_without_execution_overrides(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    write_packet(root, "p1")
    _ledger(root, {"p1": {"state": "DISPATCHABLE", "role": "worker",
                            "parent_enabled": True,
                            "parent_session_id": "parent-1",
                            "manifest_id": "manifest-1"}})
    (paths.refill_dir / "parent_sessions.json").parent.mkdir(parents=True, exist_ok=True)
    (paths.refill_dir / "parent_sessions.json").write_text(json.dumps({"parents": {
        "parent-1": {"active": True, "manifest_id": "manifest-1"}}}),
        encoding="utf-8")
    tasks = select_tasks(paths, {
        "deficit": {"total": 1, "v4": 1, "k3": 0},
        "parents": {"parent-1": {
            "spawnable": {"total": 1, "v4": 1, "k3": 0}}},
    })
    assert tasks == [{"task_id": "p1", "task_name": "test goal",
                      "packet_id": "p1", "role": "worker",
                      "parent_session_id": "parent-1",
                      "manifest_id": "manifest-1"}]
    assert all(key not in tasks[0] for key in ("prompt", "cwd", "sandbox"))


@pytest.mark.parametrize(("registered_manifest", "packet_manifest", "selected"), [
    (None, None, True),
    ("manifest-1", "manifest-1", True),
    ("manifest-2", "manifest-1", False),
    ("manifest-1", None, False),
])
def test_parent_selection_uses_strict_manifest_generation(
        tmp_path, registered_manifest, packet_manifest, selected):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    write_packet(root, "p1", parent_enabled=True)
    entry = {"state": "DISPATCHABLE", "role": "worker",
             "parent_enabled": True, "parent_session_id": "parent-1"}
    parent = {"active": True}
    if registered_manifest is not None:
        parent["manifest_id"] = registered_manifest
    if packet_manifest is not None:
        entry["manifest_id"] = packet_manifest
    _ledger(root, {"p1": entry})
    paths.refill_dir.mkdir(parents=True, exist_ok=True)
    (paths.refill_dir / "parent_sessions.json").write_text(
        json.dumps({"parents": {"parent-1": parent}}), encoding="utf-8")
    tasks = select_tasks(paths, {
        "deficit": {"total": 1, "v4": 1, "k3": 0},
        "parents": {"parent-1": {
            "spawnable": {"total": 1, "v4": 1, "k3": 0}}},
    })

    assert bool(tasks) is selected


def test_inactive_parent_is_not_selected(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    write_packet(root, "p1", parent_enabled=True)
    _ledger(root, {"p1": {"state": "DISPATCHABLE", "role": "worker",
                            "parent_enabled": True,
                            "parent_session_id": "parent-1"}})
    (paths.refill_dir / "parent_sessions.json").parent.mkdir(parents=True, exist_ok=True)
    (paths.refill_dir / "parent_sessions.json").write_text(
        json.dumps({"parents": {"parent-1": {"active": False}}}), encoding="utf-8")
    assert select_tasks(paths, {"deficit": {"total": 1, "v4": 1, "k3": 0}}) == []


def test_parent_packets_fail_closed_without_controller_parent_debt(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    write_packet(root, "p1", parent_enabled=True)
    _ledger(root, {"p1": {"state": "DISPATCHABLE", "role": "worker",
                            "parent_enabled": True,
                            "parent_session_id": "parent-1",
                            "manifest_id": "manifest-1"}})
    paths.refill_dir.mkdir(parents=True, exist_ok=True)
    (paths.refill_dir / "parent_sessions.json").write_text(json.dumps({"parents": {
        "parent-1": {"active": True, "manifest_id": "manifest-1"}}}),
        encoding="utf-8")
    assert select_tasks(paths, {
        "deficit": {"total": 1, "v4": 1, "k3": 0}, "parents": {},
    }) == []


def test_two_parent_refill_is_deterministically_fair(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    packets = {}
    for parent in ("a", "b"):
        for index in range(4):
            packet_id = f"{parent}{index}"
            write_packet(root, packet_id, parent_enabled=True)
            packets[packet_id] = {
                "state": "DISPATCHABLE", "role": "worker",
                "parent_enabled": True, "parent_session_id": f"parent-{parent}",
                "manifest_id": f"manifest-{parent}",
            }
    _ledger(root, packets)
    paths.refill_dir.mkdir(parents=True, exist_ok=True)
    (paths.refill_dir / "parent_sessions.json").write_text(json.dumps({"parents": {
        "parent-a": {"active": True, "manifest_id": "manifest-a"},
        "parent-b": {"active": True, "manifest_id": "manifest-b"},
    }}), encoding="utf-8")
    state = {
        "deficit": {"total": 4, "v4": 4, "k3": 0},
        "parents": {
            "parent-a": {"spawnable": {"total": 2, "v4": 2, "k3": 0}},
            "parent-b": {"spawnable": {"total": 2, "v4": 2, "k3": 0}},
        },
    }
    tasks = select_tasks(paths, state)
    assert [task["parent_session_id"] for task in tasks] == [
        "parent-a", "parent-b", "parent-a", "parent-b"]


def test_dry_run_preserves_queue_and_uses_dispatcher(tmp_path, capsys):
    root = make_root(tmp_path)
    write_packet(root, "w1")
    _ledger(root, {"w1": {"state": "DISPATCHABLE", "role": "worker"}})
    rc, result = run_once(root, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0 and result["status"] == "dry_run"
    assert "DRY-RUN w1" in out
    assert result["after"]["pending"]["v4"] == 1
    assert result["after"]["active"]["total"] == 0


def test_run_once_consumes_dispatched_events_before_return(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    write_packet(root, "w1")
    _ledger(root, {"w1": {"state": "DISPATCHABLE", "role": "worker",
                            "history": [], "attempts": 0}})

    def fake_wave(args):
        paths = LoopPaths.resolve(root)
        with paths.events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"packet_id": "w1", "event": "dispatched",
                                     "attempt": 0,
                                     "detail": {"run_id": "w1-a0-test"}}) + "\n")
        return 0

    monkeypatch.setattr(refill_consumer_v2.headless_wave, "run", fake_wave)
    rc, result = run_once(root, dry_run=False)
    ledger = json.loads((root / "data" / "progress_ledger.json").read_text())
    assert rc == 0
    assert result["state_machine"]["w1"] == "RUNNING"
    assert ledger["packets"]["w1"]["state"] == "RUNNING"
    assert ledger["packets"]["w1"]["current_run_id"] == "w1-a0-test"
    assert result["after"]["pending"]["v4"] == 0


def test_schedule_run_starts_packet_only_consumer(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    calls = []

    class Proc:
        pid = 4321

    monkeypatch.setattr(refill_consumer_v2.subprocess, "Popen",
                        lambda command, **kwargs: calls.append((command, kwargs)) or Proc())
    monkeypatch.setattr(refill_consumer_v2, "_proc_start_ticks", lambda pid: 99)
    result = schedule_run(root, source="desktop_subagent_stop")
    assert result["status"] == "scheduled" and result["pid"] == 4321
    command = calls[0][0]
    assert command[1].endswith("refill_consumer_v2.py")
    assert "orchestration_epilogue.py" not in " ".join(command)
    assert command[-2:] == ["--source", "desktop_subagent_stop"]


def test_schedule_run_coalesces_live_actuator(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    marker = root / "data" / "orchestration" / "refill_actuator.pid"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"pid": 1234, "proc_start_ticks": 77}),
                      encoding="utf-8")
    monkeypatch.setattr(refill_consumer_v2, "_process_generation_alive",
                        lambda pid, ticks: (pid, ticks) == (1234, 77))
    monkeypatch.setattr(refill_consumer_v2.subprocess, "Popen",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("coalesced run must not spawn")))
    result = schedule_run(root, source="desktop_subagent_stop")
    assert result == {"status": "coalesced", "pid": 1234,
                      "source": "desktop_subagent_stop"}
    requests = json.loads((root / "data" / "orchestration" /
                           "refill_requests.json").read_text())
    assert requests["requested_seq"] == 1 and requests["consumed_seq"] == 0


def test_drain_requested_repeats_when_edge_arrives_mid_pass(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    orchestration = root / "data" / "orchestration"
    orchestration.mkdir(parents=True)
    requests = orchestration / "refill_requests.json"
    requests.write_text(json.dumps({"requested_seq": 1, "consumed_seq": 0}))
    calls = []

    def once(root_arg, dry_run=False, observe_timeout=5.0):
        calls.append(1)
        if len(calls) == 1:
            requests.write_text(json.dumps({"requested_seq": 2,
                                            "consumed_seq": 0}))
        return 0, {"status": "idle"}

    monkeypatch.setattr(refill_consumer_v2, "run_once", once)
    rc, result = drain_requested(root, source="test")
    final = json.loads(requests.read_text())
    assert rc == 0 and len(calls) == 2
    assert result["requested_seq"] == result["consumed_seq"] == 2
    assert final["requested_seq"] == final["consumed_seq"] == 2


def test_debt_schedules_wake_after_provider_backoff(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    health = root / "data" / "provider_health" / "k3.json"
    health.parent.mkdir(parents=True)
    health.write_text(json.dumps({"backoff_until": 500.0}))
    captured = []
    monkeypatch.setattr(refill_consumer_v2.time, "time", lambda: 100.0)
    monkeypatch.setattr(refill_consumer_v2, "schedule_delayed_run",
                        lambda root, wake_at, source: captured.append(
                            (wake_at, source)) or {"status": "scheduled"})
    result = schedule_retry_if_debt(
        root, {"deficit": {"total": 4}}, source="test")
    assert result["status"] == "scheduled"
    assert captured == [(501.0, "test")]


def test_zero_debt_never_schedules_delayed_wake(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setattr(refill_consumer_v2, "schedule_delayed_run",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("zero debt must not schedule")))
    assert schedule_retry_if_debt(
        root, {"deficit": {"total": 0}}, source="test") is None


def test_successful_pass_with_residual_debt_gets_one_second_followup(
        tmp_path, monkeypatch):
    root = make_root(tmp_path)
    captured = []
    monkeypatch.setattr(refill_consumer_v2.time, "time", lambda: 100.0)
    monkeypatch.setattr(refill_consumer_v2, "schedule_delayed_run",
                        lambda root, wake_at, source: captured.append(
                            (wake_at, source)) or {"status": "scheduled"})
    result = schedule_followup_if_debt(
        root, {"deficit": {"total": 1}}, failed=False,
        source="refill_residual_debt")
    assert result["status"] == "scheduled"
    assert captured == [(101.0, "refill_residual_debt")]
