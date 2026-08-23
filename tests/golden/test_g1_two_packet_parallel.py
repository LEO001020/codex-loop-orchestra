# ============================================================================
# test_g1_two_packet_parallel.py — Golden case G1 (spec §7)
# Two disjoint packets run write-parallel through the full loop:
# planned -> DAG assert -> dispatch -> mock executors -> reports -> L0 diff
# validation -> acceptance -> serial merge queue -> MERGED -> WAVE_DONE.
# Pass standard: both packets MERGED, wave reaches WAVE_DONE, Sol woken <= 3
# times (here: zero out-of-band wakes).
# ============================================================================
import json

from tests.conftest import PY, MOCK


def test_g1_two_disjoint_packets_reach_wave_done(repo_loop, tmp_path):
    loop = repo_loop
    p1, p2 = "w1-p1", "w1-p2"
    loop.write_packet(p1, paths=["src/alpha/"])
    loop.write_packet(p2, paths=["src/beta/"])
    loop.write_dag(edges=[], waves=[[p1, p2]])

    # --- DAG assert gate: acyclic, write-disjoint wave -----------------------
    p = loop.run([PY, loop.harness("dag_assert.py")])
    assert p.returncode == 0, p.stderr

    for pid in (p1, p2):
        loop.append_event(pid, "planned")
        loop.append_event(pid, "dag_assert_pass")
    rc, states = loop.step()
    assert states == {p1: "DISPATCHABLE", p2: "DISPATCHABLE"}

    # --- dry-run is audit-only; physical mock dispatch follows below --------
    p = loop.run([PY, loop.harness("dispatch.py"), "--mode", "single", "--dry-run"])
    assert p.returncode == 0, p.stderr
    rc, states = loop.step()
    assert states == {p1: "DISPATCHABLE", p2: "DISPATCHABLE"}
    for pid in (p1, p2):
        loop.append_event(pid, "dispatched", {"mode": "mock"})
    rc, states = loop.step()
    assert states == {p1: "RUNNING", p2: "RUNNING"}

    # --- mock executors work in their isolated worktrees ---------------------
    wt1, wt2 = loop.allocate(p1), loop.allocate(p2)
    assert loop.mock_spawn(p1, wt1).returncode == 0   # scenario: normal
    assert loop.mock_spawn(p2, wt2).returncode == 0
    rc, states = loop.step()
    assert states == {p1: "REPORTED", p2: "REPORTED"}

    # --- L0 diff validation inside authorized boundaries ---------------------
    for pid, wt in ((p1, wt1), (p2, wt2)):
        diff = loop.worktree_diff(pid)
        assert diff.strip(), "mock executor must have committed a change"
        dfile = tmp_path / ("%s.diff" % pid)
        dfile.write_text(diff)
        ofile = tmp_path / ("%s.oracle.json" % pid)
        ofile.write_text(json.dumps({"test_count": 3}))
        v = loop.run([PY, loop.harness("diffvalidator.py"),
                      "--packet", loop.data / "packets" / ("%s.json" % pid),
                      "--diff", dfile, "--oracle", ofile,
                      "--candidate-test-count", "3"])
        assert v.returncode == 0, "L0 must accept in-boundary diff: %s" % v.stderr
        loop.append_event(pid, "acceptance_pass")
    rc, states = loop.step()
    assert states == {p1: "ACCEPTED", p2: "ACCEPTED"}

    # --- serial merge queue (advancing integration branch) --------------------
    m = loop.pool("merge-queue", p1, p2)
    assert m.returncode == 0, m.stderr
    rc, states = loop.step()
    assert states == {p1: "MERGED", p2: "MERGED"}

    # --- wave completeness check + WAVE_DONE ------------------------------------
    w = loop.sm("wave-check")
    assert w.returncode == 0 and "WAVE_DONE_READY" in w.stdout
    for pid in (p1, p2):
        loop.append_event(pid, "wave_complete")
    rc, states = loop.step()
    assert rc == 0
    assert states == {p1: "WAVE_DONE", p2: "WAVE_DONE"}

    # --- pass standard: Sol woken at most 3 times (no out-of-band wakes) -------
    assert len(loop.sol_wakes()) <= 3
    assert loop.sol_wakes() == []               # stricter: happy path is silent
