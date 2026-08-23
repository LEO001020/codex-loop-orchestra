# ============================================================================
# test_path_id_safety.py 鈥?Packet-id path safety (duty_rulings, duty_review,
# dead_letters, sol_wake, l4_queue).
# Cases: invalid pids (..// traversal, absolute, Unicode separators) are
#        fail-visible (exit 1, stderr) and write NOTHING outside root;
#        valid 1-96 ASCII ids (letters/digits/._-) still write normally;
#        length boundary 96 ok / 97 rejected.
# ============================================================================
import json
import subprocess

import pytest

from tests.conftest import PY, HARNESS

BAD_PIDS = ["../escape", "..", "a/b", "a\\b", "C:/abs", "pi\u2215x",
            ""]


def assert_nothing_written(loop):
    for d in ("duty_rulings", "duty_review", "dead_letters", "sol_wake",
              "l4_queue"):
        p = loop.data / d
        assert not p.exists() or list(p.glob("*")) == [], d
    assert not (loop.root / "escape.json").exists()
    assert not (loop.root / "escape.md").exists()
    assert not (loop.data / "escape.json").exists()


@pytest.mark.parametrize("bad", BAD_PIDS)
def test_retry_rejects_invalid_pid_without_writes(loop, bad):
    before = len(loop.events())
    p = loop.run([PY, loop.harness("retry.py"), "--packet", bad,
                  "--error", "connection reset by peer ECONNRESET"])
    assert p.returncode == 1
    assert "invalid packet id" in p.stderr
    assert len(loop.events()) == before          # no event appended
    assert bad not in loop.ledger()["packets"]   # no ledger mutation
    assert_nothing_written(loop)


@pytest.mark.parametrize("bad", BAD_PIDS)
def test_duty_gate_rejects_invalid_pid_without_writes(loop, bad):
    if bad == "":
        pytest.skip("empty pid is the pre-existing 'no packet id' skip in duty_gate")
    ruling = {"class": "fixable", "evidence": ["report.md:41"],
              "confidence": 0.9,
              "progress_ledger_delta": {"packet_id": bad},
              "fix_hint": "pin tz to UTC"}
    p = subprocess.run([PY, str(HARNESS / "duty_gate.py"), "--enforce", "true"],
                       input=json.dumps(ruling), capture_output=True, text=True,
                       timeout=30, env=loop.env())
    assert p.returncode == 1
    assert "invalid packet id" in p.stderr
    assert_nothing_written(loop)


@pytest.mark.parametrize("bad", BAD_PIDS)
def test_statemachine_rejects_invalid_pid_without_writes(loop, bad):
    loop.append_event(bad, "gamma_ray_burst")    # off-table -> would DLQ+wake
    p = loop.sm("step")
    assert p.returncode == 1
    assert "invalid packet id" in p.stderr
    assert_nothing_written(loop)


def test_statemachine_l4_queue_rejects_invalid_pid(loop):
    bad = "../../escape"
    (loop.data / "replan_counters.json").write_text("{not valid json")
    led = loop.ledger()
    led["packets"][bad] = {"state": "SOL_ADJUDICATE", "history": [], "attempts": 0}
    loop.set_ledger(led)
    loop.append_event(bad, "sol_replan")         # counter I/O -> forced direct_l4
    p = loop.sm("step")
    assert p.returncode == 1
    assert "invalid packet id" in p.stderr
    assert_nothing_written(loop)


def test_valid_packet_ids_still_write(loop):
    pid = "w1-p01.a_2"
    led = loop.ledger()
    led["packets"][pid] = {"state": "FAILED", "history": [], "attempts": 0}
    loop.set_ledger(led)
    # retry.py -> duty_review ticket
    p = loop.run([PY, loop.harness("retry.py"), "--packet", pid,
                  "--error", "zorblatt quux 0xDEADBEEF"])
    assert p.returncode == 4
    assert (loop.data / "duty_review" / ("%s.json" % pid)).exists()
    # statemachine -> dead_letters + sol_wake
    loop.append_event(pid, "gamma_ray_burst")
    rc, states = loop.step()
    assert rc == 2 and states[pid] == "DEAD_LETTER"
    assert (loop.data / "dead_letters" / ("%s.json" % pid)).exists()
    assert any(pid in w.name for w in loop.sol_wakes())
    # duty_gate -> duty_rulings
    ruling = {"class": "fixable", "evidence": ["report.md:41"],
              "confidence": 0.9,
              "progress_ledger_delta": {"packet_id": pid},
              "fix_hint": "pin tz to UTC"}
    p = subprocess.run([PY, str(HARNESS / "duty_gate.py"), "--enforce", "true"],
                       input=json.dumps(ruling), capture_output=True, text=True,
                       timeout=30, env=loop.env())
    assert p.returncode == 0
    assert (loop.data / "duty_rulings" / ("%s.json" % pid)).exists()


def test_packet_id_length_boundary(loop):
    ok = "p" + "a" * 95                          # exactly 96 chars: legal
    led = loop.ledger()
    led["packets"][ok] = {"state": "FAILED", "history": [], "attempts": 0}
    loop.set_ledger(led)
    p = loop.run([PY, loop.harness("retry.py"), "--packet", ok,
                  "--error", "zorblatt quux 0xDEADBEEF"])
    assert p.returncode == 4
    assert (loop.data / "duty_review" / ("%s.json" % ok)).exists()
    too_long = "p" + "a" * 96                    # 97 chars: rejected
    p = loop.run([PY, loop.harness("retry.py"), "--packet", too_long,
                  "--error", "zorblatt quux 0xDEADBEEF"])
    assert p.returncode == 1
    assert "invalid packet id" in p.stderr
