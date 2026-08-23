# ============================================================================
# test_adversarial.py — Adversarial paths for statemachine.py (spec §7)
# Cases: ① unknown event -> DEAD_LETTER + Sol wake (never silent)
#        ② hook event lost but report file present -> REPORTED (reconcile)
#        ③ report missing but hook says done -> DEAD_LETTER (forged claim)
#        plus: dead-letter file forensics, duplicate stop after DLQ stays dead.
# ============================================================================
import json


def test_unknown_event_dead_letters_and_wakes_sol(loop):
    """① An event outside the 23-transition table is fail-visible."""
    pid = "adv-unknown"
    loop.append_event(pid, "planned")
    loop.append_event(pid, "gamma_ray_burst", {"who": "nobody"})
    rc, states = loop.step()
    assert rc == 2
    assert states[pid] == "DEAD_LETTER"
    # Sol wake artifact + escalation log line + dead-letter file all present
    wakes = loop.sol_wakes()
    assert len(wakes) == 1 and pid in wakes[0].name
    assert loop.escalations(level="SOL_WAKE")
    dl = json.loads((loop.data / "dead_letters" / ("%s.json" % pid)).read_text())
    assert dl["reason"] == "off_table_event"
    assert dl["detail"]["event"] == "gamma_ray_burst"
    assert dl["detail"]["from_state"] == "PLANNED"   # forensics: where it happened


def test_hook_lost_report_present_reconciles_to_reported(loop):
    """② Stop-hook event never arrived; the report file is ground truth."""
    pid = "adv-hooklost"
    for ev in ("planned", "dag_assert_pass", "dispatched"):
        loop.append_event(pid, ev)
    rc, states = loop.step()
    assert states[pid] == "RUNNING"
    loop.write_report(pid)                      # report lands, no hook event
    rc, states = loop.step()                    # reconcile() sweep inside step
    assert rc == 0
    assert states[pid] == "REPORTED"
    hist = loop.history(pid)
    assert hist[-1]["via"] == "report_file_fallback"
    assert hist[-1]["t"] == 4
    assert loop.sol_wakes() == []               # recovered, no escalation


def test_report_missing_but_hook_claims_done_dead_letters(loop):
    """③ A completion claim without the report artifact is a forged output."""
    pid = "adv-forged"
    for ev in ("planned", "dag_assert_pass", "dispatched", "subagent_stop"):
        loop.append_event(pid, ev)              # deliberately NO report file
    rc, states = loop.step()
    assert rc == 2
    assert states[pid] == "DEAD_LETTER"
    dl = json.loads((loop.data / "dead_letters" / ("%s.json" % pid)).read_text())
    assert dl["reason"] == "report_missing_on_stop"
    assert len(loop.sol_wakes()) == 1           # Sol is woken, never silent


def test_events_after_dead_letter_stay_off_table(loop):
    """A dead packet cannot resurrect itself by replaying happy-path events."""
    pid = "adv-zombie"
    for ev in ("planned", "dag_assert_pass", "dispatched", "subagent_stop"):
        loop.append_event(pid, ev)              # -> DEAD_LETTER (no report)
    rc, _ = loop.step()
    assert rc == 2
    loop.write_report(pid)                      # too late: packet already dead
    loop.append_event(pid, "acceptance_pass")   # DEAD_LETTER+acceptance_pass
    rc, states = loop.step()
    assert rc == 2                              # off-table again, still visible
    assert states[pid] == "DEAD_LETTER"
