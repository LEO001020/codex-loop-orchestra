"""test_statemachine_v2.py — the extended deterministic state machine.

Covers: all 12 new transitions (t27–t38) individually, SOL_ADJUDICATE
non-terminality, preserved t22/t23 semantics, the cursor race fix (P1-8),
the watchdog blind-spot fix (P1-9), the structural table invariants, and
off-table (invalid) transition rejection.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from conftest import emit_event, make_root

import statemachine_v2 as smv2
from orchestration_common import LoopPaths
from statemachine_v2 import (
    L2_STATES,
    REPLAN_CAP,
    SCHEMA,
    STATES,
    T,
    TERMINAL,
    TIMEOUT_RETRY_MAX,
    StateMachine,
    TransitionError,
    validate_transition_table,
)


@pytest.fixture
def sm(tmp_path):
    root = make_root(tmp_path)
    return StateMachine(LoopPaths.resolve(root))


def _pkt(state: str, **over) -> dict:
    pkt = {"state": state, "history": [], "attempts": 0}
    pkt.update(over)
    return pkt


def _led(pid: str, state: str, **over) -> dict:
    return {"packets": {pid: _pkt(state, **over)}}


# ---------------------------------------------------------------------------
# t27–t38: every new transition individually
# ---------------------------------------------------------------------------
NEW_TRANSITIONS = [
    # (t, from_state, event, to_state)
    (27, "NONE", "skeleton_ready", "EXPAND_K3"),
    (28, "EXPAND_K3", "expansion_valid", "PLANNED"),
    (29, "EXPAND_K3", "expansion_invalid", "SOL_ADJUDICATE"),
    (30, "REPORTED", "l2_requested", "L2_VERIFY"),
    (31, "L2_VERIFY", "verdict_pass", "ACCEPTED"),
    (32, "L2_VERIFY", "verdict_redo", "FAILED"),
    (33, "L2_VERIFY", "verdict_escalate_l2_5", "L2_RANK"),
    (34, "L2_VERIFY", "verdict_escalate_l3", "SOL_ADJUDICATE"),
    (35, "L2_VERIFY", "exec_failed", "FAILED"),
    (36, "L2_RANK", "best_candidate", "L2_VERIFY"),
    (37, "TIMED_OUT", "retry_dispatch", "RUNNING"),
    (38, "DEAD_LETTER", "duty_triage", "DUTY_REVIEW"),
]


@pytest.mark.parametrize("num,frm,event,to", NEW_TRANSITIONS,
                         ids=["t%d" % n for n, *_ in NEW_TRANSITIONS])
def test_new_transition_on_table(num, frm, event, to):
    assert T[(frm, event)] == (to, num)


@pytest.mark.parametrize("num,frm,event,to", NEW_TRANSITIONS,
                         ids=["t%d_apply" % n for n, *_ in NEW_TRANSITIONS])
def test_new_transition_applies(sm, num, frm, event, to):
    led = _led("p1", frm)
    result = sm.apply_event(led, {"packet_id": "p1", "event": event})
    assert result == to
    assert led["packets"]["p1"]["state"] == to
    assert led["packets"]["p1"]["history"][-1]["t"] == num


def test_t37_timeout_retry_capped(sm):
    """First timeout retries (t37); the second dead-letters (cap=1)."""
    assert TIMEOUT_RETRY_MAX == 1
    led = _led("p1", "TIMED_OUT")
    assert sm.apply_event(led, {"packet_id": "p1",
                                "event": "retry_dispatch"}) == "RUNNING"
    assert led["packets"]["p1"]["attempts"] == 1
    led["packets"]["p1"]["state"] = "TIMED_OUT"
    assert sm.apply_event(led, {"packet_id": "p1",
                                "event": "retry_dispatch"}) == "DEAD_LETTER"
    assert (sm.paths.data / "dead_letters" / "p1.json").exists()


def test_t35_l2_attempts_cap_escalates_l3(sm):
    """A second L2 exec failure escalates to bounded Sol adjudication."""
    led = _led("p1", "L2_VERIFY")
    assert sm.apply_event(led, {"packet_id": "p1",
                                "event": "exec_failed"}) == "FAILED"
    led["packets"]["p1"]["state"] = "L2_VERIFY"
    assert sm.apply_event(led, {"packet_id": "p1",
                                "event": "exec_failed"}) == "SOL_ADJUDICATE"
    assert led["packets"]["p1"]["history"][-1]["via"] == "l2_attempts_exhausted"


# ---------------------------------------------------------------------------
# terminal set / SOL_ADJUDICATE routability
# ---------------------------------------------------------------------------
def test_sol_adjudicate_not_terminal():
    assert "SOL_ADJUDICATE" not in TERMINAL
    assert TERMINAL == frozenset({"MERGED", "DEAD_LETTER", "DONE"})


def test_declared_manifest_agrees_with_code():
    manifest = json.loads(
        (Path(smv2.__file__).resolve().parent.parent / "config" /
         "statemachine_v2_transitions.json").read_text(encoding="utf-8"))
    assert "SOL_ADJUDICATE" not in manifest["terminal_states"]
    assert sorted(manifest["terminal_states"]) == sorted(TERMINAL)
    for tname, spec in manifest["transitions"].items():
        num = int(tname[1:])
        assert T[(spec["from"], spec["event"])] == (spec["to"], num)


def test_t22_replan_still_works_and_capped(sm):
    """SOL_ADJUDICATE → PLANNED via sol_replan, capped at REPLAN_CAP (H-04)."""
    led = _led("p1", "SOL_ADJUDICATE")
    for _ in range(REPLAN_CAP):
        assert sm.apply_event(led, {"packet_id": "p1",
                                    "event": "sol_replan"}) == "PLANNED"
        led["packets"]["p1"]["state"] = "SOL_ADJUDICATE"
    # cap exceeded: forced to the human L4 gate, packet stays adjudicating
    assert sm.apply_event(led, {"packet_id": "p1",
                                "event": "sol_replan"}) == "SOL_ADJUDICATE"
    l4 = list((sm.paths.data / "l4_queue").glob("*_p1.md"))
    assert l4, "replan cap must queue a direct_l4 human summary"


def test_t23_human_release_still_works(sm):
    led = _led("p1", "SOL_ADJUDICATE")
    assert sm.apply_event(led, {"packet_id": "p1",
                                "event": "release_merge"}) == "DONE"
    assert led["packets"]["p1"]["history"][-1]["t"] == 23


# ---------------------------------------------------------------------------
# cursor race fix (P1-8)
# ---------------------------------------------------------------------------
def test_cursor_advances_only_to_consumed_offset(tmp_path):
    root = make_root(tmp_path)
    sm = StateMachine(LoopPaths.resolve(root))
    emit_event(root, "p1", "planned")
    assert sm.step()["p1"] == "PLANNED"
    events = root / "data" / "events.ndjson"
    cursor = root / "data" / ".sm_cursor"
    consumed = int(cursor.read_text())
    assert consumed == events.stat().st_size

    # A concurrent writer appends a PARTIAL line (no trailing newline):
    # the cursor must NOT advance past it — the event is re-read next step.
    partial = json.dumps({"ts": time.time(), "packet_id": "p1",
                          "event": "dag_assert_pass"})
    with events.open("a", encoding="utf-8") as fh:
        fh.write(partial)  # no newline: mid-write snapshot
    assert sm.step()["p1"] == "PLANNED", "partial line must not be applied"
    assert int(cursor.read_text()) == consumed, \
        "cursor must never jump past a partial concurrent append"

    with events.open("a", encoding="utf-8") as fh:
        fh.write("\n")  # writer finishes the line
    assert sm.step()["p1"] == "DISPATCHABLE", "completed line applied on re-read"


def test_concurrent_append_during_step_not_skipped(tmp_path):
    """Events appended after the read loop (e.g. by the watchdog) are
    re-read next step instead of being skipped by a getsize() jump."""
    root = make_root(tmp_path)
    sm = StateMachine(LoopPaths.resolve(root), job_max_runtime_seconds=10)
    emit_event(root, "p1", "planned")
    emit_event(root, "p1", "dag_assert_pass")
    emit_event(root, "p1", "dispatched")
    # spawn far in the past => the watchdog fires and APPENDS a timeout event
    times = {"p1": {"ts": time.time() - 999, "mode": "legacy"}}
    (root / "data" / "spawn_times.json").write_text(json.dumps(times))
    states = sm.step()
    assert states["p1"] == "TIMED_OUT"
    assert sm.dead_this_step == 0
    # next step re-reads the watchdog's own timeout append: it must be an
    # idempotent confirmation, never an off-table dead letter.
    states = sm.step()
    assert states["p1"] == "TIMED_OUT"
    assert sm.dead_this_step == 0
    led = sm.load_ledger()
    vias = [h.get("via") for h in led["packets"]["p1"]["history"]]
    assert "timeout_confirmed_idempotent" in vias


# ---------------------------------------------------------------------------
# watchdog blind spot fix (P1-9)
# ---------------------------------------------------------------------------
def test_watchdog_synthetic_spawn_ts_from_history(sm):
    """RUNNING packet absent from spawn_times.json still times out using the
    ledger's transition-to-RUNNING timestamp."""
    sm.job_max_runtime_seconds = 10
    led = {"packets": {"p1": {
        "state": "RUNNING", "attempts": 1,
        "history": [{"ts": time.time() - 500, "to": "RUNNING", "via": "dispatched"}],
    }}}
    fired = sm.watchdog(led)
    assert fired == ["p1"]
    assert led["packets"]["p1"]["state"] == "TIMED_OUT"


