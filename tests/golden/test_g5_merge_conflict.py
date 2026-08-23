# ============================================================================
# test_g5_merge_conflict.py — Golden case G5 (spec §7)
# Two packets in the same wave claim the same file. Layer 1: dag_assert
# intercepts the overlap up front (the normal outcome). Layer 2: if planning
# is bypassed, the serial merge queue physically hits the conflict — first
# packet merges, second aborts with MERGE_CONFLICT -> conflict_pointer ->
# SOL_ADJUDICATE. No corrupt merge ever lands on the integration branch.
# ============================================================================
import json

from tests.conftest import PY


def test_g5_dag_assert_intercepts_overlap_up_front(loop):
    loop.write_packet("p1", paths=["shared.txt"])
    loop.write_packet("p2", paths=["shared.txt"])
    loop.write_dag(edges=[], waves=[["p1", "p2"]])
    p = loop.run([PY, loop.harness("dag_assert.py")])
    assert p.returncode == 1                     # first line of defense
    assert "intersect" in p.stderr


def test_g5_bypassed_planning_conflict_goes_to_sol(repo_loop):
    loop = repo_loop
    p1, p2 = "w1-p1", "w1-p2"
    for pid in (p1, p2):
        loop.write_packet(pid, paths=["shared.txt"])   # illegal overlap
        for ev in ("planned", "dag_assert_pass", "dispatched"):
            loop.append_event(pid, ev)          # dag gate bypassed on purpose
    loop.step()

    wt1, wt2 = loop.allocate(p1), loop.allocate(p2)
    assert loop.mock_spawn(p1, wt1, scenario="merge_conflict").returncode == 0
    assert loop.mock_spawn(p2, wt2, scenario="merge_conflict").returncode == 0
    rc, states = loop.step()
    assert states == {p1: "REPORTED", p2: "REPORTED"}
    for pid in (p1, p2):
        loop.append_event(pid, "acceptance_pass")
    rc, states = loop.step()
    assert states == {p1: "ACCEPTED", p2: "ACCEPTED"}

    # --- serial merge queue: p1 lands, p2 physically conflicts ----------------
    m = loop.pool("merge-queue", p1, p2)
    assert m.returncode == 3                    # conflict exit code contract
    assert "queue stopped" in m.stderr

    rc, states = loop.step()
    assert states[p1] == "MERGED"               # first packet is safe
    assert states[p2] == "MERGE_CONFLICT"       # transition 17

    # integration branch holds ONLY p1's version — no corrupt merge landed
    show = loop.run(["git", "-C", loop.repo, "show",
                     "loop-integration:shared.txt"])
    assert show.stdout.splitlines()[0] == "edited-by-%s" % p1
    assert p2 not in show.stdout

    # conflict pointer hands adjudication to Sol (transition 20)
    loop.append_event(p2, "conflict_pointer",
                      {"pointer": "worktrees/%s (rebase aborted)" % p2})
    rc, states = loop.step()
    assert rc == 0
    assert states[p2] == "SOL_ADJUDICATE"
    evs = [e for e in loop.events()
           if e["packet_id"] == p2 and e["event"] == "merge_conflict"]
    assert evs, "pool must have recorded the merge_conflict event"
