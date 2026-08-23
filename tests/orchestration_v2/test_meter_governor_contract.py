"""Cross-component regressions for the audited v2 orchestration contract."""
from __future__ import annotations

import json
import time
import tomllib
from types import SimpleNamespace

import pytest

from tests.orchestration_v2.conftest import green_guards, make_root, set_routing_mode
from agent_router import AgentRouter, RoutingMode
from budget_controller import BudgetController
from layered_gate import LayeredGate
from model_token_share_v2 import MeterV2
from orchestration_common import LoopPaths, OrchestrationPolicy
from root_turn_governor import RootTurnGovernor
from trigger_eval_v2 import apply_routing_mode


def _meter(root, now: float, *, enough: bool = True) -> MeterV2:
    with (root / "config" / "orchestration_policy_v2.toml").open("rb") as fh:
        policy = tomllib.load(fh)
    meter = MeterV2(root, policy, clock=lambda: now)
    models = policy["models"]
    usage = root / "data" / "usage"
    usage.mkdir(parents=True, exist_ok=True)
    (usage / "run_role_map.json").write_text(json.dumps({
        "sol-run": {"role": "sol", "model": models["sol_model"]},
        "k3-run": {"role": "verifier", "model": models["k3_model"]},
    }), encoding="utf-8")
    sol_tokens, k3_tokens = ((1_200_000, 800_000) if enough else (100, 0))
    meter.record_turn(
        task_id="task", root_session_id="root", agent_id="sol-root",
        run_id="sol-run", model=models["sol_model"], step_id="sol-step",
        usage={"input_tokens": sol_tokens, "output_tokens": 0},
    )
    if k3_tokens:
        meter.record_turn(
            task_id="task", root_session_id="root", agent_id="k3-child",
            run_id="k3-run", model=models["k3_model"], step_id="k3-step",
            usage={"input_tokens": k3_tokens, "output_tokens": 0},
        )
    meter.refresh(force=True)
    return meter


def test_governor_reads_real_meter_report(tmp_path):
    root = make_root(tmp_path)
    _meter(root, time.time())
    governor = RootTurnGovernor(LoopPaths.resolve(root))
    share, status = governor.meter_status()
    assert status == "OK"
    assert share == pytest.approx(0.60)


def test_governor_detects_stale_real_meter_report(tmp_path):
    root = make_root(tmp_path)
    _meter(root, time.time() - 8_000)
    assert RootTurnGovernor(LoopPaths.resolve(root)).meter_status() == (
        None, "STALE")


def test_budget_controller_reads_real_meter_report(tmp_path):
    root = make_root(tmp_path)
    _meter(root, time.time())
    quota = BudgetController(LoopPaths.resolve(root)).global_quota_check()
    assert quota["status"] == "OK"
    assert quota["sol_share"] == pytest.approx(0.60)
    assert quota["k3_share"] == pytest.approx(0.40)


def test_insufficient_real_meter_data_never_actuates(tmp_path):
    root = make_root(tmp_path)
    _meter(root, time.time(), enough=False)
    paths = LoopPaths.resolve(root)
    assert RootTurnGovernor(paths).meter_status() == (None, "INSUFFICIENT_DATA")
    assert BudgetController(paths).global_quota_check()["status"] == \
        "INSUFFICIENT_DATA"


def test_trigger_eval_l2_record_carries_created_ts(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    green_guards(root)
    paths = LoopPaths.resolve(root)
    apply_routing_mode("send_l2", "pkt", "run", 1, paths,
                       OrchestrationPolicy.load(paths),
                       rules_hit=["exit_persistent_failure"])
    record = json.loads(paths.l2_pending.read_text(encoding="utf-8").splitlines()[0])
    assert record["created_ts"] > 0
    assert record["reason"] == "exit_persistent_failure"


def test_router_l2_record_carries_created_ts(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    router = AgentRouter(paths, OrchestrationPolicy.load(paths))
    decision = router.route_action("pkt", "send_l2")
    router.emit_l2_request("pkt", "run", 1, decision)
    record = json.loads(paths.l2_pending.read_text(encoding="utf-8").splitlines()[0])
    assert record["created_ts"] > 0
    assert record["reason"] == decision.reason.value


def test_gate_pass_writes_guard_stamps_and_layered_is_reachable(tmp_path):
    root = make_root(tmp_path)
    green_guards(root)
    with (root / "config" / "orchestration_policy_v2.toml").open("rb") as fh:
        legacy_policy = tomllib.load(fh)
    gate = LayeredGate(
        root, policy=legacy_policy,
        canary_runner=lambda: SimpleNamespace(ok=True, detail="canary pass"),
    )
    assert gate.check_exactly_once_canary().ok
    assert gate.check_validator_enabled().ok

    paths = LoopPaths.resolve(root)
    paths.l2_heartbeat.parent.mkdir(parents=True, exist_ok=True)
    paths.l2_heartbeat.write_text(json.dumps({"ts": time.time()}),
                                  encoding="utf-8")
    paths.ledger.write_text(
        json.dumps({"schema": "codex-loop-statemachine/v2", "packets": {}}),
        encoding="utf-8")
    result = gate.enable()
    assert result.allow
    mode, report = AgentRouter(paths, OrchestrationPolicy.load(paths)).effective_mode()
    assert mode is RoutingMode.LAYERED
    assert report is not None and report.passed


def test_gate_failure_writes_no_canary_stamp(tmp_path):
    root = make_root(tmp_path)
    with (root / "config" / "orchestration_policy_v2.toml").open("rb") as fh:
        policy = tomllib.load(fh)
    gate = LayeredGate(
        root, policy=policy,
        canary_runner=lambda: SimpleNamespace(ok=False, detail="canary fail"),
    )
    assert not gate.check_exactly_once_canary().ok
    assert not gate.canary_marker.exists()
