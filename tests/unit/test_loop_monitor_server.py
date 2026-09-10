"""Regression tests for the Windows 8765 Desktop rollout fallback."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def load_monitor():
    path = Path(__file__).resolve().parents[3] / "launchers" / "loop_monitor_server.py"
    if not path.exists():
        pytest.skip("Windows host-overlay monitor is not present on this plane")
    spec = importlib.util.spec_from_file_location("loop_monitor_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_rollout(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def event(kind: str) -> dict:
    return {"type": "event_msg", "payload": {"type": kind}}


def test_resumed_parent_latest_started_is_not_terminal(tmp_path):
    monitor = load_monitor()
    path = tmp_path / "parent.jsonl"
    write_rollout(path, [event("task_started"), event("task_complete"), event("task_started")])
    assert monitor.rollout_terminal(path) is False


def test_latest_complete_is_terminal(tmp_path):
    monitor = load_monitor()
    path = tmp_path / "child.jsonl"
    write_rollout(path, [event("task_started"), event("task_complete")])
    assert monitor.rollout_terminal(path) is True


def test_fork_bootstrap_sol_is_separate_from_active_sonnet(tmp_path):
    monitor = load_monitor()
    monitor._model_cache.clear()
    path = tmp_path / "child.jsonl"
    write_rollout(path, [
        {"type": "turn_context", "payload": {"model": "gpt-5.6-sol"}},
        {"type": "event_msg", "payload": {"type": "thread_settings_applied",
         "thread_settings": {"model": "weiwu/aws.claude-sonnet-4.6"}}},
        {"type": "turn_context", "payload": {
         "model": "weiwu/aws.claude-sonnet-4.6"}},
    ])
    profile = monitor.read_rollout_model_profile(path)
    assert profile["first_observed_model"] == "gpt-5.6-sol"
    assert profile["inherited_history_model"] == "gpt-5.6-sol"
    assert profile["active_turn_model"] == "weiwu/aws.claude-sonnet-4.6"
    assert monitor.read_rollout_model(path) == "weiwu/aws.claude-sonnet-4.6"


def test_task_name_after_large_bootstrap_is_detected(tmp_path):
    monitor = load_monitor()
    monitor._task_name_cache.clear()
    path = tmp_path / "child.jsonl"
    rows = [{"type": "event_msg", "payload": {"type": "token_count"}}
            for _ in range(24)]
    rows.extend([
        {"type": "event_msg", "payload": {"type": "thread_settings_applied",
         "thread_settings": {"model": "weiwu/aws.claude-sonnet-4.6"}}},
        {"type": "event_msg", "payload": {"type": "user_message",
         "message": "任务名：审计模型归因\n只读检查。"}},
    ])
    write_rollout(path, rows)
    assert monitor.read_rollout_task_name(path) == "审计模型归因"


def test_page_does_not_render_logical_pool_as_observed_model():
    monitor = load_monitor()
    assert "function taskModel(x)" in monitor.PAGE
    assert "审核池·模型待观测" in monitor.PAGE
    assert "执行池·模型待观测" in monitor.PAGE
    assert "shortModel(x.model||x.pool)" not in monitor.PAGE


def test_active_child_of_resumed_parent_is_visible(tmp_path, monkeypatch):
    monitor = load_monitor()
    monitor._rollout_cache = {"checked_at": 0.0, "value": {"tasks": [], "updated_at": None}}
    monitor._task_name_cache.clear()
    monitor._model_cache.clear()
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)

    parent_id = "parent-1"
    child_id = "child-1"
    parent = tmp_path / ("rollout-" + parent_id + ".jsonl")
    child = tmp_path / ("rollout-" + child_id + ".jsonl")
    write_rollout(parent, [
        {"type": "session_meta", "payload": {"id": parent_id, "cwd": "repo"}},
        event("task_complete"),
        event("task_started"),
    ])
    write_rollout(child, [
        {"type": "session_meta", "payload": {"id": child_id,
         "parent_thread_id": parent_id, "agent_role": "verifier", "cwd": "repo"}},
        event("task_started"),
        {"type": "turn_context", "payload": {"model": "weiwu-k3/kimi-k3",
         "effort": "max"}},
    ])

    result = monitor.scan_rollout_subagents(tmp_path)
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["agent_id"] == child_id
    assert result["tasks"][0]["model"] == "weiwu-k3/kimi-k3"
    assert result["tasks"][0]["role"] == "verifier"


def test_later_birth_does_not_hide_earlier_nonterminal_wave(tmp_path, monkeypatch):
    monitor = load_monitor()
    monitor._rollout_cache = {"checked_at": 0.0, "value": {"tasks": [], "updated_at": None}}
    monitor._task_name_cache.clear()
    monitor._model_cache.clear()
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)

    parent_id = "parent-wide-audit"
    write_rollout(tmp_path / ("rollout-" + parent_id + ".jsonl"), [
        {"type": "session_meta", "payload": {"id": parent_id, "cwd": "repo"}},
        event("task_started"),
    ])
    for child_id, born in (("first-wave", "2026-08-14T09:00:00Z"),
                           ("follow-up", "2026-08-14T09:01:31Z")):
        write_rollout(tmp_path / ("rollout-" + child_id + ".jsonl"), [
            {"type": "session_meta", "payload": {
                "id": child_id, "parent_thread_id": parent_id,
                "agent_role": "verifier", "cwd": "repo", "timestamp": born,
            }},
            event("task_started"),
            {"type": "turn_context", "payload": {
                "model": "weiwu/aws.claude-sonnet-4.6", "effort": "ultra"}},
        ])

    result = monitor.scan_rollout_subagents(tmp_path)
    assert [row["agent_id"] for row in result["tasks"]] == [
        "first-wave", "follow-up"]
    assert result["open_sessions"] == 0


def test_active_child_keeps_quiet_parent_group_visible(tmp_path, monkeypatch):
    monitor = load_monitor()
    monitor._rollout_cache = {"checked_at": 0.0, "value": {"tasks": [], "updated_at": None}}
    monitor._task_name_cache.clear()
    monitor._model_cache.clear()
    monkeypatch.setattr(monitor.time, "time", lambda: 1000.0)
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)

    parent_id, child_id = "quiet-parent", "active-child"
    parent = tmp_path / ("rollout-" + parent_id + ".jsonl")
    child = tmp_path / ("rollout-" + child_id + ".jsonl")
    write_rollout(parent, [
        {"type": "session_meta", "payload": {"id": parent_id, "cwd": "repo"}},
        event("task_started"),
    ])
    write_rollout(child, [
        {"type": "session_meta", "payload": {
            "id": child_id, "parent_thread_id": parent_id,
            "agent_role": "verifier", "cwd": "repo"}},
        event("task_started"),
        {"type": "turn_context", "payload": {
            "model": "weiwu/aws.claude-sonnet-4.6", "effort": "ultra"}},
    ])
    import os
    os.utime(parent, (200.0, 200.0))
    os.utime(child, (990.0, 990.0))

    result = monitor.scan_rollout_subagents(tmp_path)
    assert [row["agent_id"] for row in result["tasks"]] == [child_id]
    assert result["open_sessions"] == 0


def test_rollout_terminal_is_sampled_once_per_scan(tmp_path, monkeypatch):
    monitor = load_monitor()
    monitor._rollout_cache = {"checked_at": 0.0,
                              "value": {"tasks": [], "updated_at": None}}
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)

    parent_id, child_id = "single-sample-parent", "single-sample-child"
    parent = tmp_path / ("rollout-" + parent_id + ".jsonl")
    child = tmp_path / ("rollout-" + child_id + ".jsonl")
    write_rollout(parent, [
        {"type": "session_meta", "payload": {"id": parent_id, "cwd": "repo"}},
        event("task_started"),
    ])
    write_rollout(child, [
        {"type": "session_meta", "payload": {
            "id": child_id, "parent_thread_id": parent_id,
            "agent_role": "verifier", "cwd": "repo"}},
        event("task_started"),
    ])
    real = monitor.rollout_terminal
    calls: dict[str, int] = {}

    def counted(path):
        key = str(path)
        calls[key] = calls.get(key, 0) + 1
        return real(path)

    monkeypatch.setattr(monitor, "rollout_terminal", counted)
    result = monitor.scan_rollout_subagents(tmp_path)

    assert [row["agent_id"] for row in result["tasks"]] == [child_id]
    assert calls == {str(parent): 1, str(child): 1}


def test_native_roster_without_model_uses_matching_rollout_model(tmp_path, monkeypatch):
    monitor = load_monitor()
    monitor._rollout_cache = {"checked_at": 0.0, "value": {"tasks": [], "updated_at": None}}
    monitor._task_name_cache.clear()
    monitor._model_cache.clear()
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})

    root = tmp_path / "loop"
    windows_root = tmp_path / "windows"
    sessions = tmp_path / "sessions"
    parent_id, child_id = "parent-native", "child-native"
    write_rollout(sessions / ("rollout-" + parent_id + ".jsonl"), [
        {"type": "session_meta", "payload": {"id": parent_id, "cwd": "repo"}},
        event("task_started"),
    ])
    write_rollout(sessions / ("rollout-" + child_id + ".jsonl"), [
        {"type": "session_meta", "payload": {"id": child_id,
         "parent_thread_id": parent_id, "agent_role": "worker", "cwd": "repo"}},
        event("task_started"),
        {"type": "turn_context", "payload": {"model": "weiwu-k3/kimi-k3",
         "effort": "max"}},
    ])
    roster = {"updated_at": 1.0, "pending": [], "agents": {child_id: {
        "agent_id": child_id, "agent_role": "worker", "task_name": "K3 worker",
        "status": "running", "updated_at": 1.0}}}
    path = windows_root / "data" / "lifecycle" / "native_roster.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(roster), encoding="utf-8")

    result = monitor.snapshot(root, windows_root, sessions)
    assert result["counts"]["running"] == 1
    assert result["pools"]["k3"] == 1
    assert result["pools"]["v4"] == 0
    assert result["tasks"][0]["pool"] == "k3"


def test_windows_headless_roster_is_merged_into_dashboard(tmp_path, monkeypatch):
    monitor = load_monitor()
    monkeypatch.setattr(monitor.time, "time", lambda: 10.0)
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})
    root = tmp_path / "wsl"
    windows_root = tmp_path / "windows"
    sessions = tmp_path / "sessions"
    wsl_life = root / "data" / "lifecycle"
    win_life = windows_root / "data" / "lifecycle"
    wsl_life.mkdir(parents=True)
    win_life.mkdir(parents=True)
    (wsl_life / "exec_roster.json").write_text(json.dumps({"jobs": {
        "wsl": {"packet_id": "wsl", "task_name": "WSL", "state": "running",
                "role": "verifier", "model": "weiwu-k3/kimi-k3", "updated_at": 1}}}),
        encoding="utf-8")
    (win_life / "exec_roster.json").write_text(json.dumps({"jobs": {
        "win": {"packet_id": "win", "task_name": "Windows", "state": "running",
                "role": "worker", "model": "weiwu-k3/kimi-k3", "updated_at": 2}}}),
        encoding="utf-8")
    result = monitor.snapshot(root, windows_root, sessions)
    assert result["counts"]["running"] == 2
    assert result["pools"]["k3"] == 2
    assert result["planes"]["headless"] == 2
    assert result["planes"]["desktop"] == 0
    assert result["headroom"] == 78
    assert result["deficit"] == 0
    assert {row["name"] for row in result["tasks"]} == {"WSL", "Windows"}


def test_windows_refill_snapshot_participates_in_freshness(tmp_path, monkeypatch):
    monitor = load_monitor()
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})
    monkeypatch.setattr(monitor.time, "time", lambda: 100.0)
    root = tmp_path / "wsl"
    windows_root = tmp_path / "windows"
    life = windows_root / "data" / "lifecycle"
    life.mkdir(parents=True)
    (life / "native_roster.json").write_text(json.dumps({
        "updated_at": 75.0, "pending": [], "agents": {"a": {
            "status": "running", "model": "weiwu/deepseek-v4-flash",
            "task_name": "quiet desktop", "updated_at": 75.0,
        }}}), encoding="utf-8")
    refill = windows_root / "data" / "refill"
    refill.mkdir(parents=True)
    (refill / "refill_state.json").write_text(json.dumps({
        "updated_at": 95.0, "target_total": 48,
    }), encoding="utf-8")
    result = monitor.snapshot(root, windows_root, tmp_path / "sessions")
    assert result["freshness"] == "LIVE"
    assert result["age_seconds"] == 5.0


def test_dashboard_merges_parent_target_debt_with_observed_planes(tmp_path, monkeypatch):
    monitor = load_monitor()
    monkeypatch.setattr(monitor.time, "time", lambda: 10.0)
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})
    root = tmp_path / "loop"
    windows_root = tmp_path / "windows"
    lifecycle = root / "data" / "lifecycle"
    refill = root / "data" / "refill"
    lifecycle.mkdir(parents=True)
    refill.mkdir(parents=True)
    (lifecycle / "exec_roster.json").write_text(json.dumps({"jobs": {"p1": {
        "packet_id": "p1", "state": "running", "role": "worker",
        "model": "weiwu/aws.claude-sonnet-4.6", "heartbeat_at": 9.0,
        "updated_at": 9.0, "parent_session_id": "parent-1",
    }}}), encoding="utf-8")
    (refill / "refill_state.json").write_text(json.dumps({
        "updated_at": 10.0, "target_total": 80,
        "parents": {"parent-1": {
            "active": True, "target": 20, "running": 1, "initializing": 0,
            "pending": {"total": 30}, "deficit": 19,
            "spawnable": {"total": 19}, "reason": "below_parent_target",
            "manifest_id": "manifest-1",
        }},
    }), encoding="utf-8")

    result = monitor.snapshot(root, windows_root, tmp_path / "sessions")
    parent = result["parents"]["parent-1"]
    assert parent["running"] == 1
    assert result["tasks"][0]["model"] == "weiwu/aws.claude-sonnet-4.6"
    assert parent["target"] == 20
    assert parent["pending"] == 30
    assert parent["deficit"] == 19
    assert parent["spawnable"] == 19
    assert parent["reason"] == "below_parent_target"


def test_quiet_latest_wave_still_counts_as_occupied(
        tmp_path, monkeypatch):
    monitor = load_monitor()
    monitor._rollout_cache = {"checked_at": 0.0, "value": {"tasks": [], "updated_at": None}}
    monitor._task_name_cache.clear()
    monitor._model_cache.clear()
    monkeypatch.setattr(monitor.time, "time", lambda: 200.0)
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: 10.0)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})

    root = tmp_path / "wsl"
    windows_root = tmp_path / "windows"
    sessions = tmp_path / "sessions"
    parent_id, child_id = "parent-quiet", "child-quiet"
    parent = sessions / ("rollout-" + parent_id + ".jsonl")
    child = sessions / ("rollout-" + child_id + ".jsonl")
    write_rollout(parent, [
        {"type": "session_meta", "payload": {"id": parent_id, "cwd": "repo"}},
        event("task_started"),
    ])
    write_rollout(child, [
        {"type": "session_meta", "payload": {"id": child_id,
         "parent_thread_id": parent_id, "agent_role": "worker", "cwd": "repo"}},
        event("task_started"),
        {"type": "turn_context", "payload": {
            "model": "weiwu/deepseek-v4-flash", "effort": "ultra"}},
    ])
    # Rollout files are current-generation but intentionally quiet beyond the
    # 120-second real-activity window.
    import os
    os.utime(parent, (40.0, 40.0))
    os.utime(child, (40.0, 40.0))
    life = windows_root / "data" / "lifecycle"
    life.mkdir(parents=True)
    (life / "native_roster.json").write_text(json.dumps({
        "updated_at": 40.0, "pending": [], "agents": {child_id: {
            "agent_id": child_id, "status": "running",
            "model": "weiwu/deepseek-v4-flash", "task_name": "quiet task",
            "updated_at": 40.0,
        }}}), encoding="utf-8")

    result = monitor.snapshot(root, windows_root, sessions)
    assert result["counts"]["running"] == 1
    assert result["counts"]["recent_activity"] == 0
    assert result["planes"]["desktop"] == 1


def test_stale_native_row_without_current_rollout_stays_stale(tmp_path, monkeypatch):
    monitor = load_monitor()
    monkeypatch.setattr(monitor.time, "time", lambda: 100.0)
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: 10.0)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})
    root = tmp_path / "wsl"
    windows_root = tmp_path / "windows"
    life = windows_root / "data" / "lifecycle"
    life.mkdir(parents=True)
    (life / "native_roster.json").write_text(json.dumps({
        "updated_at": 40.0, "pending": [], "agents": {"orphan": {
            "status": "running", "model": "weiwu/deepseek-v4-flash",
            "task_name": "unproven", "updated_at": 40.0,
        }}}), encoding="utf-8")
    result = monitor.snapshot(root, windows_root, tmp_path / "sessions")
    assert result["freshness"] == "STALE"
    assert result["counts"]["running"] == 0
    assert result["counts"]["stale_native"] == 1
    assert result["planes"]["desktop"] == 0
    assert result["liveness"]["desktop_rollout_evidence"] == 0
    assert result["tasks"] == []


def test_stale_headless_heartbeat_is_not_hidden_by_fresh_refill(tmp_path, monkeypatch):
    monitor = load_monitor()
    monkeypatch.setattr(monitor.time, "time", lambda: 100.0)
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})
    root = tmp_path / "wsl"
    windows_root = tmp_path / "windows"
    life = root / "data" / "lifecycle"
    life.mkdir(parents=True)
    (life / "exec_roster.json").write_text(json.dumps({"jobs": {
        "old": {"state": "running", "task_name": "old headless",
                "model": "weiwu-k3/kimi-k3", "role": "verifier",
                "heartbeat_at": 20.0, "updated_at": 20.0},
    }}), encoding="utf-8")
    refill = root / "data" / "refill"
    refill.mkdir(parents=True)
    (refill / "refill_state.json").write_text(json.dumps({
        "updated_at": 99.0, "target_total": 48,
    }), encoding="utf-8")

    result = monitor.snapshot(root, windows_root, tmp_path / "sessions")
    assert result["freshness"] == "STALE"
    assert result["age_seconds"] == 1.0
    assert result["liveness"]["headless_age_seconds"] == 80.0
    assert result["counts"]["running"] == 0
    assert result["counts"]["stale_headless"] == 1
    assert result["planes"]["headless"] == 0
    assert result["tasks"] == []


def test_recent_native_row_counts_during_rollout_flush_gap(tmp_path, monkeypatch):
    monitor = load_monitor()
    monkeypatch.setattr(monitor.time, "time", lambda: 100.0)
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: 10.0)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})
    root = tmp_path / "wsl"
    windows_root = tmp_path / "windows"
    life = windows_root / "data" / "lifecycle"
    life.mkdir(parents=True)
    (life / "native_roster.json").write_text(json.dumps({
        "updated_at": 95.0, "pending": [], "agents": {"new": {
            "status": "running", "model": "weiwu-k3/kimi-k3",
            "task_name": "new verifier", "updated_at": 95.0,
        }}}), encoding="utf-8")
    result = monitor.snapshot(root, windows_root, tmp_path / "sessions")
    assert result["freshness"] == "LIVE"
    assert result["counts"]["running"] == 1
    assert result["counts"]["stale_native"] == 0
    assert result["pools"]["k3"] == 1


def test_terminal_native_id_suppresses_nonterminal_rollout_fallback(tmp_path,
                                                                    monkeypatch):
    monitor = load_monitor()
    monitor._rollout_cache = {"checked_at": 0.0, "value": {"tasks": [], "updated_at": None}}
    monitor._task_name_cache.clear()
    monitor._model_cache.clear()
    monkeypatch.setattr(monitor.time, "time", lambda: 100.0)
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: 10.0)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})
    root, windows_root, sessions = (tmp_path / "wsl", tmp_path / "windows",
                                    tmp_path / "sessions")
    parent_id, child_id = "parent-terminal", "child-terminal"
    write_rollout(sessions / ("rollout-" + parent_id + ".jsonl"), [
        {"type": "session_meta", "payload": {"id": parent_id, "cwd": "repo"}},
        event("task_started"),
    ])
    write_rollout(sessions / ("rollout-" + child_id + ".jsonl"), [
        {"type": "session_meta", "payload": {"id": child_id,
         "parent_thread_id": parent_id, "agent_role": "verifier", "cwd": "repo"}},
        event("task_started"),
        {"type": "turn_context", "payload": {"model": "weiwu-k3/kimi-k3"}},
    ])
    life = windows_root / "data/lifecycle"
    life.mkdir(parents=True)
    (life / "native_roster.json").write_text(json.dumps({
        "updated_at": 99.0, "pending": [], "agents": {child_id: {
            "status": "terminal", "agent_role": "verifier", "updated_at": 99.0,
        }}
    }), encoding="utf-8")

    result = monitor.snapshot(root, windows_root, sessions)
    assert result["counts"]["running"] == 0
    assert result["counts"]["estimated"] == 0
    assert result["planes"]["desktop"] == 0


def test_unnamed_task_labels_never_expose_runtime_uuid():
    monitor = load_monitor()
    assert monitor.unnamed_task_label("verifier", "k3") == "未命名 K3 验证任务"
    assert monitor.unnamed_task_label("worker", "v4") == "未命名执行任务"
    assert "019f" not in monitor.unnamed_task_label("reviewer", "k3")
    assert monitor.runtime_id_like("019ff5cf-2a92-73d1-a1d3-91d531460877")
    assert not monitor.runtime_id_like("Provider出生门审计")


def test_pool_classification_prefers_actual_model_over_role():
    monitor = load_monitor()
    assert monitor.pool_for("weiwu-k3/kimi-k3", "worker") == "k3"
    assert monitor.pool_for("weiwu/deepseek-v4-flash", "reviewer") == "v4"
    assert monitor.pool_for("", "reviewer") == "k3"
    assert monitor.pool_for("", "worker") == "v4"
    assert monitor.pool_for("snaillmou/grok-4.6", None) == "v4"
    assert monitor.pool_for("snaillmou/grok-4.6", "reviewer") == "v4"
    assert monitor.model_family("snaillmou/grok-4.6") == "grok"
    assert monitor.model_family("weiwu/deepseek-v4-pro") == "v4_pro"
    assert monitor.model_family("weiwu/deepseek-v4-flash") == "v4"


def test_short_model_labels_grok_and_v4_pro():
    monitor = load_monitor()
    assert "return 'grok4.6'" in monitor.PAGE
    assert "return 'V4 Pro'" in monitor.PAGE
    assert 'id="model-grok"' in monitor.PAGE
    assert 'id="model-v4-pro"' in monitor.PAGE
    assert "grok4.6" in monitor.PAGE


def test_active_execution_profile_is_exposed(tmp_path):
    monitor = load_monitor()
    root = tmp_path / "loop"
    config = root / "config"
    config.mkdir(parents=True)
    (config / "model_profiles.toml").write_text(
        'active_profile = "glm"\n[profiles.glm]\nlabel = "GLM 5.2"\n'
        'execution_model = "weiwu/glm-5.2"\n'
        'execution_reasoning = "ultra"\n', encoding="utf-8")
    assert monitor.read_execution_profile(root) == {
        "name": "glm", "label": "GLM 5.2",
        "model": "weiwu/glm-5.2", "reasoning": "ultra",
        "review_model": "", "review_reasoning": ""}


def test_dashboard_shows_observed_models_without_controller_pool_semantics():
    monitor = load_monitor()
    assert "Codex LOOP · 实时状态" in monitor.PAGE
    assert "LOOP 全局模式" in monitor.PAGE
    assert "gm.effective_active===true" in monitor.PAGE
    assert "当前运行" in monitor.PAGE
    assert "实际运行模型" in monitor.PAGE
    assert 'id="model-v4"' in monitor.PAGE
    assert 'id="model-sonnet"' in monitor.PAGE
    assert 'id="model-k3"' in monitor.PAGE
    assert 'id="model-sol"' in monitor.PAGE
    assert 'id="provider-health"' in monitor.PAGE
    assert "d.models||{}" in monitor.PAGE
    assert "taskModel(x)" in monitor.PAGE
    assert "x.model||x.pool" not in monitor.PAGE
    assert "shortModel" in monitor.PAGE
    assert "Desktop / Headless" in monitor.PAGE
    assert "可借调基准" not in monitor.PAGE
    assert "待补债务" not in monitor.PAGE
    assert "Desktop rollout 估算" not in monitor.PAGE
    assert "stale native" not in monitor.PAGE
    assert "recent_tasks" not in monitor.PAGE


def test_global_mode_status_requires_marker_managed_hooks_and_active_agreement(
        tmp_path, monkeypatch):
    monitor = load_monitor()
    home = tmp_path / "home"
    monkeypatch.setattr(monitor.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.delenv("CODEX_LOOP_REQUIREMENTS_TOML", raising=False)
    windows_root = tmp_path / "codex-LOOP" / "codex-loop-s-f2"
    state = windows_root.parent / "state"
    state.mkdir(parents=True)
    (state / "global-loop-mode.json").write_text(json.dumps({
        "schema": "codex-loop-global-mode/v1", "active": True,
        "control_root": str(windows_root),
    }), encoding="utf-8")
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    (codex_home / "requirements.toml").write_text(
        f"[features]\nhooks = true\n[hooks]\nwindows_managed_dir = '{windows_root / 'hooks'}'\n"
        "# --component spawn-gate\n", encoding="utf-8")
    (codex_home / "AGENTS.md").write_text(
        "# Active Codex LOOP global mode\n"
        f"LOOP_CONTROL_ROOT={windows_root}\nMandatory LOOP model routing\n",
        encoding="utf-8")
    result = monitor.read_global_mode_status(windows_root)
    assert result["effective_active"] is True
    (codex_home / "requirements.toml").unlink()
    assert monitor.read_global_mode_status(windows_root)["effective_active"] is False
    (codex_home / "hooks.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [{"hooks": [{
            "command": f"py -3 {windows_root / 'hooks' / 'global_loop_mode.py'} --component spawn-gate"
        }]}]}}), encoding="utf-8")
    assert monitor.read_global_mode_status(windows_root)["effective_active"] is True


def test_dashboard_javascript_references_only_existing_dom_ids():
    monitor = load_monitor()
    import re
    referenced = set(re.findall(r"\$\('([^']+)'\)", monitor.PAGE))
    declared = set(re.findall(r'id="([^"]+)"', monitor.PAGE))
    assert referenced <= declared
    assert "model-sonnet" in referenced


def test_preferred_reflects_sonnet_only_profile(tmp_path, monkeypatch):
    """snapshot() preferred dict shows {sonnet: target} when both exec and review are Sonnet."""
    monitor = load_monitor()
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})
    root = tmp_path / "loop"
    windows_root = tmp_path / "windows"
    config = root / "config"
    config.mkdir(parents=True)
    (config / "model_profiles.toml").write_text(
        'active_profile = "sonnet46-all"\n'
        '[profiles.sonnet46-all]\nlabel = "Claude Sonnet 4.6 Execution + Review"\n'
        'execution_model = "weiwu/aws.claude-sonnet-4.6"\n'
        'execution_reasoning = "ultra"\n'
        'review_model = "weiwu/aws.claude-sonnet-4.6"\n'
        'review_reasoning = "ultra"\n',
        encoding="utf-8",
    )
    # Provide a refill state so snapshot is IDLE not UNINITIALIZED
    refill = windows_root / "data" / "refill"
    refill.mkdir(parents=True)
    (refill / "refill_state.json").write_text(
        json.dumps({"updated_at": 1.0, "target_total": 80}), encoding="utf-8"
    )
    result = monitor.snapshot(root, windows_root, tmp_path / "sessions")
    assert result["preferred"] == {"sonnet": 80}, (
        "Sonnet-only profile must yield preferred={sonnet: target}, "
        "not stale preferred=v4,k3 semantics"
    )


def test_preferred_reflects_sonnet_dual_profile(tmp_path, monkeypatch):
    """snapshot() preferred shows {sonnet: exec_slots, k3: review_slots} for dual profile."""
    monitor = load_monitor()
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})
    root = tmp_path / "loop"
    windows_root = tmp_path / "windows"
    config = root / "config"
    config.mkdir(parents=True)
    (config / "model_profiles.toml").write_text(
        'active_profile = "sonnet46-dual"\n'
        '[profiles.sonnet46-dual]\nlabel = "Claude Sonnet 4.6 Execution + K3 Review"\n'
        'execution_model = "weiwu/aws.claude-sonnet-4.6"\n'
        'execution_reasoning = "ultra"\n'
        'review_model = "weiwu-k3/kimi-k3"\n'
        'review_reasoning = "max"\n',
        encoding="utf-8",
    )
    refill = windows_root / "data" / "refill"
    refill.mkdir(parents=True)
    (refill / "refill_state.json").write_text(
        json.dumps({"updated_at": 1.0, "target_total": 80}), encoding="utf-8"
    )
    result = monitor.snapshot(root, windows_root, tmp_path / "sessions")
    pref = result["preferred"]
    assert "sonnet" in pref, "Sonnet-dual profile must have sonnet key in preferred"
    assert "k3" in pref, "Sonnet-dual profile must retain k3 key in preferred"
    assert pref["sonnet"] + pref["k3"] == 80, (
        "Sonnet-dual preferred totals must equal target"
    )


def test_preferred_v4_k3_preserved_for_legacy_profile(tmp_path, monkeypatch):
    """v4/k3 preferred semantics are preserved when execution model is not Sonnet."""
    monitor = load_monitor()
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})
    root = tmp_path / "loop"
    windows_root = tmp_path / "windows"
    config = root / "config"
    config.mkdir(parents=True)
    (config / "model_profiles.toml").write_text(
        'active_profile = "v4f"\n'
        '[profiles.v4f]\nlabel = "DeepSeek V4 Flash"\n'
        'execution_model = "weiwu/deepseek-v4-flash"\n'
        'execution_reasoning = "ultra"\n'
        'review_model = "weiwu-k3/kimi-k3"\n'
        'review_reasoning = "max"\n',
        encoding="utf-8",
    )
    refill = windows_root / "data" / "refill"
    refill.mkdir(parents=True)
    (refill / "refill_state.json").write_text(
        json.dumps({"updated_at": 1.0, "target_total": 80}), encoding="utf-8"
    )
    result = monitor.snapshot(root, windows_root, tmp_path / "sessions")
    pref = result["preferred"]
    assert "v4" in pref, "v4 key must be present for legacy v4/k3 profile"
    assert "k3" in pref, "k3 key must be present for legacy v4/k3 profile"
    assert "sonnet" not in pref, "sonnet key must NOT appear for non-Sonnet profile"


def test_provider_health_has_sonnet_key(tmp_path, monkeypatch):
    """snapshot() provider_health always includes a sonnet key."""
    monitor = load_monitor()
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})
    root = tmp_path / "loop"
    windows_root = tmp_path / "windows"
    refill = windows_root / "data" / "refill"
    refill.mkdir(parents=True)
    (refill / "refill_state.json").write_text(
        json.dumps({"updated_at": 1.0, "target_total": 80}), encoding="utf-8"
    )
    result = monitor.snapshot(root, windows_root, tmp_path / "sessions")
    assert "sonnet" in result["provider_health"], (
        "provider_health must contain sonnet key for Sonnet-profile monitoring"
    )
    assert "k3" in result["provider_health"], "provider_health must still contain k3 key"


def test_provider_health_sonnet_reads_sonnet_json(tmp_path, monkeypatch):
    """When sonnet.json exists its status is surfaced in provider_health.sonnet."""
    monitor = load_monitor()
    monkeypatch.setattr(monitor, "desktop_app_server_started_at", lambda now=None: None)
    monkeypatch.setattr(monitor, "opencodex_health", lambda: {"ok": True})
    root = tmp_path / "loop"
    windows_root = tmp_path / "windows"
    ph_dir = root / "data" / "provider_health"
    ph_dir.mkdir(parents=True)
    (ph_dir / "sonnet.json").write_text(json.dumps({
        "schema": "codex-loop-provider-health/v2",
        "provider": "weiwu",
        "model": "weiwu/aws.claude-sonnet-4.6",
        "status": "healthy",
        "ts": 1000.0,
        "backoff_until": 0.0,
        "logical_pool": "sonnet",
    }), encoding="utf-8")
    refill = windows_root / "data" / "refill"
    refill.mkdir(parents=True)
    (refill / "refill_state.json").write_text(
        json.dumps({"updated_at": 1.0, "target_total": 80}), encoding="utf-8"
    )
    result = monitor.snapshot(root, windows_root, tmp_path / "sessions")
    sh = result["provider_health"]["sonnet"]
    assert sh["status"] == "healthy"
    assert sh["updated_at"] == 1000.0


def test_dashboard_shows_sonnet_provider_health_for_active_sonnet_tasks():
    """PAGE JS must reference sonnet provider health and show it when sonnet tasks run."""
    monitor = load_monitor()
    assert "d.provider_health?.sonnet" in monitor.PAGE, (
        "PAGE must reference d.provider_health?.sonnet"
    )
    assert "Sonnet上游" in monitor.PAGE, (
        "PAGE must label Sonnet provider health in Chinese"
    )
