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
import time
from pathlib import Path

# Planning-stage parallel-recon enforcement: root may serially read this many
# times before the gate starts denying with a spawn order (throughput-first
# delegation, AGENTS.md).  A successful spawn resets the counter; after
# RECON_HARD_CAP consecutive denies the gate fails open (never paralyze root).
RECON_THRESHOLD = 5
RECON_HARD_CAP = 8
RECON_WINDOW = 900

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


def recon_enforce(root, tool):
    """PLANNING parallel-recon enforcement.  Serial root reads past
    RECON_THRESHOLD are denied with an explicit spawn order; a spawn resets
    the counter; RECON_HARD_CAP consecutive denies fail open."""
    if "spawn" in tool or "subagent" in tool or "dispatch" in tool:
        f = _recon_file(root)
        if f.exists():
            f.unlink()
        return ""
    now = time.time()
    f = _recon_file(root)
    try:
        st = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    if now - float(st.get("window", 0)) > RECON_WINDOW:
        st = {}
    count = int(st.get("count", 0)) + 1
    st.update({"count": count, "window": st.get("window") or now, "last_tool": tool})
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(st), encoding="utf-8")
    if count > RECON_HARD_CAP:
        return ""
    if count >= RECON_THRESHOLD:
        pin_model, pin_effort = _pinned_worker_route(root)
        if pin_model:
            pin_line = ('"model": "%s", "reasoning_effort": "%s", '
                        % (pin_model, pin_effort))
            pin_note = ("model 字段必须逐字带上（来自当前激活 profile），"
                        "禁止省略——省略会被路由到错误的模型：")
        else:
            pin_line = '"model": "<读取 config/model_profiles.toml active_profile 的 execution_model>", '
            pin_note = ("model 字段必须来自当前激活 profile（本机无法读取时按占位符"
                        "提示查询），禁止省略——省略会被路由到错误的模型：")
        tpl = (
            "PLANNING 强制委派：root 已连续串行读取 %d 次（本窗口）。"
            "spawn 工具是延迟加载的：若本会话工具列表里没有 multi_agent_v1.spawn_agent，"
            "必须先调用 tool_search 工具（参数 {\"query\": \"spawn agent\"}）激活它，"
            "它会在你的下一轮出现，然后再 spawn。"
            "照抄此模板，一轮内并列发出 ≥4 个调用（每个 message 不同分片，只读侦查，禁止写）。"
            + pin_note +
            '{"agent_type": "worker", "fork_context": false, '
            + pin_line +
            '"message": "任务名：侦查harness目录结构\\n只读侦查 <具体路径/检索目标>，'
            '输出<你要的结论格式>。禁止修改任何文件。"}  '
            "4 个并行分片建议：目录结构与文件清单 / 核心模块符号表(rg) / 测试面与验收命令 / "
            "git 状态与历史 evidence。spawn 成功后本 gate 自动放行；"
            "继续发串行读取会被继续拒绝。" % count)
        return tpl
    return ""


def _pinned_worker_route(root):
    """Execution route from the ACTIVE profile (config/model_profiles.toml) —
    the same source root_agent_spawn_gate.approved_route enforces.  The deny
    template must never hardcode a model string: on the next profile rotation
    a baked-in pin would instruct a model the spawn gate then DENIES."""
    try:
        import tomllib
        doc = tomllib.loads((Path(root) / "config" / "model_profiles.toml")
                            .read_text(encoding="utf-8"))
        profile = (doc.get("profiles") or {}).get(doc.get("active_profile")) or {}
        model = profile.get("execution_model")
        effort = profile.get("execution_reasoning")
        if (isinstance(model, str) and "/" in model
                and isinstance(effort, str) and effort.strip()):
            return model.strip(), effort.strip()
    except Exception:
        pass
    return None, None


def _recon_file(root):
    return Path(root) / "data" / "governor" / "recon_gate.json"


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail-open: no payload, nothing to judge
    # Subagents are never gated — they are exactly where the work SHOULD run.
    tool = (payload.get("tool_name") or "").lower()
    if "spawn" in tool or "subagent" in tool or "dispatch" in tool:
        # a spawn IS the forced delegation: reset the recon counter
        try:
            rf = _recon_file(loop_root(payload))
            if rf.exists():
                rf.unlink()
        except OSError:
            pass
        return 0
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
        if state == "planning":
            reason = recon_enforce(root, tool)
            if reason:
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason}}))
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
