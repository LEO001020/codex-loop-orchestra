from __future__ import annotations

import json
from pathlib import Path

import global_desktop_mode as desktop_mode
import global_loop_mode as hook_mode
from tests.conftest import IMPL


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "control" / "codex-loop-orchestra"
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
    assert "global_loop_mode.py" not in serialized
    assert "old/subagent_lifecycle.py" not in serialized
    requirements = (codex_home / "requirements.toml").read_text(encoding="utf-8")
    assert "windows_managed_dir" in requirements
    assert "--component spawn-gate" in requirements

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


def test_restore_preserves_files_modified_after_install(tmp_path):
    root = _root(tmp_path)
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "AGENTS.md").write_text("before\n", encoding="utf-8")
    (codex_home / "hooks.json").write_text('{"hooks": {}}\n', encoding="utf-8")

    desktop_mode.install(root, codex_home)
    modified = {
        "AGENTS.md": b"user changed agents\n",
        "hooks.json": b'{"user": "changed"}\n',
        "requirements.toml": b"# user changed requirements\n",
    }
    for name, data in modified.items():
        (codex_home / name).write_bytes(data)

    result = desktop_mode.restore(root, codex_home)

    assert result["restored"] is False
    assert result["restored_files"] == []
    assert result["skipped_modified"] == [
        "AGENTS.md", "hooks.json", "requirements.toml"]
    for name, data in modified.items():
        assert (codex_home / name).read_bytes() == data
    assert desktop_mode.state_paths(root)[1].exists()


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
