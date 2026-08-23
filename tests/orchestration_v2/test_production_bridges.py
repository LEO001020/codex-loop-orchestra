from __future__ import annotations

import io
import json
import sys
import threading
import time
from pathlib import Path

from tests.orchestration_v2.conftest import make_root, set_routing_mode

import model_token_share_bridge as meter_bridge
import orchestration_epilogue
import sol_tool_gate_router


def test_hook_router_non_loop_cwd_passes_through(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LOOP_ROOT", raising=False)
    outside = tmp_path / "unrelated"
    outside.mkdir()
    payload = {"cwd": str(outside), "tool_name": "shell_command",
               "model": "gpt-5.6"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert sol_tool_gate_router.find_root(payload) is None
    assert sol_tool_gate_router.main() == 0
    assert capsys.readouterr().out == ""


def test_hook_router_finds_loop_policy_ancestor(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    nested = root / "nested" / "project"
    nested.mkdir(parents=True)
    monkeypatch.delenv("LOOP_ROOT", raising=False)
    assert sol_tool_gate_router.find_root({"cwd": str(nested)}) == root.resolve()


def test_router_invoke_pins_resolved_loop_root(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    script = root / "hooks" / "gate.py"
    script.parent.mkdir(exist_ok=True)
    script.write_text("import os; print(os.environ['LOOP_ROOT'])", encoding="utf-8")
    rc, out, err = sol_tool_gate_router.invoke(script, "{}")
    assert rc == 0 and err == ""
    assert Path(out.strip()) == root.resolve()


def test_epilogue_empty_queue_writes_real_heartbeat(tmp_path):
    root = make_root(tmp_path)
    result = orchestration_epilogue.run_epilogue(root, source="test")
    assert result["status"] == "PASS"
    assert result["l2"]["scanned"] == 0
    assert result["meter"]["status"] == "SKIPPED"
    assert result["refill"]["status"] == "idle"
    assert (root / "data" / "l2_queue" /
            "consumer_heartbeat.json").is_file()
    status = json.loads((root / "data" / "orchestration" /
                         "epilogue_status.json").read_text(encoding="utf-8"))
    assert status["source"] == "test"


def test_layered_epilogue_schedules_plan_consumer_without_waiting(tmp_path,
                                                                  monkeypatch):
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    calls = []

    class Proc:
        pid = 4321

    monkeypatch.setattr(orchestration_epilogue.subprocess, "Popen",
                        lambda command, **kwargs: calls.append((command, kwargs)) or Proc())
    result = orchestration_epilogue.run_epilogue(root, source="test-layered")
    assert result["plan"]["status"] == "scheduled"
    assert result["plan"]["pid"] == 4321
    assert len(calls) == 1
    assert calls[0][0][-1].endswith("plan_consumer.py")


def test_epilogue_transactions_are_serialized_not_skipped(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    active = 0
    maximum = 0
    calls = 0
    guard = threading.Lock()

    def step(_self):
        nonlocal active, maximum, calls
        with guard:
            active += 1
            maximum = max(maximum, active)
            calls += 1
        time.sleep(0.05)
        with guard:
            active -= 1
        return {}

    monkeypatch.setattr(orchestration_epilogue.StateMachine, "step", step)
    results = []
    threads = [threading.Thread(
        target=lambda: results.append(orchestration_epilogue.run_epilogue(
            root, source="concurrent"))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 2 and all(x["status"] == "PASS" for x in results)
    assert calls == 2, "the later epilogue must run after the lock, not be dropped"
    assert maximum == 1, "StateMachine/refill epilogues must never overlap"


def test_meter_bridge_single_scan_feeds_v1_and_v2(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rows = [
        {"ts": 1000.0, "model": "gpt-5.6", "usage": {
            "input_tokens": 100, "cached_input_tokens": 0,
            "output_tokens": 20, "reasoning_output_tokens": 0,
            "total_tokens": 120}, "bucket": "sol", "session": "s1",
         "agent_id": "a1", "parent_session_id": None, "source": "r1"},
        {"ts": 1000.0, "model": "gpt-5.6-terra", "usage": {
            "input_tokens": 300, "cached_input_tokens": 0,
            "output_tokens": 60, "reasoning_output_tokens": 0,
            "total_tokens": 360}, "bucket": "worker", "session": "s2",
         "agent_id": "a2", "parent_session_id": "s1", "source": "r2"},
    ]
    calls = {"collect": 0}
    monkeypatch.setattr(meter_bridge.legacy, "load_role_maps",
                        lambda _path: ({}, {}))

    def collect(*_args, **_kwargs):
        calls["collect"] += 1
        return rows

    monkeypatch.setattr(meter_bridge.legacy, "collect", collect)
    result = meter_bridge.refresh(root, sessions, force=True, now=1000.0,
                                  f2_start=1.0)
    assert result["status"] == "OK" and calls["collect"] == 1
    assert (root / "data" / "usage" / "model_token_share.json").is_file()
    comparison = json.loads((root / "data" / "usage" /
                             "meter_shadow_comparison.json").read_text(
                                 encoding="utf-8"))
    assert comparison["status"] == "PASS"
    again = meter_bridge.refresh(root, sessions, force=True, now=1001.0,
                                 f2_start=1.0)
    assert again["v2_rows_added"] == 0
    assert again["v2_rows_duplicate"] == 2


def test_hook_router_shadow_enforces_v1_and_records_v2(tmp_path, monkeypatch,
                                                        capsys):
    root = make_root(tmp_path)
    payload = {"cwd": str(root), "tool_name": "shell_command",
               "model": "gpt-5.6"}
    monkeypatch.setattr(sol_tool_gate_router, "find_root", lambda _p: root)
    monkeypatch.setattr(sol_tool_gate_router, "read_mode", lambda _r: "shadow")

    def invoke(path: Path, _raw: str):
        if path.name == "sol_tool_gate.py":
            return 0, '{"deny":"v1"}\n', ""
        return 0, "", ""

    monkeypatch.setattr(sol_tool_gate_router, "invoke", invoke)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert sol_tool_gate_router.main() == 0
    assert "deny" in capsys.readouterr().out
    rows = [json.loads(line) for line in (root / "data" / "governor" /
            "hook_shadow.ndjsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["v1_deny"] is True
    assert rows[-1]["v2_deny"] is False
    assert rows[-1]["agree"] is False
