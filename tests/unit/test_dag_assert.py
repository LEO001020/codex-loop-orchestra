# ============================================================================
# test_dag_assert.py — Unit tests for harness/dag_assert.py
# Cases: acyclic + disjoint pass (normal), cycle detection (failure),
#        path prefix-intersection detection (failure), identical-path
#        intersection, disjoint files in same dir pass (boundary),
#        explicit dag path argument.
# ============================================================================
import sys

from tests.conftest import PY


def run_dag(loop, dag_path=None):
    cmd = [PY, loop.harness("dag_assert.py")]
    if dag_path:
        cmd.append(dag_path)
    return loop.run(cmd)


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
