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


def test_wrong_model_and_missing_effort_are_denied(tmp_path):
    gate = load_gate()
    review_model, _ = gate.approved_route("verifier")
    rollout(tmp_path / "rollout-root.jsonl", {"id": "root"})
    for model, effort in (("other/model", "high"),
                          ("another/model", "medium"),
                          (review_model, "")):
        result = gate.decision(payload(agent_type="verifier", model=model,
                                       reasoning_effort=effort), tmp_path)
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_explicit_profile_role_and_independent_context_is_allowed(tmp_path):
    gate = load_gate()
    review_model, review_effort = gate.approved_route("verifier")
    rollout(tmp_path / "rollout-root.jsonl", {"id": "root"})
    result = gate.decision(payload(
        agent_type="verifier", model=review_model,
        reasoning_effort=review_effort, fork_context=False,
    ), tmp_path)
    assert result is None


def test_child_is_left_to_leaf_gate(tmp_path):
    gate = load_gate()
    review_model, review_effort = gate.approved_route("verifier")
    rollout(tmp_path / "rollout-child.jsonl", {"id": "child", "parent_thread_id": "root"})
    item = payload(agent_type="verifier", model=review_model,
                   reasoning_effort=review_effort)
    item["session_id"] = "child"
    assert gate.decision(item, tmp_path) is None