def test_watchdog_fresh_synthetic_ts_does_not_fire(sm):
    sm.job_max_runtime_seconds = 3600
    led = {"packets": {"p1": {
        "state": "RUNNING", "attempts": 1,
        "history": [{"ts": time.time() - 5, "to": "RUNNING", "via": "dispatched"}],
    }}}
    assert sm.watchdog(led) == []
    assert led["packets"]["p1"]["state"] == "RUNNING"


def test_watchdog_no_history_no_crash(sm):
    """No spawn record AND no RUNNING history: elapsed stays None; no fire,
    no exception (the v1 blind spot at least fails safe)."""
    sm.job_max_runtime_seconds = 1
    led = {"packets": {"p1": {"state": "RUNNING", "history": [], "attempts": 0}}}
    assert sm.watchdog(led) == []


# ---------------------------------------------------------------------------
# transition-table structural invariants
# ---------------------------------------------------------------------------
def test_validate_transition_table_passes_on_shipped_table():
    validate_transition_table()  # must not raise (also runs at import)


def test_validate_rejects_missing_t37():
    table = {k: v for k, v in T.items() if v[1] != 37}
    with pytest.raises(TransitionError):
        validate_transition_table(table)


def test_validate_rejects_duplicate_numbers():
    table = dict(T)
    table[("PLANNED", "bogus_event")] = ("DISPATCHABLE", 2)  # duplicate t2
    with pytest.raises(TransitionError, match="duplicate"):
        validate_transition_table(table)


