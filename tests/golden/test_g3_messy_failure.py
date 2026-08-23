# ============================================================================
# test_g3_messy_failure.py — Golden case G3 (spec §7)
# A packet fails with unclassifiable gibberish (no retry-class regex match).
# The failure routes to the duty officer partition; in F2 cold start
# (duty_officer.enforce=false) the officer's ruling is RECORDED ONLY and the
# packet still dead-letters, waking Sol. Pass standard: regex no-match ->
# DUTY_REVIEW, F2 records only, packet -> DEAD_LETTER + Sol wake. A control
# run with enforce=true shows the same ruling WOULD route back to RUNNING.
# ============================================================================
import json

from tests.conftest import PY

GIBBERISH = "zorblatt quux discombobulated 0xDEADBEEF wibble (unclassifiable)"


def drive_to_duty_review(loop, pid, wt):
    for ev in ("planned", "dag_assert_pass", "dispatched"):
        loop.append_event(pid, ev)
    loop.step()
    r = loop.mock_spawn(pid, wt, scenario="messy_failure")
    assert r.returncode == 1
    rc, states = loop.step()
    assert states[pid] == "FAILED"
    # retry.py: gibberish matches NO class -> duty_review, never silent
    p = loop.run([PY, loop.harness("retry.py"), "--packet", pid,
                  "--error", GIBBERISH])
    assert p.returncode == 4
    decision = json.loads(p.stdout.strip().splitlines()[-1])
    assert decision["action"] == "duty_review_offtable"
    assert decision["class"] is None
    rc, states = loop.step()
    assert states[pid] == "DUTY_REVIEW"


def officer_ruling(loop, enforce):
    """Valid ENUM ruling through the S6 whitelist gate."""
    ruling = {"class": "retryable",
              "evidence": ["report.json:1", "events.ndjson line 5"],
              "confidence": 0.9, "progress_ledger_delta": {}}
    rfile = loop.root / "ruling.json"
    rfile.write_text(json.dumps(ruling))
    return loop.run([PY, loop.harness("duty_gate.py"), "--ruling", rfile,
                     "--enforce", enforce])


def test_g3_messy_failure_f2_records_only_and_dead_letters(repo_loop):
    loop = repo_loop
    pid = "w1-messy"
    loop.write_packet(pid, paths=["src/alpha/"])
    wt = loop.allocate(pid)
    loop.write_config(duty_enforce=False)       # F2 cold start
    drive_to_duty_review(loop, pid, wt)

    # duty gate: ruling is VALID but only RECORDED in F2 (exit 3)
    g = officer_ruling(loop, enforce="false")
    assert g.returncode == 3
    assert json.loads(g.stdout)["gate"] == "RECORDED_NOT_ENFORCED"

    # the recorded ruling event does NOT route: packet dead-letters + Sol wake
    loop.append_event(pid, "duty_retryable")
    rc, states = loop.step()
    assert rc == 2
    assert states[pid] == "DEAD_LETTER"
    assert loop.escalations(level="DUTY_RECORD_ONLY"), "ruling must be logged"
    assert len(loop.sol_wakes()) == 1           # Sol adjudicates, never silent
    dl = json.loads((loop.data / "dead_letters" / ("%s.json" % pid)).read_text())
    assert dl["reason"] == "duty_ruling_recorded_not_routed"


def test_g3_control_enforce_true_routes_back_to_running(repo_loop):
    loop = repo_loop
    pid = "w1-messy2"
    loop.write_packet(pid, paths=["src/alpha/"])
    wt = loop.allocate(pid)
    loop.write_config(duty_enforce=True)        # production switch flipped
    drive_to_duty_review(loop, pid, wt)

    g = officer_ruling(loop, enforce="true")
    assert g.returncode == 0                    # VALID and enforced

    # re-dispatch contract: clear the failed attempt's report slot so the
    # reconcile fallback cannot re-advance the packet on the stale report
    (loop.data / "reports" / pid / "report.json").unlink()
    loop.append_event(pid, "duty_retryable")
    rc, states = loop.step()
    assert rc == 0
    assert states[pid] == "RUNNING"             # transition 11: ruling routed
    assert loop.sol_wakes() == []
