from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness import headless_wave as mod
from harness.orchestration_common import LoopPaths, OrchestrationPolicy


def manifest(tmp_path: Path, tasks: list[dict]) -> Path:
    path = tmp_path / "wave.json"
    path.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    return path


def task(task_id: str = "a", **extra):
    value = {"task_id": task_id, "task_name": "审计", "prompt": "read only",
             "cwd": ".", "role": "verifier", "sandbox": "read-only"}
    value.update(extra)
    return value


def test_manifest_rejects_duplicate_ids(tmp_path):
    path = manifest(tmp_path, [task(), task()])
    with pytest.raises(mod.WaveError, match="duplicate"):
        mod.load_manifest(path)


def test_parallel_writes_need_distinct_workspaces(tmp_path):
    path = manifest(tmp_path, [
        task("a", sandbox="workspace-write", allow_write=True, cwd=str(tmp_path)),
        task("b", sandbox="workspace-write", allow_write=True, cwd=str(tmp_path)),
    ])
    with pytest.raises(mod.WaveError, match="distinct cwd"):
        mod.load_manifest(path)


def test_read_only_wave_accepts_shared_workspace(tmp_path):
    path = manifest(tmp_path, [task("a", cwd=str(tmp_path)), task("b", cwd=str(tmp_path))])
    assert [row["task_id"] for row in mod.load_manifest(path)] == ["a", "b"]


def test_packet_manifest_has_no_prompt_or_cwd(tmp_path):
    path = manifest(tmp_path, [{"task_id": "p1", "task_name": "执行真实包",
                                "packet_id": "p1", "role": "worker"}])
    assert mod.load_manifest(path)[0]["packet_id"] == "p1"


def test_packet_manifest_rejects_execution_overrides(tmp_path):
    path = manifest(tmp_path, [{"task_id": "p1", "task_name": "执行真实包",
                                "packet_id": "p1", "role": "worker",
                                "prompt": "bypass dispatcher"}])
    with pytest.raises(mod.WaveError, match="may not override"):
        mod.load_manifest(path)


def test_observed_requires_matching_generation(tmp_path):
    life = tmp_path / "lifecycle"
    life.mkdir()
    (life / "exec_roster.json").write_text(json.dumps({"jobs": {
        "p": {"run_id": "old", "state": "running"}}}), encoding="utf-8")
    assert mod.observed(tmp_path, "p", "new") is False
    assert mod.observed(tmp_path, "p", "old") is True


def test_wait_generation_change_ignores_old_terminal_row(tmp_path, monkeypatch):
    rows = iter([
        {"run_id": "old", "state": "timed_out"},
        {"run_id": "old", "state": "timed_out"},
        {"run_id": "new", "state": "starting"},
    ])
    monkeypatch.setattr(mod, "roster_item", lambda *args: next(rows))
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    row = mod.wait_generation_change(tmp_path, "p", "old", timeout=1.0)
    assert row["run_id"] == "new" and row["state"] == "starting"


def test_wait_for_terminal_keeps_parent_result_until_completion(tmp_path, monkeypatch):
    report = tmp_path / "last_message.txt"
    report.write_text("PASS: bounded report\nfull details omitted", encoding="utf-8")
    rows = iter([
        {"packet_id": "p", "run_id": "r", "state": "running"},
        {"packet_id": "p", "run_id": "r", "state": "completed",
         "published_report": str(report)},
    ])
    monkeypatch.setattr(mod, "recover_live_supervisor",
                        lambda *args, **kwargs: next(rows))
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    results = [{"packet_id": "p", "run_id": "r", "status": "running"}]

    mod.wait_for_terminal(tmp_path, results, timeout=1.0)

    assert results[0]["status"] == "completed"
    assert results[0]["report_path"] == str(report)
    assert results[0]["summary"] == "PASS: bounded report"


def test_wait_for_terminal_marks_unfinished_parent_result_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "recover_live_supervisor",
                        lambda *args, **kwargs: {
                            "packet_id": "p", "run_id": "r", "state": "running"})
    monkeypatch.setattr(mod.time, "monotonic", lambda: 10.0)
    results = [{"packet_id": "p", "run_id": "r", "status": "running"}]

    mod.wait_for_terminal(tmp_path, results, timeout=0.0)

    assert results[0]["status"] == "wait_timeout"
    assert results[0]["failure"]["reason"] == "parent_wave_wait_timeout"


