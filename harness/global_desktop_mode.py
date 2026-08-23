#!/usr/bin/env python3
"""Install, activate, deactivate, and roll back Desktop-wide LOOP mode."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODE_SCHEMA = "codex-loop-global-mode/v1"
INSTALL_SCHEMA = "codex-loop-global-mode-install/v1"
MANAGED_SCRIPTS = (
    "global_loop_mode.py",
    "subagent_lifecycle.py",
    "sol_tool_gate.py",
    "leaf_agent_spawn_gate.py",
    "root_agent_spawn_gate.py",
    "sol_tool_gate_router.py",
    "reconcile_subagent_metering.ps1",
    "reconcile_subagent_metering.py",
)
NEUTRAL_AGENTS = """# Codex Desktop mode boundary

LOOP behavior is selected by the machine-local Desktop mode launcher, not by
the current repository name or working directory.  When LOOP mode is active,
the global SessionStart/SubagentStart hooks provide the complete LOOP working
agreement as developer context and bind orchestration to its fixed control
root.  When that context is absent, do not infer or apply LOOP concurrency,
model-routing, packet, or harness rules merely because a previous task used
LOOP or because a repository is named after a previous LOOP installation.
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def json_bytes(doc: Any) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def state_paths(root: Path) -> tuple[Path, Path]:
    override = os.environ.get("CODEX_LOOP_STATE_DIR")
    state = (Path(override).expanduser().resolve() if override else
             root / "data" / "global-mode")
    return state / "global-loop-mode.json", state / "global-loop-mode-install.json"


def active_agents(root: Path) -> bytes:
    """Return the trust-independent active-mode agreement for global AGENTS."""
    blocks = [
        "# Active Codex LOOP global mode",
        "",
        f"LOOP_CONTROL_ROOT={root}",
        "The current task workspace is the target workspace; it does not need to contain the LOOP runtime.",
        "All packets, lifecycle state, model routing, and observer state use LOOP_CONTROL_ROOT.",
        "These instructions apply to every target workspace while global LOOP mode is active.",
    ]
    for source in (root / "config" / "global_working_agreement.md", root / "AGENTS.md"):
        content = source.read_text(encoding="utf-8-sig")
        blocks.extend(("", f"<!-- source: {source} -->", render_install_root(content, root).strip()))
    return ("\n".join(blocks).strip() + "\n").encode("utf-8")


def render_install_root(text: str, root: Path) -> str:
    """Render portable package placeholders without breaking TOML on Windows."""
    resolved = root.resolve()
    windows_root = str(resolved)
    posix_root = resolved.as_posix()
    python_windows = str(Path(sys.executable).resolve())
    return text.replace("<PYTHON_WINDOWS>", python_windows).replace(
        "<LOOP_INSTALL_DIR>\\", windows_root + "\\"
    ).replace("<LOOP_INSTALL_DIR>", posix_root)


def managed_requirements(root: Path) -> bytes:
    text = (root / "config" / "global_requirements.toml").read_text(encoding="utf-8-sig")
    text = render_install_root(text, root)
    doc = tomllib.loads(text)
    hooks = doc.get("hooks") if isinstance(doc, dict) else None
    if not isinstance(hooks, dict) or not hooks.get("windows_managed_dir"):
        raise ValueError("managed global_requirements.toml is invalid")
    if "<LOOP_INSTALL_DIR>" in text or "<PYTHON_WINDOWS>" in text:
        raise ValueError("managed global_requirements.toml still contains an install placeholder")
    return text.encode("utf-8")


