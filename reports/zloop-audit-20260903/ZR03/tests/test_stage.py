"""Stage pipeline tests: risk floors, base gate, FSM (VOL-08)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import db as zdb          # noqa: E402
from zloop import stage as zstage    # noqa: E402


@pytest.fixture()
def store(tmp_path):
    conn = zdb.connect(tmp_path, create=True)
    yield zdb.ControlStore(tmp_path, conn, project_id="testproj")
    conn.close()


def _mk_stage(store, objective="implement the stage FSM", risk="NORMAL"):
    run_id = store.create_run("objective")
    st = zstage.create_stage(
        store, run_id, objective, risk,
        expected_head="abc123", dirty_digest="",
        stage_base_ref="refs/zloop/R001/S01/base", stage_base_tree="tree123")
    return run_id, st


def _kinds(store):
    return [r["kind"] for r in store.conn.execute(
        "SELECT kind FROM events ORDER BY seq")]


# ---- deterministic risk floor rules (VOL-08 §2) ----------------------------

def test_floor_rules_builtin():
    assert zstage.compute_risk_floor("integrate live trading with the exchange") == "CRITICAL"
    assert zstage.compute_risk_floor("place live orders and live withdrawal flows") == "CRITICAL"
    assert zstage.compute_risk_floor("production deploy of the payment service") == "CRITICAL"
    assert zstage.compute_risk_floor("destructive data migration of the users table") == "CRITICAL"
    assert zstage.compute_risk_floor("rotate the service credential") == "CRITICAL"
    assert zstage.compute_risk_floor("update the auth boundary for sessions") == "HIGH"
    assert zstage.compute_risk_floor("schema migration for the events table") == "HIGH"
    assert zstage.compute_risk_floor("bump dependency versions in CI pipeline") == "HIGH"
    assert zstage.compute_risk_floor("refactor utils") == "NORMAL"
    # case-insensitive matching
    assert zstage.compute_risk_floor("REFACTOR UTILS") == "NORMAL"
    assert zstage.compute_risk_floor("LIVE TRADING module") == "CRITICAL"


def test_floor_rules_extra_rules_only_raise():
    # extra rules from project config can raise a floor...
    assert zstage.compute_risk_floor("refactor utils",
                                     extra_rules=[("utils", "HIGH")]) == "HIGH"
    assert zstage.compute_risk_floor("refactor utils",
                                     extra_rules=[("util", "CRITICAL")]) == "CRITICAL"
    # ...but never lower a built-in match or the NORMAL default
    assert zstage.compute_risk_floor("live trading gateway",
                                     extra_rules=[("trading", "LOW")]) == "CRITICAL"
    assert zstage.compute_risk_floor("refactor utils",
                                     extra_rules=[("refactor", "LOW")]) == "NORMAL"
    with pytest.raises(ValueError):
        zstage.compute_risk_floor("refactor utils", extra_rules=[("utils", "EXTREME")])
    with pytest.raises(ValueError):
        zstage.compute_risk_floor("refactor utils", extra_rules=[("utils",)])


def test_risk_effective_ordering_and_validation():
    assert zstage.risk_effective("LOW", "NORMAL") == "NORMAL"
    assert zstage.risk_effective("NORMAL", "LOW") == "NORMAL"
    assert zstage.risk_effective("HIGH", "CRITICAL") == "CRITICAL"
    assert zstage.risk_effective("CRITICAL", "NORMAL") == "CRITICAL"
    assert zstage.risk_effective("NORMAL", "NORMAL") == "NORMAL"
    with pytest.raises(ValueError):
        zstage.risk_effective("SUPER", "NORMAL")
    with pytest.raises(ValueError):
        zstage.risk_effective("LOW", "NOPE")


# ---- stage base cleanliness gate [I37] (VOL-08 §3) --------------------------

def test_check_stage_base():
    assert zstage.check_stage_base("") == (True, "ok")
    assert zstage.check_stage_base("sha256:deadbeef") == (False, "BLOCKED_DIRTY_BASE")
    assert zstage.check_stage_base(None) == (False, "BLOCKED_DIRTY_BASE")  # fail-closed


# ---- create_stage (VOL-08 §1/§3) --------------------------------------------

def test_create_stage_row(store):
    run_id, st = _mk_stage(store)
    assert st["stage_id"] == "S01"
    assert st["state"] == "PLANNING"
    assert st["stage_revision"] == 1
    assert st["risk_requested"] == "NORMAL"
    assert st["risk_floor"] == "NORMAL"
    assert st["risk_effective"] == "NORMAL"
    assert st["expected_canonical_head"] == "abc123"
    assert st["canonical_dirty_digest"] == ""
    assert st["stage_base_ref"] == "refs/zloop/R001/S01/base"
    assert st["stage_base_tree"] == "tree123"
    # next stage in the same run -> S02 (per-run numbering)
    st2 = zstage.create_stage(store, run_id, "second slice", "LOW",
                              expected_head="h2", dirty_digest="",
                              stage_base_ref="refs/zloop/R001/S02/base",
                              stage_base_tree="tree2")
    assert st2["stage_id"] == "S02"
    # floor >= requested even when the request is below the keyword floor
    st3 = zstage.create_stage(store, run_id, "hotfix the live trading path", "LOW",
                              expected_head="h3", dirty_digest="",
                              stage_base_ref="refs/zloop/R001/S03/base",
                              stage_base_tree="tree3")
    assert st3["risk_floor"] == "CRITICAL"
    assert st3["risk_effective"] == "CRITICAL"          # max(LOW, CRITICAL)
    # persisted + readable back + audited
    assert zstage.get_stage(store, run_id, "S01") == st
    assert zstage.get_stage(store, run_id, "S99") is None
    assert "stage_created" in _kinds(store)


def test_create_stage_validates_inputs(store):
    run_id = store.create_run("objective")
    with pytest.raises(ValueError):
        zstage.create_stage(store, "R999", "slice", "NORMAL", expected_head="h",
                            dirty_digest="", stage_base_ref="r", stage_base_tree="t")
    with pytest.raises(ValueError):
        zstage.create_stage(store, run_id, "slice", "NORMAL", expected_head="",
                            dirty_digest="", stage_base_ref="r", stage_base_tree="t")


# ---- stage FSM guard table (VOL-08 §4) ---------------------------------------

def test_transition_legal_path(store):
    run_id, st = _mk_stage(store)
    sid = st["stage_id"]
    path = ["EXECUTING", "EXECUTING", "STAGED", "PROMOTING", "PROMOTED", "CLOSED"]
    for to_state in path:
        row = zstage.transition_stage(store, run_id, sid, to_state)
        assert row["state"] == to_state
    final = zstage.get_stage(store, run_id, sid)
    assert final["state"] == "CLOSED"
    assert final["updated_at"] >= final["created_at"]
    # every hop is audited (VOL-04 §4), with from/to detail
    kinds = _kinds(store)
    assert kinds.count("stage_transition") == len(path)
    details = [json.loads(r["detail_json"]) for r in store.conn.execute(
        "SELECT detail_json FROM events WHERE kind='stage_transition' ORDER BY seq")]
    assert details[0] == {"from": "PLANNING", "to": "EXECUTING"}
    # terminal states have no outgoing edges (late findings -> NEW stage)
    with pytest.raises(ValueError):
        zstage.transition_stage(store, run_id, sid, "EXECUTING")


def test_transition_illegal_raises(store):
    run_id, st = _mk_stage(store)
    sid = st["stage_id"]
    with pytest.raises(ValueError):                     # PLANNING -> STAGED illegal
        zstage.transition_stage(store, run_id, sid, "STAGED")
    assert zstage.get_stage(store, run_id, sid)["state"] == "PLANNING"  # unchanged
    with pytest.raises(ValueError):                     # unknown target state
        zstage.transition_stage(store, run_id, sid, "BOGUS")
    with pytest.raises(ValueError):                     # unknown stage
        zstage.transition_stage(store, run_id, "S99", "EXECUTING")


def test_transition_blocked_and_cancel(store):
    run_id, st = _mk_stage(store)
    sid = st["stage_id"]
    zstage.transition_stage(store, run_id, sid, "EXECUTING")
    # EXECUTING -> BLOCKED is legal (dirty base / hard-gate failure / drift)
    row = zstage.transition_stage(store, run_id, sid, "BLOCKED")
    assert row["state"] == "BLOCKED"
    # any non-terminal -> CANCELLED (explicit user/root cancel)
    assert zstage.transition_stage(store, run_id, sid, "CANCELLED")["state"] == "CANCELLED"
    with pytest.raises(ValueError):
        zstage.transition_stage(store, run_id, sid, "EXECUTING")


def test_transition_expected_fields_cas(store):
    run_id, st = _mk_stage(store)
    sid = st["stage_id"]
    out = zstage.transition_stage(store, run_id, sid, "EXECUTING",
                                  expected_fields={"state": "PLANNING",
                                                   "stage_revision": 1})
    assert out["state"] == "EXECUTING"
    zstage.transition_stage(store, run_id, sid, "STAGED")
    # stale expectation aborts before any write
    with pytest.raises(ValueError):
        zstage.transition_stage(store, run_id, sid, "PROMOTING",
                                expected_fields={"state": "EXECUTING"})
    assert zstage.get_stage(store, run_id, sid)["state"] == "STAGED"