def test_wait_for_terminal_uses_run_history_after_new_generation_overwrite(
        tmp_path, monkeypatch):
    report = tmp_path / "r1-last-message.txt"
    report.write_text("PASS r1\n", encoding="utf-8")
    monkeypatch.setattr(mod, "recover_live_supervisor",
                        lambda *args, **kwargs: {
                            "packet_id": "p", "run_id": "r2", "state": "running",
                            "history": [
                                {"run_id": "r1", "state": "completed",
                                 "published_report": str(report)},
                                {"run_id": "r2", "state": "generation_started"},
                                {"run_id": "r2", "state": "running"},
                            ]})
    results = [{"packet_id": "p", "run_id": "r1", "status": "running"}]

    mod.wait_for_terminal(tmp_path, results, timeout=1.0)

    assert results[0]["status"] == "completed"
    assert results[0]["report_path"] == str(report)
    assert results[0]["summary"] == "PASS r1"


def test_starting_is_not_effective_concurrency(tmp_path):
    life = tmp_path / "lifecycle"
    life.mkdir()
    (life / "exec_roster.json").write_text(json.dumps({"jobs": {
        "p": {"run_id": "new", "state": "starting"}}}), encoding="utf-8")
    assert mod.observed(tmp_path, "p", "new") is False


def test_lost_live_supervisor_is_recovered(tmp_path, monkeypatch):
    life = tmp_path / "lifecycle"
    life.mkdir()
    (life / "exec_roster.json").write_text(json.dumps({"jobs": {
        "p": {"packet_id": "p", "run_id": "r", "attempt": 0,
              "state": "lost", "supervisor_pid": 42, "os_pid": 43,
              "history": []}}}),
        encoding="utf-8")
    monkeypatch.setattr(mod, "process_alive",
                        lambda pid, expected=None: pid in {42, 43})
    row = mod.recover_live_supervisor(tmp_path, "p")
    assert row["state"] == "running"
    assert row["recovered_by"] == "headless_wave"


def test_pid_reuse_token_prevents_lost_generation_recovery(tmp_path, monkeypatch):
    life = tmp_path / "lifecycle"
    life.mkdir()
    (life / "exec_roster.json").write_text(json.dumps({"jobs": {
        "p": {"packet_id": "p", "run_id": "r", "attempt": 0,
              "state": "lost", "supervisor_pid": 42, "os_pid": 43,
              "supervisor_proc_start_ticks": 100,
              "worker_proc_start_ticks": 200, "history": []}}}),
        encoding="utf-8")
    monkeypatch.setattr(mod, "process_start_token",
                        lambda pid: {42: 999, 43: 200}.get(pid))
    assert mod.recover_live_supervisor(tmp_path, "p")["state"] == "lost"


def test_headless_default_disables_desktop_apps_plane(tmp_path):
    root = Path(__file__).resolve().parents[2]
    paths = LoopPaths.resolve(root)
    policy = OrchestrationPolicy.load(paths)
    packet, run_id, supervisor, worker, report_dir = mod.build_commands(
        task(cwd=str(root)), tmp_path / "manifest.json", paths, policy, 30)
    joined = " ".join(worker)
    assert "features.apps=false" in joined
    assert "features.plugins=false" in joined
    assert "mcp_servers.ipybox.enabled=false" in joined
    assert ("mcp_servers.node_repl.enabled=false" in joined) == (mod.os.name == "nt")
    assert worker[worker.index("-m") + 1] == policy.model_pin("k3")


def test_headless_worker_uses_active_execution_profile(tmp_path):
    root = Path(__file__).resolve().parents[2]
    paths = LoopPaths.resolve(root)
    policy = OrchestrationPolicy.load(paths)
    _, _, _, worker, _ = mod.build_commands(
        task(cwd=str(root), role="worker"), tmp_path / "manifest.json",
        paths, policy, 30)
    assert worker[worker.index("-m") + 1] == policy.model_pin("v4")


def test_headless_propagates_parent_session_id(tmp_path):
    root = Path(__file__).resolve().parents[2]
    paths = LoopPaths.resolve(root)
    policy = OrchestrationPolicy.load(paths)
    _, _, supervisor, _, _ = mod.build_commands(
        task(cwd=str(root), role="worker", parent_session_id="parent-thread",
             manifest_id="manifest-1"),
        tmp_path / "manifest.json", paths, policy, 30)
    pos = supervisor.index("--parent-session-id")
    assert supervisor[pos + 1] == "parent-thread"
    manifest_pos = supervisor.index("--manifest-id")
    assert supervisor[manifest_pos + 1] == "manifest-1"


def test_lost_success_with_report_recovers_completed(tmp_path):
    report = tmp_path / "report.txt"
    report.write_text("done", encoding="utf-8")
    life = tmp_path / "lifecycle"
    life.mkdir()
    (life / "exec_roster.json").write_text(json.dumps({"jobs": {
        "p": {"packet_id": "p", "run_id": "r", "attempt": 0,
              "state": "lost", "exit_code": 0, "published_report": str(report),
              "history": []}}}), encoding="utf-8")
    row = mod.recover_live_supervisor(tmp_path, "p")
    assert row["state"] == "completed"
    assert row["recovered_by"] == "headless_wave_completed_report"


