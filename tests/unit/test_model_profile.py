from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest


def load_module():
    path = Path(__file__).resolve().parents[2] / "harness" / "model_profile.py"
    spec = importlib.util.spec_from_file_location("model_profile", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def profile_env(tmp_path):
    source = Path(__file__).resolve().parents[2]
    root = tmp_path / "loop"
    home = tmp_path / "codex-home"
    for rel in ("config", "agents"):
        shutil.copytree(source / rel, root / rel)
    (home / "agents").mkdir(parents=True)
    (home / "config.toml").write_text(
        'user_key = "preserved"\n\n[agents]\n'
        'enabled = true\n'
        'default_subagent_model = "old-model"\n'
        'default_subagent_reasoning_effort = "low"\n',
        encoding="utf-8",
    )
    for name in ("worker", "duty_officer"):
        shutil.copy2(source / "agents" / f"{name}.toml",
                     home / "agents" / f"{name}.toml")
    return root, home


def read(path):
    return path.read_text(encoding="utf-8")


def test_profiles_switch_execution_and_review_roles(profile_env):
    module = load_module()
    root, home = profile_env
    expected = {
        "portable": ("gpt-5.6-terra", "medium", "gpt-5.6", "high"),
        "deep": ("gpt-5.6", "high", "gpt-5.6", "high"),
        "three-family-example": ("provider-a/fast-executor", "high",
                                  "provider-b/independent-reviewer", "high"),
    }
    for profile, (model, effort, review_model, review_effort) in expected.items():
        assert module.main(["set", profile, "--root", str(root),
                            "--codex-home", str(home), "--no-wsl"]) == 0
        worker = module.tomllib.loads(read(root / "agents" / "worker.toml"))
        assert (worker["model"], worker["model_reasoning_effort"]) == (model, effort)
        global_config = module.tomllib.loads(read(home / "config.toml"))
        assert (global_config["agents"]["default_subagent_model"],
                global_config["agents"]["default_subagent_reasoning_effort"]) == (model, effort)
        for name in ("reviewer", "verifier", "plan_expander"):
            review = module.tomllib.loads(read(root / "agents" / f"{name}.toml"))
            assert (review["model"], review["model_reasoning_effort"]) == (
                review_model, review_effort)
        roles = read(root / "config" / "roles.yaml")
        verifier_start = roles.index("  verifier:")
        verifier_end = roles.index("\n  plan_expander:", verifier_start)
        verifier_block = roles[verifier_start:verifier_end]
        assert f"model: {review_model} #" in verifier_block
        assert f"model: {review_model}#" not in verifier_block


def test_wsl_user_codex_home_receives_execution_and_review_roles(profile_env, tmp_path):
    module = load_module()
    root, home = profile_env
    wsl_root = tmp_path / "wsl-home" / "codex-loop-s-f2"
    wsl_codex_home = wsl_root.parent / ".codex"
    shutil.copytree(root, wsl_root)
    (wsl_codex_home / "agents").mkdir(parents=True)
    shutil.copy2(home / "config.toml", wsl_codex_home / "config.toml")
    for name in ("worker", "reviewer", "verifier", "plan_expander"):
        shutil.copy2(root / "agents" / f"{name}.toml",
                     wsl_codex_home / "agents" / f"{name}.toml")

    assert module.main(["set", "portable", "--root", str(root),
                        "--codex-home", str(home),
                        "--wsl-root", str(wsl_root),
                        "--wsl-codex-home", str(wsl_codex_home)]) == 0

    worker = module.tomllib.loads(read(wsl_codex_home / "agents" / "worker.toml"))
    assert (worker["model"], worker["model_reasoning_effort"]) == (
        "gpt-5.6-terra", "medium")
    for name in ("reviewer", "verifier", "plan_expander"):
        review = module.tomllib.loads(read(wsl_codex_home / "agents" / f"{name}.toml"))
        assert (review["model"], review["model_reasoning_effort"]) == (
            "gpt-5.6", "high")


def test_transaction_restores_original_bytes_after_write_failure(tmp_path, monkeypatch):
    module = load_module()
    first, second = tmp_path / "one", tmp_path / "two"
    first.write_text("old-one", encoding="utf-8")
    second.write_text("old-two", encoding="utf-8")
    real = module.atomic_write
    failed = False

    def fail_once(path, text):
        nonlocal failed
        if path == second and not failed:
            failed = True
            raise OSError("injected")
        real(path, text)

    monkeypatch.setattr(module, "atomic_write", fail_once)
    with pytest.raises(OSError, match="injected"):
        module.apply_transaction({first: "new-one", second: "new-two"})
    assert first.read_text(encoding="utf-8") == "old-one"
    assert second.read_text(encoding="utf-8") == "old-two"


def test_transaction_removes_new_file_during_rollback(tmp_path, monkeypatch):
    module = load_module()
    created, blocker = tmp_path / "created", tmp_path / "blocker"
    blocker.write_text("old", encoding="utf-8")
    real = module.atomic_write

    def fail_on_blocker(path, text):
        if path == blocker:
            raise OSError("injected")
        real(path, text)

    monkeypatch.setattr(module, "atomic_write", fail_on_blocker)
    with pytest.raises(OSError, match="injected"):
        module.apply_transaction({created: "new", blocker: "changed"})
    assert not created.exists()
    assert blocker.read_text(encoding="utf-8") == "old"


def test_atomic_write_removes_temp_file_when_replace_fails(tmp_path, monkeypatch):
    module = load_module()
    target = tmp_path / "config.toml"

    def fail_replace(source, destination):
        raise PermissionError("injected replace failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="injected"):
        module.atomic_write(target, "new")

    assert not target.exists()
    assert not target.with_name(
        f"{target.name}.tmp.{module.os.getpid()}").exists()


def test_global_profile_adds_missing_documented_agent_keys(profile_env):
    module = load_module()
    root, home = profile_env
    (home / "config.toml").write_text('user_key = "keep"\n', encoding="utf-8")
    assert module.main(["set", "portable", "--root", str(root),
                        "--codex-home", str(home), "--no-wsl"]) == 0
    doc = module.tomllib.loads(read(home / "config.toml"))
    assert doc["user_key"] == "keep"
    assert doc["agents"]["default_subagent_model"] == "gpt-5.6-terra"


def test_profile_never_rewrites_provider_catalogs(profile_env):
    module = load_module()
    root, home = profile_env
    catalog = home / "opencodex-catalog.json"
    catalog.write_bytes(b'{"private": true}\n')
    assert module.main(["set", "portable", "--root", str(root),
                        "--codex-home", str(home), "--no-wsl"]) == 0
    assert catalog.read_bytes() == b'{"private": true}\n'
