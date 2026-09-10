"""test_refill_controller_v2.py — pool-aware sustained refill (P0-6 fix).

Covers: queue_sync_ledger writing the K3 pool, k3_target=12 demand matching,
borrowable reservations, and pool-aware demand/deficit calculation.
"""
from __future__ import annotations

import json

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


# ---------------------------------------------------------------------------
# k3_target = 12 demand matching (fail-closed policy source)
# ---------------------------------------------------------------------------
def test_targets_come_from_refill_policy(ctl):
    assert ctl.targets() == {"v4": 36, "k3": 12}
    assert ctl.target_total() == 48
    assert ctl.low_waters() == {"v4": 27, "k3": 9}


def test_missing_policy_fails_closed(tmp_path):
    root = make_root(tmp_path)
    (root / "config" / "refill_policy.toml").unlink()
    with pytest.raises(PolicyError):
        RefillControllerV2(LoopPaths.resolve(root))


def test_k3_demand_matching_with_both_pools_active(ctl):
    ctl.queue_set(40, "v4")
    ctl.queue_set(20, "k3")
    state = ctl.recompute(emit=False)
    assert state["refill_required_by_pool"] == {"v4": True, "k3": True}
    assert state["deficit"]["k3"] == 12, "K3 deficit honours k3_target=12"
    assert state["deficit"]["v4"] == 36
    assert state["target"] == {"v4": 36, "k3": 12}, \
        "preferred reservations honoured while both pools have demand"


def test_zero_demand_means_zero_k3_spawns(ctl):
    """Anti-pattern canary: no pending work => no refill, ever."""
    state = ctl.recompute(emit=False)
    assert state["queue_empty"] is True
    assert state["refill_required"] is False
    assert state["deficit"] == {"total": 0, "v4": 0, "k3": 0}
    assert state["reason"] == "queue_empty"


def test_k3_refill_only_below_low_water(ctl):
    """With BOTH pools demand-backed (preferred watermarks apply), a pool at
    or above its own low water is never refilled."""
    ctl.queue_set(5, "k3")
    ctl.queue_set(5, "v4")
    agents = {}
    for i in range(9):    # k3 running == k3_low_water (9)
        agents["k%d" % i] = {"model": "weiwu-k3/kimi-k3", "status": "running"}
    for i in range(27):   # v4 running == v4_low_water (27)
        agents["v%d" % i] = {"model": "weiwu/deepseek-v4-flash",
                             "status": "running"}
    ctl.roster_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.roster_path.write_text(json.dumps({"agents": agents}))
    state = ctl.recompute(emit=False)
    assert state["refill_required_by_pool"] == {"v4": False, "k3": False}
    assert state["reason"] == "at_or_above_low_water"


# ---------------------------------------------------------------------------
# borrowable reservations
# ---------------------------------------------------------------------------
def test_only_v4_active_borrows_k3_reservation(ctl):
    assert ctl.policy.reservations_borrowable() is True
    ctl.queue_set(60, "v4")
    state = ctl.recompute(emit=False)
    assert state["target"] == {"v4": 48, "k3": 0}, \
        "an empty K3 queue lends its reservation to V4"
    assert state["deficit"]["v4"] == 48
    assert state["preferred_target"] == {"v4": 36, "k3": 12}, \
        "the preferred reservation stays declared for reclaim"


def test_only_k3_active_borrows_v4_reservation(ctl):
    ctl.queue_set(60, "k3")
    state = ctl.recompute(emit=False)
    assert state["target"] == {"v4": 0, "k3": 48}
    assert state["deficit"]["k3"] == 48


def test_watermark_reclaim_once_k3_demand_appears(ctl):
    """V4 borrowed everything; K3 demand arriving restores the 36/12 split."""
    ctl.queue_set(60, "v4")
    assert ctl.recompute(emit=False)["target"]["k3"] == 0
    ctl.queue_add(6, "k3")
    state = ctl.recompute(emit=False)
    assert state["target"] == {"v4": 36, "k3": 12}, \
        "demand-backed K3 work reclaims its reservation"
    assert state["deficit"]["k3"] >= 1


# ---------------------------------------------------------------------------
# demand calculation details
# ---------------------------------------------------------------------------
def test_initializing_births_never_clear_debt(ctl):
    ctl.queue_set(20, "k3")
    exec_roster = {"jobs": {"j%d" % i: {"state": "starting",
                                        "model": "weiwu-k3/kimi-k3"}
                            for i in range(3)}}
    ctl.exec_roster_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.exec_roster_path.write_text(json.dumps(exec_roster))
    state = ctl.recompute(emit=False)
    assert state["unfulfilled_demand"]["k3"] > state["deficit"]["k3"], \
        "initializing reserves capacity but debt stays visible"


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


def test_pools_constant():
    assert POOLS == ("v4", "k3")
