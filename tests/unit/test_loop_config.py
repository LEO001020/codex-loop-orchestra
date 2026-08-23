import importlib.util
from pathlib import Path

import pytest


PKG = Path(__file__).resolve().parents[2]
SCRIPT = PKG / "harness" / "loop_config.py"
SPEC = importlib.util.spec_from_file_location("loop_config_test", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[agents]\nnormal_wave_concurrency = %d\n" % value,
                    encoding="utf-8")


def test_explicit_config_has_highest_priority(tmp_path, monkeypatch):
    root, home, explicit = tmp_path / "root", tmp_path / "home", tmp_path / "chosen.toml"
    write(root / "config" / "config.toml", 12)
    write(home / "config.toml", 14)
    write(explicit, 16)
    monkeypatch.setenv("LOOP_CONFIG", str(explicit))
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert MOD.config_int("agents", "normal_wave_concurrency", 1, root) == 16


def test_project_config_precedes_codex_home(tmp_path, monkeypatch):
    root, home = tmp_path / "root", tmp_path / "home"
    write(root / "config" / "config.toml", 15)
    write(home / "config.toml", 16)
    monkeypatch.delenv("LOOP_CONFIG", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert MOD.config_int("agents", "normal_wave_concurrency", 1, root) == 15


def test_codex_home_is_runtime_fallback_before_example(tmp_path, monkeypatch):
    root, home = tmp_path / "root", tmp_path / "home"
    write(home / "config.toml", 16)
    write(root / "config" / "config.toml.example", 10)
    monkeypatch.delenv("LOOP_CONFIG", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert MOD.config_int("agents", "normal_wave_concurrency", 1, root) == 16


def test_malformed_authoritative_config_is_fail_visible(tmp_path, monkeypatch):
    root = tmp_path / "root"
    path = root / "config" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("[broken", encoding="utf-8")
    monkeypatch.delenv("LOOP_CONFIG", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing-home"))
    with pytest.raises(MOD.ConfigError, match="unreadable"):
        MOD.load_config(root)
