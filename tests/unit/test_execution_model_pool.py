from __future__ import annotations

import importlib.util
from pathlib import Path


def load_module():
    path = Path(__file__).resolve().parents[2] / "harness" / "headless_wave.py"
    spec = importlib.util.spec_from_file_location("headless_wave", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


POOL = ["trae01/glm-5.3", "trae02/glm-5.3", "trae03/glm-5.3"]
BASE_V4 = "weiwu/deepseek-v4-flash"
BASE_K3 = "weiwu-k3/kimi-k3"


def make_policy(module, models_extra: dict | None = None):
    models = {
        "v4_model": BASE_V4, "v4_reasoning": "max",
        "k3_model": BASE_K3, "k3_reasoning": "max",
    }
    models.update(models_extra or {})
    return module.OrchestrationPolicy({"models": models}, None)


def worker_task(tmp_path: Path, role: str = "worker") -> dict:
    return {"task_id": "a", "task_name": "执行包", "prompt": "solve",
            "cwd": str(tmp_path), "role": role,
            "sandbox": "read-only" if role != "worker" else "workspace-write"}


# ---------------------------------------------------------------------------
# OrchestrationPolicy.execution_model_pool() parsing
# ---------------------------------------------------------------------------
def test_pool_missing_returns_empty_list():
    module = load_module()
    policy = make_policy(module)
    assert policy.execution_model_pool() == []


def test_pool_list_preserves_declaration_order():
    module = load_module()
    policy = make_policy(module, {"execution_model_pool": list(reversed(POOL))})
    assert policy.execution_model_pool() == list(reversed(POOL))


def test_pool_string_becomes_single_element_list():
    module = load_module()
    policy = make_policy(module, {"execution_model_pool": POOL[0]})
    assert policy.execution_model_pool() == [POOL[0]]


def test_pool_entries_stripped_and_blank_dropped():
    module = load_module()
    policy = make_policy(module,
                         {"execution_model_pool": ["  a/m  ", "", "   "]})
    assert policy.execution_model_pool() == ["a/m"]


# ---------------------------------------------------------------------------
# Slot mapping (pure helper execution_pool_model)
# ---------------------------------------------------------------------------
def test_slot_mapping_wraps_around_modulo():
    module = load_module()
    policy = make_policy(module, {"execution_model_pool": POOL})
    assert module.execution_pool_model(policy, "worker", 1) == POOL[0]
    assert module.execution_pool_model(policy, "worker", 2) == POOL[1]
    assert module.execution_pool_model(policy, "worker", 3) == POOL[2]
    assert module.execution_pool_model(policy, "worker", 4) == POOL[0]


def test_empty_or_missing_pool_keeps_base_pin():
    module = load_module()
    missing = make_policy(module)
    assert module.execution_pool_model(missing, "worker", 1) is None
    empty = make_policy(module, {"execution_model_pool": []})
    assert module.execution_pool_model(empty, "worker", 2) is None


def test_non_worker_roles_ignore_pool():
    module = load_module()
    policy = make_policy(module, {"execution_model_pool": POOL})
    assert module.execution_pool_model(policy, "verifier", 1) is None
    assert module.execution_pool_model(policy, "reviewer", 4) is None
    assert module.execution_pool_model(policy, "worker", None) is None


# ---------------------------------------------------------------------------
# build_commands integration: the actual -m carries the pooled model
# ---------------------------------------------------------------------------
def test_build_commands_worker_pins_pool_model_for_slot(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "resolve_codex_binary", lambda: "codex")
    policy = make_policy(module, {"execution_model_pool": POOL})
    paths = module.LoopPaths(tmp_path)
    manifest = tmp_path / "wave.json"
    manifest.write_text("{}", encoding="utf-8")
    for slot in (1, 3, 4):
        _, _, supervisor, worker, _ = module.build_commands(
            worker_task(tmp_path), manifest, paths, policy, 30, slot=slot)
        expected = POOL[(slot - 1) % len(POOL)]
        assert worker[worker.index("-m") + 1] == expected
        assert supervisor[supervisor.index("--model") + 1] == expected


def test_build_commands_without_slot_keeps_legacy_v4_pin(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "resolve_codex_binary", lambda: "codex")
    policy = make_policy(module, {"execution_model_pool": POOL})
    paths = module.LoopPaths(tmp_path)
    manifest = tmp_path / "wave.json"
    manifest.write_text("{}", encoding="utf-8")
    _, _, _, worker, _ = module.build_commands(
        worker_task(tmp_path), manifest, paths, policy, 30)
    assert worker[worker.index("-m") + 1] == BASE_V4


def test_build_commands_verifier_ignores_pool(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "resolve_codex_binary", lambda: "codex")
    policy = make_policy(module, {"execution_model_pool": POOL})
    paths = module.LoopPaths(tmp_path)
    manifest = tmp_path / "wave.json"
    manifest.write_text("{}", encoding="utf-8")
    _, _, _, worker, _ = module.build_commands(
        worker_task(tmp_path, role="verifier"), manifest, paths, policy, 30,
        slot=2)
    assert worker[worker.index("-m") + 1] == BASE_K3