def test_validate_rejects_unknown_state():
    table = dict(T)
    table[("NOWHERE", "x")] = ("PLANNED", 99)
    with pytest.raises(TransitionError, match="unknown from-state"):
        validate_transition_table(table)


def test_validate_rejects_l2_edge_into_merged():
    """L2 states carry BLOCK/ESCALATE-only power — an edge into MERGED/DONE
    must be structurally unrepresentable."""
    table = dict(T)
    table[("L2_VERIFY", "sneaky_release")] = ("MERGED", 99)
    with pytest.raises(TransitionError, match="BLOCK/ESCALATE"):
        validate_transition_table(table)


def test_validate_rejects_sol_adjudicate_exit_drift():
    table = dict(T)
    table[("SOL_ADJUDICATE", "third_exit")] = ("RUNNING", 99)
    with pytest.raises(TransitionError, match="SOL_ADJUDICATE exits drifted"):
        validate_transition_table(table)


def test_l2_states_declared():
    assert L2_STATES == frozenset({"EXPAND_K3", "L2_VERIFY", "L2_RANK"})
    assert L2_STATES <= STATES


def test_schema_id():
    assert SCHEMA == "codex-loop-statemachine/v2"


# ---------------------------------------------------------------------------
# invalid transitions rejected (off-table => DEAD_LETTER, fail-visible)
# ---------------------------------------------------------------------------
def test_off_table_event_dead_letters(sm):
    led = _led("p1", "PLANNED")
    result = sm.apply_event(led, {"packet_id": "p1", "event": "merged"})
    assert result == "DEAD_LETTER"
    dl = json.loads((sm.paths.data / "dead_letters" / "p1.json").read_text())
    assert dl["reason"] == "off_table_event"
    assert list((sm.paths.data / "sol_wake").glob("*_p1.md")), \
        "off-table dead letter must be fail-visible via a SOL wake"


def test_t4_gated_on_report_file(sm):
    """subagent_stop without a current report file dead-letters (two truths)."""
    led = _led("p1", "RUNNING")
    result = sm.apply_event(led, {"packet_id": "p1", "event": "subagent_stop"})
    assert result == "DEAD_LETTER"
    # with the report present, the transition passes
    led2 = _led("p2", "RUNNING")
    rdir = sm.paths.data / "reports" / "p2"
    rdir.mkdir(parents=True)
    (rdir / "report.json").write_text("{}")
    assert sm.apply_event(led2, {"packet_id": "p2",
                                 "event": "subagent_stop"}) == "REPORTED"


def test_stale_generation_event_ignored(sm):
    """Events stamped with an older attempt than the packet's are ignored."""
    led = _led("p1", "RUNNING", attempts=2)
    result = sm.apply_event(led, {"packet_id": "p1", "event": "exec_failed",
                                  "attempt": 1})
    assert result == "RUNNING"
    assert led["packets"]["p1"]["history"][-1]["via"] == "stale_generation_ignored"


def test_invalid_packet_id_refused(sm):
    with pytest.raises(SystemExit):
        sm.to_dead_letter({"packets": {}}, "../escape", "x", {})
