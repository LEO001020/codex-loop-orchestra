#!/usr/bin/env python3
"""Mode router for the v1/v2 Sol PreToolUse gates.

cold_start enforces v1; shadow enforces v1 and records the v2 counterfactual;
layered enforces v2.  A single routing.mode edit rolls back immediately.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
from orchestration_common import layered_authorization  # noqa: E402

GATED_PREFIXES = ("shell", "shell_command", "bash", "local_shell",
                  "exec_command", "functions.exec", "run_terminal", "terminal",
                  "web_search", "search", "grep", "glob", "mcp__",
                  "read_mcp_resource", "list_mcp", "read_many_files",
                  "read_file", "list_files", "pytest", "test")


def find_root(payload: dict) -> Path | None:
    explicit = os.environ.get("LOOP_ROOT")
    if explicit:
        candidate = Path(explicit).resolve()
        if (candidate / "config" / "orchestration_policy_v2.toml").exists():
            return candidate
    start = Path(payload.get("cwd") or os.getcwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "config" / "orchestration_policy_v2.toml").exists():
            return candidate
    # The saved Desktop project is the package parent (E:/codex-LOOP), while
    # the policy-bearing implementation is its canonical codex-loop-s-f2
    # child.  Recognize only this exact package relation; unrelated cwd values
    # still pass through.
    package = Path(__file__).resolve().parents[1]
    if start == package.parent and (
            package / "config" / "orchestration_policy_v2.toml").exists():
        return package
    # A user-level installation also receives hooks for unrelated projects.
    # No policy ancestor means this is not a LOOP operation: pass through
    # instead of silently applying the package root's policy to another cwd.
    return None


def read_mode(root: Path) -> str:
    path = root / "config" / "orchestration_policy_v2.toml"
    with path.open("rb") as handle:
        doc = tomllib.load(handle)
    mode = str(doc.get("routing", {}).get("mode", "cold_start"))
    if mode not in ("cold_start", "shadow", "layered"):
        raise ValueError("invalid routing.mode %r" % mode)
    return mode


def invoke(script: Path, raw: str) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["LOOP_ROOT"] = str(script.resolve().parents[1])
    proc = subprocess.run([sys.executable, str(script)], input=raw,
                          text=True, capture_output=True, timeout=8, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def append_shadow(root: Path, payload: dict, v1: tuple[int, str, str],
                  v2: tuple[int, str, str]) -> None:
    path = root / "data" / "governor" / "hook_shadow.ndjsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(), "event": "hook_shadow_decision",
        "tool": payload.get("tool_name"),
        "payload_hash": hashlib.sha256(json.dumps(
            payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24],
        "v1_rc": v1[0], "v1_deny": bool(v1[1].strip()),
        "v2_rc": v2[0], "v2_deny": bool(v2[1].strip()),
        "agree": (v1[0] == v2[0] and bool(v1[1].strip()) ==
                  bool(v2[1].strip())),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def deny(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": reason}})


def layered_authorized(root: Path) -> bool:
    return layered_authorization(root)[0]


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    if not payload:
        return 0
    root = find_root(payload)
    if root is None:
        return 0
    try:
        mode = read_mode(root)
    except Exception as exc:
        # The installed cold-start gate is the rollback authority.  A broken
        # v2 policy must not silently select layered behavior.
        sys.stderr.write("sol_tool_gate_router: policy unreadable (%s); cold_start\n" % exc)
        mode = "cold_start"
    v1_script = root / "hooks" / "sol_tool_gate.py"
    # RootTurnGovernor is the single v2 implementation.  The old
    # sol_tool_gate_v2.py remains historical/rollback evidence only.
    v2_script = root / "harness" / "root_turn_governor.py"
    try:
        if mode == "cold_start":
            rc, out, err = invoke(v1_script, raw)
            if err:
                sys.stderr.write(err)
            if rc != 0:
                sys.stderr.write("sol_tool_gate_router: v1 gate rc=%d, fail-open rollback\n" % rc)
                return 0
            if out:
                sys.stdout.write(out)
            return 0
        if mode == "shadow":
            v1 = invoke(v1_script, raw)
            v2 = invoke(v2_script, raw)
            append_shadow(root, payload, v1, v2)
            if v1[2]:
                sys.stderr.write(v1[2])
            if v1[0] == 0 and v1[1]:
                sys.stdout.write(v1[1])
            return 0
        tool = str(payload.get("tool_name") or "").lower()
        if tool.startswith(GATED_PREFIXES) and not layered_authorized(root):
            print(deny("layered mode lacks a fresh full gate authorization; "
                       "run layered_gate.py enable or set routing.mode=cold_start"))
            return 0
        rc, out, err = invoke(v2_script, raw)
        if err:
            sys.stderr.write(err)
        if rc != 0:
            print(deny("v2 Sol gate failed rc=%d — layered mode fails closed; "
                       "set routing.mode=cold_start to roll back" % rc))
        elif out:
            sys.stdout.write(out)
        return 0
    except (OSError, subprocess.SubprocessError) as exc:
        if mode == "layered":
            print(deny("v2 Sol gate unavailable (%s) — layered mode fails closed; "
                       "set routing.mode=cold_start to roll back" % exc))
        else:
            sys.stderr.write("sol_tool_gate_router: %s (rollback mode fail-open)\n" % exc)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
