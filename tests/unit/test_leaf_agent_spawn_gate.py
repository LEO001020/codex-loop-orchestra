from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load_gate():
    path = Path(__file__).resolve().parents[2] / "hooks" / "leaf_agent_spawn_gate.py"
    spec = importlib.util.spec_from_file_location("leaf_agent_spawn_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def rollout(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "session_meta", "payload": payload}) + "\n",
                    encoding="utf-8")


def test_root_spawn_is_allowed(tmp_path):
    gate = load_gate()
    rollout(tmp_path / "rollout-root.jsonl", {"id": "root"})
    assert gate.decision({"tool_name": "spawn_agent", "session_id": "root"},
                         tmp_path) is None


def test_child_spawn_is_denied(tmp_path):
    gate = load_gate()
    rollout(tmp_path / "rollout-child.jsonl",
            {"id": "child", "parent_thread_id": "root"})
    result = gate.decision({"tool_name": "multi_agent_v1__spawn_agent",
                            "session_id": "child"}, tmp_path)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "leaf agents" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_non_spawn_tool_is_allowed_for_child(tmp_path):
    gate = load_gate()
    rollout(tmp_path / "rollout-child.jsonl",
            {"id": "child", "parent_thread_id": "root"})
    assert gate.decision({"tool_name": "shell_command", "session_id": "child"},
                         tmp_path) is None


def test_headless_leaf_marker_denies_without_desktop_parent(tmp_path, monkeypatch):
    gate = load_gate()
    monkeypatch.setenv("LOOP_LEAF_AGENT", "1")
    result = gate.decision({"tool_name": "spawn_agent", "session_id": "headless"},
                           tmp_path)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_nested_source_metadata_is_detected(tmp_path):
    gate = load_gate()
    rollout(tmp_path / "rollout-child.jsonl", {"id": "child", "source": {
        "subagent": {"thread_spawn": {"parent_thread_id": "root"}}}})
    assert gate.session_is_child("child", tmp_path) is True


def test_compact_agent_name_is_a_spawn_tool():
    gate = load_gate()
    assert gate.is_spawn_tool("Agent") is True
    assert gate.is_spawn_tool("spawnAgent") is True
    assert gate.is_spawn_agent_tool("Agent") is True
    assert gate.is_spawn_agent_tool("spawnAgent") is True
    assert gate.is_spawn_tool("close_agent") is False
    assert gate.is_spawn_agent_tool("close_agent") is False
    assert gate.is_spawn_agent_tool("create_thread") is False
    assert gate.is_spawn_tool("create_thread") is True
