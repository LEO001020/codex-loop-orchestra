#!/usr/bin/env python3
"""Fail closed when a LOOP root attempts an ambiguous child birth.

The configured default child model is defence in depth, not authorization.
Every ordinary Desktop child must be born with an explicit LOOP role, the
role-specific model and effort from the active model profile, and an
independent context.
"""
from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from leaf_agent_spawn_gate import is_spawn_agent_tool, is_spawn_tool, session_is_child


PROFILE_PATH = Path(__file__).resolve().parents[1] / "config" / "model_profiles.toml"
QUARANTINE_PATH = Path(__file__).resolve().parents[1] / "data" / "governor" / "quarantined_models.json"
EXECUTION_ROLES = frozenset({"worker", "duty_officer"})
REVIEW_ROLES = frozenset({"verifier", "reviewer", "plan_expander"})
APPROVED_ROLES = EXECUTION_ROLES | REVIEW_ROLES
APPROVED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})
def approved_route(role: str, profile_path: Path | None = None) -> tuple[str, str]:
    """Return the active profile route for *role*, failing closed on drift."""
    profile_path = PROFILE_PATH if profile_path is None else profile_path
    try:
        doc = tomllib.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot load {profile_path}: {exc}") from exc

    active = doc.get("active_profile")
    profiles = doc.get("profiles")
    if not isinstance(active, str) or not active.strip() or not isinstance(profiles, dict):
        raise ValueError("active_profile or profiles table is missing")
    profile = profiles.get(active)
    if not isinstance(profile, dict):
        raise ValueError(f"active profile {active!r} is missing")

    if role in EXECUTION_ROLES:
        model_key, effort_key = "execution_model", "execution_reasoning"
    elif role in REVIEW_ROLES:
        model_key, effort_key = "review_model", "review_reasoning"
    else:
        raise ValueError(f"role {role!r} is not approved")

    model = profile.get(model_key)
    effort = profile.get(effort_key)
    if not isinstance(model, str) or "/" not in model or not model.strip():
        raise ValueError(f"{model_key} is invalid in active profile {active!r}")
    if not isinstance(effort, str) or effort.casefold() not in APPROVED_EFFORTS:
        raise ValueError(f"{effort_key} is invalid in active profile {active!r}")
    return model.strip(), effort.casefold()


def deny(reason: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "LOOP root spawn denied: " + reason,
    }}


def model_is_quarantined(model: str) -> bool:
    try:
        doc = json.loads(QUARANTINE_PATH.read_text(encoding="utf-8"))
        item = (doc.get("models") or {}).get(model) or {}
        return item.get("status") == "quarantined"
    except (OSError, ValueError, AttributeError):
        return False


def decision(payload: dict[str, Any], sessions: Path) -> dict[str, Any] | None:
    tool_name = str(payload.get("tool_name") or payload.get("tool") or payload.get("name") or "")
    if not is_spawn_tool(tool_name):
        return None
    if not is_spawn_agent_tool(tool_name):
        # create_thread / fork_thread skip model pinning.
        return None
    session_id = payload.get("session_id") or payload.get("thread_id")
    if os.environ.get("LOOP_LEAF_AGENT", "").strip() == "1" or session_is_child(session_id, sessions):
        # The dedicated leaf gate owns this case and denies all descendants.
        return None
    arguments = payload.get("tool_input")
    if not isinstance(arguments, dict):
        return deny("tool_input is absent or malformed; ambiguous births fail closed")

    role = str(arguments.get("agent_type") or "").strip().casefold()
    if not role or role == "default":
        return deny("agent_type must be an explicit LOOP role; roleless/default is prohibited")
    if role not in APPROVED_ROLES:
        return deny(f"agent_type={role!r} is not an approved LOOP child role")

    try:
        approved_model, approved_effort = approved_route(role)
    except ValueError as exc:
        return deny(f"active model profile is unavailable or invalid: {exc}")

    model = str(arguments.get("model") or "").strip()
    if model_is_quarantined(model):
        return deny(f"model {model} is quarantined and cannot spawn")
    if model != approved_model:
        return deny(f"model must be explicitly pinned to {approved_model} for role {role}")

    effort = str(arguments.get("reasoning_effort") or "").strip().casefold()
    if effort != approved_effort:
        return deny(
            f"reasoning_effort must be explicitly pinned to {approved_effort} for role {role}"
        )

    if arguments.get("fork_context") is True:
        return deny("fork_context=true is prohibited; LOOP children use independent context")
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    sessions = Path(os.environ.get(
        "CODEX_SESSIONS_DIR",
        str(Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"),
    )).resolve()
    try:
        result = decision(payload if isinstance(payload, dict) else {}, sessions)
    except Exception as exc:  # backstop: an ambiguous gate must fail CLOSED
        sys.stderr.write("root_spawn_gate crashed (%r); denying spawn\n" % (exc,))
        result = {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "spawn gate internal error — ambiguous births fail closed "
                "per LOOP routing invariant. Retry the spawn; if it persists, "
                "inspect data/governor/ gate logs."),
        }}
    if result is not None:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
