from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import global_desktop_mode as desktop_mode
import global_loop_mode as hook_mode
from conftest import IMPL


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "control" / "codex-loop-s-f2"
    (root / "config").mkdir(parents=True)
    (root / "hooks").mkdir()
    (root / "config" / "global_hooks.json").write_bytes(
        (IMPL / "config" / "global_hooks.json").read_bytes())
    (root / "config" / "global_requirements.toml").write_bytes(
        (IMPL / "config" / "global_requirements.toml").read_bytes())
    (root / "config" / "global_working_agreement.md").write_text(
        "GLOBAL-WORKING-AGREEMENT\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("F2-DISCIPLINE\n", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _isolate_system_requirements(tmp_path, monkeypatch):
    path = tmp_path / "programdata" / "OpenAI" / "Codex" / "requirements.toml"
    monkeypatch.setenv("CODEX_LOOP_REQUIREMENTS_TOML", str(path))
    return path


def test_marker_controls_context_for_unrelated_workspace(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)
    marker = tmp_path / "mode.json"
    monkeypatch.setenv("CODEX_LOOP_MODE_MARKER", str(marker))

    assert hook_mode.load_active_marker(root) is None
    assert hook_mode.main is not None

    marker.write_text(json.dumps({
        "schema": hook_mode.SCHEMA,
        "active": True,
        "control_root": str(root),
    }), encoding="utf-8")
    assert hook_mode.load_active_marker(root) is not None
    assert hook_mode.emit_context(root, "SessionStart") == 0
    output = json.loads(capsys.readouterr().out)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "GLOBAL-WORKING-AGREEMENT" in context
    assert "F2-DISCIPLINE" in context
    assert f"LOOP_CONTROL_ROOT={root}" in context
    # The command hook must remain encodable on legacy Windows code pages.
    serialized = json.dumps(output, ensure_ascii=True)
    serialized.encode("ascii")

    marker.write_text(json.dumps({
        "schema": hook_mode.SCHEMA,
        "active": False,
        "control_root": str(root),
    }), encoding="utf-8")
    assert hook_mode.load_active_marker(root) is None


def test_marker_rejects_different_control_root(tmp_path, monkeypatch):
    root = _root(tmp_path)
    marker = tmp_path / "mode.json"
    monkeypatch.setenv("CODEX_LOOP_MODE_MARKER", str(marker))
    marker.write_text(json.dumps({
        "schema": hook_mode.SCHEMA,
        "active": True,
        "control_root": str(tmp_path / "other"),
    }), encoding="utf-8")
    assert hook_mode.load_active_marker(root) is None


def test_install_merges_unrelated_hooks_and_restore_is_exact(tmp_path):
    root = _root(tmp_path)
    codex_home = tmp_path / "home" / ".codex"
    codex_home.mkdir(parents=True)
    original_agents = b"ORIGINAL NORMAL GUIDANCE\n"
    original_hooks = {
        "description": "user hooks",
        "hooks": {
            "SessionStart": [{
                "matcher": "startup",
                "hooks": [{"type": "command", "command": "user-session-hook"}],
            }],
            "Stop": [{
                "matcher": ".*",
                "hooks": [{
                    "type": "command",
                    "command": "py old/subagent_lifecycle.py --event Stop",
                }],
            }],
        },
    }
    original_hook_bytes = desktop_mode.json_bytes(original_hooks)
    (codex_home / "AGENTS.md").write_bytes(original_agents)
    (codex_home / "hooks.json").write_bytes(original_hook_bytes)

    installed = desktop_mode.install(root, codex_home)
    assert installed["installed"] is True
    assert (codex_home / "AGENTS.md").read_text(encoding="utf-8") == desktop_mode.NEUTRAL_AGENTS
    hooks = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
    serialized = json.dumps(hooks)
    assert "user-session-hook" in serialized
    assert "global_loop_mode.py" in serialized
    assert "old/subagent_lifecycle.py" not in serialized
    requirements = (codex_home / "requirements.toml").read_text(encoding="utf-8")
    assert "windows_managed_dir" in requirements
    assert "--component spawn-gate" in requirements
    assert 'matcher = "^(Bash|' in requirements
    assert ('matcher = "^(Agent|spawnAgent|closeAgent)$|spawn_agent|close_agent|'
            in requirements)
    system_path = desktop_mode.system_requirements_path()
    assert system_path.exists()
    assert system_path.read_bytes() == (codex_home / "requirements.toml").read_bytes()
    verified = desktop_mode.status(root, codex_home)
    assert verified["hooks_trusted_or_managed"] is True
    assert verified["user_hooks_contain_loop"] is True
    assert verified["system_requirements_exact"] is True

    active = desktop_mode.set_active(root, codex_home, True)
    assert active["active"] is True
    assert desktop_mode.status(root, codex_home)["active"] is True
    inactive = desktop_mode.set_active(root, codex_home, False)
    assert inactive["active"] is False
    assert desktop_mode.status(root, codex_home)["active"] is False

    restored = desktop_mode.restore(root, codex_home)
    assert restored["restored"] is True
    assert (codex_home / "AGENTS.md").read_bytes() == original_agents
    assert (codex_home / "hooks.json").read_bytes() == original_hook_bytes
    assert not (codex_home / "requirements.toml").exists()
    assert not system_path.exists()


def test_install_is_idempotent_without_duplicate_managed_handlers(tmp_path):
    root = _root(tmp_path)
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "AGENTS.md").write_text("before\n", encoding="utf-8")
    (codex_home / "hooks.json").write_text('{"hooks": {}}\n', encoding="utf-8")

    desktop_mode.install(root, codex_home)
    first = (codex_home / "requirements.toml").read_bytes()
    desktop_mode.install(root, codex_home)
    second = (codex_home / "requirements.toml").read_bytes()
    assert first == second
    assert second.count(b"--component spawn-gate") == 2
    hooks = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
    serialized = json.dumps(hooks)
    assert serialized.count("--component spawn-gate") == 2


