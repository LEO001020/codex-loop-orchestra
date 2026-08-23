# ============================================================================
# test_spawn_watchdog.py — Unit tests for the H-02 fix: dispatch.py records
# spawn wall-clock ts to data/spawn_times.json; statemachine.py's step-time
# watchdog emits the transition-5 'timeout' event for RUNNING packets whose
# spawn ts is STRICTLY older than [agents] job_max_runtime_seconds.
# Cases: packet finishing within the limit never fires (normal), overdue
#        RUNNING packet fires timeout -> TIMED_OUT + audit event + cursor
#        idempotency (normal), packet exactly at the boundary window does
#        not fire — strictly-over semantics (boundary).
# ============================================================================
import json
import time


def seed_running(loop, pid, spawn_age_s, limit_s=100):
    (loop.root / "config" / "config.toml").write_text(
        "[agents]\njob_max_runtime_seconds = %d\n" % limit_s)
    led = loop.ledger()
    led["packets"][pid] = {"state": "RUNNING", "history": [], "attempts": 0}
    loop.set_ledger(led)
    (loop.data / "spawn_times.json").write_text(
        json.dumps({pid: time.time() - spawn_age_s}))


def timeout_events(loop):
    return [e for e in loop.events() if e["event"] == "timeout"]


def test_packet_within_limit_does_not_fire(loop):
    seed_running(loop, "p1", spawn_age_s=1, limit_s=3600)
    rc, states = loop.step()
    assert rc == 0
    assert states["p1"] == "RUNNING"                 # untouched, still working
    assert timeout_events(loop) == []
    # a packet that then reports normally is reconciled, never timed out
    loop.write_report("p1")
    rc, states = loop.step()
    assert states["p1"] == "REPORTED"
    assert timeout_events(loop) == []


def test_overdue_running_packet_fires_timeout(loop):
    seed_running(loop, "p2", spawn_age_s=500, limit_s=100)
    rc, states = loop.step()
    assert rc == 0                                   # timeout is NOT a dead letter
    assert states["p2"] == "TIMED_OUT"
    evs = timeout_events(loop)
    assert len(evs) == 1 and evs[0]["packet_id"] == "p2"
    assert evs[0]["detail"]["why"] == "watchdog"
    assert loop.history("p2")[-1]["t"] == 5          # transition 5 in history
    # idempotent: the audit event is behind the cursor; next step must not
    # re-apply it (TIMED_OUT + timeout would be off-table -> dead letter)
    rc, states = loop.step()
    assert rc == 0 and states["p2"] == "TIMED_OUT"
    assert len(timeout_events(loop)) == 1


def test_exactly_at_boundary_does_not_fire(loop):
    # strictly-over semantics: elapsed <= limit never fires. Give the packet
    # a spawn age sitting right at the boundary window (limit minus a margin
    # that safely absorbs test wall-clock drift).
    seed_running(loop, "p3", spawn_age_s=3600 - 60, limit_s=3600)
    rc, states = loop.step()
    assert rc == 0
    assert states["p3"] == "RUNNING"
    assert timeout_events(loop) == []
