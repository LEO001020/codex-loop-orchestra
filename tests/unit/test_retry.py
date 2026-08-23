# ============================================================================
# test_retry.py — Unit tests for harness/retry.py (uses the REAL shipped
# config/retry_classes.yaml — schema compat verified).
# Cases: regex match -> retry (rc 0), 2 consecutive same-class -> duty (rc 4),
#        off-table gibberish -> duty review, never silent (rc 4), permanent
#        class -> DLQ (rc 5), duty_review-action class -> rc 4, per-class
#        budget exhausted -> DLQ (rc 5), run-level budget -> DLQ (rc 5;
#        budget = retry_classes.yaml run_level_retry_budget = 6), circuit
#        breaker opens after session_circuit_breaker = 10 rapid failures
#        (rc 6) — both thresholds are read from the yaml, not hardcoded.
# ============================================================================
import json

from tests.conftest import PY, CONFIG


def retry(loop, pid, error, classes=None):
    cmd = [PY, loop.harness("retry.py"), "--packet", pid, "--error", error]
    if classes:
        cmd += ["--classes", classes]
    p = loop.run(cmd)
    decision = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    return p.returncode, decision


def seed_attempts(loop, pid, attempts):
    led = loop.ledger()
    led["packets"][pid] = {"state": "FAILED", "history": [], "attempts": attempts}
    loop.set_ledger(led)


def test_regex_match_schedules_retry(loop):
    rc, d = retry(loop, "p1", "connection reset by peer ECONNRESET")
    assert rc == 0
    assert d["action"] == "retry" and d["class"] == "network_transient"
    assert 0 <= d["delay_s"] <= 30               # full jitter within backoff_cap
    evs = [e for e in loop.events() if e["event"] == "retry_dispatch"]
    assert len(evs) == 1 and evs[0]["packet_id"] == "p1"


def test_retry_same_generation_is_idempotent(loop):
    # Same packet, same failed generation: a repeated retry.py call must
    # never append a second retry_dispatch.  Before the step the table's
    # consecutive-same-class rule fires instead (duty_review, rc 4) - which
    # is not a duplicate dispatch.
    rc1, d1 = retry(loop, "p1", "connection reset by peer ECONNRESET")
    assert rc1 == 0 and d1["action"] == "retry"
    evs = [e for e in loop.events() if e["event"] == "retry_dispatch"]
    assert len(evs) == 1
    rc2, d2 = retry(loop, "p1", "connection reset by peer ECONNRESET")
    assert rc2 == 4 and d2["action"] == "duty_review_repeat"
    assert len([e for e in loop.events() if e["event"] == "retry_dispatch"]) == 1


def test_retry_after_apply_is_noop(loop):
    # Once the state machine applied retry_dispatch (packet DISPATCHABLE), a
    # re-invocation of the same failure is a no-op: the state guard fires
    # before the consecutive-same-class rule, so neither a second
    # retry_dispatch nor an off-table duty_review is appended.
    rc1, d1 = retry(loop, "p1", "connection reset by peer ECONNRESET")
    assert rc1 == 0 and d1["action"] == "retry"
    rc, states = loop.step()                     # apply: FAILED -> DISPATCHABLE
    assert states["p1"] == "DISPATCHABLE"
    rc2, d2 = retry(loop, "p1", "connection reset by peer ECONNRESET")
    assert rc2 == 0 and d2["action"] == "retry_already_scheduled"
    assert len([e for e in loop.events() if e["event"] == "retry_dispatch"]) == 1
    assert [e for e in loop.events() if e["event"] == "duty_review"] == []


def test_two_consecutive_same_class_goes_duty_partition(loop):
    rc1, _ = retry(loop, "p1", "ETIMEDOUT while waiting")
    assert rc1 == 0
    rc2, d2 = retry(loop, "p1", "deadline exceeded again")   # same class: timeout
    assert rc2 == 4
    assert d2["action"] == "duty_review_repeat"
    evs = [e for e in loop.events() if e["event"] == "duty_review"]
    assert evs and evs[-1]["detail"]["why"] == "2_consecutive_same_class"


def test_off_table_error_is_never_silent(loop):
    rc, d = retry(loop, "p1", "zorblatt quux discombobulated 0xDEADBEEF")
    assert rc == 4
    assert d["action"] == "duty_review_offtable" and d["class"] is None
    evs = [e for e in loop.events() if e["event"] == "duty_review"]
    assert evs and evs[-1]["detail"]["why"] == "regex_no_match"


def test_permanent_class_goes_dead_letter(loop):
    rc, d = retry(loop, "p1", "fatal: no space left on device")
    assert rc == 5
    assert d["action"] == "dead_letter_permanent" and d["class"] == "disk_full"
    assert (loop.data / "dead_letters" / "p1.json").exists()
    assert any(e["event"] == "budget_exhausted" for e in loop.events())


def test_duty_review_action_class_routes_to_duty(loop):
    rc, d = retry(loop, "p1", "SyntaxError: invalid syntax in foo.py line 3")
    assert rc == 4
    assert d["class"] == "compilation_error"


def test_per_class_budget_exhausted_goes_dlq(loop):
    seed_attempts(loop, "p1", 3)                 # >= max attempts for any class
    rc, d = retry(loop, "p1", "request timed out after 30s")
    assert rc == 5
    assert d["action"] == "dead_letter_budget"
    assert (loop.data / "dead_letters" / "p1.json").exists()


def test_run_level_budget_exhausted_goes_dlq(loop):
    led = loop.ledger()                          # total retries across ALL packets
    for i in range(3):
        led["packets"]["q%d" % i] = {"state": "FAILED", "history": [], "attempts": 2}
    loop.set_ledger(led)                         # sum = 6 >= run_level_retry_budget (yaml: 6)
    rc, d = retry(loop, "p-fresh", "ETIMEDOUT")
    assert rc == 5
    assert d["action"] == "dead_letter_budget"


def test_run_level_budget_below_yaml_value_still_retries(loop):
    led = loop.ledger()                          # 5 < yaml budget 6 (old hardcoded
    for i in range(5):                           # default was 10 — now yaml-wired)
        led["packets"]["q%d" % i] = {"state": "FAILED", "history": [], "attempts": 1}
    loop.set_ledger(led)
    rc, d = retry(loop, "p-fresh", "ETIMEDOUT")
    assert rc == 0
    assert d["action"] == "retry"


def test_circuit_breaker_opens_after_rapid_failures(loop):
    # Threshold = retry_classes.yaml session_circuit_breaker (10). Distinct
    # packets + alternating classes keep the same-class duty rule quiet.
    errors = ["ETIMEDOUT", "ECONNRESET", "deadline exceeded",
              "connection refused", "SIGALRM timed out",
              "ECONNREFUSED again", "request timed out", "connection reset",
              "job_max_runtime exceeded", "EAI_AGAIN dns lookup"]
    rcs = [retry(loop, "p%d" % i, err)[0] for i, err in enumerate(errors)]
    assert rcs[:9] == [0] * 9                    # 9 failures: breaker still closed
    assert rcs[9] == 6                           # 10th failure inside window -> open
    breaker = json.loads((loop.data / ".breaker.json").read_text())
    assert breaker["open_until"] > 0


def test_missing_classes_table_treats_all_as_off_table(loop, tmp_path):
    rc, d = retry(loop, "p1", "ETIMEDOUT", classes=str(tmp_path / "absent.yaml"))
    assert rc == 4                               # fail-visible, not a crash
    assert d["action"] == "duty_review_offtable"
