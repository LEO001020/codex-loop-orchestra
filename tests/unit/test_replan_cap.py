# ============================================================================
# test_replan_cap.py — Unit tests for the H-04 fix in harness/statemachine.py:
# transition 22 (SOL_ADJUDICATE -> PLANNED) is counted per packet in
# data/replan_counters.json (same bump pattern as the trigger_eval L3 cap);
# over 2 replans the transition is REFUSED and a direct_l4 summary is queued
# for the human release gate. Counter I/O failure counts as exceeded — fail
# toward human, never toward unlimited replanning (same direction as L3 cap).
# Cases: 1st and 2nd replan proceed normally (normal), 3rd replan is forced
#        to direct_l4 (boundary), counter I/O failure treated as over-cap
#        (failure injection).
# ============================================================================
import json


def seed_adjudicate(loop, pid):
    led = loop.ledger()
    led["packets"][pid] = {"state": "SOL_ADJUDICATE", "history": [], "attempts": 0}
    loop.set_ledger(led)


def replan_once(loop, pid):
    loop.append_event(pid, "sol_replan")
    return loop.step()


def l4_summaries(loop):
    d = loop.data / "l4_queue"
    return sorted(d.glob("*.md")) if d.exists() else []


def test_first_two_replans_proceed_normally(loop):
    pid = "p1"
    for expected_count in (1, 2):
        seed_adjudicate(loop, pid)
        rc, states = replan_once(loop, pid)
        assert rc == 0
        assert states[pid] == "PLANNED"              # t22 taken normally
        counters = json.loads((loop.data / "replan_counters.json").read_text())
        assert counters[pid] == expected_count
    assert l4_summaries(loop) == []                  # no escalation yet


def test_third_replan_is_forced_to_direct_l4(loop):
    pid = "p2"
    for _ in range(2):
        seed_adjudicate(loop, pid)
        replan_once(loop, pid)
    seed_adjudicate(loop, pid)
    rc, states = replan_once(loop, pid)              # 3rd: over cap=2
    assert states[pid] == "SOL_ADJUDICATE"           # PLANNED transition refused
    assert loop.history(pid)[-1]["via"] == "replan_cap_forced_l4"
    assert len(l4_summaries(loop)) == 1              # human-gate summary queued
    body = l4_summaries(loop)[0].read_text(encoding="utf-8")
    assert "replan_cap_exceeded" in body and pid in body
    l4_logs = loop.escalations(level="DIRECT_L4")
    assert l4_logs and l4_logs[-1]["reason"] == "replan_cap_exceeded"


def test_counter_io_failure_counts_as_exceeded(loop):
    pid = "p3"
    # unreadable counter state: fail toward the human gate on the FIRST replan
    (loop.data / "replan_counters.json").write_text("{not valid json")
    seed_adjudicate(loop, pid)
    rc, states = replan_once(loop, pid)
    assert states[pid] == "SOL_ADJUDICATE"           # never PLANNED
    assert loop.history(pid)[-1]["via"] == "replan_cap_forced_l4"
    assert len(l4_summaries(loop)) == 1
