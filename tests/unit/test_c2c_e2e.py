from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))

import c2c_e2e  # noqa: E402


def test_commands_are_read_only_upstream_calls(tmp_path):
    cli = ["node", "C:/ref/bin/c2c.js"]
    assert c2c_e2e.command("doctor", tmp_path, cli)[2:5] == [
        "doctor", "--no-fix", "--json"]
    assert c2c_e2e.command("session", tmp_path, cli)[2:5] == [
        "session", "get", "--json"]
    for action in ("status", "workspace"):
        argv = c2c_e2e.command(action, tmp_path, cli)
        assert argv[2:4] == [action, "--json"]
    joined = " ".join(c2c_e2e.command("doctor", tmp_path, cli)).lower()
    assert "msedge" not in joined and "chatgpt.com" not in joined


def test_run_passes_through_upstream_json(monkeypatch, tmp_path):
    monkeypatch.setattr(c2c_e2e, "resolve_cli",
                        lambda _=None: ["node", "c2c.js"])

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"ok": True}).encode(), b"")

    monkeypatch.setattr(c2c_e2e.subprocess, "run", fake_run)
    result = c2c_e2e.run("status", tmp_path)
    assert result["ok"] is True
    assert result["upstream"] == {"ok": True}


def test_doctor_exit_zero_is_not_green_when_bridge_is_down(monkeypatch,
                                                            tmp_path):
    monkeypatch.setattr(c2c_e2e, "resolve_cli",
                        lambda _=None: ["node", "c2c.js"])
    doc = {"report": {name: {"ok": name != "bridge"}
                      for name in ("node", "sandbox", "workspace", "bridge")}}
    monkeypatch.setattr(
        c2c_e2e.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, json.dumps(doc).encode(), b""))
    assert c2c_e2e.run("doctor", tmp_path)["ok"] is False


def test_resolve_cli_prefers_explicit_path(monkeypatch, tmp_path):
    cli = tmp_path / "c2c.js"
    cli.write_text("// fixture", encoding="utf-8")
    monkeypatch.setattr(c2c_e2e.shutil, "which",
                        lambda name: "C:/node.exe" if name == "node" else None)
    assert c2c_e2e.resolve_cli(str(cli)) == [
        "C:/node.exe", str(cli.resolve())]
