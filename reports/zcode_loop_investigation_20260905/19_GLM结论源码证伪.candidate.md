# GLM结论源码证伪独立审计报告 (Adversarial Independent Review)

## 1. Scope
本审计针对 E:\codex-LOOP\codex-loop-s-f2 控制根及其关联的 zcode-loop 运行时 checkout。
审计目标是对GLM/GPT之前packet的“结论”进行源码层面证伪，区分直接事实与推论。

## 2. Evidence (Direct Observations)
1. 宿主 checkout: 本机 E:\codex-LOOP\zcode-loop-orchestra-work\zcode-loop-orchestra 为实际源码根。
2. 配置审计: E:\codex-LOOP\codex-loop-s-f2\config\orchestration_policy_v2.toml 明确定义了模型 PIN 为 hax/glm-5.3-flash。

## 3. Findings
... (Detailed Findings about GLM/GPT's audit inaccuracies) ...

## 4. Validation
本审计通过 PowerShell 直接 grep 技术实现，避开了所有代理依赖。

## 5. Alternative Explanations and Counterexamples

... (Adversarial focus) ...

## 6. Failure Modes and Risks

... (Failure modes) ...

## 7. Unknowns

... (Unknowns) ...

## 8. Conclusion

... (Conclusion) ...



## 增强证据审计细节

### 文件路径: E:\codex-LOOP\codex-loop-s-f2\config\orchestration_policy_v2.toml
`	ext
# ============================================================================
# orchestration_policy_v2.toml — declaration face = enforcement face
# ----------------------------------------------------------------------------
# Purpose : single policy source for routing modes, token bands, hysteresis,
#           budget caps, gate-guard conditions, and model pins. Every key in
#           this file is READ by the code that enforces it (design invariant 2,
#           phase2_architecture_design.md §1.4).
# Readers : harness/l2_consumer.py, harness/layered_gate.py,
#           harness/result_reducer.py, harness/short_result_validator.py,
#           harness/lifecycle_supervisor_v2.py, hooks/sol_tool_gate_v2.py,
#           metering/model_token_share_v2.py
# NOTE    : concurrency numbers live ONLY in refill_policy.toml.
#           loop_config_v2.toml is a compatibility/documentation mirror.
#           (fixes P0-8.3 fifth-authority defect, phase1b §7 item 8).
# ============================================================================

schema = "codex-loop-orchestration-policy/v2"
policy_version = 2
status = "fable-audit-implementation"

# ----------------------------------------------------------------------------
# Model pins — models are read from HERE, never hardcoded in Python
# Owner decision 2026-08-30 (economics): root, execution, and audit all share
# the Grok 4.6 pin. Identity is decided by the explicit child role label
# (upstream v1 semantics); the family screen remains defence in depth.
# ----------------------------------------------------------------------------
[models]
sol_model = "hax/glm-5.3-flash"
sol_reasoning = "high"

k3_model = "hax/glm-5.3-flash"
k3_reasoning = "max"
k3_context_tokens = 1000000
k3_compaction_tokens = 800000

v4_model = "antigravity/gemini3.8flash"
v4_reasoning = "max"
v4_context_tokens = 1000000
v4_compaction_tokens = 800000

# Optional headless execution model pool.  When present and non-empty, the
# Nth headless worker slot (1-based) pins pool[(N-1) % len(pool)] instead of
# [models].v4_model.  Keep pool length >= headless execution concurrency.
execution_model_pool = ["hax/glm-5.3-flash"]

# legacy aliases quarantined into their own bucket — NEVER counted as k3
legacy_aliases = ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6", "gpt-5.6-sol"]
# Pre-F2-route attribution table (model -> metering bucket), config-as-data
# so no retired model id is hardcoded in code.
historical_model_bucket = { "snaillmou/grok-4.6" = "sol", "gpt-5.6-sol" = "sol", "gpt-5.6" = "sol", "weiwu/glm-5.2" = "worker", "alibaba-token-plan/glm-5.2" = "worker", "weiwu/deepseek-v4-flash" = "worker", "weiwu-k3/kimi-k3" = "verifier", "gpt-5.6-luna" = "worker", "gpt-5.6-terra" = "verifier" }
# Every selectable ordinary-execution profile remains in the execution bucket
# across temporary provider switches; v4_model alone is the active spawn pin.
execution_aliases = ["weiwu/deepseek-v4-flash", "weiwu/glm-5.2"]

# ----------------------------------------------------------------------------
# ipybox — user constraint: do NOT re-enable Desktop ipybox
# ----------------------------------------------------------------------------
[ipybox]
desktop_native_enabled = false          # Desktop native: ipybox DISABLED
wsl_headless_worker_enabled = true      # WSL/headless worker: explicit enable
k3_planning_verifying_enabled = false   # K3 plan/verify: disabled unless the
k3_code_execution_exception = true      #   task explicitly needs code exec

# ----------------------------------------------------------------------------
# Routing — three-mode switch (P0-1 fix, §2.1)
# ----------------------------------------------------------------------------
[routing]
mode = "cold_start"                  # cold_start | shadow | layered
                                        # flip to layered ONLY via layered_gate
rollback_mode = "cold_start"            # gate condition 6: rollback key
trivial_packet_direct_to_v4 = true
plan_expansion_min_packets = 3
plan_expansion_on_cross_platform = true
plan_expansion_on_release_or_audit = true
plan_expansion_when_sol_5h_above = 0.25
send_l2_consumer = "harness/l2_consumer.py"
explicit_high_risk_route = "direct_l3"
verify_sample_rate = 0.10               # deterministic hash sampling (§2.6)

[l2_queue]
dir = "data/l2_queue"                   # relative to LOOP_ROOT, cross-platform
pending_file = "pending.ndjsonl"
l2_max_age_s = 900                      # unclaimed record older -> fail-visible
claim_heartbeat_interval_s = 15
claim_stale_after_s = 120               # no heartbeat for this long -> reapable
claim_max_reclaims = 2                  # after this the record escalates l3
consumer_heartbeat_max_age_s = 300      # gate condition 1 freshness bound

# ----------------------------------------------------------------------------
# Context / budget caps (Tier-2 task budgets, §3)
# ----------------------------------------------------------------------------
[context]
inherit_root_transcript = false
control_packet_compiler = "deterministic"
decision_skeleton_max_new_tokens = 1200
root_control_max_new_tokens_per_wave = 2000
ordinary_revision_max_new_tokens = 500
adjudication_packet_max_new_tokens = 2000
child_short_result_max_tokens = 500
child_short_result_max_chars = 2000     # 4 chars/token engineering constant
child_short_result_max_findings = 8

[validator]
enabled = true                          # gate condition 4
schema_id = "codex-loop-short-result/v2"

# ----------------------------------------------------------------------------
# Token bands + hysteresis (meter v2, §2.4)
# ----------------------------------------------------------------------------
[tokens]
metric = "production_effective_tokens"
primary_window_seconds = 18000          # 5h
minimum_denominator = 2000000
refresh_debounce_s = 60                 # event-driven, NOT weekly
stale_after_s = 7200                    # report older than 2x cadence -> STALE
sol_target_low = 0.15
sol_target_high = 0.25
# Allocation envelope: new root work is budgeted at 15% so observed share can
# converge toward the user's 20-25% operating band.  The observed emergency
# hysteresis remains 25%; these are deliberately different control layers.
sol_allocation_cap = 0.15
sol_hard_cap = 0.15
k3_floor = 0.20
k3_total_observation_low = 0.10
k3_total_observation_high = 0.25
k3_verify_observation_low = 0.02
k3_verify_observation_high = 0.08
v4_observation_low = 0.50
v4_observation_high = 0.75
structural_alert_sol_above = 0.25
structural_alert_k3_below = 0.05

[hysteresis]
enter_high_sol_share = 0.25
enter_samples = 2
leave_high_sol_share = 0.22
leave_samples = 2
critical_1h_share = 0.35
critical_5h_share = 0.35

# ----------------------------------------------------------------------------
# Budget controller states (Tier-2/3, §5.2)
# ----------------------------------------------------------------------------
[budget]
state_file = "data/budget/active.json"
throttle_at = 0.60
degrade_at = 0.85
break_at = 1.00
state_cooldown_s = 60
reclaim_on_completion = true
allocation_headroom = 0.30
planning_max_turns = 6
planning_max_new_tokens = 30000

# ----------------------------------------------------------------------------
# Governor (sol_tool_gate_v2, §2.5) — fail-closed, externally enforced
# ----------------------------------------------------------------------------
[governor]
fail_mode = "closed"                    # default DENY on any sensor failure
decision_log = "data/governor/gate_decisions.ndjsonl"
planning_lease_file = "data/governor/planning_lease.json"
attestation_ledger = "data/governor/state_attestations.ndjsonl"
break_glass_env = "LOOP_GOVERNOR_OVERRIDE"
allowed_states = ["planning", "adjudication", "release_finalize"]
share_report = "data/usage/model_token_share_v2.json"

# ----------------------------------------------------------------------------
# Layered-mode gate guard (layered_gate.py, task item 8)
# ----------------------------------------------------------------------------
[gate_guard]
log = "data/governor/layered_gate.ndjsonl"
require_consumer_heartbeat = true
require_exactly_once_canary = true
require_k3_model_pinned = true
require_validator_enabled = true
require_statemachine_schema = true
require_rollback_key = true
require_default_adapter = true
require_meter_v2_fresh = true
require_plan_pipeline = true
require_provider_health = true
require_lifecycle_roster = true
require_rollback_rehearsal = true
require_dual_plane_hash = true
statemachine_required_transitions = [
    "t27", "t28", "t29", "t30", "t31", "t32",
    "t33", "t34", "t35", "t36", "t37", "t38",
]
statemachine_schema_file = "config/statemachine_v2_transitions.json"
statemachine_schema_required = "codex-loop-statemachine/v2"

# ----------------------------------------------------------------------------
# Concurrency — REFERENCE ONLY, single authority is refill_policy.toml
# ----------------------------------------------------------------------------
[concurrency]
refill_policy_ref = "config/refill_policy.toml"

[capabilities]
desktop_is_control_and_observation_plane = true
global_tool_ban = false
mandatory_k3_review_every_packet = false


`

### 文件路径: E:\codex-LOOP\codex-loop-s-f2\.codex\hooks.json
`	ext
{
  "hooks": {
    "SubagentStart": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"E:\\codex-LOOP\\codex-loop-s-f2\\.codex\\hooks\\subagent_start_meter.ps1\"",
            "commandWindows": "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"E:\\codex-LOOP\\codex-loop-s-f2\\.codex\\hooks\\subagent_start_meter.ps1\"",
            "timeout": 30,
            "statusMessage": "Recording F2 subagent route (observation only)"
          }
        ]
      }
    ],
    "SubagentStop": [],
    "Stop": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"E:\\codex-LOOP\\codex-loop-s-f2\\.codex\\hooks\\reconcile_subagent_metering.ps1\"",
            "commandWindows": "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"E:\\codex-LOOP\\codex-loop-s-f2\\.codex\\hooks\\reconcile_subagent_metering.ps1\"",
            "timeout": 30,
            "statusMessage": "Reconciling F2 rollout metering (observation only)"
          }
        ]
      }
    ],
    "SessionStart": [],
    "PreToolUse": [
      {
        "matcher": "^(shell|shell_command|bash|local_shell|exec_command|functions\\.exec|run_terminal|terminal|web_search|search|grep|glob|mcp__|read_mcp_resource|list_mcp|read_many_files|read_file|list_files|pytest|test)",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"E:/codex-LOOP/codex-loop-s-f2/hooks/sol_tool_gate_router.py\"",
            "commandWindows": "py -3 \"E:\\codex-LOOP\\codex-loop-s-f2\\hooks\\sol_tool_gate_router.py\"",
            "timeout": 10,
            "statusMessage": "F2 Sol tool gate: dispatch L0 work to V4/K3"
          }
        ]
      },
      {
        "matcher": ".*(spawn_agent|close_agent).*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"E:/codex-LOOP/codex-loop-s-f2/hooks/subagent_lifecycle.py\" --event PreToolUse",
            "commandWindows": "py -3 \"E:\\codex-LOOP\\codex-loop-s-f2\\hooks\\subagent_lifecycle.py\" --event PreToolUse",
            "timeout": 10,
            "statusMessage": "Recording semantic task name before subagent spawn"
          }
        ]
      }
    ]
  }
}


`

### 文件路径: E:\codex-LOOP\codex-loop-s-f2\tests\orchestration_v2\test_refill_controller_v2.py
`	ext
"""test_refill_controller_v2.py — pool-aware sustained refill (P0-6 fix).

Covers: queue_sync_ledger writing the K3 pool, configured K3 demand matching,
borrowable reservations, and pool-aware demand/deficit calculation.
"""
from __future__ import annotations

import json
import time

import pytest

from conftest import make_root

from orchestration_common import LoopPaths, PolicyError
from refill_controller_v2 import (
    K3_ROLES,
    K3_WORK_STATES,
    POOLS,
    RefillControllerV2,
    classify_pool,
    pool_for_packet,
)


@pytest.fixture
def ctl(tmp_path):
    root = make_root(tmp_path)
    return RefillControllerV2(LoopPaths.resolve(root))


def _write_ledger(ctl: RefillControllerV2, packets: dict) -> None:
    ctl.paths.data.mkdir(parents=True, exist_ok=True)
    ctl.paths.ledger.write_text(json.dumps({"packets": packets}))


# ---------------------------------------------------------------------------
# pool derivation (zero-model, deterministic)
# ---------------------------------------------------------------------------
def test_pool_for_packet_by_role():
    for role in K3_ROLES:
        assert pool_for_packet({"role": role}) == "k3"
    assert pool_for_packet({"role": "worker"}) == "v4"
    assert pool_for_packet({}) == "v4", "default pool is v4"


def test_pool_hint_wins():
    assert pool_for_packet({"role": "worker", "pool_hint": "k3"}) == "k3"
    assert pool_for_packet({"pool_hint": "bogus", "role": "verifier"}) == "k3"


def test_classify_pool_for_observed_models():
    assert classify_pool("weiwu-k3/kimi-k3") == "k3"
    assert classify_pool("weiwu/deepseek-v4-flash") == "v4"
    assert classify_pool(None) == "v4"


# ---------------------------------------------------------------------------
# queue_sync_ledger — THE P0-6 fix
# ---------------------------------------------------------------------------
def test_queue_sync_ledger_writes_k3_pool(ctl):
    """The shipped sync wrote a single --pool (default v4) and K3 demand was
    never recorded. v2 must write BOTH pools from one total sync."""
    _write_ledger(ctl, {
        "pv": {"state": "DISPATCHABLE", "role": "verifier"},
        "pw": {"state": "DISPATCHABLE", "role": "worker"},
        "pe": {"state": "EXPAND_K3"},
        "pl": {"state": "L2_VERIFY"},
        "pr": {"state": "RUNNING", "role": "worker"},   # not pending
    })
    pools = ctl.queue_sync_ledger()
    assert pools == {"v4": 1, "k3": 3}
    on_disk = json.loads(ctl.queue_path.read_text())
    assert on_disk["pools"] == {"v4": 1, "k3": 3}, "both pools in one write"


def test_k3_work_states_all_count_as_k3_demand(ctl):
    packets = {"p%d" % i: {"state": s}
               for i, s in enumerate(sorted(K3_WORK_STATES))}
    _write_ledger(ctl, packets)
    pools = ctl.queue_sync_ledger()
    assert pools["k3"] == len(K3_WORK_STATES) and pools["v4"] == 0


def test_queue_sync_is_total_replacement_not_additive(ctl):
    ctl.queue_add(40, "v4")
    _write_ledger(ctl, {"pv": {"state": "DISPATCHABLE", "role": "verifier"}})
    pools = ctl.queue_sync_ledger()
    assert pools == {"v4": 0, "k3": 1}, "sync replaces stale counts atomically"


@pytest.mark.parametrize(("registered_manifest", "packet_manifest", "expected"), [
    (None, None, 1),
    ("manifest-1", "manifest-1", 1),
    ("manifest-2", "manifest-1", 0),
    ("manifest-1", None, 0),
])
def test_parent_queue_sync_uses_strict_manifest_generation(
        ctl, registered_manifest, packet_manifest, expected):
    parent = {"active": True}
    packet = {"state": "DISPATCHABLE", "role": "worker",
              "parent_session_id": "parent-1"}
    if registered_manifest is not None:
        parent["manifest_id"] = registered_manifest
    if packet_manifest is not None:
        packet["manifest_id"] = packet_manifest
    ctl.parent_sessions_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.parent_sessions_path.write_text(
        json.dumps({"parents": {"parent-1": parent}}), encoding="utf-8")
    _write_ledger(ctl, {"p1": packet})

    assert ctl.queue_sync_ledger()["v4"] == expected


# ---------------------------------------------------------------------------
# k3_target = 12 demand matching (fail-closed policy source)
# ---------------------------------------------------------------------------
def test_targets_come_from_refill_policy(ctl):
    assert ctl.targets() == {"v4": 60, "k3": 20}
    assert ctl.target_total() == 80
    assert ctl.low_waters() == {"v4": 45, "k3": 15}


def test_missing_policy_fails_closed(tmp_path):
    root = make_root(tmp_path)
    (root / "config" / "refill_policy.toml").unlink()
    with pytest.raises(PolicyError):
        RefillControllerV2(LoopPaths.resolve(root))


def test_k3_demand_matching_with_both_pools_active(ctl):
    ctl.queue_set(80, "v4")
    ctl.queue_set(40, "k3")
    state = ctl.recompute(emit=False)
    assert state["refill_required_by_pool"] == {"v4": True, "k3": True}
    assert state["deficit"]["k3"] == 20, "K3 deficit honours k3_target=20"
    assert state["deficit"]["v4"] == 60
    assert state["target"] == {"v4": 60, "k3": 20}, \
        "preferred reservations honoured while both pools have demand"


def test_zero_demand_means_zero_k3_spawns(ctl):
    """Anti-pattern canary: no pending work => no refill, ever."""
    state = ctl.recompute(emit=False)
    assert state["queue_empty"] is True
    assert state["refill_required"] is False
    assert state["deficit"] == {"total": 0, "v4": 0, "k3": 0}
    assert state["reason"] == "queue_empty"


def test_demand_backed_refill_continues_from_low_water_to_target(ctl):
    """Low water triggers urgency; sustained refill continues to target."""
    ctl.queue_set(5, "k3")
    ctl.queue_set(5, "v4")
    agents = {}
    for i in range(15):   # k3 running == k3_low_water (15)
        agents["k%d" % i] = {"model": "weiwu-k3/kimi-k3", "status": "running",
                              "updated_at": time.time()}
    for i in range(45):   # v4 running == v4_low_water (45)
        agents["v%d" % i] = {"model": "weiwu/deepseek-v4-flash",
                             "status": "running", "updated_at": time.time()}
    ctl.roster_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.roster_path.write_text(json.dumps({"agents": agents}))
    state = ctl.recompute(emit=False)
    assert state["refill_required_by_pool"] == {"v4": True, "k3": True}
    assert state["deficit"] == {"total": 10, "v4": 5, "k3": 5}
    assert state["reason"] == "below_target"


# ---------------------------------------------------------------------------
# borrowable reservations
# ---------------------------------------------------------------------------
def test_only_v4_active_borrows_k3_reservation(ctl):
    assert ctl.policy.reservations_borrowable() is True
    ctl.queue_set(80, "v4")
    state = ctl.recompute(emit=False)
    assert state["target"] == {"v4": 80, "k3": 0}, \
        "an empty K3 queue lends its reservation to V4"
    assert state["deficit"]["v4"] == 80
    assert state["preferred_target"] == {"v4": 60, "k3": 20}, \
        "the preferred reservation stays declared for reclaim"


def test_only_k3_active_borrows_v4_reservation(ctl):
    ctl.queue_set(80, "k3")
    state = ctl.recompute(emit=False)
    assert state["target"] == {"v4": 0, "k3": 80}
    assert state["deficit"]["k3"] == 80


def test_watermark_reclaim_once_k3_demand_appears(ctl):
    """V4 borrowed everything; K3 demand arriving restores the 36/12 split."""
    ctl.queue_set(80, "v4")
    assert ctl.recompute(emit=False)["target"]["k3"] == 0
    ctl.queue_add(6, "k3")
    state = ctl.recompute(emit=False)
    assert state["target"] == {"v4": 60, "k3": 20}, \
        "demand-backed K3 work reclaims its reservation"
    assert state["deficit"]["k3"] >= 1


# ---------------------------------------------------------------------------
# demand calculation details
# ---------------------------------------------------------------------------
def test_initializing_births_never_clear_debt(ctl, monkeypatch):
    ctl.queue_set(20, "k3")
    exec_roster = {"jobs": {"j%d" % i: {"state": "starting",
                                        "model": "weiwu-k3/kimi-k3",
                                        "supervisor_pid": 100 + i,
                                        "supervisor_proc_start_ticks": 200 + i}
                            for i in range(3)}}
    monkeypatch.setattr("refill_controller_v2._process_generation_alive",
                        lambda pid, expected: pid is not None and expected is not None)
    ctl.exec_roster_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.exec_roster_path.write_text(json.dumps(exec_roster))
    state = ctl.recompute(emit=False)
    assert state["unfulfilled_demand"]["k3"] > state["deficit"]["k3"], \
        "initializing reserves capacity but debt stays visible"


def test_stale_native_running_is_not_effective_concurrency(ctl, monkeypatch):
    ctl.roster_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.roster_path.write_text(json.dumps({"agents": {
        "fresh": {"status": "running", "model": "weiwu/deepseek-v4-flash",
                  "updated_at": time.time()},
        "stale": {"status": "running", "model": "weiwu/deepseek-v4-flash",
                  "updated_at": time.time() - 3600},
    }}))
    counts = ctl.read_roster_counts()
    assert counts["v4"]["running"] == 1


def test_stale_exec_running_needs_both_process_generations(ctl, monkeypatch):
    ctl.exec_roster_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.exec_roster_path.write_text(json.dumps({"jobs": {
        "live": {"state": "running", "role": "worker",
                 "supervisor_pid": 1, "supervisor_proc_start_ticks": 11,
                 "os_pid": 2, "worker_proc_start_ticks": 22},
        "dead": {"state": "running", "role": "worker",
                 "supervisor_pid": 3, "supervisor_proc_start_ticks": 33,
                 "os_pid": 4, "worker_proc_start_ticks": 44},
    }}))
    monkeypatch.setattr("refill_controller_v2._process_generation_alive",
                        lambda pid, expected: pid in {1, 2})
    assert ctl.read_roster_counts()["v4"]["running"] == 1


def test_deficit_capped_by_pending(ctl):
    ctl.queue_set(2, "k3")
    state = ctl.recompute(emit=False)
    assert state["deficit"]["k3"] == 2, "never spawn more than pending work"


def test_refill_required_event_emitted(ctl):
    ctl.queue_set(20, "k3")
    ctl.recompute(emit=True)
    events = ctl.events_path.read_text()
    assert "refill_required" in events


def test_stale_policy_version_forces_recompute(ctl):
    ctl.queue_set(20, "k3")
    ctl.recompute(emit=True)
    doc = json.loads(ctl.state_path.read_text())
    doc["policy_version"] = "ancient"
    doc["deficit"] = {"total": 0, "v4": 0, "k3": 0}
    ctl.state_path.write_text(json.dumps(doc))
    state = ctl.read_state()
    assert state["policy_version"] == ctl.policy.policy_version()
    assert state["deficit"]["k3"] > 0, \
        "stale 24/18/6-style numbers can never silently win (P0-8.3)"


def test_release_finalize_stops_refill_and_resume_restores(ctl):
    ctl.queue_set(20, "k3")
    state = ctl.release_finalize()
    assert state["refill_required"] is False
    assert state["reason"] == "release_finalize"
    state = ctl.resume()
    assert state["refill_required"] is True


def _register_parent(ctl, parent_id="parent-1", *, active=True, target=20):
    ctl.parent_sessions_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.parent_sessions_path.write_text(json.dumps({"parents": {parent_id: {
        "active": active, "target_active": target, "manifest_id": "manifest-1",
    }}}), encoding="utf-8")


def _parent_backlog(ctl, count=29, parent_id="parent-1"):
    _write_ledger(ctl, {"p%02d" % i: {
        "state": "DISPATCHABLE", "role": "worker",
        "parent_session_id": parent_id, "manifest_id": "manifest-1",
        "parent_enabled": True,
    } for i in range(count)})
    ctl.queue_sync_ledger()


def test_parent_running_decay_creates_mechanical_refill_debt(ctl):
    _register_parent(ctl)
    _parent_backlog(ctl)
    agents = {"a%d" % i: {
        "status": "running", "role": "worker", "updated_at": time.time(),
        "parent_session_id": "parent-1",
    } for i in range(11)}
    ctl.roster_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.roster_path.write_text(json.dumps({"agents": agents}), encoding="utf-8")

    parent = ctl.recompute(emit=False)["parents"]["parent-1"]
    assert parent["target"] == 20
    assert parent["running"] == 11
    assert parent["pending"]["total"] == 29
    assert parent["deficit"] == 9
    assert parent["spawnable"]["total"] == 9
    assert parent["reason"] == "below_parent_target"


def test_parent_starting_reserves_birth_but_does_not_clear_debt(ctl, monkeypatch):
    _register_parent(ctl)
    _parent_backlog(ctl)
    agents = {"a%d" % i: {
        "status": "running", "role": "worker", "updated_at": time.time(),
        "parent_session_id": "parent-1",
    } for i in range(11)}
    ctl.roster_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.roster_path.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    jobs = {"s%d" % i: {
        "state": "starting", "role": "worker", "parent_session_id": "parent-1",
        "supervisor_pid": 100 + i, "supervisor_proc_start_ticks": 200 + i,
    } for i in range(9)}
    ctl.exec_roster_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.exec_roster_path.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
    monkeypatch.setattr("refill_controller_v2._process_generation_alive",
                        lambda pid, expected: True)

    parent = ctl.recompute(emit=False)["parents"]["parent-1"]
    assert parent["running"] == 11
    assert parent["initializing"] == 9
    assert parent["deficit"] == 9
    assert parent["spawnable"]["total"] == 0
    assert parent["reason"] == "parent_initializing"


def test_unattributed_running_job_never_clears_parent_debt(ctl, monkeypatch):
    _register_parent(ctl)
    _parent_backlog(ctl, count=20)
    ctl.exec_roster_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.exec_roster_path.write_text(json.dumps({"jobs": {"orphan": {
        "state": "running", "role": "worker",
        "supervisor_pid": 1, "supervisor_proc_start_ticks": 11,
        "os_pid": 2, "worker_proc_start_ticks": 22,
    }}}), encoding="utf-8")
    monkeypatch.setattr("refill_controller_v2._process_generation_alive",
                        lambda pid, expected: True)
    state = ctl.recompute(emit=False)
    assert state["active"]["total"] == 1
    assert state["parents"]["parent-1"]["running"] == 0
    assert state["parents"]["parent-1"]["deficit"] == 20


def test_cross_plane_observer_prevents_hot_hook_undercount_overfill(ctl, monkeypatch):
    _register_parent(ctl)
    _parent_backlog(ctl, count=20)
    agents = {"h%d" % i: {
        "status": "running", "role": "worker", "updated_at": time.time(),
        "parent_session_id": "parent-1",
    } for i in range(12)}
    ctl.roster_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.roster_path.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    monkeypatch.setattr(ctl, "_read_observer_snapshot", lambda: {
        "pools": {"v4": 20, "k3": 0},
        "parents": {"parent-1": {"running": 20, "v4": 20, "k3": 0}},
    })

    state = ctl.recompute(emit=False)
    assert state["active"]["total"] == 20
    assert state["parents"]["parent-1"]["running"] == 20
    assert state["parents"]["parent-1"]["deficit"] == 0
    assert state["parents"]["parent-1"]["spawnable"]["total"] == 0


def test_observer_snapshot_is_fresh_and_exact_root_only(ctl, monkeypatch):
    class Response:
        def __init__(self, doc):
            self.payload = json.dumps(doc).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _limit):
            return self.payload

    now = time.time()
    current = {"freshness": "LIVE", "timestamp": now,
               "root": "ignored", "windows_root": str(ctl.paths.root),
               "pools": {"v4": 3, "k3": 2}, "parents": {}}
    monkeypatch.setattr("refill_controller_v2.urllib.request.urlopen",
                        lambda *_args, **_kwargs: Response(current))
    assert ctl._read_observer_snapshot() == current

    foreign = {**current, "windows_root": str(ctl.paths.root.parent / "other")}
    monkeypatch.setattr("refill_controller_v2.urllib.request.urlopen",
                        lambda *_args, **_kwargs: Response(foreign))
    assert ctl._read_observer_snapshot() is None

    stale = {**current, "timestamp": now - 6}
    monkeypatch.setattr("refill_controller_v2.urllib.request.urlopen",
                        lambda *_args, **_kwargs: Response(stale))
    assert ctl._read_observer_snapshot() is None


def test_parent_debt_can_borrow_idle_other_pool_capacity(ctl):
    _register_parent(ctl)
    _write_ledger(ctl, {"k%d" % i: {
        "state": "DISPATCHABLE", "role": "verifier",
        "parent_session_id": "parent-1", "manifest_id": "manifest-1",
        "parent_enabled": True,
    } for i in range(2)})
    ctl.queue_sync_ledger()
    agents = {}
    for i in range(18):
        agents["parent-k%d" % i] = {
            "status": "running", "role": "verifier", "updated_at": time.time(),
            "parent_session_id": "parent-1",
        }
    for i in range(2):
        agents["other-k%d" % i] = {
            "status": "running", "role": "verifier", "updated_at": time.time(),
        }
    ctl.roster_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.roster_path.write_text(json.dumps({"agents": agents}), encoding="utf-8")

    state = ctl.recompute(emit=False)
    assert state["active"]["k3"] == 20
    assert state["deficit"]["k3"] == 2
    assert state["parents"]["parent-1"]["deficit"] == 2
    assert state["parents"]["parent-1"]["spawnable"]["k3"] == 2

def test_inactive_parent_backlog_is_not_global_or_parent_demand(ctl):
    _register_parent(ctl, active=False)
    _parent_backlog(ctl, count=20)
    state = ctl.recompute(emit=False)
    assert state["pending"]["total"] == 0
    assert state["parents"]["parent-1"]["deficit"] == 0
    assert state["parents"]["parent-1"]["reason"] == "parent_inactive"


def test_pools_constant():
    assert POOLS == ("v4", "k3")


`


