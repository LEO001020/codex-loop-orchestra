#!/usr/bin/env python3
"""Atomic execution/review model profile switch for Codex LOOP.

Profiles may switch the ordinary execution family and the logical review
family independently. Roles and pools remain distinct even when a temporary
profile assigns both families to the same physical model. Provider
registration and API credentials remain owned by the user's Codex or gateway
configuration. This command deliberately does not rewrite undocumented
provider catalogs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CODEX_HOME = Path.home() / ".codex"


class ProfileError(RuntimeError):
    pass


def q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def section_span(text: str, section: str | None) -> tuple[int, int]:
    headers = list(re.finditer(r"(?m)^\s*\[([^]\r\n]+)]\s*(?:#.*)?$", text))
    if section is None:
        return 0, headers[0].start() if headers else len(text)
    for idx, match in enumerate(headers):
        if match.group(1).strip() == section:
            return match.end(), headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
    raise ProfileError(f"missing TOML section [{section}]")


def set_toml_key(text: str, section: str | None, key: str, value: str) -> str:
    start, end = section_span(text, section)
    body = text[start:end]
    rx = re.compile(rf'(?m)^(\s*{re.escape(key)}\s*=\s*)[^\r\n]*(\r?\n|$)')
    replacement = rf"\g<1>{q(value)}\g<2>"
    changed, count = rx.subn(replacement, body, count=1)
    if count != 1:
        where = "top level" if section is None else f"[{section}]"
        raise ProfileError(f"missing or duplicate {where}.{key}")
    return text[:start] + changed + text[end:]


def upsert_toml_key(text: str, section: str, key: str, value: str) -> str:
    """Set a user-config key, creating the documented section/key if absent."""
    try:
        return set_toml_key(text, section, key, value)
    except ProfileError:
        try:
            _, end = section_span(text, section)
        except ProfileError:
            separator = "" if not text or text.endswith("\n\n") else (
                "\n" if text.endswith("\n") else "\n\n"
            )
            return text + separator + f"[{section}]\n{key} = {q(value)}\n"
        insertion = f"{key} = {q(value)}\n"
        return text[:end] + insertion + text[end:]


def set_inline_reasoning(text: str, section: str, key: str, effort: str) -> str:
    start, end = section_span(text, section)
    body = text[start:end]
    rx = re.compile(rf'(?m)^(\s*{re.escape(key)}\s*=\s*\{{[^\r\n]*?reasoning\s*=\s*")[^"]+("[^\r\n]*\}}\s*(?:#.*)?)(\r?\n|$)')
    changed, count = rx.subn(rf"\g<1>{effort}\g<2>\g<3>", body, count=1)
    if count != 1:
        raise ProfileError(f"missing inline reasoning for [{section}].{key}")
    return text[:start] + changed + text[end:]


def set_yaml_role(text: str, role: str, model: str, effort: str) -> str:
    rx = re.compile(rf"(?ms)^(  {re.escape(role)}:\s*.*?)(?=^  [A-Za-z_][\w-]*:\s*|\Z)")
    match = rx.search(text)
    if not match:
        raise ProfileError(f"missing YAML role {role}")
    block = match.group(1)
    def replace_scalar(match: re.Match[str], value: str) -> str:
        comment = match.group(2)
        return f"{match.group(1)}{value}" + (f" {comment}" if comment else "")

    block, mc = re.subn(r'(?m)^(\s+model:\s*)[^#\r\n]*?(?:\s*(#.*))?$',
                        lambda m: replace_scalar(m, model), block, count=1)
    block, ec = re.subn(
        r'(?m)^(\s+reasoning_effort:\s*)[^#\r\n]*?(?:\s*(#.*))?$',
        lambda m: replace_scalar(m, effort), block, count=1)
    if mc != 1 or ec != 1:
        raise ProfileError(f"incomplete YAML role {role}")
    return text[:match.start(1)] + block + text[match.end(1):]


def load_profiles(root: Path) -> tuple[dict[str, Any], Path]:
    path = root / "config" / "model_profiles.toml"
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"profile config unreadable: {path}: {exc}") from exc
    profiles = doc.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ProfileError("no model profiles configured")
    return doc, path


def profile_values(doc: dict[str, Any], name: str) -> tuple[str, str, str, str]:
    item = (doc.get("profiles") or {}).get(name)
    if not isinstance(item, dict):
        raise ProfileError(f"unknown profile: {name}")
    model, effort = item.get("execution_model"), item.get("execution_reasoning")
    review_model = item.get("review_model")
    review_effort = item.get("review_reasoning")
    if (not isinstance(model, str) or not model.strip()
            or not isinstance(effort, str)
            or not isinstance(review_model, str) or not review_model.strip()
            or not isinstance(review_effort, str)):
        raise ProfileError(f"invalid profile: {name}")
    return model, effort, review_model, review_effort


def project_updates(root: Path, profile_path: Path, name: str,
                    model: str, effort: str, review_model: str,
                    review_effort: str) -> dict[Path, str]:
    updates: dict[Path, str] = {}

    def edit(rel: str, fn) -> None:
        path = root / rel
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        updates[path] = fn(text)

    profile_text = profile_path.read_text(encoding="utf-8")
    updates[profile_path] = set_toml_key(profile_text, None, "active_profile", name)

    def v2(text: str) -> str:
        text = set_toml_key(text, "models", "v4_model", model)
        text = set_toml_key(text, "models", "v4_reasoning", effort)
        text = set_toml_key(text, "models", "k3_model", review_model)
        return set_toml_key(text, "models", "k3_reasoning", review_effort)
    edit("config/orchestration_policy_v2.toml", v2)

    def agent(text: str) -> str:
        text = set_toml_key(text, None, "model", model)
        return set_toml_key(text, None, "model_reasoning_effort", effort)
    for rel in ("agents/worker.toml", "agents/duty_officer.toml"):
        edit(rel, agent)

    def review_agent(text: str) -> str:
        text = set_toml_key(text, None, "model", review_model)
        return set_toml_key(text, None, "model_reasoning_effort", review_effort)
    for rel in ("agents/reviewer.toml", "agents/verifier.toml",
                "agents/plan_expander.toml"):
        edit(rel, review_agent)

    def defaults(text: str) -> str:
        text = set_toml_key(text, "agents", "default_subagent_model", model)
        return set_toml_key(text, "agents", "default_subagent_reasoning_effort", effort)
    for rel in (".codex/config.toml", "config/config.toml.example"):
        edit(rel, defaults)

    def roles(text: str) -> str:
        for role in ("executor", "scout", "duty_officer"):
            text = set_yaml_role(text, role, model, effort)
        for role in ("reviewer", "verifier", "plan_expander"):
            text = set_yaml_role(text, role, review_model, review_effort)
        return text
    edit("config/roles.yaml", roles)
    return updates


def global_updates(codex_home: Path, model: str, effort: str,
                   review_model: str, review_effort: str) -> dict[Path, str]:
    updates: dict[Path, str] = {}
    config = codex_home / "config.toml"
    if config.is_file():
        text = upsert_toml_key(config.read_text(encoding="utf-8"), "agents",
                               "default_subagent_model", model)
        updates[config] = upsert_toml_key(
            text, "agents", "default_subagent_reasoning_effort", effort,
        )
    for name in ("worker", "duty_officer"):
        path = codex_home / "agents" / f"{name}.toml"
        if path.is_file():
            text = set_toml_key(path.read_text(encoding="utf-8"), None, "model", model)
            updates[path] = set_toml_key(text, None, "model_reasoning_effort", effort)
    for name in ("reviewer", "verifier", "plan_expander"):
        path = codex_home / "agents" / f"{name}.toml"
        if path.is_file():
            text = set_toml_key(path.read_text(encoding="utf-8"), None,
                                "model", review_model)
            updates[path] = set_toml_key(text, None, "model_reasoning_effort",
                                         review_effort)
    return updates


def validate_updates(updates: dict[Path, str], model: str, effort: str,
                     review_model: str, review_effort: str,
                     root: Path | None = None) -> None:
    for path, text in updates.items():
        if path.suffix == ".toml":
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError as exc:
                raise ProfileError(f"generated invalid TOML {path}: {exc}") from exc
    project_agents = [p for p in updates if p.as_posix().endswith(("agents/worker.toml", "agents/duty_officer.toml"))]
    for path in project_agents:
        doc = tomllib.loads(updates[path])
        if doc.get("model") != model or doc.get("model_reasoning_effort") != effort:
            raise ProfileError(f"execution pin mismatch after generation: {path}")
    review_agents = [p for p in updates if p.as_posix().endswith(
        ("agents/reviewer.toml", "agents/verifier.toml",
         "agents/plan_expander.toml"))]
    for path in review_agents:
        doc = tomllib.loads(updates[path])
        if (doc.get("model") != review_model
                or doc.get("model_reasoning_effort") != review_effort):
            raise ProfileError(f"review pin mismatch after generation: {path}")
    for path, text in updates.items():
        if path.suffix == ".json":
            json.loads(text)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def apply_transaction(updates: dict[Path, str]) -> None:
    originals = {path: (path.read_text(encoding="utf-8") if path.is_file() else None)
                 for path in updates}
    written: list[Path] = []
    try:
        for path, text in updates.items():
            atomic_write(path, text)
            written.append(path)
    except Exception:
        errors = []
        for path in reversed(written):
            try:
                original = originals[path]
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, original)
            except Exception as exc:  # best effort; surface every failed restore
                errors.append(f"{path}: {exc}")
        if errors:
            raise ProfileError("switch failed and rollback was incomplete: " + "; ".join(errors))
        raise


def wsl_updates(wsl_root: Path, local_updates: dict[Path, str], root: Path) -> dict[Path, str]:
    if not wsl_root.is_dir():
        return {}
    result: dict[Path, str] = {}
    for source, text in local_updates.items():
        try:
            rel = source.relative_to(root)
        except ValueError:
            continue
        target = wsl_root / rel
        result[target] = text
    return result


def state(root: Path, doc: dict[str, Any]) -> dict[str, Any]:
    active = str(doc.get("active_profile") or "")
    model, effort, review_model, review_effort = profile_values(doc, active)
    return {"profile": active, "execution_model": model,
            "execution_reasoning": effort, "review_model": review_model,
            "review_reasoning": review_effort}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="atomic LOOP execution-model profile switch")
    ap.add_argument("command", choices=["list", "status", "set"])
    ap.add_argument("profile", nargs="?")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--codex-home", type=Path, default=DEFAULT_CODEX_HOME)
    ap.add_argument(
        "--wsl-root", type=Path,
        help="explicit LOOP root inside WSL; omitted means no WSL writes",
    )
    ap.add_argument(
        "--wsl-codex-home", type=Path,
        help="explicit WSL Codex home; never inferred from --wsl-root",
    )
    ap.add_argument("--no-global", action="store_true")
    ap.add_argument("--no-wsl", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    doc, profile_path = load_profiles(root)
    if args.command == "list":
        rows = [{"name": name, **value} for name, value in doc["profiles"].items()]
        print(json.dumps(rows, ensure_ascii=False, indent=2) if args.json else
              "\n".join(
                  f"{r['name']}: execution={r['execution_model']} "
                  f"({r['execution_reasoning']}); review="
                  f"{r.get('review_model', '<not configured>')} "
                  f"({r.get('review_reasoning', 'max')})" for r in rows))
        return 0
    if args.command == "status":
        value = state(root, doc)
        print(json.dumps(value, ensure_ascii=False) if args.json else
              f"{value['profile']}: execution={value['execution_model']} "
              f"({value['execution_reasoning']}); review="
              f"{value['review_model']} ({value['review_reasoning']})")
        return 0
    if not args.profile:
        ap.error("set requires a profile")
    model, effort, review_model, review_effort = profile_values(doc, args.profile)
    updates = project_updates(root, profile_path, args.profile, model, effort,
                              review_model, review_effort)
    if not args.no_global:
        updates.update(global_updates(args.codex_home.resolve(), model, effort,
                                      review_model, review_effort))
    if not args.no_wsl and args.wsl_root is not None:
        wsl_root = args.wsl_root.resolve()
        updates.update(wsl_updates(wsl_root, updates, root))
        if args.wsl_codex_home is not None:
            updates.update(global_updates(
                args.wsl_codex_home.resolve(), model, effort,
                review_model, review_effort,
            ))
    validate_updates(updates, model, effort, review_model, review_effort, root)
    apply_transaction(updates)
    marker = {"schema": "codex-loop-model-profile-state/v1", "ts": time.time(),
              "profile": args.profile, "execution_model": model,
              "execution_reasoning": effort, "review_model": review_model,
              "review_reasoning": review_effort,
              "updated_files": len(updates)}
    atomic_write(root / "data" / "governor" / "model_profile.json",
                 json.dumps(marker, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(marker, ensure_ascii=False) if args.json else
          f"{args.profile}: {model} ({effort}); updated {len(updates)} files")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ProfileError) as exc:
        print(f"model profile switch failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
