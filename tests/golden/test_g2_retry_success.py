# ============================================================================
# test_g2_retry_success.py — Golden case G2 (spec §7)
# One packet fails transiently (timeout class), the table-driven retry loop
# re-dispatches it, the second attempt succeeds and passes acceptance.
# Pass standard: FAILED -> DISPATCHABLE -> RUNNING -> ACCEPTED with NO Sol wake
# (transient failures are absorbed by the harness, never escalated).
# ============================================================================
import json

from tests.conftest import PY


def test_g2_transient_failure_retries_to_accepted(repo_loop):
    loop = repo_loop
    pid = "w1-p1"
    loop.write_packet(pid, paths=["src/alpha/"])
    loop.write_dag(waves=[[pid]])
    wt = loop.allocate(pid)

    # --- reach RUNNING ---------------------------------------------------------
    for ev in ("planned", "dag_assert_pass", "dispatched"):
        loop.append_event(pid, ev)
    rc, states = loop.step()
    assert states[pid] == "RUNNING"

    # --- attempt 1: mock executor fails transiently ------------------------------
    r = loop.mock_spawn(pid, wt, scenario="fail")
    assert r.returncode == 1                    # honest failure, honest exit code
    rc, states = loop.step()
    assert states[pid] == "FAILED"

    # --- table-driven retry: timeout class is on-table and retryable -------------
    p = loop.run([PY, loop.harness("retry.py"), "--packet", pid,
                  "--error", "ETIMEDOUT: codex exec timed out after 1800s"])
    assert p.returncode == 0, "timeout class must schedule a retry: %s" % p.stdout
    decision = json.loads(p.stdout.strip().splitlines()[-1])
    assert decision["action"] == "retry" and decision["class"] == "timeout"

    # re-dispatch contract: the failed attempt's report slot is cleared so
    # the reconcile fallback cannot mistake the stale report for attempt 2
    (loop.data / "reports" / pid / "report.json").unlink()
    rc, states = loop.step()                    # retry admitted for physical refill
    assert states[pid] == "DISPATCHABLE"
    assert loop.ledger()["packets"][pid]["attempts"] == 1

    # --- attempt 2: succeeds -------------------------------------------------------
    loop.append_event(pid, "dispatched", {"attempt": 1})
    rc, states = loop.step()
    assert states[pid] == "RUNNING"
    r = loop.mock_spawn(pid, wt)                # scenario: normal
    assert r.returncode == 0
    rc, states = loop.step()
    assert states[pid] == "REPORTED"
    report = json.loads((loop.data / "reports" / pid / "report.json").read_text())
    assert report["status"] == "done"

    loop.append_event(pid, "acceptance_pass")
    rc, states = loop.step()
    assert rc == 0
    assert states[pid] == "ACCEPTED"

    # --- pass standard: the full journey is visible in history, Sol never woken --
    path = [h["t"] for h in loop.history(pid)]
    assert path == [1, 2, 3, 6, 9, 3, 4, 7]     # retry admission is t9; physical birth owns RUNNING
    assert loop.sol_wakes() == []
    assert loop.escalations(level="SOL_WAKE") == []
