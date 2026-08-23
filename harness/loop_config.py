#!/usr/bin/env python3
"""Shared deterministic configuration lookup for Codex LOOP.

Priority: LOOP_CONFIG -> LOOP_ROOT/config/config.toml ->
CODEX_HOME/config.toml -> package config.toml.example.  The first existing
file is authoritative; malformed authoritative configuration is never
silently skipped.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    pass


def root_path() -> Path:
    return Path(os.environ.get("LOOP_ROOT", Path(__file__).resolve().parents[1])).resolve()


def candidates(root: Path | None = None) -> list[Path]:
    root = (root or root_path()).resolve()
    explicit = os.environ.get("LOOP_CONFIG")
    code_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()
    values = ([Path(explicit).resolve()] if explicit else []) + [
        root / "config" / "config.toml",
        code_home / "config.toml",
        root / "config" / "config.toml.example",
    ]
    out: list[Path] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def load_config(root: Path | None = None) -> tuple[dict[str, Any], Path | None]:
    for path in candidates(root):
        if not path.exists():
            continue
        try:
            with path.open("rb") as handle:
                return tomllib.load(handle), path
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError("Loop config unreadable: %s: %s" % (path, exc)) from exc
    return {}, None


def config_value(section: str, key: str, default: Any,
                 root: Path | None = None) -> Any:
    doc, _ = load_config(root)
    table = doc.get(section, {})
    return table.get(key, default) if isinstance(table, dict) else default


def config_bool(section: str, key: str, default: bool = False,
                root: Path | None = None) -> bool:
    value = config_value(section, key, default, root)
    return value if isinstance(value, bool) else default


def config_int(section: str, key: str, default: int,
               root: Path | None = None) -> int:
    value = config_value(section, key, default, root)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else default


def policy_path(root: Path | None = None) -> Path:
    root = (root or root_path()).resolve()
    explicit = os.environ.get("LOOP_REFILL_POLICY")
    return Path(explicit).resolve() if explicit else root / "config" / "refill_policy.toml"


def load_policy(root: Path | None = None) -> tuple[dict[str, Any], Path | None]:
    """Load LOOP's private scheduling policy, never Codex's config.toml.

    Missing policy is allowed for portable/tests defaults.  A present but
    malformed policy fails visibly so birth controls can never disappear due
    to a parse error.
    """
    path = policy_path(root)
    if not path.exists():
        return {}, None
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle), path
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("Loop refill policy unreadable: %s: %s" % (path, exc)) from exc


def policy_value(section: str, key: str, default: Any,
                 root: Path | None = None) -> Any:
    doc, _ = load_policy(root)
    table = doc.get(section, {})
    return table.get(key, default) if isinstance(table, dict) else default


def policy_int(section: str, key: str, default: int,
               root: Path | None = None) -> int:
    value = policy_value(section, key, default, root)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else default
