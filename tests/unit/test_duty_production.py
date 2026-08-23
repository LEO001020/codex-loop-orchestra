# ============================================================================
# test_duty_production.py 鈥?DUTY_REVIEW production wiring
# Chain under test: retry.py routes a failure to DUTY_REVIEW (event + ticket)
# -> statemachine applies FAILED -> DUTY_REVIEW -> duty_driver.py drains the
# retry ticket and adjudicates a ruling through duty_route.py / duty_gate.py
# -> exactly one duty_retryable|duty_fixable|duty_terminal event -> state
# machine enforces: enforce=true routes retryable/fixable back to RUNNING,
# enforce=false records only and dead-letters; illegal/low-confidence
# rulings are terminal and dead-letter (fail-visible); re-runs are
# idempotent (never a second adjudication event).
# ============================================================================
import json

import pytest

from tests.conftest import PY


def seed_failed(loop, pid):
    led = loop.ledger()
    led["packets"][pid] = {"state": "FAILED", "history": [], "attempts": 1}
    loop.set_ledger(led)


def route_to_duty_review(loop, pid, error):
    p = loop.run([PY, loop.harness("retry.py"), "--packet", pid, "--error", error])
    assert p.returncode == 4, p.stdout + p.stderr
    ticket = loop.data / "duty_review" / ("%s.json" % pid)
    assert ticket.exists(), "retry.py must queue a duty ticket"
    return json.loads(ticket.read_text())


def write_ruling(tmp_path, class_="retryable", confidence=0.9, fix_hint=None):
    ruling = {"class": class_, "evidence": ["report.json:1"],
              "confidence": confidence, "progress_ledger_delta": {}}
    if fix_hint is not None:
        ruling["fix_hint"] = fix_hint
    path = tmp_path / "ruling.json"
    path.write_text(json.dumps(ruling))
    return path


def run_driver(loop, *args):
    return loop.run([PY, loop.harness("duty_driver.py"), *args])


def test_retry_ticket_driver_routes_retryable_back_to_running(loop, tmp_path):
    pid = "p-route"
    seed_failed(loop, pid)
    ticket = route_to_duty_review(loop, pid, "zorblatt quux discombobulated")
    assert ticket["why"] == "regex_no_match"
    rc, states = loop.step()
    assert states[pid] == "DUTY_REVIEW"

    loop.write_config(duty_enforce=True)      # production switch
    ruling = write_ruling(tmp_path)
    p = run_driver(loop, "--ruling", ruling, "--enforce", "true")
    assert p.returncode == 0, p.stdout + p.stderr
    assert loop.events()[-1]["event"] == "duty_retryable"

    rc, states = loop.step()
    assert rc == 0
    assert states[pid] == "RUNNING"           # transition 11, enforced
    assert loop.escalations(level="DUTY_RECORD_ONLY") == []


def test_fixable_ruling_with_hint_routes_to_running(loop, tmp_path):
    pid = "p-fix"
    seed_failed(loop, pid)
    ticket = route_to_duty_review(loop, pid, "SyntaxError: invalid syntax line 3")
    assert ticket["why"] == "class_action_duty_review"
    loop.step()

    loop.write_config(duty_enforce=True)
    ruling = write_ruling(tmp_path, class_="fixable", fix_hint="import os")
    p = run_driver(loop, "--ruling", ruling, "--enforce", "true")
    assert p.returncode == 0, p.stdout + p.stderr
    assert loop.events()[-1]["event"] == "duty_fixable"
    rc, states = loop.step()
    assert states[pid] == "RUNNING"           # transition 12, enforced


def test_enforce_false_records_only_and_dead_letters(loop, tmp_path):
    pid = "p-record"
    seed_failed(loop, pid)
    route_to_duty_review(loop, pid, "gorble warble 0xBAD")
    loop.step()

    loop.write_config(duty_enforce=False)     # F2 cold start
    p = run_driver(loop, "--ruling", write_ruling(tmp_path), "--enforce", "false")
    assert p.returncode == 0, p.stdout + p.stderr
    assert loop.events()[-1]["event"] == "duty_retryable"

    rc, states = loop.step()
    assert rc == 2
    assert states[pid] == "DEAD_LETTER"       # record-only path, never routed
    assert loop.escalations(level="DUTY_RECORD_ONLY"), "ruling must be logged"
    dl = json.loads((loop.data / "dead_letters" / ("%s.json" % pid)).read_text())
    assert dl["reason"] == "duty_ruling_recorded_not_routed"


