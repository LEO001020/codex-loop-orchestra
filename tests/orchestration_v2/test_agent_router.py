"""test_agent_router.py — three-mode routing switch + gate guards + pins.

Covers: cold_start default safety, shadow parallel logging, layered K3-first
routing, all six layered gate guards, mechanical auto-downgrade to shadow on
guard failure, and model-pin enforcement (no hardcoded model ids).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests.orchestration_v2.conftest import (IMPL, V2_SOURCE_FILES, green_guards, make_root,
                      set_routing_mode)

import agent_router as ar_mod
from agent_router import (
    AgentRouter,
    ConfidenceCalibrator,
    GateGuard,
    Route,
    RouteReason,
    RoutingMode,
    check_layered_gate_guards,
)
from orchestration_common import LoopPaths, ModelPinError, OrchestrationPolicy


def _router(root: Path) -> AgentRouter:
    paths = LoopPaths.resolve(root)
    return AgentRouter(paths, OrchestrationPolicy.load(paths))


def _calibrate_low_escalation(router: AgentRouter, signals: dict,
                              rules_hit: list[str]) -> None:
    """Feed enough non-escalation outcomes that p_escalate drops below 0.5."""
    fp = ConfidenceCalibrator.fingerprint(signals, rules_hit)
    for _ in range(20):
        router.calibrator.record_outcome(fp, escalated=False)


# ---------------------------------------------------------------------------
# cold_start (default safe)
# ---------------------------------------------------------------------------
def test_default_mode_is_cold_start(tmp_path):
    root = make_root(tmp_path)
    router = _router(root)
    mode, report = router.effective_mode()
    assert mode is RoutingMode.COLD_START and report is None


def test_missing_mode_key_defaults_cold_start(tmp_path):
    root = make_root(tmp_path)
    policy_path = root / "config" / "orchestration_policy_v2.toml"
    text = policy_path.read_text(encoding="utf-8")
    policy_path.write_text(text.replace('mode = "cold_start"', "", 1),
                           encoding="utf-8")
    assert _router(root).policy.routing_mode() == "cold_start"


def test_cold_start_upgrades_everything_to_sol(tmp_path):
    root = make_root(tmp_path)
    router = _router(root)
    for action in ("pass", "annotated_pass", "send_l2", "spawn_duty_officer"):
        decision = router.route_action("p1", action)
        assert decision.effective_action == "direct_l3"
        assert decision.route is Route.SOL_ADJUDICATE
    ledger = (root / "data" / "router" / "route_ledger.ndjsonl").read_text()
    assert ledger.count("\n") == 4, "every decision is ledgered"


def test_cold_start_direct_l4_preserved(tmp_path):
    root = make_root(tmp_path)
    decision = _router(root).route_action("p1", "direct_l4")
    assert decision.route is Route.L4_HUMAN
    assert decision.effective_action == "direct_l4"


# ---------------------------------------------------------------------------
# shadow (parallel: executes as cold_start, logs the layered counterfactual)
# ---------------------------------------------------------------------------
def test_shadow_executes_cold_start_but_logs_layered(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "shadow")
    router = _router(root)
    decision = router.route_action("p1", "send_l2")
    assert decision.mode is RoutingMode.SHADOW
    assert decision.effective_action == "direct_l3", "execution stays legacy"
    shadow = root / "data" / "router" / "shadow_log.ndjsonl"
    rec = json.loads(shadow.read_text().splitlines()[0])
    assert rec["shadow_route"] == Route.K3_VERIFY.value
    assert rec["shadow_action"] == "send_l2", \
        "the as-if-layered decision accumulates as calibration corpus"


# ---------------------------------------------------------------------------
# layered (K3-first)
# ---------------------------------------------------------------------------
def test_layered_send_l2_routes_to_k3(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    green_guards(root)
    decision = _router(root).route_action("p1", "send_l2")
    assert decision.mode is RoutingMode.LAYERED
    assert decision.route is Route.K3_VERIFY
    assert decision.effective_action == "send_l2", "no default direct_l3 upgrade"


def test_layered_k3_suited_class_goes_k3_first(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    green_guards(root)
    decision = _router(root).route_action(
        "p1", "pass", packet_meta={"class": "verification"})
    assert decision.route is Route.K3_VERIFY
    assert decision.reason is RouteReason.K3_SUITED_CLASS


def test_layered_calibrated_pass_reaches_merge_queue(tmp_path):
    """With calibrated low escalation probability, a table pass merges
    mechanically — zero Sol involvement."""
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    green_guards(root)
    router = _router(root)
    signals = {"exit_codes": [0], "retry_count": 0}
    _calibrate_low_escalation(router, signals, ["exit_clean_single_run"])
    decision = router.route_action("p1", "pass", signals=signals,
                                   rules_hit=["exit_clean_single_run"])
    assert decision.route is Route.MERGE_QUEUE
    assert decision.confidence is not None and decision.confidence < 0.5


def test_layered_uncalibrated_pass_verifies_conservatively(tmp_path):
    """No shadow corpus => the conservative prior routes pass to K3 verify."""
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    green_guards(root)
    decision = _router(root).route_action("p1", "pass")
    assert decision.route is Route.K3_VERIFY
    assert decision.reason is RouteReason.SAMPLED_VERIFICATION


def test_layered_high_risk_always_sol(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    green_guards(root)
    decision = _router(root).route_action("p1", "pass", high_risk=True)
    assert decision.route is Route.SOL_ADJUDICATE
    assert decision.reason is RouteReason.SOL_HIGH_RISK, \
        "Sol routes REQUIRE a closed reason code"


def test_layered_unknown_action_fails_toward_sol(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    green_guards(root)
    decision = _router(root).route_action("p1", "made_up_action")
    assert decision.route is Route.SOL_ADJUDICATE
    assert decision.reason is RouteReason.SOL_OFF_TABLE


# ---------------------------------------------------------------------------
# the six gate guards
# ---------------------------------------------------------------------------
def test_all_six_guards_pass_when_green(tmp_path):
    root = make_root(tmp_path)
    green_guards(root)
    paths = LoopPaths.resolve(root)
    report = check_layered_gate_guards(paths, OrchestrationPolicy.load(paths))
    assert report.passed, report.details
    assert set(report.results) == {g.value for g in GateGuard}
    assert len(report.results) == len(GateGuard)


@pytest.mark.parametrize("breaker,guard", [
    ("heartbeat", GateGuard.CONSUMER_HEARTBEAT_FRESH),
    ("canary", GateGuard.EXACTLY_ONCE_CANARY),
    ("pin", GateGuard.K3_VERIFIER_MODEL_PINNED),
    ("validator", GateGuard.SHORT_RESULT_VALIDATOR_ENABLED),
    ("schema", GateGuard.STATEMACHINE_SCHEMA_COMPATIBLE),
    ("rollback", GateGuard.ROLLBACK_KEY_AVAILABLE),
])
def test_each_guard_failure_detected(tmp_path, breaker, guard):
    root = make_root(tmp_path)
    green_guards(root)
    paths = LoopPaths.resolve(root)
    policy = OrchestrationPolicy.load(paths)
    if breaker == "heartbeat":
        (paths.l2_heartbeat).unlink()
    elif breaker == "canary":
        (paths.l2_queue_dir / "exactly_once_canary.json").write_text(
            json.dumps({"status": "FAIL"}))
    elif breaker == "pin":
        policy.doc["models"] = {}
    elif breaker == "validator":
        (root / "data" / "validators" / "short_result_validator.json"
         ).write_text(json.dumps({"enabled": False}))
    elif breaker == "schema":
        policy.doc.setdefault("gate_guard", {})["statemachine_schema_required"] = \
            "codex-loop-statemachine/v99"
    elif breaker == "rollback":
        policy.doc.setdefault("routing", {})["rollback_mode"] = ""
    report = check_layered_gate_guards(paths, policy)
    assert not report.passed
    assert guard.value in report.failing(), \
        "the failing guard is named exactly (fail-visible)"


def test_pin_disagreement_with_role_toml_fails_guard(tmp_path):
    root = make_root(tmp_path)
    green_guards(root)
    agents = root / "agents"
    agents.mkdir()
    (agents / "verifier.toml").write_text('model = "someone/other-model"\n')
    paths = LoopPaths.resolve(root)
    report = check_layered_gate_guards(paths, OrchestrationPolicy.load(paths))
    assert GateGuard.K3_VERIFIER_MODEL_PINNED.value in report.failing()


# ---------------------------------------------------------------------------
# auto-downgrade layered -> shadow
# ---------------------------------------------------------------------------
def test_auto_downgrade_to_shadow_on_gate_failure(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    # guards NOT green (no heartbeat/canary/etc.)
    router = _router(root)
    mode, report = router.effective_mode()
    assert mode is RoutingMode.SHADOW, "guards failing => observe, never actuate"
    assert report is not None and not report.passed
    downgrades = root / "data" / "router" / "mode_downgrades.ndjsonl"
    rec = json.loads(downgrades.read_text().splitlines()[0])
    assert rec["requested"] == "layered" and rec["effective"] == "shadow"
    assert rec["failing_guards"], "the downgrade names its failing guards"


def test_downgraded_routing_executes_as_shadow(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    decision = _router(root).route_action("p1", "send_l2")
    assert decision.mode is RoutingMode.SHADOW
    assert decision.effective_action == "direct_l3"
    assert decision.requested_mode is RoutingMode.LAYERED


# ---------------------------------------------------------------------------
# model pins — never hardcoded
# ---------------------------------------------------------------------------
def test_model_pins_read_from_config(tmp_path):
    root = make_root(tmp_path)
    router = _router(root)
    for family in ("sol", "k3", "v4"):
        assert router.model_pin(family) == \
            router.policy.doc["models"][f"{family}_model"]


def test_enforce_model_pin_refuses_divergence(tmp_path):
    root = make_root(tmp_path)
    router = _router(root)
    pin = router.model_pin("k3")
    assert router.enforce_model_pin("k3", pin) == pin
    with pytest.raises(ModelPinError, match="pin violation"):
        router.enforce_model_pin("k3", "sneaky/other-model")


def test_missing_pin_fails_visible(tmp_path):
    root = make_root(tmp_path)
    router = _router(root)
    router.policy.doc["models"] = {}
    with pytest.raises(ModelPinError):
        router.model_pin("sol")


def _non_docstring_strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    doc_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value,
                                                          ast.Constant):
                doc_nodes.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in doc_nodes:
            out.append(node.value)
    return out


def test_no_hardcoded_model_ids_anywhere_in_code():
    """Constraint check: the configured model pins appear ONLY in config
    files (and docstrings/comments), never in executable Python literals."""
    import tomllib
    with open(IMPL / "config" / "orchestration_policy_v2.toml", "rb") as fh:
        models = tomllib.load(fh)["models"]
    pins = {models[key] for key in ("sol_model", "k3_model", "v4_model")}
    offenders = []
    for py in V2_SOURCE_FILES:
        for literal in _non_docstring_strings(py):
            for pin in pins:
                if pin in literal:
                    offenders.append((str(py), literal[:60]))
    assert not offenders, "hardcoded model ids found: %r" % offenders
