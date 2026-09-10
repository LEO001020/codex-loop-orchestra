# ============================================================================
# test_all_transitions.py — All 23 legal transitions of spec §3.3
# Purpose : Parameterized end-to-end path test: for every numbered transition
#           an event sequence is replayed through statemachine.py `step` and
#           the resulting state, transition number, exit code, and Sol-wake
#           silence (for legal paths) are asserted.
# ============================================================================
import pytest

RUN = ["planned", "dag_assert_pass", "dispatched"]                    # -> RUNNING
REP = RUN + ["subagent_stop"]                                         # -> REPORTED
ACC = REP + ["acceptance_pass"]                                       # -> ACCEPTED
WD = ACC + ["merged", "wave_complete"]                                # -> WAVE_DONE
SOL_W = WD + ["wave_summary"]                                         # -> SOL_ADJUDICATE
DLQ = RUN + ["exec_failed", "budget_exhausted"]                       # -> DEAD_LETTER

# (t, events, expected_state, needs_report, expected_rc, needs_enforce)
TRANSITIONS = [
    (1, ["planned"], "PLANNED", False, 0, False),
    (2, ["planned", "dag_assert_pass"], "DISPATCHABLE", False, 0, False),
    (3, RUN, "RUNNING", False, 0, False),
    (4, REP, "REPORTED", True, 0, False),
    (5, RUN + ["timeout"], "TIMED_OUT", False, 0, False),
    (6, RUN + ["exec_failed"], "FAILED", False, 0, False),
    (7, ACC, "ACCEPTED", True, 0, False),
    (8, REP + ["acceptance_fail"], "FAILED", True, 0, False),
    (9, RUN + ["exec_failed", "retry_dispatch"], "DISPATCHABLE", False, 0, False),
    (10, RUN + ["exec_failed", "duty_review"], "DUTY_REVIEW", False, 0, False),
    (11, RUN + ["exec_failed", "duty_review", "duty_retryable"],
     "RUNNING", False, 0, True),
    (12, RUN + ["exec_failed", "duty_review", "duty_fixable"],
     "RUNNING", False, 0, True),
    (13, RUN + ["exec_failed", "duty_review", "duty_terminal"],
     "DEAD_LETTER", False, 2, False),
    (14, RUN + ["timeout", "budget_exhausted"], "DEAD_LETTER", False, 2, False),
    (15, DLQ, "DEAD_LETTER", False, 2, False),
    (16, ACC + ["merged"], "MERGED", True, 0, False),
    (17, ACC + ["merge_conflict"], "MERGE_CONFLICT", True, 0, False),
    (18, WD, "WAVE_DONE", True, 0, False),
    (19, DLQ + ["dead_letter_summary"], "SOL_ADJUDICATE", False, 2, False),
    (20, ACC + ["merge_conflict", "conflict_pointer"],
     "SOL_ADJUDICATE", True, 0, False),
    (21, SOL_W, "SOL_ADJUDICATE", True, 0, False),
    (22, SOL_W + ["sol_replan"], "PLANNED", True, 0, False),
    (23, SOL_W + ["release_merge"], "DONE", True, 0, False),
]


@pytest.mark.parametrize("t,events,expected,needs_report,rc_expected,enforce",
                         TRANSITIONS, ids=["t%02d-%s" % (c[0], c[2])
                                           for c in TRANSITIONS])
def test_transition(loop, t, events, expected, needs_report, rc_expected, enforce):
    pid = "w1-t%02d" % t
    if needs_report:
        loop.write_report(pid)          # subagent_stop gate (t4) needs the file
    if enforce:
        loop.write_config(duty_enforce=True)   # t11/t12 routing switch
    for ev in events:
        loop.append_event(pid, ev)
    rc, states = loop.step()
    assert states[pid] == expected, "t%d: got %s" % (t, states[pid])
    assert rc == rc_expected
    hist = loop.history(pid)
    assert hist[-1]["t"] == t, "last transition number must be %d" % t
    assert len(hist) == len(events), "one ledger entry per event"
    # ALL 23 transitions are on-table: none of them is an out-of-band Sol
    # wake (dead-letter FILES for t13/14/15 are produced upstream by retry.py;
    # the state machine only flags DLQ landings via rc=2).
    assert loop.sol_wakes() == []


def test_full_table_is_exactly_23_transitions():
    assert len(TRANSITIONS) == 23
    assert [c[0] for c in TRANSITIONS] == list(range(1, 24))
