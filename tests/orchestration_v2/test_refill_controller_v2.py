"""test_refill_controller_v2.py — pool-aware sustained refill (P0-6 fix).

Covers: queue_sync_ledger writing the K3 pool, configured K3 demand matching,
borrowable reservations, and pool-aware demand/deficit calculation.
"""
from __future__ import annotations

import json
import time

import pytest

from tests.orchestration_v2.conftest import make_root

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
    assert classify_pool("provider-b/k3-reviewer") == "k3"
    assert classify_pool("provider-a/v4-executor") == "v4"
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
        agents["k%d" % i] = {"model": "provider-b/k3-reviewer", "status": "running",
                              "updated_at": time.time()}
    for i in range(45):   # v4 running == v4_low_water (45)
        agents["v%d" % i] = {"model": "provider-a/v4-executor",
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
                                        "model": "provider-b/k3-reviewer",
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
        "fresh": {"status": "running", "model": "provider-a/v4-executor",
                  "updated_at": time.time()},
        "stale": {"status": "running", "model": "provider-a/v4-executor",
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
