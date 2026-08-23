#!/usr/bin/env python3
"""Install portable Codex agent files and merge documented user config keys."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import tomllib
from pathlib import Path


SECTION_RE = re.compile(r"^\s*\[([A-Za-z0-9_.-]+)]\s*$")
KEY_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        if path.exists():
            os.chmod(temporary, path.stat().st_mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        if path.exists():
            os.chmod(temporary, path.stat().st_mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def backup_path(path: Path, stamp: str) -> Path:
    candidate = path.with_name(path.name + f".bak.{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(path.name + f".bak.{stamp}.{counter}")
        counter += 1
    return candidate


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_state_path(root: Path) -> Path:
    return root / "data" / "global-mode" / "user-config-install.json"


def has_key(doc: dict, section: str, key: str) -> bool:
    node: object = doc
    for part in filter(None, section.split(".")):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, dict) and key in node


def merge_config(example_path: Path, user_path: Path) -> tuple[str, list[str]]:
    example_text = example_path.read_text(encoding="utf-8-sig")
    example_doc = tomllib.loads(example_text)
    del example_doc  # parsing is the validation boundary
    if user_path.exists():
        user_text = user_path.read_text(encoding="utf-8-sig")
        user_doc = tomllib.loads(user_text)
        lines = user_text.splitlines()
    else:
        user_doc, lines = {}, []

    missing: dict[str, list[str]] = {}
    section = ""
    for raw in example_text.splitlines():
        match = SECTION_RE.match(raw)
        if match:
            section = match.group(1)
            continue
        match = KEY_RE.match(raw)
        if match and not raw.lstrip().startswith("#"):
            key = match.group(1)
            if not has_key(user_doc, section, key):
                missing.setdefault(section, []).append(raw)

    changed: list[str] = []
    for section, values in missing.items():
        if not values:
            continue
        header = f"[{section}]"
        index = next((i for i, line in enumerate(lines) if line.strip() == header), None)
        if index is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend(["# Added by Codex LOOP Orchestra", header, *values])
        else:
            lines[index + 1:index + 1] = values
        changed.extend(f"{section}.{KEY_RE.match(value).group(1)}" for value in values)
    rendered = "\n".join(lines).rstrip() + "\n"
    tomllib.loads(rendered)
    return rendered, changed


def install(root: Path, codex_home: Path, dry_run: bool = False) -> dict:
    root, codex_home = root.resolve(), codex_home.resolve()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f".{time.time_ns()}"
    result: dict[str, object] = {"dry_run": dry_run, "agents": [], "config_keys": []}

    # Validate and plan the complete transaction before the first write.
    agents_dir = codex_home / "agents"
    agent_changes: list[tuple[Path, bytes]] = []
    for source in sorted((root / "agents").glob("*.toml")):
        source_bytes = source.read_bytes()
        tomllib.loads(source_bytes.decode("utf-8-sig"))
        target = agents_dir / source.name
        action = "unchanged"
        if not target.exists() or target.read_bytes() != source_bytes:
            action = "install" if not target.exists() else "replace"
            agent_changes.append((target, source_bytes))
        result["agents"].append({"name": source.name, "action": action})

    config_path = codex_home / "config.toml"
    rendered, changed = merge_config(root / "config" / "config.toml.example", config_path)
    result["config_keys"] = changed
    if dry_run:
        return result

    targets = [target for target, _ in agent_changes]
    if changed:
        targets.append(config_path)
    originals = {
        path: (path.read_bytes() if path.exists() else None)
        for path in targets
    }
    backups: dict[Path, Path] = {}
    state_path = install_state_path(root)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = None
    if state is not None:
        if state.get("schema") != "codex-loop-user-config-install/v1":
            raise RuntimeError("unrecognized user-config restore ledger")
        if Path(str(state.get("codex_home", ""))).resolve() != codex_home:
            raise RuntimeError(
                "this LOOP installation is already bound to a different CODEX_HOME; "
                "restore it before activating another home"
            )
    entries = dict((state or {}).get("files") or {})
    backup_root = Path(str((state or {}).get("backup_dir") or (
        root / "data" / "backups" / "user-config" / stamp
    )))
    try:
        for path, content in originals.items():
            if content is not None:
                destination = backup_path(path, stamp)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                backups[path] = destination
            rel = path.relative_to(codex_home).as_posix()
            if rel not in entries:
                managed_backup = backup_root / rel
                entry: dict[str, object] = {"existed": content is not None}
                if content is not None:
                    managed_backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, managed_backup)
                    entry["backup"] = str(managed_backup)
                    entry["original_sha256"] = hashlib.sha256(content).hexdigest()
                entries[rel] = entry
        for target, content in agent_changes:
            atomic_write_bytes(target, content)
        if changed:
            atomic_write(config_path, rendered)
        for path in targets:
            rel = path.relative_to(codex_home).as_posix()
            entries[rel]["installed_sha256"] = file_digest(path)
        ledger = {
            "schema": "codex-loop-user-config-install/v1",
            "codex_home": str(codex_home),
            "backup_dir": str(backup_root),
            "files": entries,
        }
        atomic_write(
            state_path,
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
        )
    except Exception:
        rollback_errors: list[str] = []
        for path in reversed(targets):
            try:
                original = originals[path]
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(path, original)
            except Exception as exc:  # best effort, but never hide incomplete rollback
                rollback_errors.append(f"{path}: {exc}")
        if rollback_errors:
            raise RuntimeError(
                "user config install failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            )
        raise
    return result


def restore(root: Path, codex_home: Path, dry_run: bool = False) -> dict:
    root, codex_home = root.resolve(), codex_home.resolve()
    state_path = install_state_path(root)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("schema") != "codex-loop-user-config-install/v1":
        raise RuntimeError("unrecognized user-config restore ledger")
    if Path(str(state.get("codex_home", ""))).resolve() != codex_home:
        raise RuntimeError("restore ledger CODEX_HOME does not match requested home")
    restored: list[str] = []
    skipped: list[str] = []
    for rel, entry in (state.get("files") or {}).items():
        target = (codex_home / rel).resolve()
        if not target.is_relative_to(codex_home):
            raise RuntimeError(f"unsafe restore ledger path: {rel}")
        installed = str(entry.get("installed_sha256") or "")
        if not target.is_file() or not installed or file_digest(target) != installed:
            skipped.append(rel)
            continue
        if not dry_run:
            if entry.get("existed"):
                backup = Path(str(entry.get("backup") or ""))
                content = backup.read_bytes()
                expected = str(entry.get("original_sha256") or "")
                if hashlib.sha256(content).hexdigest() != expected:
                    raise RuntimeError(f"backup hash mismatch: {rel}")
                atomic_write_bytes(target, content)
            else:
                target.unlink(missing_ok=True)
        restored.append(rel)
    if not dry_run and not skipped:
        state_path.unlink(missing_ok=True)
    return {"dry_run": dry_run, "restored": restored, "skipped_modified": skipped}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", nargs="?", choices=("install", "restore"),
                        default="install")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    operation = install if args.action == "install" else restore
    print(json.dumps(operation(args.root, args.codex_home, args.dry_run),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
