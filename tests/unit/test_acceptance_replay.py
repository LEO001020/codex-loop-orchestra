# ============================================================================
# test_acceptance_replay.py — Unit tests for harness/acceptance_replay.sh
# (L0 mechanical acceptance trio, freeze/replay leg — P-01 coverage).
# Cases: normal freeze -> replay round trip (oracle frozen first, counts
#        extracted mechanically); boundary — replay without an oracle is a
#        hard rc-2 refusal, empty acceptance list freezes cleanly at count 0;
#        failure injection — a tampered oracle makes diffvalidator FAIL the
#        packet, a re-freeze onto an existing oracle is refused (rc 2,
#        immutability), and a failing acceptance command fails replay (rc 1).
# ============================================================================
import json
import os

import pytest

from tests.conftest import PY

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX bash path contract")


def replay_sh(loop, mode, packet, tree, oracle):
    return loop.run(["bash", loop.harness("acceptance_replay.sh"),
                     mode, packet, tree, oracle])


def write_packet_file(tmp_path, acceptance, pid="p1"):
    f = tmp_path / ("%s.json" % pid)
    f.write_text(json.dumps({"packet_id": pid, "goal": "g",
                             "authorized_paths": ["src/%s/" % pid],
                             "acceptance": acceptance, "constraints": []}))
    return f


# ---- normal: freeze then replay ---------------------------------------------

def test_freeze_then_replay_normal(loop, tmp_path):
    pkt = write_packet_file(tmp_path, ["echo '3 passed'", "true",
                                       "min_test_count>=2"])
    tree = tmp_path / "tree"
    tree.mkdir()
    oracle = tmp_path / "oracle.json"

    f = replay_sh(loop, "freeze", pkt, tree, oracle)
    assert f.returncode == 0, f.stdout + f.stderr
    assert "ORACLE_FROZEN" in f.stdout
    doc = json.loads(oracle.read_text())
    assert doc["frozen"] is True and doc["test_count"] == 3
    # min_test_count pseudo-command is parsed OUT, not executed as shell
    assert all("min_test_count" not in c["cmd"] for c in doc["commands"])

    r = replay_sh(loop, "replay", pkt, tree, oracle)
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads((tmp_path / "oracle.json.replay.json").read_text())
    assert rep["commands_passed"] is True and rep["test_count"] == 3


# ---- boundary: missing oracle / empty acceptance ----------------------------

def test_replay_without_oracle_is_refused(loop, tmp_path):
    pkt = write_packet_file(tmp_path, ["true"])
    tree = tmp_path / "tree"
    tree.mkdir()
    r = replay_sh(loop, "replay", pkt, tree, tmp_path / "absent-oracle.json")
    assert r.returncode == 2                       # freeze-first hard order
    assert "REPLAY_WITHOUT_ORACLE" in (r.stdout + r.stderr)


def test_empty_acceptance_freezes_at_zero(loop, tmp_path):
    pkt = write_packet_file(tmp_path, [])
    tree = tmp_path / "tree"
    tree.mkdir()
    oracle = tmp_path / "oracle.json"
    f = replay_sh(loop, "freeze", pkt, tree, oracle)
    assert f.returncode == 0, f.stdout + f.stderr
    doc = json.loads(oracle.read_text())
    assert doc["test_count"] == 0 and doc["commands"] == []


# ---- failure injection -------------------------------------------------------

def test_tampered_oracle_fails_diffvalidator(loop, tmp_path):
    # Freeze an honest oracle, then tamper its test_count upward (as if a
    # candidate tried to redefine "passing"): the diffvalidator leg of the
    # trio must FAIL the packet on TEST_COUNT_DECREASE. Fail-visible.
    pkt = write_packet_file(tmp_path, ["echo '3 passed'"])
    tree = tmp_path / "tree"
    tree.mkdir()
    oracle = tmp_path / "oracle.json"
    assert replay_sh(loop, "freeze", pkt, tree, oracle).returncode == 0

    doc = json.loads(oracle.read_text())
    doc["test_count"] = 999                        # tamper
    oracle.write_text(json.dumps(doc))

    diff = tmp_path / "cand.diff"
    diff.write_text("diff --git a/src/p1/a.py b/src/p1/a.py\n"
                    "--- a/src/p1/a.py\n+++ b/src/p1/a.py\n"
                    "@@ -1 +1,2 @@\n x\n+y\n")
    v = loop.run([PY, loop.harness("diffvalidator.py"), "--packet", pkt,
                  "--diff", diff, "--oracle", oracle,
                  "--candidate-test-count", "3"])
    assert v.returncode == 1
    assert "TEST_COUNT_DECREASE" in v.stderr


def test_refreeze_onto_existing_oracle_is_refused(loop, tmp_path):
    # Oracle immutability: overwriting a frozen oracle (the direct tamper
    # path through the script itself) is a hard rc-2 refusal.
    pkt = write_packet_file(tmp_path, ["echo '2 passed'"])
    tree = tmp_path / "tree"
    tree.mkdir()
    oracle = tmp_path / "oracle.json"
    assert replay_sh(loop, "freeze", pkt, tree, oracle).returncode == 0
    f2 = replay_sh(loop, "freeze", pkt, tree, oracle)
    assert f2.returncode == 2
    assert "ORACLE_ALREADY_FROZEN" in (f2.stdout + f2.stderr)


def test_failing_acceptance_command_fails_replay(loop, tmp_path):
    pkt = write_packet_file(tmp_path, ["true"])
    tree = tmp_path / "tree"
    tree.mkdir()
    oracle = tmp_path / "oracle.json"
    assert replay_sh(loop, "freeze", pkt, tree, oracle).returncode == 0
    bad_pkt = write_packet_file(tmp_path, ["false"], pid="p1")  # now failing
    r = replay_sh(loop, "replay", bad_pkt, tree, oracle)
    assert r.returncode == 1                       # any failing command fails
    rep = json.loads((tmp_path / "oracle.json.replay.json").read_text())
    assert rep["commands_passed"] is False


def test_usage_error_on_missing_args(loop):
    p = loop.run(["bash", loop.harness("acceptance_replay.sh"), "freeze"])
    assert p.returncode == 2
