from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest


def load_module():
    path = Path(__file__).resolve().parents[2] / "harness" / "install_user_config.py"
    spec = importlib.util.spec_from_file_location("install_user_config", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def package(tmp_path):
    source = Path(__file__).resolve().parents[2]
    root = tmp_path / "package"
    shutil.copytree(source / "agents", root / "agents")
    (root / "config").mkdir()
    shutil.copy2(source / "config" / "config.toml.example",
                 root / "config" / "config.toml.example")
    return root


def test_dry_run_is_zero_write(package, tmp_path):
    module = load_module()
    home = tmp_path / "missing-home"
    result = module.install(package, home, dry_run=True)
    assert result["dry_run"] is True
    assert result["config_keys"]
    assert not home.exists()


def test_install_preserves_user_keys_and_backs_up(package, tmp_path):
    module = load_module()
    home = tmp_path / "home"
    home.mkdir()
    config = home / "config.toml"
    original = b'user_key = "keep"\n\n[agents]\nenabled = false\n'
    config.write_bytes(original)
    result = module.install(package, home)
    assert result["config_keys"]
    rendered = config.read_text(encoding="utf-8")
    assert 'user_key = "keep"' in rendered
    assert "enabled = false" in rendered
    assert list(home.glob("config.toml.bak.*"))[0].read_bytes() == original


def test_repeated_backup_names_never_overwrite(package, tmp_path, monkeypatch):
    module = load_module()
    home = tmp_path / "home"
    (home / "agents").mkdir(parents=True)
    target = home / "agents" / "worker.toml"
    target.write_bytes(b"first\n")
    monkeypatch.setattr(module.time, "time_ns", lambda: 7)
    module.install(package, home)
    target.write_bytes(b"second\n")
    module.install(package, home)
    backups = list((home / "agents").glob("worker.toml.bak.*"))
    assert {path.read_bytes() for path in backups} >= {b"first\n", b"second\n"}


def test_invalid_user_config_fails_before_any_agent_write(package, tmp_path):
    module = load_module()
    home = tmp_path / "home"
    (home / "agents").mkdir(parents=True)
    worker = home / "agents" / "worker.toml"
    worker.write_bytes(b"user-owned\n")
    (home / "config.toml").write_text("[broken", encoding="utf-8")
    with pytest.raises(module.tomllib.TOMLDecodeError):
        module.install(package, home)
    assert worker.read_bytes() == b"user-owned\n"
    assert not list(home.rglob("*.bak.*"))


def test_write_failure_rolls_back_all_targets(package, tmp_path, monkeypatch):
    module = load_module()
    home = tmp_path / "home"
    (home / "agents").mkdir(parents=True)
    worker = home / "agents" / "worker.toml"
    worker.write_bytes(b"original-worker\n")
    real = module.atomic_write

    def fail_config(path, text):
        if path.name == "config.toml":
            raise OSError("injected")
        real(path, text)

    monkeypatch.setattr(module, "atomic_write", fail_config)
    with pytest.raises(OSError, match="injected"):
        module.install(package, home)
    assert worker.read_bytes() == b"original-worker\n"
    assert not (home / "config.toml").exists()
    assert not list(home.rglob("*.tmp"))


def test_restore_reverts_unchanged_managed_files(package, tmp_path):
    module = load_module()
    home = tmp_path / "home"
    (home / "agents").mkdir(parents=True)
    worker = home / "agents" / "worker.toml"
    worker.write_bytes(b"original-worker\n")
    config = home / "config.toml"
    config.write_bytes(b'user_key = "keep"\n')
    module.install(package, home)
    result = module.restore(package, home)
    assert not result["skipped_modified"]
    assert worker.read_bytes() == b"original-worker\n"
    assert config.read_bytes() == b'user_key = "keep"\n'


def test_restore_preserves_user_modified_managed_file(package, tmp_path):
    module = load_module()
    home = tmp_path / "home"
    module.install(package, home)
    worker = home / "agents" / "worker.toml"
    worker.write_text("user changed this after install\n", encoding="utf-8")
    result = module.restore(package, home)
    assert "agents/worker.toml" in result["skipped_modified"]
    assert worker.read_text(encoding="utf-8") == "user changed this after install\n"