def runtime_canary(root: Path, codex_home: Path, mode: dict[str, Any]) -> dict[str, Any]:
    """Find a post-activation root rollout outside the fixed control root."""
    try:
        activated = datetime.fromisoformat(
            str(mode["updated_at"]).replace("Z", "+00:00")
        ).timestamp()
    except (KeyError, TypeError, ValueError):
        return {"verified": False}
    sessions = codex_home / "sessions"
    if not sessions.exists():
        return {"verified": False}
    candidates = sorted(
        sessions.rglob("rollout-*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates[:128]:
        try:
            if path.stat().st_mtime + 1 < activated:
                break
            with path.open(encoding="utf-8", errors="replace") as handle:
                first = json.loads(handle.readline())
                meta = first.get("payload") if first.get("type") == "session_meta" else {}
                if not isinstance(meta, dict) or meta.get("parent_thread_id"):
                    continue
                cwd = Path(str(meta.get("cwd") or "")).resolve()
                if cwd == root or root in cwd.parents:
                    continue
                handle.seek(0)
                text = handle.read(4 * 1024 * 1024).replace("\\\\", "\\")
        except (OSError, TypeError, ValueError):
            continue
        if all(token in text for token in (
            "Active Codex LOOP global mode",
            f"LOOP_CONTROL_ROOT={root}",
            "Mandatory LOOP model routing",
        )):
            return {
                "verified": True,
                "session_id": str(meta.get("id") or ""),
                "cwd": str(cwd),
                "rollout": str(path),
            }
    return {"verified": False}


def command_is_managed(hook: dict[str, Any]) -> bool:
    text = " ".join(str(hook.get(key, "")) for key in ("command", "commandWindows"))
    normalized = text.replace("/", "\\").casefold()
    return any(name.casefold() in normalized for name in MANAGED_SCRIPTS)


def strip_managed_hooks(doc: dict[str, Any]) -> dict[str, Any]:
    result = dict(doc) if isinstance(doc, dict) else {}
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    cleaned: dict[str, list[dict[str, Any]]] = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        event_groups: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                event_groups.append(group)
                continue
            retained = [handler for handler in handlers
                        if not (isinstance(handler, dict) and command_is_managed(handler))]
            if retained:
                copy = dict(group)
                copy["hooks"] = retained
                event_groups.append(copy)
        if event_groups:
            cleaned[event] = event_groups
    result["hooks"] = cleaned
    return result


def merged_hooks(root: Path, codex_home: Path) -> dict[str, Any]:
    destination = read_json(codex_home / "hooks.json", {})
    if not isinstance(destination, dict):
        raise ValueError(f"global hooks file is not a JSON object: {codex_home / 'hooks.json'}")
    source = read_json(root / "config" / "global_hooks.json", None)
    if not isinstance(source, dict) or not isinstance(source.get("hooks"), dict):
        raise ValueError("managed global_hooks.json is invalid")
    merged = strip_managed_hooks(destination)
    hooks = merged.setdefault("hooks", {})
    for event, groups in source["hooks"].items():
        hooks.setdefault(event, []).extend(groups)
    managed_description = str(source.get("description") or "").strip()
    existing_description = str(merged.get("description") or "").strip()
    if existing_description and managed_description not in existing_description:
        merged["description"] = existing_description + " | " + managed_description
    elif existing_description:
        merged["description"] = existing_description
    else:
        merged["description"] = managed_description
    return merged


def ensure_backup(root: Path, codex_home: Path, install_state: Path) -> dict[str, Any]:
    existing = read_json(install_state, None)
    if isinstance(existing, dict) and existing.get("schema") == INSTALL_SCHEMA:
        if Path(str(existing.get("codex_home", ""))).resolve() != codex_home.resolve():
            raise ValueError(
                "this LOOP installation is already bound to another CODEX_HOME; "
                "restore it before activating a different home"
            )
        requirements_path = codex_home / "requirements.toml"
        if (requirements_path.exists()
                and requirements_path.read_bytes() != managed_requirements(root)):
            raise ValueError(
                "managed requirements.toml changed after activation; restore or "
                "review it before re-activating"
            )
        agents_path = codex_home / "AGENTS.md"
        if agents_path.exists() and agents_path.read_bytes() not in {
                NEUTRAL_AGENTS.encode("utf-8"), active_agents(root)}:
            raise ValueError(
                "managed AGENTS.md changed after activation; restore or review "
                "it before re-activating"
            )
        # v1 installations created before managed hooks did not record
        # requirements.toml. Extend the immutable restore ledger before the
        # first managed write, preserving the exact current bytes if present.
        files = existing.setdefault("files", {})
        if "requirements.toml" not in files:
            source = codex_home / "requirements.toml"
            record: dict[str, Any] = {"existed": source.exists()}
            if source.exists():
                backup = Path(str(existing["backup_dir"])) / "requirements.toml"
                data = source.read_bytes()
                shutil.copy2(source, backup)
                record.update({"sha256": digest(data), "backup": str(backup)})
            files["requirements.toml"] = record
            atomic_write(install_state, json_bytes(existing))
        return existing
    # Microseconds plus PID prevent two first-time launchers in the same second
    # from selecting the same immutable backup directory.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + f"-{os.getpid()}"
    backup = install_state.parent / "backups" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    files: dict[str, dict[str, Any]] = {}
    for name in ("AGENTS.md", "hooks.json", "requirements.toml"):
        source = codex_home / name
        record: dict[str, Any] = {"existed": source.exists()}
        if source.exists():
            data = source.read_bytes()
            shutil.copy2(source, backup / name)
            record.update({"sha256": digest(data), "backup": str(backup / name)})
        files[name] = record
    doc = {
        "schema": INSTALL_SCHEMA,
        "installed_at": utc_now(),
        "control_root": str(root),
        "codex_home": str(codex_home),
        "backup_dir": str(backup),
        "files": files,
    }
    atomic_write(install_state, json_bytes(doc))
    return doc


def install(root: Path, codex_home: Path) -> dict[str, Any]:
    marker, install_state = state_paths(root)
    backup = ensure_backup(root, codex_home, install_state)
    # Remove prior LOOP user hooks so trusting them can never become an
    # accidental second execution path. The authoritative hooks are managed
    # inline by requirements.toml and therefore trusted by policy.
    existing_hooks = read_json(codex_home / "hooks.json", {})
    hooks = strip_managed_hooks(existing_hooks if isinstance(existing_hooks, dict) else {})
    requirements = managed_requirements(root)
    atomic_write(codex_home / "hooks.json", json_bytes(hooks))
    atomic_write(codex_home / "requirements.toml", requirements)
    atomic_write(codex_home / "AGENTS.md", NEUTRAL_AGENTS.encode("utf-8"))
    return {
        "installed": True,
        "control_root": str(root),
        "codex_home": str(codex_home),
        "marker": str(marker),
        "backup_dir": backup.get("backup_dir"),
        "agents_sha256": digest(NEUTRAL_AGENTS.encode("utf-8")),
        "hooks_sha256": digest(json_bytes(hooks)),
        "requirements_sha256": digest(requirements),
    }


def set_active(root: Path, codex_home: Path, active: bool) -> dict[str, Any]:
    marker, _ = state_paths(root)
    doc = {
        "schema": MODE_SCHEMA,
        "active": active,
        "control_root": str(root),
        "codex_home": str(codex_home),
        "updated_at": utc_now(),
        "scope": "all Desktop tasks regardless of target workspace" if active else "inactive",
    }
    atomic_write(marker, json_bytes(doc))
    return doc


def status(root: Path, codex_home: Path) -> dict[str, Any]:
    marker, install_state = state_paths(root)
    mode = read_json(marker, {})
    installed = read_json(install_state, {})
    declared_active = (
        isinstance(mode, dict) and mode.get("schema") == MODE_SCHEMA
        and mode.get("active") is True
    )
    requirements_expected = managed_requirements(root)
    requirements_path = codex_home / "requirements.toml"
    requirements_exact = (
        requirements_path.exists()
        and digest(requirements_path.read_bytes()) == digest(requirements_expected)
    )
    active_expected = active_agents(root)
    agents_active = (
        (codex_home / "AGENTS.md").exists()
        and digest((codex_home / "AGENTS.md").read_bytes()) == digest(active_expected)
    )
    context_verified = False
    spawn_gate_verified = False
    if declared_active and requirements_exact:
        try:
            context = subprocess.run(
                [sys.executable, str(root / "hooks" / "global_loop_mode.py"),
                 "--component", "context", "--event", "SessionStart"],
                input="{}", text=True, capture_output=True, timeout=15,
            )
            try:
                context_doc = json.loads(context.stdout)
                context_text = str(
                    context_doc["hookSpecificOutput"]["additionalContext"]
                )
            except (KeyError, TypeError, ValueError):
                context_text = ""
            context_verified = (
                context.returncode == 0
                and "Active Codex LOOP global mode" in context_text
                and f"LOOP_CONTROL_ROOT={root}" in context_text
                and "Mandatory LOOP model routing" in context_text
            )
            denied = subprocess.run(
                [sys.executable, str(root / "hooks" / "global_loop_mode.py"),
                 "--component", "spawn-gate"],
                input=json.dumps({
                    "tool_name": "multi_agent_v1__spawn_agent",
                    "session_id": "loop-install-canary-root",
                    "tool_input": {"fork_context": True},
                }),
                text=True, capture_output=True, timeout=15,
            )
            spawn_gate_verified = (
                denied.returncode == 0 and '"permissionDecision": "deny"' in denied.stdout
            )
        except (OSError, subprocess.SubprocessError):
            context_verified = False
            spawn_gate_verified = False
    effective_active = all((
        declared_active,
        requirements_exact,
        agents_active,
        context_verified,
        spawn_gate_verified,
    ))
    runtime = runtime_canary(root, codex_home, mode) if declared_active else {"verified": False}
    return {
        "installed": isinstance(installed, dict) and installed.get("schema") == INSTALL_SCHEMA,
        "active": declared_active,
        "declared_active": declared_active,
        "hooks_installed": requirements_exact,
        "hooks_trusted_or_managed": requirements_exact,
        "context_injection_verified": context_verified,
        "spawn_gate_verified": spawn_gate_verified,
        "active_agreement_present": agents_active,
        "effective_active": effective_active,
        "runtime_canary_verified": runtime.get("verified") is True,
        "runtime_canary_required": declared_active and runtime.get("verified") is not True,
        "runtime_canary": runtime,
        "control_root": str(root),
        "codex_home": str(codex_home),
        "marker": str(marker),
        "hooks_present": (codex_home / "hooks.json").exists(),
        "agents_present": (codex_home / "AGENTS.md").exists(),
    }


def restore(root: Path, codex_home: Path) -> dict[str, Any]:
    marker, install_state = state_paths(root)
    state = read_json(install_state, None)
    if not isinstance(state, dict) or state.get("schema") != INSTALL_SCHEMA:
        raise ValueError("no global LOOP installation backup is registered")
    if Path(str(state.get("codex_home", ""))).resolve() != codex_home:
        raise ValueError("registered CODEX_HOME does not match requested restore target")
    files = state.get("files", {})
    for name in ("AGENTS.md", "hooks.json", "requirements.toml"):
        record = files.get(name, {}) if isinstance(files, dict) else {}
        destination = codex_home / name
        if record.get("existed"):
            backup = Path(str(record["backup"]))
            data = backup.read_bytes()
            if digest(data) != record.get("sha256"):
                raise ValueError(f"backup hash mismatch for {name}")
            atomic_write(destination, data)
        else:
            destination.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    install_state.unlink(missing_ok=True)
    return {"restored": True, "codex_home": str(codex_home), "backup_dir": state.get("backup_dir")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "activate", "deactivate", "status", "restore"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    codex_home = args.codex_home.expanduser().resolve()
    if args.action == "install":
        result = install(root, codex_home)
    elif args.action == "activate":
        installed = install(root, codex_home)
        mode = set_active(root, codex_home, True)
        atomic_write(codex_home / "AGENTS.md", active_agents(root))
        verified = status(root, codex_home)
        if not verified.get("effective_active"):
            set_active(root, codex_home, False)
            atomic_write(codex_home / "AGENTS.md", NEUTRAL_AGENTS.encode("utf-8"))
            raise RuntimeError(
                "LOOP activation failed effective-active verification: "
                + json.dumps(verified, ensure_ascii=False, sort_keys=True)
            )
        result = {"installation": installed, "mode": mode, "verification": verified}
    elif args.action == "deactivate":
        result = set_active(root, codex_home, False)
        atomic_write(codex_home / "AGENTS.md", NEUTRAL_AGENTS.encode("utf-8"))
    elif args.action == "restore":
        result = restore(root, codex_home)
    else:
        result = status(root, codex_home)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
