# ============================================================================
# test_g4_path_violation.py — Golden case G4 (spec §7)
# A mock executor commits OUTSIDE its authorized paths. Pass standard:
# L0 diffvalidator rejects with PATH_BOUNDARY; the L1 trigger table tiers
# the packet direct_l3 via the high-risk boundary rule — non-overridable
# even with passthrough enabled (no LLM verdict can release it).
# ============================================================================
import json
import re

from tests.conftest import PY


def test_g4_boundary_violation_is_caught_and_tiers_direct_l3(repo_loop, tmp_path):
    loop = repo_loop
    pid = "w1-p1"
    loop.write_packet(pid, paths=["src/alpha/"])
    wt = loop.allocate(pid)
    for ev in ("planned", "dag_assert_pass", "dispatched"):
        loop.append_event(pid, ev)
    loop.step()

    # --- executor breaches its write boundary --------------------------------
    r = loop.mock_spawn(pid, wt, scenario="path_violation")
    assert r.returncode == 0                     # it *claims* success
    rc, states = loop.step()
    assert states[pid] == "REPORTED"

    # --- L0: diffvalidator rejects the out-of-boundary diff -------------------
    diff = loop.worktree_diff(pid)
    paths_touched = sorted(set(re.findall(r"^\+\+\+ b/(.+)$", diff, re.M)))
    assert any("zone_forbidden" in p for p in paths_touched)
    dfile = tmp_path / "cand.diff"
    dfile.write_text(diff)
    ofile = tmp_path / "oracle.json"
    ofile.write_text(json.dumps({"test_count": 3}))
    v = loop.run([PY, loop.harness("diffvalidator.py"),
                  "--packet", loop.data / "packets" / ("%s.json" % pid),
                  "--diff", dfile, "--oracle", ofile,
                  "--candidate-test-count", "3"])
    assert v.returncode == 1
    assert "PATH_BOUNDARY" in v.stderr
    assert "zone_forbidden" in v.stderr          # names the offending path

    # --- L1: high-risk boundary rule tiers direct_l3, non-overridable ---------
    signals = {"packet_id": pid, "exit_codes": [0], "retry_count": 0,
               "diff_lines": len(diff.splitlines()), "diff_budget": 400,
               "path_boundary_attempts": 1, "paths_touched": paths_touched,
               "command_history": ["git commit"], "observation_lengths": [80]}
    sfile = tmp_path / "signals.json"
    sfile.write_text(json.dumps(signals))
    t = loop.run([PY, loop.harness("trigger_eval.py"), "--signals", sfile,
                  "--triggers", loop.root / "config" / "triggers.yaml",
                  "--passthrough-enabled", "true",   # even with passthrough OPEN
                  "--log", loop.data / "escalation_log.jsonl"])
    assert t.returncode == 0, t.stderr
    out = json.loads(t.stdout)
    assert out["action"] == "direct_l3"
    assert out["raw_action"] == "direct_l3"      # not an F2 upgrade: intrinsic
    assert "path_boundary_attempt" in out["rules_hit"]

    # the violation is on the escalation record for Sol / release gate review
    assert any(r.get("action") == "direct_l3" for r in loop.escalations())
    # and acceptance can therefore never pass -> packet must NOT reach ACCEPTED
    assert loop.state(pid) == "REPORTED"
