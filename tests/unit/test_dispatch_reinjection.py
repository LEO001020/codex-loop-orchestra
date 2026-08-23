# ============================================================================
# test_dispatch_reinjection.py — Unit tests for the H-03 fix in
# harness/dispatch.py: re-dispatch (attempts>0) appends ONE previous-attempt
# handle line (report PATH + retry class, optionally the gated duty fix_hint)
# to the spawn prompt; the first dispatch prompt stays byte-identical to the
# spec §4.3 four-field form (no injection).
# Cases: first dispatch has no injection (normal), re-dispatch injects the
#        handle line + fix_hint splice (normal), repeated re-dispatches
#        accumulate in the attempts counter of the handle line (boundary).
# ============================================================================
import json

from tests.conftest import PY, HARNESS


def prompt_for(loop, pid):
    """Render the spawn prompt exactly as dispatch_single would."""
    code = ("import json, sys\n"
            "sys.path.insert(0, %r)\n"
            "import dispatch\n"
            "pkt = json.load(open(%r))\n"
            "print(dispatch.spawn_prompt(pkt, '<wt>'))"
            % (str(HARNESS), str(loop.data / "packets" / ("%s.json" % pid))))
    p = loop.run([PY, "-c", code])
    assert p.returncode == 0, p.stderr
    return p.stdout


def seed(loop, pid, attempts, fail_class=None):
    loop.write_packet(pid)
    led = loop.ledger()
    led["packets"][pid] = {"state": "DISPATCHABLE", "history": [],
                           "attempts": attempts}
    if fail_class:
        led["packets"][pid]["last_fail_class"] = fail_class
    loop.set_ledger(led)


def test_first_dispatch_prompt_has_no_injection(loop):
    seed(loop, "p1", attempts=0)
    prompt = prompt_for(loop, "p1")
    assert "previous_attempt" not in prompt
    assert "fix_hint" not in prompt
    # the four-field skeleton is intact
    for field in ("goal:", "authorized_paths:", "acceptance:", "constraints:"):
        assert field in prompt


def test_redispatch_injects_handle_line_and_fix_hint(loop):
    seed(loop, "p2", attempts=1, fail_class="test_failure")
    # gated fixable ruling on disk (written by duty_gate.py on VALID exit)
    d = loop.data / "duty_rulings"
    d.mkdir(exist_ok=True)
    (d / "p2.json").write_text(json.dumps(
        {"class": "fixable", "fix_hint": "pin the tz to UTC in conftest",
         "confidence": 0.9}))
    prompt = prompt_for(loop, "p2")
    # handle line: PATH + class only — never the report content (axiom 5)
    assert ("previous_attempt: data/reports/p2/previous/attempt-0.json "
            "(failed: test_failure, attempts: 1)") in prompt
    assert "fix_hint: pin the tz to UTC in conftest" in prompt
    # no content injection: the line count grows by exactly one line
    seed(loop, "p2b", attempts=0)
    assert len(prompt.splitlines()) == len(prompt_for(loop, "p2b").splitlines()) + 1


def test_repeated_redispatches_accumulate_attempt_count(loop):
    seed(loop, "p3", attempts=3, fail_class="flaky_network")
    prompt = prompt_for(loop, "p3")
    assert ("previous_attempt: data/reports/p3/previous/attempt-2.json "
            "(failed: flaky_network, attempts: 3)") in prompt
    assert "fix_hint" not in prompt            # no gated ruling -> no hint
    assert prompt.count("previous_attempt") == 1  # always ONE handle line
