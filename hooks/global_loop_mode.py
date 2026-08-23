#!/usr/bin/env python3
"""Conditional global LOOP hook dispatcher.

Codex Desktop is a single application surface whose tasks may use unrelated
working directories.  Project-local hooks therefore cannot define a Desktop
"LOOP edition".  This wrapper makes LOOP mode explicit and app-wide without
copying the F2 runtime into every target repository:

* a marker under ``<package>/data/global-mode/global-loop-mode.json`` selects mode;
* SessionStart/SubagentStart inject the complete LOOP instructions;
* lifecycle and policy hooks run against the fixed F2 control root;
* an absent, inactive, malformed, or mismatched marker is a safe no-op.

The wrapper is installed as a user/global hook.  Project-local F2 hooks remain
valid and continue to work independently.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA = "codex-loop-global-mode/v1"
DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def marker_path(root: Path) -> Path:
    override = os.environ.get("CODEX_LOOP_MODE_MARKER")
    return (_resolved(Path(override)) if override else
            root / "data" / "global-mode" / "global-loop-mode.json")


def load_active_marker(root: Path) -> dict[str, Any] | None:
    path = marker_path(root)
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"global_loop_mode: marker unreadable ({exc}); inactive\n")
        return None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or doc.get("active") is not True:
        return None
    try:
        selected = _resolved(Path(str(doc["control_root"])))
    except (KeyError, OSError, TypeError, ValueError):
        return None
    if selected != _resolved(root):
        sys.stderr.write(
            f"global_loop_mode: marker control_root={selected} does not match installed root={root}; inactive\n"
        )
        return None
    return doc


def instruction_text(root: Path) -> str:
    sources = (
        root / "config" / "global_working_agreement.md",
        root / "AGENTS.md",
    )
    blocks: list[str] = [
        "# Active Codex LOOP global mode",
        "",
        f"LOOP_CONTROL_ROOT={root}",
        "The current task workspace is the target workspace; it does not need to contain the LOOP runtime.",
        "All packets, lifecycle state, model routing, and observer state use LOOP_CONTROL_ROOT.",
        "These instructions apply to every target workspace while global LOOP mode is active.",
    ]
    for source in sources:
        blocks.extend(("", f"<!-- source: {source} -->", source.read_text(encoding="utf-8-sig").strip()))
    return "\n".join(blocks).strip() + "\n"


def emit_context(root: Path, event: str) -> int:
    if event not in {"SessionStart", "SubagentStart"}:
        return 0
    try:
        context = instruction_text(root)
    except OSError as exc:
        sys.stderr.write(f"global_loop_mode: instruction source unavailable ({exc})\n")
        return 0
    # Hook hosts on Windows may expose a legacy console code page (for
    # example GBK).  ASCII JSON escapes preserve the complete Unicode context
    # while avoiding an encoder failure before Codex can parse the payload.
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }, ensure_ascii=True))
    return 0


def dispatch(root: Path, component: str, event: str | None, raw: str) -> int:
    if component == "lifecycle":
        if not event:
            raise ValueError("lifecycle dispatch requires --event")
        command = [sys.executable, str(root / "hooks" / "subagent_lifecycle.py"), "--event", event]
    elif component == "gate":
        command = [sys.executable, str(root / "hooks" / "sol_tool_gate_router.py")]
    elif component == "leaf-gate":
        command = [sys.executable, str(root / "hooks" / "leaf_agent_spawn_gate.py")]
    elif component == "spawn-gate":
        command = [sys.executable, str(root / "hooks" / "root_agent_spawn_gate.py")]
    else:
        raise ValueError(f"unsupported component {component!r}")
    env = os.environ.copy()
    env["LOOP_ROOT"] = str(root)
    proc = subprocess.run(
        command,
        input=raw,
        text=True,
        capture_output=True,
        timeout=25,
        env=env,
        cwd=str(root),
    )
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--component",
        choices=("context", "lifecycle", "gate", "leaf-gate", "spawn-gate"),
        required=True,
    )
    parser.add_argument("--event")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    root = _resolved(args.root)
    raw = sys.stdin.read()
    if load_active_marker(root) is None:
        return 0
    try:
        if args.component == "context":
            return emit_context(root, str(args.event or ""))
        return dispatch(root, args.component, args.event, raw)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        # Global mode must fail visibly without making ordinary Desktop
        # unusable.  The policy gate itself retains its own layered-mode
        # fail-closed semantics once it is successfully invoked.
        sys.stderr.write(f"global_loop_mode: {args.component} failed ({exc})\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