def test_low_confidence_ruling_is_terminal_fail_visible(loop, tmp_path):
    pid = "p-low"
    seed_failed(loop, pid)
    route_to_duty_review(loop, pid, "wibble wobble 0xCAFE")
    loop.step()

    loop.write_config(duty_enforce=True)
    ruling = write_ruling(tmp_path, confidence=0.2)   # below theta 0.7
    p = run_driver(loop, "--ruling", ruling, "--enforce", "true")
    assert p.returncode == 1
    assert loop.events()[-1]["event"] == "duty_terminal"
    rc, states = loop.step()
    assert states[pid] == "DEAD_LETTER"       # transition 13, fail-visible


def test_driver_is_idempotent(loop, tmp_path):
    pid = "p-idem"
    seed_failed(loop, pid)
    route_to_duty_review(loop, pid, "quux florp discombobulated")
    loop.step()
    ruling = write_ruling(tmp_path)

    p = run_driver(loop, "--ruling", ruling, "--enforce", "true")
    assert p.returncode == 0
    before = len(loop.events())
    p = run_driver(loop, "--ruling", ruling, "--enforce", "true")
    assert p.returncode == 3                  # SKIP: prior duty_* outcome
    assert len(loop.events()) == before       # no duplicate adjudication


def test_single_packet_mode_adjudicates_and_skips_after_route(loop, tmp_path):
    pid = "p-single"
    seed_failed(loop, pid)
    route_to_duty_review(loop, pid, "flibbertigibbet nonsense")
    loop.step()
    ruling = write_ruling(tmp_path)

    p = run_driver(loop, "--packet", pid, "--error", "flibbertigibbet nonsense",
                   "--ruling", ruling, "--enforce", "true")
    assert p.returncode == 0, p.stdout + p.stderr
    assert loop.events()[-1]["event"] == "duty_retryable"
    before = len(loop.events())
    p = run_driver(loop, "--packet", pid, "--error", "flibbertigibbet nonsense",
                   "--ruling", ruling, "--enforce", "true")
    assert p.returncode == 3
    assert len(loop.events()) == before


@pytest.mark.parametrize("corrupt", ["missing", "empty"])
def test_unusable_ticket_terminal_once_and_idempotent(loop, corrupt):
    # Malformed/empty-error tickets must reuse the state + prior-outcome
    # idempotency gates: first drain appends ONE duty_terminal, a repeated
    # drain skips (rc 3) and never appends a second terminal event.
    pid = "p-bad-" + corrupt
    seed_failed(loop, pid)
    route_to_duty_review(loop, pid, "gorble wobble 0xFEED")
    loop.step()                                 # FAILED -> DUTY_REVIEW
    ticket_path = loop.data / "duty_review" / ("%s.json" % pid)
    ticket = json.loads(ticket_path.read_text())
    if corrupt == "missing":
        del ticket["error"]
    else:
        ticket["error"] = ""
    ticket_path.write_text(json.dumps(ticket))

    p = run_driver(loop)                        # drain, no ruling
    assert p.returncode == 1, p.stdout + p.stderr
    terminals = [e for e in loop.events() if e["event"] == "duty_terminal"]
    assert len(terminals) == 1
    assert terminals[0]["detail"]["gate"]["gate"] == "DEAD_LETTER"

    p2 = run_driver(loop)                       # repeated drain: idempotent skip
    assert p2.returncode == 3, p2.stdout + p2.stderr
    assert len([e for e in loop.events() if e["event"] == "duty_terminal"]) == 1


def test_unusable_ticket_wrong_state_appends_nothing(loop):
    # The state gate applies to malformed tickets too: a packet that never
    # reached DUTY_REVIEW must not receive a duty_terminal event.
    pid = "p-wrong-state"
    seed_failed(loop, pid)                      # state FAILED, never stepped
    d = loop.data / "duty_review"
    d.mkdir(exist_ok=True)
    (d / ("%s.json" % pid)).write_text(
        json.dumps({"packet_id": pid, "why": "regex_no_match"}))
    p = run_driver(loop)
    assert p.returncode == 2, p.stdout + p.stderr
    assert [e for e in loop.events() if e["event"] == "duty_terminal"] == []
