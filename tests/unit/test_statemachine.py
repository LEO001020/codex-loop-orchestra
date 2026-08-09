# ============================================================================
# test_statemachine.py — Unit tests for harness/statemachine.py
# Cases: normal main chain, off-table -> DEAD_LETTER + Sol wake (fail-visible),
#        report-file fallback (reconcile), report-missing-on-stop gate,
#        duty enforce toggle both ways, malformed event line, cursor
#        idempotency, state/wave-check subcommands.
# ============================================================================
import json


MAIN_CHAIN = ["planned", "dag_assert_pass", "dispatched", "subagent_stop",
              "acceptance_pass", "merged", "wave_complete"]


def test_normal_main_chain_reaches_wave_done(loop):
    pid = "w1-p01"
    loop.write_report(pid)                       # gate for subagent_stop (t4)
    for ev in MAIN_CHAIN:
        loop.append_event(pid, ev)
    rc, states = loop.step()
    assert rc == 0, "no dead letters expected on the happy path"
    assert states[pid] == "WAVE_DONE"
    assert loop.sol_wakes() == []
    # history carries the transition numbers 1,2,3,4,7,16,18 in order
    nums = [h["t"] for h in loop.history(pid)]
    assert nums == [1, 2, 3, 4, 7, 16, 18]


def test_off_table_event_goes_dead_letter_and_wakes_sol(loop):
    pid = "w1-p02"
    loop.append_event(pid, "planned")
    loop.append_event(pid, "merged")             # PLANNED + merged = off-table
    rc, states = loop.step()
    assert rc == 2                               # dead-letters produced this step
    assert states[pid] == "DEAD_LETTER"
    assert len(loop.sol_wakes()) == 1
    assert (loop.data / "dead_letters" / ("%s.json" % pid)).exists()
    dl = json.loads((loop.data / "dead_letters" / ("%s.json" % pid)).read_text())
    assert dl["reason"] == "off_table_event"
    assert loop.escalations(level="SOL_WAKE"), "SOL_WAKE must be logged"


def test_report_file_fallback_advances_running_to_reported(loop):
    pid = "w1-p03"
    for ev in ("planned", "dag_assert_pass", "dispatched"):
        loop.append_event(pid, ev)
    loop.write_report(pid)                       # report landed, hook event LOST
    rc, states = loop.step()                     # step runs reconcile() too
    assert rc == 0
    assert states[pid] == "REPORTED"
    assert loop.history(pid)[-1]["via"] == "report_file_fallback"


def test_stop_without_report_is_dead_letter(loop):
    pid = "w1-p04"
    for ev in ("planned", "dag_assert_pass", "dispatched", "subagent_stop"):
        loop.append_event(pid, ev)               # NO report file written
    rc, states = loop.step()
    assert rc == 2
    assert states[pid] == "DEAD_LETTER"
    dl = json.loads((loop.data / "dead_letters" / ("%s.json" % pid)).read_text())
    assert dl["reason"] == "report_missing_on_stop"


def test_duty_ruling_record_only_in_f2_cold_start(loop):
    pid = "w1-p05"
    loop.write_config(duty_enforce=False)        # explicit F2 default
    for ev in ("planned", "dag_assert_pass", "dispatched", "exec_failed",
               "duty_review", "duty_retryable"):
        loop.append_event(pid, ev)
    rc, states = loop.step()
    assert rc == 2
    assert states[pid] == "DEAD_LETTER"          # original dead-letter path kept
    assert loop.escalations(level="DUTY_RECORD_ONLY")


def test_duty_ruling_routes_when_enforce_true(loop):
    pid = "w1-p06"
    loop.write_config(duty_enforce=True)         # single-key production switch
    for ev in ("planned", "dag_assert_pass", "dispatched", "exec_failed",
               "duty_review", "duty_retryable"):
        loop.append_event(pid, ev)
    rc, states = loop.step()
    assert rc == 0
    assert states[pid] == "RUNNING"              # transition 11 executed


def test_malformed_event_line_is_fail_visible(loop):
    (loop.data / "events.ndjson").write_text("this is not json\n")
    rc, states = loop.step()
    assert rc == 2                               # never silently discarded
    assert states.get("malformed") == "DEAD_LETTER"


def test_cursor_makes_step_idempotent(loop):
    pid = "w1-p07"
    loop.append_event(pid, "planned")
    rc1, states1 = loop.step()
    assert states1[pid] == "PLANNED"
    rc2, states2 = loop.step()                   # no new events -> no re-processing
    assert states2[pid] == "PLANNED"
    assert len(loop.history(pid)) == 1


def test_state_subcommand_and_wave_check(loop):
    pid = "w1-p08"
    p = loop.sm("state", "--packet", pid)
    assert p.stdout.strip() == "NONE"
    # wave-check: packet MERGED + report present -> ready
    loop.write_packet(pid)
    loop.write_report(pid)
    led = loop.ledger()
    led["packets"][pid] = {"state": "MERGED", "history": [], "attempts": 0}
    loop.set_ledger(led)
    p = loop.sm("wave-check")
    assert p.returncode == 0 and "WAVE_DONE_READY" in p.stdout
    # missing report -> incomplete (fail-visible fallback channel)
    (loop.data / "reports" / pid / "report.json").unlink()
    p = loop.sm("wave-check")
    assert p.returncode == 1 and "WAVE_INCOMPLETE" in p.stdout
