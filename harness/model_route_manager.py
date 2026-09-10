#!/usr/bin/env python3
"""Temporary LOOP model switching plus a curated OpenCodex model catalog.

Provider credentials remain exclusively in ``~/.opencodex/config.json``.
This manager changes model references and provider allowlists, never API keys.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CODEX_HOME = Path.home() / ".codex"
OPENCODEX_HOME = Path.home() / ".opencodex"
# Deployments with a different WSL distro/user override via env; the literal
# is only this package's original default.
WSL_ROOT = Path(os.environ.get(
    "CODEX_LOOP_WSL_ROOT",
    r"\\wsl.localhost\Ubuntu\home\codexloop\codex-loop-s-f2"))
PROFILE_TOOL = ROOT / "harness" / "model_profile.py"
PROFILE_CONFIG = ROOT / "config" / "model_profiles.toml"
ALLOWLIST_CONFIG = ROOT / "config" / "model_catalog_allowlist.toml"
MARKER = ROOT / "data" / "governor" / "temporary_model_route.json"
TEMPORARY_PROFILE = "grok-v4p"


class RouteManagerError(RuntimeError):
    pass


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RouteManagerError(f"cannot read {path}: {exc}") from exc


def profile_state() -> dict[str, str]:
    document = load_toml(PROFILE_CONFIG)
    active = str(document.get("active_profile") or "")
    profile = (document.get("profiles") or {}).get(active)
    if not active or not isinstance(profile, dict):
        raise RouteManagerError("active model profile is missing")
    return {
        "profile": active,
        "execution_model": str(profile.get("execution_model") or ""),
        "execution_reasoning": str(profile.get("execution_reasoning") or ""),
        "review_model": str(profile.get("review_model") or ""),
        "review_reasoning": str(profile.get("review_reasoning") or ""),
    }


def run_profile(name: str) -> None:
    command = [
        sys.executable, str(PROFILE_TOOL), "set", name,
        "--root", str(ROOT),
        "--codex-home", str(CODEX_HOME),
        "--wsl-root", str(WSL_ROOT),
        "--json",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RouteManagerError(f"profile switch to {name!r} failed: {detail}")


def backup_runtime() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = OPENCODEX_HOME / "backups" / f"model-route-manager-{stamp}"
    suffix = 0
    while destination.exists():
        suffix += 1
        destination = OPENCODEX_HOME / "backups" / f"model-route-manager-{stamp}-{suffix}"
    destination.mkdir(parents=True)
    sources = {
        OPENCODEX_HOME / "config.json": "opencodex-config.json",
        CODEX_HOME / "config.toml": "codex-config.toml",
        CODEX_HOME / "opencodex-catalog.json": "opencodex-catalog.json",
        PROFILE_CONFIG: "model_profiles.toml",
    }
    for source, name in sources.items():
        if source.is_file():
            shutil.copy2(source, destination / name)
    return destination


def allowlist() -> tuple[list[str], dict[str, list[str]], list[str]]:
    document = load_toml(ALLOWLIST_CONFIG)
    native = [str(item) for item in (document.get("native") or {}).get("visible", [])]
    providers = {
        str(name): [str(item) for item in values]
        for name, values in (document.get("providers") or {}).items()
        if isinstance(values, list)
    }
    fallbacks = [str(item) for item in (document.get("featured") or {}).get("fallbacks", [])]
    if not native or not providers:
        raise RouteManagerError("model catalog allowlist is empty")
    return native, providers, fallbacks


def routed_allowset(providers: dict[str, list[str]]) -> set[str]:
    return {f"{provider}/{model}" for provider, models in providers.items() for model in models}


def curated_featured(native: list[str], providers: dict[str, list[str]],
                     fallbacks: list[str]) -> list[str]:
    state = profile_state()
    permitted = set(native) | routed_allowset(providers)
    candidates = [state["execution_model"], state["review_model"], *fallbacks]
    result: list[str] = []
    for candidate in candidates:
        if candidate and candidate in permitted and candidate not in result:
            result.append(candidate)
        if len(result) == 5:
            break
    if state["execution_model"] not in result:
        raise RouteManagerError("active execution model is absent from the catalog allowlist")
    return result


def curate_opencodex() -> dict[str, Any]:
    config_path = OPENCODEX_HOME / "config.json"
    catalog_path = CODEX_HOME / "opencodex-catalog.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RouteManagerError(f"cannot read OpenCodex config: {exc}") from exc
    native, provider_allowlists, fallbacks = allowlist()
    providers = config.get("providers")
    if not isinstance(providers, dict):
        raise RouteManagerError("OpenCodex providers object is missing")
    for name, models in provider_allowlists.items():
        provider = providers.get(name)
        if not isinstance(provider, dict):
            raise RouteManagerError(f"allowlisted provider is not configured: {name}")
        provider["selectedModels"] = models
        provider["liveModels"] = False

    allowed_routed = routed_allowset(provider_allowlists)
    custom = config.get("customModels")
    if isinstance(custom, list):
        config["customModels"] = [
            item for item in custom
            if isinstance(item, dict)
            and f"{item.get('provider')}/{item.get('modelId')}" in allowed_routed
        ]

    disabled = {str(item) for item in config.get("disabledModels", []) if "/" in str(item)}
    if catalog_path.is_file():
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            for item in catalog.get("models", []):
                slug = str(item.get("slug") or "") if isinstance(item, dict) else ""
                if slug and "/" not in slug and slug not in native:
                    disabled.add(slug)
        except (OSError, ValueError):
            pass
    disabled.difference_update(native)
    disabled.difference_update(allowed_routed)
    config["disabledModels"] = sorted(disabled)
    featured = curated_featured(native, provider_allowlists, fallbacks)
    config["subagentModels"] = featured
    atomic_json(config_path, config)
    return {"featured": featured, "allowed_routed": len(allowed_routed),
            "visible_native": native}


def catalog_context(config: dict[str, Any], provider: str, model: str) -> int:
    provider_doc = (config.get("providers") or {}).get(provider) or {}
    windows = provider_doc.get("modelContextWindows") or {}
    value = windows.get(model) if isinstance(windows, dict) else None
    if value is None:
        for item in config.get("customModels", []):
            if (isinstance(item, dict) and item.get("provider") == provider
                    and item.get("modelId") == model):
                value = item.get("contextWindow")
                break
    if value is None:
        caps = config.get("providerContextCaps") or {}
        value = caps.get(provider) if isinstance(caps, dict) else None
    try:
        return int(value) if value is not None else 990000
    except (TypeError, ValueError):
        return 990000


def native_catalog_source() -> list[dict[str, Any]]:
    """Official Codex model records used to restore native picker entries."""
    path = CODEX_HOME / "models_cache.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    models = document.get("models")
    if not isinstance(models, list):
        return []
    return [item for item in models if isinstance(item, dict) and item.get("slug")]


def curate_catalog_file() -> dict[str, Any]:
    config = json.loads((OPENCODEX_HOME / "config.json").read_text(encoding="utf-8"))
    path = CODEX_HOME / "opencodex-catalog.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    models = catalog.get("models")
    if not isinstance(models, list):
        raise RouteManagerError("Codex model catalog has no models list")
    native, provider_allowlists, _ = allowlist()
    allowed = set(native) | routed_allowset(provider_allowlists)
    kept = [item for item in models if isinstance(item, dict)
            and str(item.get("slug") or "") in allowed]
    by_slug = {str(item.get("slug")): item for item in kept}
    official = {str(item.get("slug")): item for item in native_catalog_source()}
    for slug in native:
        item = official.get(slug)
        if not item:
            continue
        if slug in by_slug:
            index = next(i for i, row in enumerate(kept) if str(row.get("slug")) == slug)
            kept[index] = item
        else:
            kept.append(item)
        by_slug[slug] = item
    template = next((item for item in models if isinstance(item, dict)
                     and "/" in str(item.get("slug") or "")), None)
    if template is None:
        raise RouteManagerError("Codex catalog has no routed model template")
    for provider, model_ids in provider_allowlists.items():
        for model_id in model_ids:
            slug = f"{provider}/{model_id}"
            if slug in by_slug:
                continue
            item = dict(template)
            item.update({
                "slug": slug,
                "display_name": slug,
                "description": "OpenCodex allowlisted routed model",
                "context_window": catalog_context(config, provider, model_id),
                "priority": 20,
            })
            kept.append(item)
            by_slug[slug] = item
    featured = list(config.get("subagentModels") or [])
    rank = {slug: index for index, slug in enumerate(featured)}
    for item in kept:
        slug = str(item.get("slug") or "")
        if slug in rank:
            item["priority"] = rank[slug]
    kept.sort(key=lambda item: (int(item.get("priority", 1000)),
                                str(item.get("slug") or "")))
    catalog["models"] = kept
    atomic_json(path, catalog)
    return {"models": len(kept), "slugs": [str(item.get("slug")) for item in kept]}


def sync_catalog() -> str:
    appdata = Path(os.environ.get("APPDATA", ""))
    package = appdata / "npm" / "node_modules" / "@bitkyc08" / "opencodex"
    bun = package / "node_modules" / "bun" / "bin" / "bun.exe"
    cli = package / "src" / "cli" / "index.ts"
    if not bun.is_file() or not cli.is_file():
        raise RouteManagerError("OpenCodex Bun/CLI installation is unavailable")
    result = subprocess.run([str(bun), str(cli), "sync"], text=True,
                            capture_output=True, timeout=120)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RouteManagerError(f"OpenCodex catalog sync failed: {detail}")
    return (result.stdout + result.stderr).strip()


def curate_and_sync(no_sync: bool) -> dict[str, Any]:
    result = curate_opencodex()
    if not no_sync:
        result["sync"] = sync_catalog()
    result["catalog_file"] = curate_catalog_file()
    return result


def enable_temporary(no_sync: bool) -> dict[str, Any]:
    before = profile_state()
    if MARKER.is_file():
        marker = json.loads(MARKER.read_text(encoding="utf-8"))
        if marker.get("target_profile") != TEMPORARY_PROFILE:
            raise RouteManagerError("a different temporary model override is already active")
        return {"status": "already_active", **profile_state(),
                "catalog": curate_and_sync(no_sync)}
    backup = backup_runtime()
    marker = {
        "schema": "codex-loop-temporary-model-route/v1",
        "status": "switching",
        "previous_profile": before["profile"],
        "target_profile": TEMPORARY_PROFILE,
        "backup": str(backup),
        "started_at": time.time(),
    }
    atomic_json(MARKER, marker)
    try:
        run_profile(TEMPORARY_PROFILE)
        catalog = curate_and_sync(no_sync)
    except Exception:
        try:
            run_profile(before["profile"])
        except Exception as rollback_error:
            marker["status"] = "rollback_failed"
            marker["rollback_error"] = str(rollback_error)
            atomic_json(MARKER, marker)
            raise
        MARKER.unlink(missing_ok=True)
        raise
    marker["status"] = "active"
    marker["activated_at"] = time.time()
    atomic_json(MARKER, marker)
    return {"status": "active", **profile_state(), "backup": str(backup),
            "catalog": catalog}


def restore(no_sync: bool) -> dict[str, Any]:
    if not MARKER.is_file():
        return {"status": "not_active", **profile_state()}
    marker = json.loads(MARKER.read_text(encoding="utf-8"))
    previous = str(marker.get("previous_profile") or "")
    if not previous:
        raise RouteManagerError("temporary route marker has no previous profile")
    # Cross-check the marker against the ACTUAL active profile: a marker two
    # rotations behind would "restore" the running codex to a model nobody
    # configured. Fail and demand manual reconcile instead of guessing.
    try:
        active = profile_state().get("profile")
    except Exception:
        active = None
    if marker.get("status") != "active" or (
            active and active != TEMPORARY_PROFILE):
        raise RouteManagerError(
            "temporary route marker is stale (marker status=%r, active "
            "profile=%r, marker target=%r). Reconcile config/"
            "model_profiles.toml manually, then delete data/governor/"
            "temporary_model_route.json - refusing to guess a restore "
            "target." % (marker.get("status"), active,
                         marker.get("target_profile")))
    backup = backup_runtime()
    run_profile(previous)
    catalog = curate_and_sync(no_sync)
    MARKER.unlink(missing_ok=True)
    return {"status": "restored", **profile_state(), "backup": str(backup),
            "catalog": catalog}


def status() -> dict[str, Any]:
    result: dict[str, Any] = {"temporary": MARKER.is_file(), **profile_state()}
    if MARKER.is_file():
        try:
            marker = json.loads(MARKER.read_text(encoding="utf-8"))
            result["previous_profile"] = marker.get("previous_profile")
            result["temporary_status"] = marker.get("status")
        except ValueError:
            result["temporary_status"] = "corrupt"
    config = json.loads((OPENCODEX_HOME / "config.json").read_text(encoding="utf-8"))
    result["featured"] = config.get("subagentModels", [])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LOOP model route and catalog manager")
    parser.add_argument("command", choices=["enable-grok", "restore", "catalog-sync", "status"])
    parser.add_argument("--no-sync", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "enable-grok":
        result = enable_temporary(args.no_sync)
    elif args.command == "restore":
        result = restore(args.no_sync)
    elif args.command == "catalog-sync":
        backup = backup_runtime()
        result = {"status": "catalog_synced", "backup": str(backup),
                  "catalog": curate_and_sync(args.no_sync)}
    else:
        result = status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RouteManagerError, subprocess.SubprocessError) as exc:
        print(f"model route manager failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
