#!/usr/bin/env python3
"""Deny recursive subagent births while leaving root-agent dispatch untouched.

The global throughput agreement authorizes roots to fill the shared LOOP
capacity.  A worker/verifier/reviewer is a leaf: allowing every leaf to apply
the same agreement recursively creates a 16x16 birth cascade.  This hook uses
the immutable rollout session_meta parent_thread_id as the authority.  It
does not cap root concurrency and does not terminate existing work.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


SPAWN_NAMES = ("spawn_agent", "create_thread", "fork_thread")


def is_spawn_tool(name: Any) -> bool:
    value = str(name or "").casefold()
    return any(item in value for item in SPAWN_NAMES)


def session_is_child(session_id: Any, sessions: Path) -> bool:
    value = str(session_id or "").strip()
    if not value or not sessions.exists():
        return False
    matches = sorted(sessions.rglob("rollout-*%s*.jsonl" % value),
                     key=lambda path: path.stat().st_mtime, reverse=True)
    for path in matches:
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("type") != "session_meta":
                        continue
                    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                    parent = payload.get("parent_thread_id")
                    if not parent:
                        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
                        subagent = source.get("subagent") if isinstance(source.get("subagent"), dict) else {}
                        spawn = subagent.get("thread_spawn") if isinstance(subagent.get("thread_spawn"), dict) else {}
                        parent = spawn.get("parent_thread_id") or spawn.get("parent_session_id")
                    return bool(parent)
        except OSError:
            continue
    return False


def decision(payload: dict[str, Any], sessions: Path) -> dict[str, Any] | None:
    if not is_spawn_tool(payload.get("tool_name") or payload.get("tool") or payload.get("name")):
        return None
    session_id = payload.get("session_id") or payload.get("thread_id")
    marked_leaf = os.environ.get("LOOP_LEAF_AGENT", "").strip() == "1"
    if not marked_leaf and not session_is_child(session_id, sessions):
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "LOOP leaf agents cannot recursively spawn agents. Return the bounded "
            "result to the root orchestrator; the root owns shared global-60 refill."
        ),
    }}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    sessions = Path(os.environ.get(
        "CODEX_SESSIONS_DIR",
        str(Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"),
    )).resolve()
    result = decision(payload if isinstance(payload, dict) else {}, sessions)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
