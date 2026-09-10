# ============================================================================
# test_dag_assert.py — Unit tests for harness/dag_assert.py
# Cases: acyclic + disjoint pass (normal), cycle detection (failure),
#        path prefix-intersection detection (failure), identical-path
#        intersection, disjoint files in same dir pass (boundary),
#        explicit dag path argument.
# CLI surface: -h/--help exits 0 with usage; missing/malformed input exits
#        nonzero with a clean message and never a traceback; the documented
#        exit 0 = pass / exit 1 = assertion-failed contract is pinned.
# ============================================================================
import sys

from conftest import PY


def run_dag(loop, dag_path=None):
    cmd = [PY, loop.harness("dag_assert.py")]
    if dag_path:
        cmd.append(dag_path)
    return loop.run(cmd)


def run_argv(loop, *argv):
    return loop.run([PY, loop.harness("dag_assert.py"), *argv])


def test_acyclic_disjoint_wave_passes(loop):
    loop.write_packet("p1", paths=["src/alpha/"])
    loop.write_packet("p2", paths=["src/beta/", "tests/test_beta.py"])
    loop.write_dag(edges=[["p1", "p2"]], waves=[["p1", "p2"]])
    p = run_dag(loop)
    assert p.returncode == 0, p.stderr
    assert "PASS" in p.stdout


def test_cycle_detected(loop):
    loop.write_packet("p1", paths=["src/alpha/"])
    loop.write_packet("p2", paths=["src/beta/"])
    loop.write_dag(edges=[["p1", "p2"], ["p2", "p1"]], waves=[])
    p = run_dag(loop)
    assert p.returncode == 1
    assert "cycle" in p.stderr.lower()


def test_prefix_path_intersection_rejected(loop):
    # src/foo/ contains src/foo/sub/ — write-parallel isolation violated
    loop.write_packet("p1", paths=["src/foo/"])
    loop.write_packet("p2", paths=["src/foo/sub/"])
    loop.write_dag(edges=[], waves=[["p1", "p2"]])
    p = run_dag(loop)
    assert p.returncode == 1
    assert "intersect" in p.stderr


def test_identical_path_intersection_rejected(loop):
    loop.write_packet("p1", paths=["shared.txt"])
    loop.write_packet("p2", paths=["shared.txt"])
    loop.write_dag(edges=[], waves=[["p1", "p2"]])
    p = run_dag(loop)
    assert p.returncode == 1
    assert "intersect" in p.stderr


def test_sibling_dirs_do_not_intersect(loop):
    # boundary: src/foo/ vs src/foobar/ must NOT be treated as a prefix hit
    loop.write_packet("p1", paths=["src/foo/"])
    loop.write_packet("p2", paths=["src/foobar/"])
    loop.write_dag(edges=[], waves=[["p1", "p2"]])
    p = run_dag(loop)
    assert p.returncode == 0, p.stderr


def test_explicit_dag_path_argument(loop, tmp_path):
    loop.write_packet("p1", paths=["src/a/"])
    alt = tmp_path / "alt_dag.json"
    alt.write_text('{"edges": [], "waves": [["p1"]]}')
    p = run_dag(loop, dag_path=str(alt))
    assert p.returncode == 0, p.stderr


# --- CLI surface: usage and bad input must never traceback -------------------
def test_help_exits_zero_with_usage(loop):
    for flag in ("-h", "--help"):
        p = run_argv(loop, flag)
        assert p.returncode == 0, p.stderr
        assert "usage:" in p.stdout
        assert "exit:" in p.stdout                  # documents the contract
        assert "Traceback" not in p.stderr


def test_help_does_not_need_a_dag_file(loop):
    # no write_dag(): --help must not touch the filesystem at all
    p = run_argv(loop, "--help")
    assert p.returncode == 0
    assert "PASS" not in p.stdout


def test_missing_explicit_path_fails_without_traceback(loop, tmp_path):
    absent = tmp_path / "no_such_dag.json"
    p = run_dag(loop, dag_path=str(absent))
    assert p.returncode != 0
    assert "Traceback" not in p.stderr
    assert "DAG_ASSERT" in p.stderr and "not found" in p.stderr


def test_missing_default_dag_fails_without_traceback(loop):
    # fresh tree: data/packets/ exists but dag.json was never written
    p = run_dag(loop)
    assert p.returncode != 0
    assert "Traceback" not in p.stderr
    assert "not found" in p.stderr


def test_malformed_dag_json_fails_without_traceback(loop, tmp_path):
    broken = tmp_path / "broken_dag.json"
    broken.write_text('{"edges": [], "waves": ')      # truncated
    p = run_dag(loop, dag_path=str(broken))
    assert p.returncode != 0
    assert "Traceback" not in p.stderr
    assert "not valid JSON" in p.stderr


def test_missing_packet_file_fails_without_traceback(loop):
    loop.write_dag(edges=[], waves=[["ghost"]])       # no ghost.json on disk
    p = run_dag(loop)
    assert p.returncode != 0
    assert "Traceback" not in p.stderr
    assert "not found" in p.stderr


def test_exit_code_contract_pass_zero_fail_one(loop):
    # exit 0 = pass and exit 1 = assertion failed are the documented contract
    loop.write_packet("p1", paths=["src/alpha/"])
    loop.write_packet("p2", paths=["src/beta/"])
    loop.write_dag(edges=[], waves=[["p1", "p2"]])
    assert run_dag(loop).returncode == 0

    loop.write_packet("p2", paths=["src/alpha/deeper/"])   # now intersects p1
    p = run_dag(loop)
    assert p.returncode == 1                               # never 2 for a real failure
    assert "intersect" in p.stderr
