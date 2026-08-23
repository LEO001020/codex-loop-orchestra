# ============================================================================
# test_sol_tool_gate.py — Unit tests for hooks/sol_tool_gate.py (P1-1)
# Cases: mid-execution Sol shell/search calls are DENIED with the packet-
#        dispatch reason; planning (empty ledger) / adjudication (dead-letter
#        packet) / release_finalize (all merged) states allow tools; subagent
#        calls are never gated; non-gated tools pass; explicit loop_state key
#        wins; unreadable ledger fails OPEN with a stderr note (never
#        paralyzes the session).
# ============================================================================
import json

from tests.conftest import PKG, PY

GATE = PKG / "hooks" / "sol_tool_gate.py"
SOL = "gpt-5.6"
V4 = "gpt-5.6-terra"
K3 = "gpt-5.6"


def run_gate(loop, payload, ledger=None):
    if ledger is not None:
        loop.set_ledger(ledger)
    import subprocess
    return subprocess.run([PY, str(GATE)], input=json.dumps(payload),
                          capture_output=True, text=True,
                          env=loop.env())


def deny_reason(p):
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    return hso["permissionDecisionReason"]


# ---- deny: Sol doing L0 work mid-execution -------------------------------------

def test_sol_shell_denied_during_execution(loop):
    led = {"packets": {"p1": {"state": "RUNNING"}}, "waves": []}
    p = run_gate(loop, {"tool_name": "shell", "model": SOL,
                        "cwd": str(loop.root)}, led)
    assert p.returncode == 0
    reason = deny_reason(p)
    assert "LOOP state is execution" in reason
    assert "L0/L1 packet" in reason


def test_sol_search_denied_during_execution(loop):
    led = {"packets": {"p1": {"state": "DISPATCHABLE"}}, "waves": []}
    p = run_gate(loop, {"tool_name": "web_search", "model": SOL}, led)
    assert deny_reason(p)


# ---- allow: the three sanctioned states ----------------------------------------

def test_planning_state_allows_tools(loop):
    p = run_gate(loop, {"tool_name": "shell", "model": SOL},
                 {"packets": {}, "waves": []})
    assert p.returncode == 0 and p.stdout.strip() == ""


def test_adjudication_state_allows_tools(loop):
    led = {"packets": {"p1": {"state": "DEAD_LETTER"}}, "waves": []}
    p = run_gate(loop, {"tool_name": "shell", "model": SOL}, led)
    assert p.returncode == 0 and p.stdout.strip() == ""


def test_release_finalize_state_allows_tools(loop):
    led = {"packets": {"p1": {"state": "MERGED"}, "p2": {"state": "DONE"}},
           "waves": []}
    p = run_gate(loop, {"tool_name": "shell", "model": SOL}, led)
    assert p.returncode == 0 and p.stdout.strip() == ""


def test_explicit_loop_state_key_wins(loop):
    led = {"loop_state": "adjudication",
           "packets": {"p1": {"state": "RUNNING"}}, "waves": []}
    p = run_gate(loop, {"tool_name": "shell", "model": SOL}, led)
    assert p.returncode == 0 and p.stdout.strip() == ""


# ---- never gated: subagents and non-L0 tools -----------------------------------

def test_explicit_approved_child_role_is_not_gated(loop):
    led = {"packets": {"p1": {"state": "RUNNING"}}, "waves": []}
    p = run_gate(loop, {"tool_name": "shell", "model": SOL,
                        "agent_type": "reviewer", "agent_id": "a-1"}, led)
    assert p.returncode == 0 and p.stdout.strip() == ""


def test_v4_and_k3_are_not_gated(loop):
    led = {"packets": {"p1": {"state": "RUNNING"}}, "waves": []}
    for model, role in ((V4, "worker"), (K3, "verifier")):
        p = run_gate(loop, {"tool_name": "shell", "model": model,
                            "agent_id": "a-1", "agent_type": role}, led)
        assert p.returncode == 0 and p.stdout.strip() == ""


def test_missing_or_unknown_model_cannot_bypass_with_agent_id(loop):
    led = {"packets": {"p1": {"state": "RUNNING"}}, "waves": []}
    for model in (None, "unknown/model"):
        payload = {"tool_name": "shell", "agent_id": "a-1"}
        if model is not None:
            payload["model"] = model
        p = run_gate(loop, payload, led)
        assert "LOOP state is execution" in deny_reason(p)


def test_model_normalization_is_case_and_space_insensitive(loop):
    led = {"packets": {"p1": {"state": "RUNNING"}}, "waves": []}
    p = run_gate(loop, {"tool_name": "shell", "agent_type": "worker",
                        "model": "  GPT-5.6-TERRA  "}, led)
    assert p.returncode == 0 and p.stdout.strip() == ""


def test_non_gated_tool_passes(loop):
    led = {"packets": {"p1": {"state": "RUNNING"}}, "waves": []}
    p = run_gate(loop, {"tool_name": "spawn_agent"}, led)
    assert p.returncode == 0 and p.stdout.strip() == ""


# ---- fail-open: unreadable ledger never paralyzes the session ------------------

def test_unreadable_ledger_fails_open_with_note(loop):
    (loop.data / "progress_ledger.json").write_text("{corrupt")
    p = run_gate(loop, {"tool_name": "shell", "model": SOL})
    assert p.returncode == 0 and p.stdout.strip() == ""
    assert "failing open" in p.stderr
