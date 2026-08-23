#!/usr/bin/env python3
# ============================================================================
# sol_tool_gate.py — PreToolUse gate: mechanical enforcement of Sol discipline
# Purpose : AGENTS.md §2 says Sol rounds are only for planning/adjudication —
#           but prose cannot enforce itself (P1-1). This hook makes it
#           mechanical: when the LOOP state is NOT planning / adjudication /
#           release_finalize and the ROOT (Sol) session tries to run shell,
#           search, bulk file reads, tests, or statistics, the tool call is
#           DENIED with an instruction to dispatch the work as an L0/L1
#           packet instead. Explicit approved child roles are never gated (they
#           are the dispatch targets); spawn gates separately prevent Sol
#           children and recursive births. Gate errors fail OPEN with a note: an
#           unreadable ledger must never paralyze the session (observation
#           of the failure is the fail-visible part).
# Input   : PreToolUse hook payload (one JSON object on stdin; fields used:
#           tool_name, agent_type/agent_id if present, cwd), env LOOP_ROOT
#           (default: cwd), data/progress_ledger.json (current LOOP state:
#           explicit "loop_state" key wins, else derived from packet states).
# Output  : deny -> {"hookSpecificOutput":{"hookEventName":"PreToolUse",
#           "permissionDecision":"deny","permissionDecisionReason":...}} on
#           stdout, exit 0; allow -> no output, exit 0.
# Lines   : ~80 (excluding this header)
# ============================================================================
import json
import os
import sys
from pathlib import Path

# Sol may use any tool in these LOOP states (planning decomposition,
# adjudication evidence reads, release finalize checks).
ALLOWED_STATES = {"planning", "adjudication", "release_finalize"}

# Tool names (lowercased, prefix-matched) that constitute L0 data processing
# when issued from the root Sol session mid-execution.
GATED_TOOLS = ("shell", "shell_command", "bash", "local_shell", "exec_command",
               "functions.exec", "run_terminal", "terminal", "web_search",
               "search", "grep", "glob", "mcp__", "read_mcp_resource",
               "list_mcp", "read_many_files", "read_file", "list_files",
               "pytest", "test")

ADJUDICATION_STATES = {"SOL_ADJUDICATE", "DEAD_LETTER", "MERGE_CONFLICT",
                       "WAVE_DONE", "WAVE_DONE_READY", "SOL_WAKE"}
TERMINAL_STATES = {"MERGED", "DONE"}
CHILD_ROLES = {"worker", "verifier", "reviewer", "plan_expander",
               "duty_officer", "explorer"}


def loop_state(root):
    """Current LOOP state: explicit ledger key wins, else derived from packet
    states. No packets = planning; any adjudication-class packet = adjudication;
    all terminal = release_finalize; anything else = execution."""
    path = os.path.join(root, "data", "progress_ledger.json")
    led = json.load(open(path, encoding="utf-8"))
    explicit = led.get("loop_state")
    if isinstance(explicit, str) and explicit:
        return explicit
    states = [p.get("state") for p in led.get("packets", {}).values()]
    if not states:
        return "planning"
    if any(s in ADJUDICATION_STATES for s in states):
        return "adjudication"
    if all(s in TERMINAL_STATES for s in states):
        return "release_finalize"
    return "execution"


def loop_root(payload):
    explicit = os.environ.get("LOOP_ROOT")
    if explicit:
        return explicit
    start = Path(payload.get("cwd") or os.getcwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "data" / "progress_ledger.json").exists():
            return str(candidate)
    here = Path(__file__).resolve()
    installed = (here.parents[2] if here.parent.name == "hooks"
                 and here.parent.parent.name == ".codex" else here.parents[1])
    return str(installed)


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail-open: no payload, nothing to judge
    # Subagents are never gated — they are exactly where the work SHOULD run.
    tool = (payload.get("tool_name") or "").lower()
    if not tool.startswith(GATED_TOOLS):
        return 0
    role = str(payload.get("agent_type") or payload.get("role") or "").strip().casefold()
    if role in CHILD_ROLES:
        return 0
    if role and role not in ("sol", "root", "default"):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason":
                "PreToolUse payload.agent_type is unknown (%r); an unknown "
                "role cannot bypass the Sol policy" % role}}))
        return 0
    root = loop_root(payload)
    try:
        state = loop_state(root)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        sys.stderr.write("sol_tool_gate: ledger unreadable (%s) — failing open\n" % e)
        return 0
    if state in ALLOWED_STATES:
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason":
            "LOOP state is %s, this operation should be dispatched to an "
            "L0/L1 packet (AGENTS.md §2: Sol rounds are for planning/"
            "adjudication only — shell/search/bulk-read/test/statistics "
            "belong to the zero-token layer or a worker packet)" % state}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
