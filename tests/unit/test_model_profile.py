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
    # Project-local .codex files are machine-specific deployment state and are
    # intentionally excluded from the public release. Profile switching must
    # still work from the canonical public config and agent templates.
    for rel in ("config", "agents"):
        shutil.copytree(source / rel, root / rel)
    (home / "agents").mkdir(parents=True)
    shutil.copy2(Path.home() / ".codex" / "config.toml", home / "config.toml")
    for name in ("worker", "duty_officer"):
        shutil.copy2(source / "agents" / f"{name}.toml",
                     home / "agents" / f"{name}.toml")
    return root, home


def read(path):
    return path.read_text(encoding="utf-8")


def test_three_profiles_switch_without_touching_k3_review_roles(profile_env):
    module = load_module()
    root, home = profile_env
    expected = {
        "glm": ("weiwu/glm-5.2", "ultra",
                "weiwu-k3/kimi-k3", "max"),
        "k3-only": ("weiwu-k3/kimi-k3", "max",
                    "weiwu-k3/kimi-k3", "max"),
        "v4f": ("weiwu/deepseek-v4-flash", "ultra",
                "weiwu-k3/kimi-k3", "max"),
        "sonnet46-dual": ("weiwu/aws.claude-sonnet-4.6", "ultra",
                          "weiwu-k3/kimi-k3", "max"),
        "v4f-sonnet-review": ("weiwu/deepseek-v4-flash", "ultra",
                              "weiwu/aws.claude-sonnet-4.6", "ultra"),
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

    assert module.main(["set", "sonnet46-dual", "--root", str(root),
                        "--codex-home", str(home),
                        "--wsl-root", str(wsl_root)]) == 0

    worker = module.tomllib.loads(read(wsl_codex_home / "agents" / "worker.toml"))
    assert (worker["model"], worker["model_reasoning_effort"]) == (
        "weiwu/aws.claude-sonnet-4.6", "ultra")
    for name in ("reviewer", "verifier", "plan_expander"):
        review = module.tomllib.loads(read(wsl_codex_home / "agents" / f"{name}.toml"))
        assert (review["model"], review["model_reasoning_effort"]) == (
            "weiwu-k3/kimi-k3", "max")
    for name in ("worker", "duty_officer", "reviewer", "verifier",
                 "plan_expander"):
        assert read(home / "agents" / f"{name}.toml") == read(
            root / "agents" / f"{name}.toml")
        assert read(wsl_codex_home / "agents" / f"{name}.toml") == read(
            root / "agents" / f"{name}.toml")


def test_global_agent_sync_uses_canonical_role_instructions(profile_env):
    module = load_module()
    root, home = profile_env
    stale = home / "agents" / "worker.toml"
    stale.write_text(
        'model = "weiwu/deepseek-v4-flash"\n'
        'model_reasoning_effort = "ultra"\n'
        'developer_instructions = "stale"\n',
        encoding="utf-8",
    )

    assert module.main(["set", "v4f-v4p", "--root", str(root),
                        "--codex-home", str(home), "--no-wsl"]) == 0

    for name in ("worker", "duty_officer", "reviewer", "verifier",
                 "plan_expander"):
        canonical = read(root / "agents" / f"{name}.toml")
        installed = read(home / "agents" / f"{name}.toml")
        assert installed == canonical
        assert "Conversation meta-framework" in installed
        assert "### 学科程序" in installed
        assert "### 自由求解" in installed
        assert "### 依赖结构" in installed
        assert "Mandatory engineering rules" in installed
        assert "all eight are hard requirements" in installed
        assert installed.count("MUST NOT") == 8
        assert "reversible steps" not in installed


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