def test_runtime_canary_requires_new_external_root_with_complete_context(tmp_path):
    root = _root(tmp_path)
    codex_home = tmp_path / ".codex"
    sessions = codex_home / "sessions" / "2026" / "08" / "19"
    sessions.mkdir(parents=True)
    rollout = sessions / "rollout-canary.jsonl"
    rollout.write_text(json.dumps({
        "type": "session_meta", "payload": {
            "id": "canary", "cwd": str(tmp_path / "target-workspace")}
    }) + "\n" + json.dumps({
        "type": "response_item", "payload": {"text": (
            "Active Codex LOOP global mode\n"
            f"LOOP_CONTROL_ROOT={root}\nMandatory LOOP model routing")}
    }) + "\n", encoding="utf-8")
    mode = {"updated_at": "2000-01-01T00:00:00Z"}
    result = desktop_mode.runtime_canary(root, codex_home, mode)
    assert result["verified"] is True
    assert result["session_id"] == "canary"


def test_canonical_agreement_contains_conversation_meta_framework():
    agreement = (IMPL / "config" / "global_working_agreement.md").read_text(
        encoding="utf-8-sig")
    for marker in (
        "## Conversation meta-framework (root and every subagent)",
        "### 学科程序",
        "### 自由求解",
        "### 依赖结构",
        "### 工程交互纪律",
    ):
        assert agreement.count(marker) == 1
    assert "active profile: `sonnet46-all`" not in agreement
    assert "model=weiwu/aws.claude-sonnet-4.6" not in agreement


def test_managed_context_limits_cover_complete_canonical_instructions():
    context_bytes = len(hook_mode.instruction_text(IMPL).encode("utf-8"))
    requirements = tomllib.loads(
        (IMPL / "config" / "global_requirements.toml").read_text(
            encoding="utf-8-sig"))
    for event in ("SessionStart", "SubagentStart"):
        context_hooks = [
            hook
            for group in requirements["hooks"][event]
            for hook in group["hooks"]
            if "--component context" in hook.get("command", "")
        ]
        assert len(context_hooks) == 1
        assert context_hooks[0]["additionalContextLimit"] >= context_bytes


def test_image_derived_engineering_rules_are_mandatory_and_exact():
    agreement = (IMPL / "config" / "global_working_agreement.md").read_text(
        encoding="utf-8-sig")
    start = agreement.index("### 工程交互纪律")
    end = agreement.index("This framework never", start)
    section = agreement[start:end]
    assert "任何一条未满足时" in section
    assert "不得标记任务完成" in section
    for number in range(1, 9):
        lines = [line for line in section.splitlines()
                 if line.startswith(f"{number}. ")]
        assert len(lines) == 1
        assert "MUST " in lines[0]
        assert "MUST NOT " in lines[0]
    for removed_expansion in (
        "实时探针", "声明假设并推进", "可回滚", "替代解释", "生命周期",
    ):
        assert removed_expansion not in section

    role_rules = (
        "1. MUST verify unknown APIs or formats from documentation or source; MUST NOT guess interfaces.",
        "2. MUST clarify unclear requirements before implementation; MUST NOT implement before alignment.",
        "3. MUST ask when business rules or preferences are uncertain; MUST NOT invent them.",
        "4. MUST reuse existing components first; MUST NOT add redundant components.",
        "5. MUST validate after changes and complete the relevant tests; MUST NOT omit checks.",
        "6. MUST follow the existing architecture and naming; MUST NOT make arbitrary architectural changes.",
        "7. MUST state uncertainty explicitly; MUST NOT pretend to know.",
        "8. MUST split large changes into small steps and verify each step; MUST NOT make uncontrolled bulk changes.",
    )
    for name in ("worker", "duty_officer", "reviewer", "verifier",
                 "plan_expander"):
        doc = tomllib.loads(
            (IMPL / "agents" / f"{name}.toml").read_text(encoding="utf-8-sig"))
        instructions = doc["developer_instructions"]
        assert "all eight are hard requirements" in instructions
        assert "blocks PASS/completion until corrected" in instructions
        for rule in role_rules:
            assert instructions.count(rule) == 1
        assert "reversible steps" not in instructions
