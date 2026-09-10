from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load_module():
    path = Path(__file__).resolve().parents[2] / "harness" / "model_route_manager.py"
    spec = importlib.util.spec_from_file_location("model_route_manager", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_allowlist_keeps_native_gpt_and_drops_dead_providers():
    module = load_module()
    native, providers, _ = module.allowlist()
    assert "gpt-5.6-luna" in native
    assert "gpt-5.6-terra" in native
    assert "gpt-5.6-sol" in native
    assert "alibaba-token-plan" not in providers
    assert "cursor" not in providers
    assert "glm-5.2" in providers["weiwu"]


def test_curate_catalog_restores_official_luna_and_terra(tmp_path, monkeypatch):
    module = load_module()
    native_slugs = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
    routed = {"slug": "weiwu/deepseek-v4-flash", "display_name": "flash",
              "priority": 20}
    cache_models = [
        {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol", "priority": 0},
        {"slug": "gpt-5.6-terra", "display_name": "GPT-5.6-Terra", "priority": 104},
        {"slug": "gpt-5.6-luna", "display_name": "GPT-5.6-Luna", "priority": 104},
        {"slug": "cursor/auto", "display_name": "should-not-keep", "priority": 9},
    ]
    catalog = tmp_path / "opencodex-catalog.json"
    catalog.write_text(json.dumps({
        "models": [routed, {**cache_models[0], "display_name": "stale-sol"}]
    }), encoding="utf-8")
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": cache_models}), encoding="utf-8")
    ocx = tmp_path / "opencodex" / "config.json"
    ocx.parent.mkdir()
    ocx.write_text(json.dumps({
        "providers": {"weiwu": {"selectedModels": ["deepseek-v4-flash"],
                                "modelContextWindows": {"deepseek-v4-flash": 990000}}},
        "subagentModels": ["weiwu/deepseek-v4-flash"],
    }), encoding="utf-8")
    monkeypatch.setattr(module, "CODEX_HOME", tmp_path)
    monkeypatch.setattr(module, "OPENCODEX_HOME", ocx.parent)
    monkeypatch.setattr(module, "allowlist", lambda: (
        native_slugs, {"weiwu": ["deepseek-v4-flash"]}, ["gpt-5.6-sol"]))

    result = module.curate_catalog_file()
    slugs = result["slugs"]
    assert "gpt-5.6-luna" in slugs
    assert "gpt-5.6-terra" in slugs
    assert "gpt-5.6-sol" in slugs
    assert "cursor/auto" not in slugs
    restored = json.loads(catalog.read_text(encoding="utf-8"))
    names = {item["slug"]: item["display_name"] for item in restored["models"]}
    assert names["gpt-5.6-sol"] == "GPT-5.6-Sol"
    assert names["gpt-5.6-luna"] == "GPT-5.6-Luna"
    assert names["gpt-5.6-terra"] == "GPT-5.6-Terra"
