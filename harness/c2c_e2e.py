#!/usr/bin/env python3
"""Thin Codex-LOOP adapter for XiaoDuoYa/codex-with-chatgpt.

This file intentionally contains no bridge, OAuth, tunnel, browser, connector
or session implementation.  It only resolves and invokes the upstream CLI for
read-only local state operations.  Browser E2E remains owned by the installed
``codex-with-chatgpt`` Skill and its single in-app browser tab.
"""
from __future__ import annotations

import argparse
import json
import locale
import os
import shutil
import subprocess
from pathlib import Path

SAFE_ACTIONS = {"status", "doctor", "session", "workspace"}


def _decode(data: bytes | str | None) -> str:
    if isinstance(data, str):
        return data
    raw = data or b""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False), errors="replace")


def _upstream_ok(action: str, doc: object) -> bool:
    if not isinstance(doc, dict):
        return False
    if action == "doctor":
        report = doc.get("report")
        if not isinstance(report, dict):
            return False
        required = ("node", "sandbox", "workspace", "bridge")
        return all(isinstance(report.get(k), dict)
                   and report[k].get("ok") is True for k in required)
    return doc.get("ok") is True


def resolve_cli(explicit: str | None = None) -> list[str]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_cli = os.environ.get("CODEX_WITH_CHATGPT_CLI")
    if env_cli:
        candidates.append(Path(env_cli).expanduser())
    executable = shutil.which("c2c")
    if executable:
        return [executable]
    home = os.environ.get("CODEX_WITH_CHATGPT_HOME")
    if home:
        candidates.append(Path(home).expanduser() / "bin" / "c2c.js")
    candidates.append(Path.home() / "codex-with-chatgpt" / "bin" / "c2c.js")
    node = shutil.which("node")
    for candidate in candidates:
        if candidate.is_file():
            if candidate.suffix.lower() == ".js":
                if not node:
                    raise RuntimeError("node is required by codex-with-chatgpt")
                return [node, str(candidate.resolve())]
            return [str(candidate.resolve())]
    raise FileNotFoundError(
        "codex-with-chatgpt CLI not found; set CODEX_WITH_CHATGPT_CLI or "
        "CODEX_WITH_CHATGPT_HOME")


def command(action: str, workspace: Path, cli: list[str]) -> list[str]:
    workspace = Path(workspace).resolve()
    if action == "doctor":
        return [*cli, "doctor", "--no-fix", "--json", "-w", str(workspace)]
    if action == "session":
        return [*cli, "session", "get", "--json", "-w", str(workspace)]
    if action in {"status", "workspace"}:
        return [*cli, action, "--json", "-w", str(workspace)]
    raise ValueError(f"unsupported safe action: {action}")


def run(action: str, workspace: Path, *, explicit_cli: str | None = None,
        timeout: int = 30) -> dict:
    cli = resolve_cli(explicit_cli)
    argv = command(action, workspace, cli)
    proc = subprocess.run(argv, capture_output=True, text=False,
                          timeout=timeout, check=False)
    stdout = _decode(proc.stdout)
    stderr = _decode(proc.stderr)
    try:
        upstream = json.loads(stdout)
    except ValueError:
        upstream = None
    return {"ok": proc.returncode == 0 and _upstream_ok(action, upstream),
            "action": action, "workspace": str(Path(workspace).resolve()),
            "exit_code": proc.returncode, "upstream": upstream,
            "stderr": stderr[:1000]}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="read-only adapter for codex-with-chatgpt E2E state")
    ap.add_argument("action", choices=sorted(SAFE_ACTIONS))
    ap.add_argument("--workspace", type=Path, default=Path.cwd())
    ap.add_argument("--cli")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()
    result = run(args.action, args.workspace, explicit_cli=args.cli,
                 timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
