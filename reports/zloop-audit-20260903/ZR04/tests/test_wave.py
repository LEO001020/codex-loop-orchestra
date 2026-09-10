"""Wave/packet/launch tests: validation, run_wave, fencing (VOL-09)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import db as zdb           # noqa: E402
from zloop import stage as zstage     # noqa: E402
from zloop import wave as zwave       # noqa: E402


@pytest.fixture()
def store(tmp_path):
    conn = zdb.connect(tmp_path, create=True)
    yield zdb.ControlStore(tmp_path, conn, project_id="testproj")
    conn.close()


def _packet(pid, scope, **kw):
    p = {
        "packet_id": pid,
        "goal": f"do {pid}",
        "write_scope": scope,
        "acceptance": [f"pytest tests/{pid} -q"],
        "risk_class": "NORMAL",
        "network_policy": "none",
    }
    p.update(kw)
    return p


def _executing_stage(store, objective="implement wave semantics", risk="NORMAL"):
    run_id = store.create_run("objective")
    st = zstage.create_stage(
        store, run_id, objective, risk,
        expected_head="abc123", dirty_digest="",
        stage_base_ref="refs/zloop/R001/S01/base", stage_base_tree="tree123")
    zstage.transition_stage(store, run_id, st["stage_id"], "EXECUTING")
    return run_id, st


def _packet_rows(store, run_id, stage_id):
    return [dict(r) for r in store.conn.execute(
        "SELECT * FROM packets WHERE run_id=? AND stage_id=? ORDER BY packet_id",
        (run_id, stage_id))]


def _kinds(store):
    return [r["kind"] for r in store.conn.execute(
        "SELECT kind FROM events ORDER BY seq")]


# ---- validate_wave (VOL-09 §1-3) --------------------------------------------

def test_validate_ok_disjoint_scopes():
    packets = [_packet("P01", ["src/a/**"]),
               _packet("P02", ["src/b/**"], depends_on=["P01"])]
    v = zwave.validate_wave(packets, {})
    assert v["ok"] and v["errors"] == []


def test_validate_rejects_cycle():
    packets = [_packet("P01", ["src/a/**"], depends_on=["P02"]),
               _packet("P02", ["src/b/**"], depends_on=["P01"])]
    v = zwave.validate_wave(packets, {})
    assert not v["ok"]
    assert any("cycle" in e for e in v["errors"])


def test_validate_scope_overlap_needs_dep():
    packets = [_packet("P01", ["src/shared/**", "src/a/**"]),
               _packet("P02", ["src/shared/**"])]
    v = zwave.validate_wave(packets, {})
    assert not v["ok"]
    assert any("overlap" in e for e in v["errors"])
    # explicit depends_on serializes the conflict (VOL-09 §3)
    packets[1]["depends_on"] = ["P01"]
    assert zwave.validate_wave(packets, {})["ok"]


def test_validate_unknown_dep():
    packets = [_packet("P01", ["src/a/**"], depends_on=["P09"])]
    v = zwave.validate_wave(packets, {})
    assert not v["ok"]
    assert any("unknown" in e for e in v["errors"])
    # a dep on an existing packet of the stage is fine
    existing = {"P09": _packet("P09", ["src/z/**"])}
    assert zwave.validate_wave(packets, existing)["ok"]


def test_validate_bad_network_policy():
    p = _packet("P01", ["src/a/**"], network_policy="open-internet")
    v = zwave.validate_wave([p], {})
    assert not v["ok"]
    assert any("network_policy" in e for e in v["errors"])
    assert zwave.validate_wave(
        [_packet("P01", ["src/a/**"], network_policy="allowlist:pypi")], {})["ok"]


def test_validate_risk_below_floor():
    p = _packet("P01", ["src/a/**"], risk_class="NORMAL")
    v = zwave.validate_wave([p], {}, stage_floor="HIGH")
    assert not v["ok"]
    assert any("floor" in e for e in v["errors"])
    assert zwave.validate_wave(
        [_packet("P01", ["src/a/**"], risk_class="HIGH")], {},
        stage_floor="HIGH")["ok"]


def test_validate_missing_fields_and_duplicates():
    v = zwave.validate_wave([{"packet_id": "P01"}], {})
    assert not v["ok"]
    joined = " ".join(v["errors"])
    for field in ("goal", "write_scope", "acceptance", "risk_class",
                  "network_policy"):
        assert field in joined
    v2 = zwave.validate_wave([_packet("P01", ["src/a/**"]),
                              _packet("P01", ["src/b/**"])], {})
    assert not v2["ok"]
    assert any("duplicate" in e for e in v2["errors"])


# ---- accept_result / late_result_guard (I6/I7) ------------------------------

def _current(**kw):
    base = {"stage_revision": 3, "packet_revision": 2,
            "active_launch_id": "Labc123def456", "state": "RUNNING"}
    base.update(kw)
    return base


def _result(**kw):
    base = {"launch_id": "Labc123def456", "run_id": "R001", "stage_id": "S01",
            "stage_revision": 3, "packet_id": "P01", "packet_revision": 2,
            "attempt": 1, "status": "completed", "final_summary_ref": None,
            "delta_manifest_ref": None, "terminal_marker_seen": True}
    base.update(kw)
    return base


def test_accept_result_i6_fence():
    assert zwave.accept_result(_current(), _result()) == (True, "ok")
    assert zwave.accept_result(
        _current(), _result(launch_id="L000000000000")) == (False, "stale_launch")
    assert zwave.accept_result(
        _current(), _result(packet_revision=3)) == (False, "revision_mismatch")
    assert zwave.accept_result(
        _current(), _result(stage_revision=2)) == (False, "stale_stage_revision")
    assert zwave.accept_result(
        _current(state="REPORTED"), _result()) == (False, "not_running")
    assert zwave.accept_result(
        _current(state="SUPERSEDED", active_launch_id=None),
        _result()) == (False, "stale_launch")


def test_late_result_guard():
    cur = {"active_launch_id": "Llive00000001"}
    assert zwave.late_result_guard(cur, {"launch_id": "Lold00000000"}) is True
    assert zwave.late_result_guard(cur, {"launch_id": "Llive00000001"}) is False
    # cleared active launch (superseded) -> every arriving result is late
    assert zwave.late_result_guard({"active_launch_id": None},
                                   {"launch_id": "Lx"}) is True


# ---- run_wave with MockBackend ----------------------------------------------

def test_run_wave_reports_all(store):
    run_id, st = _executing_stage(store)
    sid = st["stage_id"]
    packets = [_packet("P01", ["src/a/**"]),
               _packet("P02", ["src/b/**"], depends_on=["P01"])]
    summary = zwave.run_wave(store, run_id, sid, 1, packets, zwave.MockBackend())
    assert summary == {"wave": "W1", "reported": 2, "rejected": []}

    rows = _packet_rows(store, run_id, sid)
    assert [r["state"] for r in rows] == ["REPORTED", "REPORTED"]
    assert [r["packet_revision"] for r in rows] == [1, 1]

    launches = [dict(r) for r in store.conn.execute(
        "SELECT * FROM launches ORDER BY packet_id")]
    assert len(launches) == 2
    for l in launches:
        assert l["workspace_id"] == f"{sid}/{l['packet_id']}/{l['launch_id']}"
        assert l["backend"] == "mock"
        assert l["stage_revision"] == 1
        assert l["attempt"] == 1 and l["packet_revision"] == 1
        assert l["intent_state"] == "RUNNING"
        assert l["backend_handle"].startswith("mock-")

    attempts = [dict(r) for r in store.conn.execute("SELECT * FROM attempts")]
    assert len(attempts) == 2
    assert all(a["attempt"] == 1 for a in attempts)

    kinds = _kinds(store)
    for k in ("packet_created", "attempt_created", "launch_intended",
              "launch_bound", "launch_running", "packet_running",
              "packet_reported", "wave_completed"):
        assert k in kinds

    # each packet's active launch is wired to its launch row
    for r in rows:
        assert r["active_launch_id"] in {l["launch_id"] for l in launches}


def test_run_wave_stale_result_rejected(store):
    class StaleBackend(zwave.MockBackend):
        def collect(self, handle, *, status="completed"):
            r = super().collect(handle, status=status)
            r["launch_id"] = "L000000000000"   # old launch id
            return r

    run_id, st = _executing_stage(store)
    sid = st["stage_id"]
    summary = zwave.run_wave(store, run_id, sid, 1,
                             [_packet("P01", ["src/a/**"])], StaleBackend())
    assert summary["reported"] == 0
    assert len(summary["rejected"]) == 1
    assert summary["rejected"][0]["reason"] == "stale_launch"
    assert summary["rejected"][0]["packet_id"] == "P01"

    rows = _packet_rows(store, run_id, sid)
    assert rows[0]["state"] == "RUNNING"       # NOT moved past RUNNING
    assert rows[0]["active_launch_id"]         # still bound to the live launch
    launches = [dict(r) for r in store.conn.execute("SELECT * FROM launches")]
    assert len(launches) == 1 and launches[0]["intent_state"] == "RUNNING"
    # the rejection is audited with its reason
    rej = [json.loads(r["detail_json"]) for r in store.conn.execute(
        "SELECT detail_json FROM events WHERE kind='result_rejected'")]
    assert len(rej) == 1 and rej[0]["reason"] == "stale_launch"


def test_run_wave_fences_and_validates(store):
    run_id, st = _executing_stage(store)
    sid = st["stage_id"]
    # overlapping write_scope without depends_on -> the whole wave is refused
    bad = [_packet("P01", ["src/x/**"]), _packet("P02", ["src/x/**"])]
    with pytest.raises(ValueError, match="validation"):
        zwave.run_wave(store, run_id, sid, 1, bad, zwave.MockBackend())
    assert _packet_rows(store, run_id, sid) == []      # nothing written (I4)
    # unknown dep -> refused
    with pytest.raises(ValueError, match="validation"):
        zwave.run_wave(store, run_id, sid, 1,
                       [_packet("P01", ["src/a/**"], depends_on=["P09"])],
                       zwave.MockBackend())
    # stale stage_revision in the proposal -> refused
    with pytest.raises(ValueError, match="stage_revision"):
        zwave.run_wave(store, run_id, sid, 7,
                       [_packet("P01", ["src/a/**"])], zwave.MockBackend())
    # stage must be EXECUTING (VOL-09 §1)
    run_id2 = store.create_run("another")
    st2 = zstage.create_stage(store, run_id2, "planning only", "NORMAL",
                              expected_head="h", dirty_digest="",
                              stage_base_ref="r", stage_base_tree="t")
    with pytest.raises(ValueError, match="EXECUTING"):
        zwave.run_wave(store, run_id2, st2["stage_id"], 1,
                       [_packet("P01", ["src/a/**"])], zwave.MockBackend())
    # dirty base blocks writable waves [I37, VOL-08 §3]
    run_id3 = store.create_run("dirty")
    st3 = zstage.create_stage(store, run_id3, "dirty base slice", "NORMAL",
                              expected_head="h", dirty_digest="sha256:abc",
                              stage_base_ref="r", stage_base_tree="t")
    zstage.transition_stage(store, run_id3, st3["stage_id"], "EXECUTING")
    with pytest.raises(ValueError, match="BLOCKED_DIRTY_BASE"):
        zwave.run_wave(store, run_id3, st3["stage_id"], 1,
                       [_packet("P01", ["src/a/**"])], zwave.MockBackend())


# ---- supersede_stage_revision (VOL-09 §2/§8) ---------------------------------

def test_supersede_stage_revision_fencing(store):
    run_id, st = _executing_stage(store)
    sid = st["stage_id"]
    zwave.run_wave(store, run_id, sid, 1,
                   [_packet("P01", ["src/a/**"]), _packet("P02", ["src/b/**"])],
                   zwave.MockBackend())
    old_rows = _packet_rows(store, run_id, sid)
    old_launch = {r["packet_id"]: r["active_launch_id"] for r in old_rows}

    res = zwave.supersede_stage_revision(store, run_id, sid, 2)
    assert res["stage_revision"] == 2
    assert sorted(res["superseded_packets"]) == ["P01", "P02"]
    assert zstage.get_stage(store, run_id, sid)["stage_revision"] == 2
    with pytest.raises(ValueError):   # must strictly increase
        zwave.supersede_stage_revision(store, run_id, sid, 2)

    rows = _packet_rows(store, run_id, sid)
    assert all(r["state"] == "SUPERSEDED" for r in rows)
    assert all(r["active_launch_id"] is None for r in rows)
    kinds = _kinds(store)
    assert "stage_revision_superseded" in kinds
    assert kinds.count("packet_superseded") == 2

    # every old-revision result is rejected by the I6 fence
    for r in rows:
        stale = {"launch_id": old_launch[r["packet_id"]],
                 "stage_revision": 1, "packet_revision": 1,
                 "status": "completed"}
        assert zwave.accept_result(r, stale)[0] is False
    # a direct stale-revision probe reports the specific reason
    ok, reason = zwave.accept_result(
        {"stage_revision": 3, "packet_revision": 1,
         "active_launch_id": "Lx", "state": "RUNNING"},
        {"launch_id": "Lx", "stage_revision": 2, "packet_revision": 1})
    assert (ok, reason) == (False, "stale_stage_revision")
    # results from a non-active launch are late/stale evidence only [I7]
    assert zwave.late_result_guard(rows[0], {"launch_id": "Lwhatever"}) is True

    # the replan also invalidates wave proposals pinned to the old revision
    with pytest.raises(ValueError, match="stage_revision"):
        zwave.run_wave(store, run_id, sid, 1,
                       [_packet("P03", ["src/c/**"])], zwave.MockBackend())
    # a fresh wave under the new revision launches cleanly beside them
    summary = zwave.run_wave(store, run_id, sid, 2,
                             [_packet("P03", ["src/c/**"], depends_on=["P01"])],
                             zwave.MockBackend(), wave="W2")
    assert summary["reported"] == 1
    p03 = [r for r in _packet_rows(store, run_id, sid) if r["packet_id"] == "P03"]
    assert p03[0]["state"] == "REPORTED"
    assert p03[0]["stage_revision"] == 2
