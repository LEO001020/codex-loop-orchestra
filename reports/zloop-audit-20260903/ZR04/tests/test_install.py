"""Install tests: hook config management (VOL-05 §1). All paths point at
tmp_path; the real ~/.zcode and real ~/.zloop (backup dir) are never
touched (ZLOOP_DATA is redirected)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import install as zi  # noqa: E402

CMD = r"C:\abs\zloop-hook.exe"
ARGS = ["handle"]


@pytest.fixture(autouse=True)
def _isolated_zloop_data(tmp_path: Path, monkeypatch):
    """Backups go to a temp ZLOOP_DATA, never the real ~/.zloop."""
    monkeypatch.setenv("ZLOOP_DATA", str(tmp_path / "zloop-data"))


def _read(cfg: Path) -> dict:
    return json.loads(cfg.read_text(encoding="utf-8"))


def test_install_writes_five_events_and_status(tmp_path: Path):
    cfg = tmp_path / "cli" / "config.json"
    r = zi.install_hooks(CMD, ARGS, config_path=cfg)
    assert r["ok"] is True
    assert r["events"] == 5
    assert r["config_path"] == str(cfg)

    data = _read(cfg)
    assert set(data) == {"hooks"}                    # nothing else invented
    hooks = data["hooks"]
    assert hooks["enabled"] is True
    assert hooks["timeoutMs"] == 8000
    assert hooks["maxOutputBytes"] == 32768
    assert list(hooks["events"]) == zi.REGISTERED_EVENTS
    assert set(hooks["events"]) == set(zi.REGISTERED_EVENTS)
    for matchers in hooks["events"].values():
        assert matchers == [{"hooks": [{"type": "process",
                                         "command": CMD,
                                         "args": ["handle"]}]}]
    assert list(hooks["events"]) == ["SessionStart", "UserPromptSubmit",
                                     "PostToolUse", "PostToolUseFailure", "Stop"]

    st = zi.hook_status(config_path=cfg)
    assert st["config_exists"] is True
    assert st["hooks_enabled"] is True
    assert st["event_count"] == 5
    assert st["zloop_managed"] is True
    assert st["command"] == CMD


def test_install_is_idempotent(tmp_path: Path):
    cfg = tmp_path / "cli" / "config.json"
    assert zi.install_hooks(CMD, ARGS, config_path=cfg)["ok"] is True
    r2 = zi.install_hooks(CMD, ARGS, config_path=cfg)
    assert r2["ok"] is True and r2["events"] == 5
    data = _read(cfg)
    assert set(data["hooks"]["events"]) == set(zi.REGISTERED_EVENTS)
    st = zi.hook_status(config_path=cfg)
    assert st["event_count"] == 5 and st["command"] == CMD


def test_install_preserves_unrelated_keys_and_uninstall(tmp_path: Path):
    cfg = tmp_path / "cli" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps(
        {"mcp": {"servers": {"x": {"command": "x"}}}, "theme": "dark"}),
        encoding="utf-8")

    r = zi.install_hooks(CMD, ARGS, config_path=cfg)
    assert r["ok"] is True
    data = _read(cfg)
    assert data["mcp"] == {"servers": {"x": {"command": "x"}}}
    assert data["theme"] == "dark"
    assert "hooks" in data

    u = zi.uninstall_hooks(config_path=cfg)
    assert u["ok"] is True and u["removed"] is True
    data = _read(cfg)
    assert "hooks" not in data
    assert data["mcp"] == {"servers": {"x": {"command": "x"}}}
    assert data["theme"] == "dark"

    st = zi.hook_status(config_path=cfg)
    assert st["config_exists"] is True
    assert st["hooks_enabled"] is False
    assert st["event_count"] == 0
    assert st["zloop_managed"] is False
    assert st["command"] is None


def test_install_backs_up_previous_config(tmp_path: Path):
    cfg = tmp_path / "cli" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"existing": 1}), encoding="utf-8")

    zi.install_hooks(CMD, ARGS, config_path=cfg)
    backups = list((tmp_path / "zloop-data" / "hygiene-backup").glob("config-*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {"existing": 1}
    # no backup when there was nothing to back up
    zi.install_hooks(CMD, ARGS, config_path=tmp_path / "cli" / "other.json")
    assert len(backups) == 1


def test_install_refuses_foreign_hooks(tmp_path: Path):
    cfg = tmp_path / "cli" / "config.json"
    foreign = {"hooks": {"enabled": True, "events": {
        "PreToolUse": [{"hooks": [{"type": "process",
                                    "command": "other-hook",
                                    "args": []}]}]}}}
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps(foreign), encoding="utf-8")

    r = zi.install_hooks(CMD, ARGS, config_path=cfg)
    assert r["ok"] is False
    assert r["reason"] == ("existing non-zloop hooks config present; "
                           "manual merge required")
    # config untouched
    assert _read(cfg) == foreign
    # uninstall also refuses to touch it
    u = zi.uninstall_hooks(config_path=cfg)
    assert u["ok"] is False
    assert u["reason"] == "hooks not managed by zloop"
    assert _read(cfg) == foreign


def test_uninstall_no_config(tmp_path: Path):
    r = zi.uninstall_hooks(config_path=tmp_path / "nope" / "config.json")
    assert r == {"ok": True, "removed": False, "reason": "no config"}
    st = zi.hook_status(config_path=tmp_path / "nope" / "config.json")
    assert st["config_exists"] is False
    assert st["hooks_enabled"] is False
    assert st["event_count"] == 0
    assert st["zloop_managed"] is False
    assert st["command"] is None


def test_install_over_empty_hooks_ok_and_no_home_write(tmp_path: Path):
    # an empty hooks block is replaceable (not "foreign")
    cfg = tmp_path / "cli" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    r = zi.install_hooks(CMD, ARGS, config_path=cfg)
    assert r["ok"] is True
    assert _read(cfg)["hooks"]["enabled"] is True

    # config_path override means the real user config is never written
    real = zi._config_path()
    before = real.exists() and real.stat().st_mtime_ns
    zi.install_hooks(CMD, ARGS, config_path=tmp_path / "cli" / "c2.json")
    after = real.exists() and real.stat().st_mtime_ns
    assert before == after


# ---- plugin emission (D-16: plugin-scope is the production target) -----------

EXE = r"C:\abs\python.exe"


def test_emit_plugin_tree(tmp_path: Path):
    dest = tmp_path / "plugin"
    r = zi.emit_plugin(dest, EXE)
    assert r["ok"] is True
    assert r["events"] == 5
    assert r["dest"] == str(dest)

    # .zcode-plugin/plugin.json — manifest
    pj = json.loads((dest / ".zcode-plugin" / "plugin.json")
                    .read_text(encoding="utf-8"))
    assert pj["name"] == "zloop"
    assert pj["version"] == "0.1.0"
    assert pj["hooks"] == "hooks/hooks.json"
    assert "post-execution events" in pj["description"]

    # hooks/hooks.json — the same 5 post-execution events, process type,
    # command templated on ${ZCODE_PLUGIN_ROOT} so the tree is relocatable
    hj = json.loads((dest / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert list(hj) == ["hooks"]
    assert list(hj["hooks"]) == zi.REGISTERED_EVENTS
    assert set(hj["hooks"]) == set(zi.REGISTERED_EVENTS)
    for matchers in hj["hooks"].values():
        assert matchers == [{"hooks": [{"type": "process",
                                         "command": r"${ZCODE_PLUGIN_ROOT}\zloop-hook.cmd",
                                         "args": ["handle"],
                                         "timeoutMs": 8000}]}]

    # zloop-hook.cmd — runs the recorded interpreter with the hook module
    cmd = (dest / "zloop-hook.cmd").read_text(encoding="utf-8")
    assert f'"{EXE}" -m zloop.hook' in cmd


def test_emit_plugin_overwrites_and_resolves_relative_python(tmp_path: Path,
                                                              monkeypatch):
    monkeypatch.chdir(tmp_path)
    dest = tmp_path / "plugin"
    r1 = zi.emit_plugin(dest, "some/other/python.exe")
    assert r1["ok"] is True
    cmd1 = (dest / "zloop-hook.cmd").read_text(encoding="utf-8")
    # relative interpreter path -> resolved to an absolute one
    assert f'"{Path("some/other/python.exe").resolve()}" -m zloop.hook' in cmd1

    # re-running overwrites cleanly (same tree, new interpreter)
    r2 = zi.emit_plugin(dest, EXE)
    assert r2["ok"] is True and r2["events"] == 5
    cmd2 = (dest / "zloop-hook.cmd").read_text(encoding="utf-8")
    assert f'"{EXE}" -m zloop.hook' in cmd2
    assert "some" not in cmd2
    # and the JSON payload is unchanged valid JSON
    hj = json.loads((dest / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert list(hj["hooks"]) == zi.REGISTERED_EVENTS