def run_args(root: Path, manifest_path: Path, *, dry_run: bool = False) -> Namespace:
    return Namespace(manifest=manifest_path, root=root, timeout=30.0,
                     observe_timeout=0.01, spawn_interval_ms=0,
                     health_every=8, dry_run=dry_run,
                     wait_all=False, detach=False)


def test_parent_bound_manifest_waits_all_by_default(tmp_path, monkeypatch, capsys):
    root = Path(__file__).resolve().parents[2]
    policy = OrchestrationPolicy.load(LoopPaths.resolve(root))
    path = manifest(tmp_path, [task(
        cwd=str(root), parent_session_id="parent-thread",
        manifest_id="parent-wave")])
    monkeypatch.setattr(mod, "provider_birth_allowed", lambda *args: True)
    monkeypatch.setattr(mod, "opencodex_healthy", lambda: True)
    monkeypatch.setattr(mod, "build_commands", lambda *args: (
        "p", "r", ["supervisor"],
        ["codex", "exec", "-m", policy.model_pin("k3")], tmp_path))
    monkeypatch.setattr(mod, "launch_supervisor", lambda *args: 123)
    monkeypatch.setattr(mod, "wait_observed", lambda *args, **kwargs: True)
    waits = []
    monkeypatch.setattr(mod, "wait_for_terminal",
                        lambda data, results, timeout: waits.append(
                            (data, [dict(row) for row in results], timeout)))

    assert mod.run(run_args(root, path)) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["wait_all"] is True
    assert waits and waits[0][1][0]["status"] == "running"


def test_parent_bound_manifest_can_explicitly_detach(tmp_path, monkeypatch, capsys):
    root = Path(__file__).resolve().parents[2]
    policy = OrchestrationPolicy.load(LoopPaths.resolve(root))
    path = manifest(tmp_path, [task(
        cwd=str(root), parent_session_id="parent-thread",
        manifest_id="parent-wave")])
    monkeypatch.setattr(mod, "provider_birth_allowed", lambda *args: True)
    monkeypatch.setattr(mod, "opencodex_healthy", lambda: True)
    monkeypatch.setattr(mod, "build_commands", lambda *args: (
        "p", "r", ["supervisor"],
        ["codex", "exec", "-m", policy.model_pin("k3")], tmp_path))
    monkeypatch.setattr(mod, "launch_supervisor", lambda *args: 123)
    monkeypatch.setattr(mod, "wait_observed", lambda *args, **kwargs: True)
    monkeypatch.setattr(mod, "wait_for_terminal",
                        lambda *args, **kwargs: pytest.fail("detached wave waited"))
    args = run_args(root, path)
    args.detach = True

    assert mod.run(args) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["wait_all"] is False


def test_generic_k3_backoff_blocks_birth_but_not_dry_run(tmp_path, monkeypatch, capsys):
    root = Path(__file__).resolve().parents[2]
    policy = OrchestrationPolicy.load(LoopPaths.resolve(root))
    path = manifest(tmp_path, [task(cwd=str(root))])
    monkeypatch.setattr(mod, "provider_birth_allowed", lambda root, model: False)
    launched = []
    monkeypatch.setattr(mod, "launch_supervisor",
                        lambda *args, **kwargs: launched.append(args) or 1)

    assert mod.run(run_args(root, path)) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["results"][0]["status"] == "provider_backoff"
    assert result["results"][0]["model"] == policy.model_pin("k3")
    assert launched == []

    assert mod.run(run_args(root, path, dry_run=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["results"][0]["status"] == "dry_run"
    assert launched == []


def test_generic_v4_is_not_blocked_by_k3_backoff(tmp_path, monkeypatch, capsys):
    root = Path(__file__).resolve().parents[2]
    policy = OrchestrationPolicy.load(LoopPaths.resolve(root))
    path = manifest(tmp_path, [task(cwd=str(root), role="worker")])
    seen = []
    monkeypatch.setattr(mod, "provider_birth_allowed",
                        lambda root, model: seen.append(model) or ("k3" not in model))
    monkeypatch.setattr(mod, "opencodex_healthy", lambda: True)
    monkeypatch.setattr(mod, "launch_supervisor", lambda *args, **kwargs: 123)
    monkeypatch.setattr(mod, "wait_observed", lambda *args, **kwargs: True)

    assert mod.run(run_args(root, path)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["results"][0]["status"] == "running"
    assert seen == [policy.model_pin("v4")]
