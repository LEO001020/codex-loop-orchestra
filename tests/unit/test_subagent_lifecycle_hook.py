import contextlib
import importlib.util
import json
import os
import tomllib
import types
from pathlib import Path


PKG = Path(__file__).resolve().parents[2]
SCRIPT = PKG / "hooks" / "subagent_lifecycle.py"
SPEC = importlib.util.spec_from_file_location("subagent_lifecycle", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def load_roster(root):
    return json.loads((root / "data" / "lifecycle" / "native_roster.json").read_text(encoding="utf-8"))


def lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


def test_recompute_refill_uses_v2_and_syncs_real_ledger(tmp_path, monkeypatch):
    import shutil

    root = tmp_path / "loop"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    for name in ("refill_policy.toml", "orchestration_policy_v2.toml"):
        shutil.copy2(PKG / "config" / name, root / "config" / name)
    monkeypatch.setenv("LOOP_ROOT", str(root))
    (root / "data" / "progress_ledger.json").write_text(json.dumps({
        "packets": {
            "worker-packet": {"state": "DISPATCHABLE", "role": "worker"},
            "verify-packet": {"state": "DISPATCHABLE", "role": "verifier"},
        }
    }), encoding="utf-8")

    state = MOD.recompute_refill(root)

    assert state["schema"] == "codex-loop-refill/v3"
    assert state["pending"] == {"total": 2, "v4": 1, "k3": 1}
    with (root / "config" / "refill_policy.toml").open("rb") as handle:
        concurrency = tomllib.load(handle)["concurrency"]
    assert state["preferred_target"] == {
        "v4": concurrency["v4_target"],
        "k3": concurrency["k3_target"],
    }
    assert state["target_total"] == concurrency["target_total"]


def test_refresh_meter_reuses_bridge_debounce_entrypoint(tmp_path, monkeypatch):
    root = tmp_path / "loop"
    (root / "metering").mkdir(parents=True)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("CODEX_SESSIONS_DIR", str(sessions))
    calls = []
    fake = types.ModuleType("model_token_share_bridge")
    fake.refresh = lambda base, sources, force=False: calls.append(
        (base, sources, force)) or {"status": "DEBOUNCED"}
    monkeypatch.setitem(__import__("sys").modules, "model_token_share_bridge", fake)

    result = MOD.refresh_meter(root)

    assert result == {"status": "DEBOUNCED"}
    assert calls == [(root.resolve(), sessions.resolve(), False)]


def test_hook_atomic_json_retries_transient_permission_error(tmp_path,
                                                             monkeypatch):
    target = tmp_path / "native_roster.json"
    real_replace = MOD.os.replace
    calls = {"count": 0}

    def flaky(source, destination):
        calls["count"] += 1
        if calls["count"] <= 3:
            raise PermissionError(5, "transient reader hold", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(MOD.os, "replace", flaky)
    MOD.atomic_json(target, {"ok": True}, replace_timeout_s=1.0)
    assert calls["count"] == 4
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert not list(tmp_path.glob("native_roster.json.*.tmp"))


def test_hook_atomic_json_persistent_block_is_visible_and_cleans_tmp(
        tmp_path, monkeypatch):
    target = tmp_path / "native_roster.json"

    def blocked(_source, destination):
        raise PermissionError(5, "persistent reader hold", str(destination))

    monkeypatch.setattr(MOD.os, "replace", blocked)
    try:
        MOD.atomic_json(target, {"ok": False}, replace_timeout_s=0.01)
    except RuntimeError as exc:
        assert "remained blocked" in str(exc)
    else:
        raise AssertionError("persistent roster block was silently ignored")
    assert not list(tmp_path.glob("native_roster.json.*.tmp"))


def test_task_name_binds_to_agent_and_stop_close_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("PreToolUse", {"tool_name": "multi_agent_v1__spawn_agent",
        "session_id": "parent-1", "tool_use_id": "tool-1",
        "tool_input": {"message": "任务名：审核生命周期边界\n只读"}})
    MOD.handle("SubagentStart", {"session_id": "parent-1", "agent_id": "agent-1",
                                  "agent_type": "worker", "model": "v4"})
    MOD.handle("SubagentStop", {"session_id": "parent-1", "agent_id": "agent-1"})
    MOD.handle("SubagentStop", {"session_id": "parent-1", "agent_id": "agent-1"})
    item = load_roster(tmp_path)["agents"]["agent-1"]
    assert item["task_name"] == "审核生命周期边界"
    assert item["status"] == "terminal"
    requests = lines(tmp_path / "data" / "lifecycle" / "close_requests.ndjson")
    assert len(requests) == 1
    assert requests[0]["action"] == "host_close_agent_required"


def test_structured_items_task_name_is_extracted(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("PreToolUse", {"tool_name": "spawn_agent", "session_id": "p",
        "tool_use_id": "t", "tool_input": {"items": [
            {"type": "text", "text": "任务名：结构化子任务\n只读"}]}})
    MOD.handle("SubagentStart", {"session_id": "p", "agent_id": "a"})
    assert load_roster(tmp_path)["agents"]["a"]["task_name"] == "结构化子任务"


def test_redacted_pretool_prompt_recovers_by_tool_id_from_parent_rollout(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    sessions = tmp_path / "sessions"; sessions.mkdir()
    monkeypatch.setenv("CODEX_SESSIONS_DIR", str(sessions))
    MOD.handle("PreToolUse", {"tool_name": "spawn_agent", "session_id": "parent-x",
                               "tool_use_id": "call-x", "tool_input": {}})
    (sessions / "rollout-now-parent-x.jsonl").write_text(json.dumps({
        "type": "response_item", "payload": {"type": "function_call",
        "call_id": "call-x", "arguments": json.dumps({
            "message": "任务名：父Rollout恢复命名\n只读"}, ensure_ascii=False)}}) + "\n",
        encoding="utf-8")
    MOD.handle("SubagentStart", {"session_id": "parent-x", "agent_id": "agent-x"})
    item = load_roster(tmp_path)["agents"]["agent-x"]
    assert item["task_name"] == "父Rollout恢复命名"
    assert item["mapping_confidence"] == "tool_use+rollout"


def test_parent_stop_marks_native_and_requests_exec_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("SubagentStart", {"session_id": "parent-2", "agent_id": "agent-2"})
    life = tmp_path / "data" / "lifecycle"
    MOD.atomic_json(life / "exec_roster.json", {"jobs": {"packet-2": {
        "state": "running", "parent_session_id": "parent-2",
        "run_id": "packet-2-a0", "attempt": 0}}})
    MOD.handle("Stop", {"session_id": "parent-2"})
    assert load_roster(tmp_path)["agents"]["agent-2"]["status"] == "interrupted"
    cancel = json.loads((life / "cancel" / "packet-2.json").read_text(encoding="utf-8"))
    assert cancel["reason"] == "parent_stop"
    assert cancel["run_id"] == "packet-2-a0" and cancel["attempt"] == 0


def test_host_close_tool_confirms_request_consumption(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("SubagentStart", {"session_id": "p-close", "agent_id": "a-close"})
    MOD.handle("SubagentStop", {"session_id": "p-close", "agent_id": "a-close"})
    MOD.handle("PreToolUse", {"tool_name": "close_agent", "session_id": "p-close",
                               "tool_input": {"target": "a-close"}})
    item = load_roster(tmp_path)["agents"]["a-close"]
    assert item["close_request_consumed"] is True
    assert item["host_close_confirmed_at"] > item["started_at"]


def test_close_before_stop_does_not_emit_a_second_close_request(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("SubagentStart", {"session_id": "p-early", "agent_id": "a-early"})
    MOD.handle("PreToolUse", {"tool_name": "close_agent", "session_id": "p-early",
                               "tool_input": {"target": "a-early"}})
    MOD.handle("SubagentStop", {"session_id": "p-early", "agent_id": "a-early"})
    item = load_roster(tmp_path)["agents"]["a-early"]
    assert item["close_request_consumed"] is True
    assert item["close_observed_before_request"] is True
    assert not (tmp_path / "data" / "lifecycle" / "close_requests.ndjson").exists()


def test_stop_without_session_id_does_not_cancel_unrelated_work(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("SubagentStart", {"session_id": "owner", "agent_id": "owned"})
    life = tmp_path / "data" / "lifecycle"
    MOD.atomic_json(life / "exec_roster.json", {"jobs": {"packet": {
        "state": "running", "parent_session_id": "owner", "run_id": "r", "attempt": 0}}})
    MOD.handle("Stop", {})
    assert load_roster(tmp_path)["agents"]["owned"]["status"] == "running"
    assert not (life / "cancel" / "packet.json").exists()


def test_stop_only_cancels_matching_parent_session(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    life = tmp_path / "data" / "lifecycle"
    MOD.atomic_json(life / "exec_roster.json", {"jobs": {
        "mine": {"state": "running", "parent_session_id": "owner-a",
                 "run_id": "mine-a0", "attempt": 0},
        "theirs": {"state": "running", "parent_session_id": "owner-b",
                   "run_id": "theirs-a0", "attempt": 0}}})
    MOD.handle("Stop", {"session_id": "owner-a"})
    assert (life / "cancel" / "mine.json").exists()
    assert not (life / "cancel" / "theirs.json").exists()


def test_spawn_result_agent_id_prevents_fifo_cross_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    sessions = tmp_path / "sessions"; sessions.mkdir()
    monkeypatch.setenv("CODEX_SESSIONS_DIR", str(sessions))
    for tool, name in (("call-one", "任务一"), ("call-two", "任务二")):
        MOD.handle("PreToolUse", {"tool_name": "spawn_agent", "session_id": "parent-y",
            "tool_use_id": tool, "tool_input": {"message": "任务名：%s" % name}})
    rows = [
        {"type": "response_item", "payload": {"type": "function_call_output",
         "call_id": "call-one", "output": json.dumps({"agent_id": "agent-one"})}},
        {"type": "response_item", "payload": {"type": "function_call_output",
         "call_id": "call-two", "output": json.dumps({"agent_id": "agent-two"})}},
    ]
    (sessions / "rollout-now-parent-y.jsonl").write_text(
        "\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
    MOD.handle("SubagentStart", {"session_id": "parent-y", "agent_id": "agent-two"})
    MOD.handle("SubagentStart", {"session_id": "parent-y", "agent_id": "agent-one"})
    agents = load_roster(tmp_path)["agents"]
    assert agents["agent-two"]["task_name"] == "任务二"
    assert agents["agent-one"]["task_name"] == "任务一"


def test_cold_start_recovers_terminal_rollout(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("SubagentStart", {"session_id": "parent-3", "agent_id": "agent-3"})
    sessions = tmp_path / "sessions" / "2026" / "08" / "10"
    sessions.mkdir(parents=True)
    rollout = sessions / "rollout-now-agent-3.jsonl"
    rollout.write_text(
        json.dumps({"type": "session_meta", "payload": {"agent_nickname": "子任务03"}}) + "\n" +
        json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n",
        encoding="utf-8")
    MOD.handle("SessionStart", {"source": "startup"}, sessions)
    item = load_roster(tmp_path)["agents"]["agent-3"]
    assert item["status"] == "terminal"
    assert item["nickname"] == "子任务03"
    assert item["terminal_reason"] == "rollout_task_complete"


def test_cold_start_marks_dead_supervisor_generation_lost(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    life = tmp_path / "data" / "lifecycle"
    MOD.atomic_json(life / "exec_roster.json", {"schema": "codex-loop-exec-roster/v2",
        "jobs": {"lost-packet": {"state": "running", "run_id": "lost-a1",
            "attempt": 1, "supervisor_pid": 999999999, "os_pid": 999999998}}})
    sessions = tmp_path / "sessions"; sessions.mkdir()
    MOD.handle("SessionStart", {"source": "startup"}, sessions)
    doc = json.loads((life / "exec_roster.json").read_text(encoding="utf-8"))
    assert doc["jobs"]["lost-packet"]["state"] == "lost"
    root_events = lines(tmp_path / "data" / "events.ndjson")
    assert root_events[-1]["event"] == "exec_failed"
    assert root_events[-1]["run_id"] == "lost-a1"
    assert root_events[-1]["detail"]["why"] == "supervisor_lost"


def test_expired_pending_is_pruned_before_duplicate_spawn_key(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(MOD.time, "time", lambda: clock["now"])
    payload = {"tool_name": "spawn_agent", "session_id": "parent-ttl",
               "tool_use_id": "tool-reused",
               "tool_input": {"task_name": "old-task"}}
    MOD.handle("PreToolUse", payload)
    clock["now"] += MOD.PENDING_TTL_SECONDS + 1
    payload["tool_input"] = {"task_name": "new-task"}
    MOD.handle("PreToolUse", payload)
    pending = load_roster(tmp_path)["pending"]
    assert len(pending) == 1 and pending[0]["task_name"] == "new-task"
    MOD.handle("SubagentStart", {"session_id": "parent-ttl", "agent_id": "agent-ttl"})
    assert load_roster(tmp_path)["agents"]["agent-ttl"]["task_name"] == "new-task"
    audits = lines(tmp_path / "data" / "lifecycle" / "events.ndjson")
    expired = [row for row in audits if row["event"] == "pending_expired"]
    assert len(expired) == 1 and expired[0]["trigger"] == "PreToolUse"


def test_cold_exec_reconcile_keeps_matching_supervisor_identity(tmp_path, monkeypatch):
    roster = MOD.NativeRoster(tmp_path)
    doc = {"schema": "codex-loop-exec-roster/v2", "jobs": {"pkt": {
        "state": "running", "run_id": "run-1", "attempt": 2,
        "supervisor_pid": 4242, "supervisor_proc_start_ticks": 777,
        "os_pid": 9001, "worker_proc_start_ticks": 123, "history": []}}}
    writes, killpg = [], []
    monkeypatch.setattr(MOD, "lock", contextlib.nullcontext)
    monkeypatch.setattr(MOD, "read_json", lambda path, default: doc)
    monkeypatch.setattr(MOD, "atomic_json", lambda path, value: writes.append(value))
    monkeypatch.setattr(MOD, "process_matches",
                        lambda pid, ticks=None: (pid, ticks) == (4242, 777))
    monkeypatch.setattr(MOD, "os", types.SimpleNamespace(
        name="posix", killpg=lambda pid, sig: killpg.append((pid, sig))))
    MOD.cold_reconcile_exec(roster)
    assert doc["jobs"]["pkt"]["state"] == "running"
    assert writes == [] and killpg == []
    assert not (tmp_path / "events.ndjson").exists()


def test_cold_exec_reconcile_never_kills_reused_worker_pid(tmp_path, monkeypatch):
    roster = MOD.NativeRoster(tmp_path)
    doc = {"schema": "codex-loop-exec-roster/v2", "jobs": {"pkt": {
        "state": "running", "run_id": "run-current", "attempt": 3,
        "supervisor_pid": 4242, "supervisor_proc_start_ticks": 777,
        "os_pid": 9001, "worker_proc_start_ticks": 123, "history": []}}}
    writes, killpg = [], []
    monkeypatch.setattr(MOD, "lock", contextlib.nullcontext)
    monkeypatch.setattr(MOD, "read_json", lambda path, default: doc)
    monkeypatch.setattr(MOD, "atomic_json", lambda path, value: writes.append(value))
    monkeypatch.setattr(MOD, "process_matches", lambda pid, ticks=None: False)
    monkeypatch.setattr(MOD, "Path", lambda value: types.SimpleNamespace(exists=lambda: True))
    monkeypatch.setattr(MOD, "os", types.SimpleNamespace(
        name="posix", killpg=lambda pid, sig: killpg.append((pid, sig))))
    MOD.cold_reconcile_exec(roster)
    assert writes[0]["jobs"]["pkt"]["state"] == "lost"
    assert killpg == []
    failed = [row for row in lines(tmp_path / "events.ndjson")
              if row["event"] == "exec_failed"]
    assert len(failed) == 1
    assert failed[0]["run_id"] == "run-current" and failed[0]["attempt"] == 3


def test_cold_exec_reconcile_cleanup_failure_keeps_reservation(tmp_path, monkeypatch):
    roster = MOD.NativeRoster(tmp_path)
    doc = {"schema": "codex-loop-exec-roster/v2", "jobs": {"pkt": {
        "state": "running", "run_id": "run-live", "attempt": 1,
        "supervisor_pid": 4242, "supervisor_proc_start_ticks": 777,
        "os_pid": 9001, "worker_proc_start_ticks": 123, "history": []}}}
    writes = []
    monkeypatch.setattr(MOD, "lock", contextlib.nullcontext)
    monkeypatch.setattr(MOD, "read_json", lambda path, default: doc)
    monkeypatch.setattr(MOD, "atomic_json", lambda path, value: writes.append(value))
    monkeypatch.setattr(MOD, "process_matches",
                        lambda pid, ticks=None: (pid, ticks) == (9001, 123))
    monkeypatch.setattr(MOD, "Path", lambda value: types.SimpleNamespace(exists=lambda: True))

    def denied(pid, sig):
        raise PermissionError("not owned")

    monkeypatch.setattr(MOD, "os", types.SimpleNamespace(name="posix", killpg=denied))
    MOD.cold_reconcile_exec(roster)
    item = writes[-1]["jobs"]["pkt"]
    assert item["state"] == "running"
    assert item["cleanup_status"] == "cleanup_failed_live"
    assert not (tmp_path / "events.ndjson").exists()


def test_agent_id_thread_id_conflict_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("SubagentStart", {"session_id": "p", "agent_id": "a-1", "thread_id": "t-9"})
    roster_path = tmp_path / "data" / "lifecycle" / "native_roster.json"
    assert not roster_path.exists()
    hits = [r for r in lines(tmp_path / "data" / "lifecycle" / "events.ndjson")
            if r["event"] == "identity_conflict"]
    assert len(hits) == 1
    assert hits[0]["pair"] == "agent_id/thread_id"
    assert hits[0]["context"] == "SubagentStart"
    assert hits[0]["action"] == "fail_closed"


def test_session_parent_thread_conflict_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("SubagentStart", {"session_id": "p-1", "parent_thread_id": "p-2",
                                  "agent_id": "a-1"})
    roster_path = tmp_path / "data" / "lifecycle" / "native_roster.json"
    assert not roster_path.exists()
    hits = [r for r in lines(tmp_path / "data" / "lifecycle" / "events.ndjson")
            if r["event"] == "identity_conflict"]
    assert len(hits) == 1
    assert hits[0]["pair"] == "session_id/parent_thread_id"
    assert hits[0]["action"] == "fail_closed"


def test_stop_identity_conflict_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("SubagentStart", {"session_id": "p", "agent_id": "a-stop"})
    MOD.handle("SubagentStop", {"session_id": "p", "agent_id": "a-stop", "thread_id": "other"})
    item = load_roster(tmp_path)["agents"]["a-stop"]
    assert item["status"] == "running"
    assert not (tmp_path / "data" / "lifecycle" / "close_requests.ndjson").exists()
    hits = [r for r in lines(tmp_path / "data" / "lifecycle" / "events.ndjson")
            if r["event"] == "identity_conflict"]
    assert len(hits) == 1
    assert hits[0]["context"] == "SubagentStop"


def test_parent_stop_conflict_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("SubagentStart", {"session_id": "p", "agent_id": "a-keep"})
    MOD.handle("Stop", {"session_id": "p", "parent_thread_id": "p-other"})
    assert load_roster(tmp_path)["agents"]["a-keep"]["status"] == "running"
    hits = [r for r in lines(tmp_path / "data" / "lifecycle" / "events.ndjson")
            if r["event"] == "identity_conflict"]
    assert len(hits) == 1
    assert hits[0]["context"] == "Stop"
    assert hits[0]["pair"] == "session_id/parent_thread_id"


def test_unmapped_start_falls_back_to_agent_id_with_degraded_source(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("SubagentStart", {"session_id": "p", "agent_id": "agent-fb"})
    item = load_roster(tmp_path)["agents"]["agent-fb"]
    assert item["task_name"] == "agent-fb"
    assert item["name_degraded"] is True
    assert item["name_source"] == "agent_id_fallback"
    assert item["mapping_confidence"] == "unmapped"
    row = lines(tmp_path / "data" / "lifecycle" / "name_map.jsonl")[0]
    assert row["task_name"] == "agent-fb"
    assert row["name_degraded"] is True and row["name_source"] == "agent_id_fallback"


def test_pretool_without_name_writes_no_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("PreToolUse", {"tool_name": "spawn_agent", "session_id": "p",
                               "tool_use_id": "t1", "tool_input": {}})
    MOD.handle("SubagentStart", {"session_id": "p", "agent_id": "a-no-name"})
    item = load_roster(tmp_path)["agents"]["a-no-name"]
    assert item["task_name"] == "a-no-name"
    assert item["name_degraded"] is True and item["name_source"] == "agent_id_fallback"
    for name in ("native_roster.json", "name_map.jsonl"):
        text = (tmp_path / "data" / "lifecycle" / name).read_text(encoding="utf-8")
        assert "未命名子任务" not in text and "未映射子任务" not in text


def test_stop_orphan_falls_back_to_agent_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    MOD.handle("SubagentStop", {"session_id": "p", "agent_id": "orphan-fb"})
    item = load_roster(tmp_path)["agents"]["orphan-fb"]
    assert item["task_name"] == "orphan-fb"
    assert item["name_degraded"] is True and item["name_source"] == "agent_id_fallback"
    req = lines(tmp_path / "data" / "lifecycle" / "close_requests.ndjson")[0]
    assert req["task_name"] == "orphan-fb"


def test_historical_placeholder_entry_is_not_rewritten(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    roster_path = tmp_path / "data" / "lifecycle" / "native_roster.json"
    roster_path.parent.mkdir(parents=True, exist_ok=True)
    roster_path.write_text(json.dumps({"schema": "codex-loop-native-roster/v1",
        "pending": [], "agents": {"old-a": {"agent_id": "old-a",
        "task_name": "未命名子任务", "parent_session_id": "p", "status": "terminal"}}}),
        encoding="utf-8")
    MOD.handle("SubagentStart", {"session_id": "p", "agent_id": "old-a"})
    MOD.handle("SubagentStop", {"session_id": "p", "agent_id": "old-a"})
    item = load_roster(tmp_path)["agents"]["old-a"]
    assert item["task_name"] == "未命名子任务"
    assert "name_degraded" not in item and "name_source" not in item


def write_refill_config(root, target=48, low=36, cap=50):
    cfg = root / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "refill_policy.toml").write_text(
        "[meta]\npolicy_version = \"test-v2\"\n"
        "[concurrency]\n"
        "target_total = %d\nv4_target = 36\nk3_target = 12\n"
        "v4_low_water = 27\nk3_low_water = 9\n"
        "reservations_borrowable = true\n"
        "[spawn_throttle]\nspawn_interval_ms = 1000\nmax_initializing = 8\n"
        "health_gate_every = 8\nfailure_backoff_seconds = 30\n"
        "health_timeout_ms = 2000\n" % target,
        encoding="utf-8")


def write_refill_queue(root, v4=0, k3=0):
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    packets = {
        **{"v4-%d" % i: {"state": "DISPATCHABLE", "role": "worker"}
           for i in range(v4)},
        **{"k3-%d" % i: {"state": "DISPATCHABLE", "role": "verifier"}
           for i in range(k3)},
    }
    (data / "progress_ledger.json").write_text(
        json.dumps({"packets": packets}), encoding="utf-8")


def read_refill_state(root):
    return json.loads((root / "data" / "refill" / "refill_state.json").read_text(encoding="utf-8"))


def test_subagent_stop_recomputes_refill_immediately(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    write_refill_config(tmp_path)
    write_refill_queue(tmp_path, v4=10)
    MOD.handle("SubagentStart", {"session_id": "parent-r", "agent_id": "agent-r1",
                                  "model": "provider-a/v4-executor"})
    MOD.handle("SubagentStart", {"session_id": "parent-r", "agent_id": "agent-r2",
                                  "model": "provider-a/v4-executor"})
    MOD.handle("SubagentStop", {"session_id": "parent-r", "agent_id": "agent-r2"})
    st = read_refill_state(tmp_path)
    assert st["refill_required"] is True
    assert st["active"]["total"] == 1
    assert st["deficit"]["total"] == 10
    assert st["model_pool"] == ["v4"]
    assert any(row["event"] == "refill_required"
               for row in lines(tmp_path / "data" / "refill" / "events.ndjson"))


def test_subagent_stop_triggers_existing_refill_actuator(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    write_refill_config(tmp_path)
    write_refill_queue(tmp_path, v4=1)
    calls = []
    monkeypatch.setattr(MOD, "schedule_refill_actuator",
                        lambda root=None, *, source: calls.append((root, source)) or
                        {"status": "scheduled", "pid": 123})
    MOD.handle("SubagentStart", {"session_id": "parent-a", "agent_id": "agent-a",
                                  "model": "provider-a/v4-executor"})
    MOD.handle("SubagentStop", {"session_id": "parent-a", "agent_id": "agent-a"})
    assert calls == [(tmp_path.resolve(), "desktop_subagent_stop")]


def test_session_start_triggers_cold_reconcile_actuator(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    write_refill_config(tmp_path)
    write_refill_queue(tmp_path, v4=1)
    calls = []
    monkeypatch.setattr(MOD, "schedule_refill_actuator",
                        lambda root=None, *, source: calls.append((root, source)) or
                        {"status": "scheduled", "pid": 124})
    MOD.handle("SessionStart", {"source": "startup"}, tmp_path / "sessions")
    assert calls == [(tmp_path.resolve(), "desktop_session_start")]


def test_refill_actuator_failure_is_audited_and_not_claimed_success(
        tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    import refill_consumer_v2

    def fail(_root, *, source):
        raise RuntimeError("synthetic launcher failure")

    monkeypatch.setattr(refill_consumer_v2, "schedule_run", fail)
    assert MOD.schedule_refill_actuator(tmp_path, source="test") is None
    events = lines(tmp_path / "data" / "lifecycle" / "events.ndjson")
    assert events[-1]["event"] == "refill_actuator_failed"
    assert "synthetic launcher failure" in events[-1]["error"]


def test_close_agent_recomputes_refill_immediately(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    write_refill_config(tmp_path)
    write_refill_queue(tmp_path, v4=5)
    MOD.handle("SubagentStart", {"session_id": "parent-c", "agent_id": "agent-c1",
                                  "model": "provider-a/v4-executor"})
    MOD.handle("PreToolUse", {"tool_name": "close_agent", "session_id": "parent-c",
                               "tool_input": {"target": "agent-c1"}})
    st = read_refill_state(tmp_path)
    assert st["refill_required"] is True
    assert st["active"]["total"] == 1  # close observed, stop not yet fired
    assert st["deficit"]["total"] == 5
    assert any(row["event"] == "refill_required"
               for row in lines(tmp_path / "data" / "refill" / "events.ndjson"))


def test_stop_with_empty_queue_clears_refill_required(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    write_refill_config(tmp_path)
    write_refill_queue(tmp_path, v4=0)
    MOD.handle("SubagentStart", {"session_id": "parent-e", "agent_id": "agent-e1"})
    MOD.handle("SubagentStop", {"session_id": "parent-e", "agent_id": "agent-e1"})
    st = read_refill_state(tmp_path)
    assert st["refill_required"] is False
    assert st["queue_empty"] is True
    assert st["deficit"]["total"] == 0
