from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def load_gate():
    hooks = Path(__file__).resolve().parents[2] / "hooks"
    sys.path.insert(0, str(hooks))
    path = hooks / "root_agent_spawn_gate.py"
    spec = importlib.util.spec_from_file_location("root_agent_spawn_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def rollout(path: Path, payload: dict) -> None:
    path.write_text(json.dumps({"type": "session_meta", "payload": payload}) + "\n",
                    encoding="utf-8")


def payload(**arguments):
    return {"tool_name": "multi_agent_v1__spawn_agent", "session_id": "root",
            "tool_input": arguments}


def test_roleless_default_and_fork_births_are_denied(tmp_path):
    gate = load_gate()
    worker_model, worker_effort = gate.approved_route("worker")
    rollout(tmp_path / "rollout-root.jsonl", {"id": "root"})
    for arguments in ({}, {"agent_type": "default"}, {
        "agent_type": "worker", "model": worker_model,
        "reasoning_effort": worker_effort, "fork_context": True,
    }):
        result = gate.decision(payload(**arguments), tmp_path)
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_k3_sol_and_missing_effort_are_denied(tmp_path):
    gate = load_gate()
    verifier_model, _ = gate.approved_route("verifier")
    rollout(tmp_path / "rollout-root.jsonl", {"id": "root"})
    for model, effort in (("weiwu-k3/kimi-k3", "ultra"),
                          ("gpt-5.6-sol", "ultra"),
                          (verifier_model, "")):
        result = gate.decision(payload(agent_type="verifier", model=model,
                                       reasoning_effort=effort), tmp_path)
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_explicit_profile_role_and_independent_context_is_allowed(tmp_path):
    gate = load_gate()
    verifier_model, verifier_effort = gate.approved_route("verifier")
    rollout(tmp_path / "rollout-root.jsonl", {"id": "root"})
    result = gate.decision(payload(
        agent_type="verifier", model=verifier_model,
        reasoning_effort=verifier_effort, fork_context=False,
    ), tmp_path)
    assert result is None


def test_active_profile_routes_execution_and_review_roles_separately():
    gate = load_gate()
    import tomllib
    with gate.PROFILE_PATH.open("rb") as handle:
        document = tomllib.load(handle)
    profile = document["profiles"][document["active_profile"]]
    worker_route = gate.approved_route("worker")
    duty_route = gate.approved_route("duty_officer")
    verifier_route = gate.approved_route("verifier")
    reviewer_route = gate.approved_route("reviewer")
    planner_route = gate.approved_route("plan_expander")
    assert worker_route == duty_route
    assert verifier_route == reviewer_route == planner_route
    assert worker_route == (profile["execution_model"], profile["execution_reasoning"])
    assert verifier_route == (profile["review_model"], profile["review_reasoning"])


def test_invalid_profile_fails_closed(tmp_path, monkeypatch):
    gate = load_gate()
    profile = tmp_path / "model_profiles.toml"
    profile.write_text('active_profile = "missing"\n[profiles]\n', encoding="utf-8")
    monkeypatch.setattr(gate, "PROFILE_PATH", profile)
    rollout(tmp_path / "rollout-root.jsonl", {"id": "root"})
    result = gate.decision(payload(
        agent_type="worker", model="weiwu/deepseek-v4-flash",
        reasoning_effort="ultra", fork_context=False,
    ), tmp_path)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "active model profile" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_child_is_left_to_leaf_gate(tmp_path):
    gate = load_gate()
    verifier_model, verifier_effort = gate.approved_route("verifier")
    rollout(tmp_path / "rollout-child.jsonl", {"id": "child", "parent_thread_id": "root"})
    item = payload(agent_type="verifier", model=verifier_model,
                   reasoning_effort=verifier_effort)
    item["session_id"] = "child"
    assert gate.decision(item, tmp_path) is None


def test_compact_agent_tool_is_pinned_like_spawn_agent(tmp_path, monkeypatch):
    gate = load_gate()
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    worker_model, worker_effort = gate.approved_route("worker")
    rollout(tmp_path / "rollout-root.jsonl", {"id": "root"})
    item = payload(agent_type="worker", model=worker_model,
                   reasoning_effort=worker_effort, fork_context=False,
                   message="任务名：执行路由核验\n只读")
    item["tool_name"] = "Agent"
    assert gate.decision(item, tmp_path) is None
    item["tool_input"]["fork_context"] = True
    assert gate.decision(item, tmp_path)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_create_thread_skips_model_pin(tmp_path):
    gate = load_gate()
    result = gate.decision({
        "tool_name": "mcp__codex_app__create_thread",
        "session_id": "root",
        "tool_input": {},
    }, tmp_path)
    assert result is None
