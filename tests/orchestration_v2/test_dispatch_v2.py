"""test_dispatch_v2.py — mode/budget/governance-aware dispatch.

Covers: sol_budget_block_v2 actually binding, K3-first dispatch in layered
mode, the Sol fallback when K3 is unavailable, budget-aware routing, and the
ipybox matrix (Desktop off, WSL on, K3 off unless the packet needs code).
"""
from __future__ import annotations

import json
import os
import time

import pytest

from tests.orchestration_v2.conftest import (
    green_guards,
    make_root,
    read_events,
    set_routing_mode,
    write_meter_report,
    write_packet,
)

from agent_router import Route, RouteReason
from budget_controller import BudgetController
from dispatch_v2 import (
    DispatchBlocked,
    DispatcherV2,
    ExecutionPlane,
    detect_plane,
    ipybox_enabled_for,
    resolve_role_pin,
    sol_budget_block_v2,
)
from orchestration_common import LoopPaths, ModelPinError, OrchestrationPolicy
from root_turn_governor import RootTurnGovernor


@pytest.fixture
def env(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    policy = OrchestrationPolicy.load(paths)
    return root, paths, policy


def _running_ledger(paths):
    paths.data.mkdir(parents=True, exist_ok=True)
    paths.ledger.write_text(json.dumps({"packets": {
        "px": {"state": "RUNNING", "history": [], "attempts": 1}}}))


# ---------------------------------------------------------------------------
# sol_budget_block_v2 — the gate that finally binds
# ---------------------------------------------------------------------------
def test_block_binds_on_missing_meter_for_sol_route(env):
    """v1's gate compared against a null model set and never fired. v2 binds
    on the route target: Sol-bound work with a blind meter is refused."""
    root, paths, policy = env
    _running_ledger(paths)  # execution state: not exempt
    gov = RootTurnGovernor(paths, policy)
    budget = BudgetController(paths, policy)
    pin = resolve_role_pin("sol", paths, policy)
    block = sol_budget_block_v2(paths, policy, pin, Route.SOL_ADJUDICATE,
                                gov, budget)
    assert block is not None
    assert block["meter_status"] == "MISSING", "sensor blind => fail closed"


def test_block_binds_on_high_hysteresis_band(env):
    root, paths, policy = env
    _running_ledger(paths)
    write_meter_report(root, 0.30)
    (paths.governor_dir).mkdir(parents=True, exist_ok=True)
    (paths.governor_dir / "hysteresis.json").write_text(
        json.dumps({"band": "HIGH"}))
    gov = RootTurnGovernor(paths, policy)
    budget = BudgetController(paths, policy)
    pin = resolve_role_pin("sol", paths, policy)
    block = sol_budget_block_v2(paths, policy, pin, Route.SOL_ADJUDICATE,
                                gov, budget)
    assert block is not None and block["band"] == "HIGH"
    assert "hard cap" in block["reason"]


def test_block_binds_on_budget_degrade(env):
    root, paths, policy = env
    _running_ledger(paths)
    write_meter_report(root, 0.05)
    (paths.governor_dir).mkdir(parents=True, exist_ok=True)
    (paths.governor_dir / "hysteresis.json").write_text(
        json.dumps({"band": "NORMAL"}))
    (paths.budget_dir).mkdir(parents=True, exist_ok=True)
    (paths.budget_dir / "active.json").write_text(json.dumps(
        {"state": "DEGRADE", "ceiling": 1, "allocated": {}, "consumed": {}}))
    gov = RootTurnGovernor(paths, policy)
    budget = BudgetController(paths, policy)
    pin = resolve_role_pin("sol", paths, policy)
    block = sol_budget_block_v2(paths, policy, pin, Route.SOL_ADJUDICATE,
                                gov, budget)
    assert block is not None and block["budget_state"] == "DEGRADE"


def test_planning_exemption_is_bounded_by_lease(env):
    root, paths, policy = env
    gov = RootTurnGovernor(paths, policy)
    budget = BudgetController(paths, policy)
    pin = resolve_role_pin("sol", paths, policy)
    # fresh root => planning, lease unexhausted => exempt
    assert sol_budget_block_v2(paths, policy, pin, Route.SOL_ADJUDICATE,
                               gov, budget) is None
    # lease exhausted => the planning exemption ends (P0-5.2)
    (paths.governor_dir).mkdir(parents=True, exist_ok=True)
    (paths.governor_dir / "planning_lease.json").write_text(json.dumps(
        {"granted_ts": time.time(), "turns_used": 99, "new_tokens_used": 0}))
    block = sol_budget_block_v2(paths, policy, pin, Route.SOL_ADJUDICATE,
                                gov, budget)
    assert block is not None, "an empty ledger is no longer a permanent pass"


def test_v4_k3_dispatch_never_tier1_blocked(env):
    """Worker/verifier dispatch REDUCES Sol share — only BREAK stops it."""
    root, paths, policy = env
    _running_ledger(paths)  # blind meter, non-exempt: Sol would be blocked
    gov = RootTurnGovernor(paths, policy)
    budget = BudgetController(paths, policy)
    pin = resolve_role_pin("worker", paths, policy)
    assert sol_budget_block_v2(paths, policy, pin, Route.V4_DIRECT,
                               gov, budget) is None


def test_budget_break_blocks_all_dispatch(env):
    root, paths, policy = env
    (paths.budget_dir).mkdir(parents=True, exist_ok=True)
    (paths.budget_dir / "active.json").write_text(json.dumps(
        {"state": "BREAK", "ceiling": 1, "allocated": {}, "consumed": {}}))
    gov = RootTurnGovernor(paths, policy)
    budget = BudgetController(paths, policy)
    pin = resolve_role_pin("worker", paths, policy)
    block = sol_budget_block_v2(paths, policy, pin, Route.V4_DIRECT,
                                gov, budget)
    assert block is not None and block["budget_state"] == "BREAK"


# ---------------------------------------------------------------------------
# K3-first dispatch in layered mode
# ---------------------------------------------------------------------------
def test_layered_k3_first_dispatch(env):
    root, paths, policy = env
    set_routing_mode(root, "layered")
    green_guards(root)
    write_packet(root, "p1", **{"class": "verification"})
    dispatcher = DispatcherV2(paths)
    role, route, reason = dispatcher.decide("p1", action="pass")
    assert role == "verifier", "K3-suited class dispatches K3-first"
    assert route is Route.K3_VERIFY
    assert reason is RouteReason.K3_SUITED_CLASS


def test_cold_start_decide_never_spawns_k3(env):
    root, paths, policy = env
    write_packet(root, "p1", **{"class": "verification"})
    dispatcher = DispatcherV2(paths)
    _, route, _ = dispatcher.decide("p1", action="pass")
    assert route is Route.SOL_ADJUDICATE, "cold_start stays byte-identical"


def test_k3_unavailable_never_falls_back_to_sol(env, monkeypatch):
    root, paths, policy = env
    set_routing_mode(root, "layered")
    green_guards(root)
    write_packet(root, "p1", **{"class": "verification"})
    dispatcher = DispatcherV2(paths)
    monkeypatch.setattr(dispatcher, "k3_available", lambda: False)
    with pytest.raises(DispatchBlocked) as caught:
        dispatcher.decide("p1", action="pass")
    assert caught.value.detail == {
        "why": "k3_unavailable", "role": "verifier",
        "route": "k3_verify", "retryable": True}
    ledger = (root / "data" / "router" / "route_ledger.ndjsonl").read_text(
        encoding="utf-8")
    assert "k3_unavailable_debt_retained" in ledger
    assert "k3_unavailable_sol_fallback" not in ledger


def test_k3_unavailable_never_degrades_to_v4(env, monkeypatch):
    root, paths, policy = env
    set_routing_mode(root, "layered")
    green_guards(root)
    # The debt remains K3-owned regardless of the current Sol budget state.
    paths.ledger.write_text(json.dumps({
        "schema": "codex-loop-statemachine/v2",
        "packets": {"px": {"state": "RUNNING", "history": [], "attempts": 1}}}))
    write_packet(root, "p1", **{"class": "verification"})
    dispatcher = DispatcherV2(paths)
    monkeypatch.setattr(dispatcher, "k3_available", lambda: False)
    with pytest.raises(DispatchBlocked) as caught:
        dispatcher.decide("p1", action="pass")
    assert caught.value.detail["why"] == "k3_unavailable"
    assert caught.value.detail["retryable"] is True
    ledger = (root / "data" / "router" / "route_ledger.ndjsonl").read_text(
        encoding="utf-8")
    assert "k3_unavailable_debt_retained" in ledger
    assert "k3_unavailable_v4_degrade" not in ledger


def test_explicit_k3_refill_role_is_blocked_at_birth_and_keeps_packet(env,
                                                                      monkeypatch):
    root, paths, _ = env
    write_packet(root, "p1", **{"class": "verification"})
    paths.ledger.write_text(json.dumps({
        "schema": "codex-loop-statemachine/v2",
        "packets": {"p1": {"state": "DISPATCHABLE", "role": "verifier"}}}),
        encoding="utf-8")
    dispatcher = DispatcherV2(paths)
    monkeypatch.setattr(dispatcher, "k3_available", lambda: False)
    assert dispatcher.dispatch(["p1"], role="verifier", dry_run=False) == 3
    assert json.loads(paths.ledger.read_text(encoding="utf-8"))["packets"]["p1"][
        "state"] == "DISPATCHABLE"
    route_ledger = (paths.router_dir / "route_ledger.ndjsonl").read_text(
        encoding="utf-8")
    assert "k3_unavailable_debt_retained" in route_ledger
    assert "k3_unavailable_sol_fallback" not in route_ledger
    assert "k3_unavailable_v4_degrade" not in route_ledger


def test_explicit_k3_dry_run_remains_observable_during_backoff(env, monkeypatch):
    root, paths, _ = env
    write_packet(root, "p1", **{"class": "verification"})
    dispatcher = DispatcherV2(paths)
    monkeypatch.setattr(dispatcher, "k3_available", lambda: False)
    assert dispatcher.dispatch(["p1"], role="verifier", dry_run=True) == 0
    assert any(event["event"] == "dispatch_dry_run" for event in read_events(root))


def test_dispatch_refused_is_event_not_crash(env):
    root, paths, _ = env
    dispatcher = DispatcherV2(paths)
    rc = dispatcher.dispatch(["missing-packet"], dry_run=True)
    assert rc == 3
    assert any(e["event"] == "dispatch_refused" for e in read_events(root))


def test_dry_run_spawn_records_route_metadata(env):
    root, paths, _ = env
    set_routing_mode(root, "layered")
    green_guards(root)
    write_packet(root, "p1", **{"class": "verification"})
    dispatcher = DispatcherV2(paths)
    rc = dispatcher.dispatch(["p1"], dry_run=True, action="pass")
    assert rc == 0
    dry = [e for e in read_events(root) if e["event"] == "dispatch_dry_run"]
    assert dry and dry[0]["detail"]["role"] == "verifier"
    assert dry[0]["detail"]["model"] == dispatcher.policy.model_pin("k3"), \
        "the spawned model is the config pin, never a hardcoded string"
    assert dry[0]["detail"]["ipybox_enabled"] is False, \
        "K3 verification runs without ipybox"


# ---------------------------------------------------------------------------
# role pins
# ---------------------------------------------------------------------------
def test_role_pin_resolves_from_policy(env):
    _, paths, policy = env
    for role, family in (("worker", "v4"), ("verifier", "k3"), ("sol", "sol")):
        pin = resolve_role_pin(role, paths, policy)
        assert pin.model == policy.model_pin(family)
        assert pin.effort, "reasoning effort must resolve"


def test_role_pin_refuses_divergent_agent_toml(env):
    root, paths, policy = env
    agents = root / "agents"
    agents.mkdir()
    (agents / "worker.toml").write_text('model = "rogue/model"\n'
                                        'model_reasoning_effort = "high"\n')
    with pytest.raises(ModelPinError, match="divergent"):
        resolve_role_pin("worker", paths, policy)


def test_unknown_role_fails_visible(env):
    _, paths, policy = env
    with pytest.raises(ModelPinError, match="unknown role"):
        resolve_role_pin("mystery_role", paths, policy)


def test_cli_overrides_carry_pins(env):
    """exec top-level processes do not read agents/*.toml — pins must ride
    the command line."""
    _, paths, policy = env
    pin = resolve_role_pin("verifier", paths, policy)
    overrides = pin.cli_overrides()
    assert overrides[:2] == ["-m", policy.model_pin("k3")]
    assert any("model_reasoning_effort" in o for o in overrides)


# ---------------------------------------------------------------------------
# ipybox matrix (task constraint: Desktop ipybox stays OFF)
# ---------------------------------------------------------------------------
def test_ipybox_desktop_native_disabled(env):
    _, _, policy = env
    for role in ("worker", "verifier", "reviewer", "plan_expander"):
        assert ipybox_enabled_for(role, ExecutionPlane.DESKTOP_NATIVE,
                                  policy) is False


def test_ipybox_wsl_worker_enabled(env):
    _, _, policy = env
    assert ipybox_enabled_for("worker", ExecutionPlane.WSL_HEADLESS,
                              policy) is True


def test_ipybox_k3_roles_disabled_by_default(env):
    _, _, policy = env
    for role in ("verifier", "reviewer", "plan_expander"):
        assert ipybox_enabled_for(role, ExecutionPlane.WSL_HEADLESS,
                                  policy) is False


def test_ipybox_k3_code_execution_exception(env):
    _, _, policy = env
    packet = {"needs_code_execution": True}
    assert ipybox_enabled_for("verifier", ExecutionPlane.WSL_HEADLESS,
                              policy, packet) is True
    assert ipybox_enabled_for("verifier", ExecutionPlane.DESKTOP_NATIVE,
                              policy, packet) is False, \
        "the exception never re-enables Desktop ipybox"


def test_ipybox_policy_file_pins_desktop_off():
    import tomllib
    from tests.orchestration_v2.conftest import IMPL
    with open(IMPL / "config" / "orchestration_policy_v2.toml", "rb") as fh:
        doc = tomllib.load(fh)
    assert doc["ipybox"]["desktop_native_enabled"] is False
    assert doc["ipybox"]["wsl_headless_worker_enabled"] is True
    assert doc["ipybox"]["k3_planning_verifying_enabled"] is False


def test_detect_plane_env_override(monkeypatch):
    monkeypatch.setenv("LOOP_EXECUTION_PLANE", "desktop_native")
    assert detect_plane() is ExecutionPlane.DESKTOP_NATIVE


def test_build_exec_command_ipybox_flags(env):
    root, paths, _ = env
    write_packet(root, "p1")
    dispatcher = DispatcherV2(paths)
    packet = json.loads((root / "data" / "packets" / "p1.json").read_text())
    cmd, _ = dispatcher.build_exec_command("p1", "verifier", "prompt", packet)
    assert "mcp_servers.ipybox.enabled=false" in cmd
    assert ("mcp_servers.node_repl.enabled=false" in cmd) == (os.name == "nt")
    if dispatcher.plane is ExecutionPlane.WSL_HEADLESS:
        cmd, _ = dispatcher.build_exec_command("p1", "worker", "prompt", packet)
        assert "mcp_servers.ipybox.enabled=true" in cmd


def test_packet_id_validation_blocks_traversal(env):
    _, paths, _ = env
    dispatcher = DispatcherV2(paths)
    with pytest.raises(DispatchBlocked):
        dispatcher.load_packet("../../etc/passwd")


def test_v2_prompt_preserves_task_name_worktree_and_retry_handle(env):
    root, paths, _ = env
    write_packet(root, "p1")
    dispatcher = DispatcherV2(paths)
    packet = dispatcher.load_packet("p1")
    prompt = dispatcher._spawn_prompt(
        packet, Route.V4_DIRECT, "X:/isolated-worktree",
        task_label="执行数据包 p1 — test goal",
        previous_attempt="previous_attempt: data/reports/p1/previous/attempt-0.json\n")
    assert prompt.startswith("任务名：执行数据包 p1")
    assert "Work ONLY inside X:/isolated-worktree" in prompt
    assert "previous_attempt: data/reports/p1/previous/attempt-0.json" in prompt


def test_parent_worker_is_forced_readonly_and_propagates_parent(env, monkeypatch):
    root, paths, _ = env
    write_packet(root, "parent-p", task_name="父任务只读审计",
                 parent_enabled=True, sandbox="read-only", cwd=str(root),
                 parent_session_id="parent-session")
    paths.ledger.write_text(json.dumps({"packets": {
        "parent-p": {"state": "DISPATCHABLE", "role": "worker"}
    }}), encoding="utf-8")
    seen = {}
    import dispatch as dispatch_v1
    monkeypatch.setattr(dispatch_v1, "dispatch_single",
                        lambda pids, dry_run, **kwargs: seen.update(kwargs) or "run-1")
    dispatcher = DispatcherV2(paths)
    monkeypatch.setattr(dispatcher.budget, "register_agent", lambda *args: None)
    monkeypatch.setattr(dispatcher, "record_run_role", lambda *args: None)
    assert dispatcher.dispatch(["parent-p"], role="worker") == 0
    assert seen["pinned"][1] == "read-only"
    assert seen["capture_report"] is True
    assert seen["parent_session_id_overrides"] == {"parent-p": "parent-session"}
    assert seen["readonly_cwd_overrides"] == {"parent-p": str(root)}


def test_parent_prompt_never_requests_report_file_write(env):
    root, paths, _ = env
    write_packet(root, "parent-p", parent_enabled=True, sandbox="read-only",
                 cwd=str(root), parent_session_id="parent-session")
    dispatcher = DispatcherV2(paths)
    prompt = dispatcher._spawn_prompt(
        dispatcher.load_packet("parent-p"), Route.V4_DIRECT,
        worktree=str(root), role="worker")
    assert "read-only parent packet" in prompt
    assert "do not write a report file" in prompt
    assert "On completion write" not in prompt


def test_reviewer_prompt_is_not_mislabeled_verifier(env):
    root, paths, _ = env
    write_packet(root, "p1")
    dispatcher = DispatcherV2(paths)
    prompt = dispatcher._spawn_prompt(dispatcher.load_packet("p1"),
                                      Route.K3_VERIFY, role="reviewer")
    assert "release-gate Reviewer" in prompt
    assert "L2 Verifier" not in prompt
