# ============================================================================
# test_diffvalidator.py — Unit tests for harness/diffvalidator.py (L0 trio leg)
# Cases: authorized pass (normal), boundary reject, empty diff reject, test
#        count decrease reject, min_test_count reject, diff budget reject,
#        path escape reject, usage error (missing input).
# ============================================================================
import json

import pytest

from tests.conftest import PY


def make_diff(path, added=("new line",), removed=()):
    lines = ["diff --git a/%s b/%s" % (path, path),
             "--- a/%s" % path, "+++ b/%s" % path,
             "@@ -1,%d +1,%d @@" % (max(len(removed), 1), max(len(added), 1))]
    lines += ["-%s" % r for r in removed]
    lines += ["+%s" % a for a in added]
    return "\n".join(lines) + "\n"


@pytest.fixture
def dv(loop, tmp_path):
    """Returns a runner: dv(diff_text, cand_tc=..., packet_kw=..., oracle_tc=3)."""
    def run(diff_text, cand_tc=None, oracle_tc=3, **packet_kw):
        pkt_path = loop.data / "packets" / "p1.json"
        loop.write_packet("p1", paths=packet_kw.pop("paths", ["src/foo/"]), **packet_kw)
        diff_path = tmp_path / "cand.diff"
        diff_path.write_text(diff_text)
        oracle_path = tmp_path / "oracle.json"
        oracle_path.write_text(json.dumps({"test_count": oracle_tc, "frozen": True}))
        cmd = [PY, loop.harness("diffvalidator.py"), "--packet", pkt_path,
               "--diff", diff_path, "--oracle", oracle_path]
        if cand_tc is not None:
            cmd += ["--candidate-test-count", str(cand_tc)]
        return loop.run(cmd)
    return run


def test_authorized_changes_pass(dv):
    p = dv(make_diff("src/foo/a.py"), cand_tc=3)
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("PASS")


def test_boundary_change_rejected(dv):
    p = dv(make_diff("src/bar/b.py"), cand_tc=3)
    assert p.returncode == 1
    assert "PATH_BOUNDARY" in p.stderr
    assert "src/bar/b.py" in p.stderr


def test_empty_diff_rejected(dv):
    p = dv("", cand_tc=3)
    assert p.returncode == 1
    assert "EMPTY_DIFF" in p.stderr


def test_test_count_decrease_rejected(dv):
    p = dv(make_diff("src/foo/a.py"), cand_tc=2, oracle_tc=3)
    assert p.returncode == 1
    assert "TEST_COUNT_DECREASE" in p.stderr


def test_min_test_count_constraint_enforced(dv):
    p = dv(make_diff("src/foo/a.py"), cand_tc=4, oracle_tc=1,
           acceptance=["pytest -q", "min_test_count>=5"])
    assert p.returncode == 1
    assert "MIN_TEST_COUNT" in p.stderr


def test_diff_line_budget_enforced(dv):
    big = make_diff("src/foo/a.py", added=["l%d" % i for i in range(30)])
    p = dv(big, cand_tc=3, constraints=["diff <= 10 lines"])
    assert p.returncode == 1
    assert "DIFF_BUDGET" in p.stderr


def test_path_escape_never_authorized(dv):
    p = dv(make_diff("../outside/evil.py"), cand_tc=3, paths=["../outside/"])
    assert p.returncode == 1                     # escapes rejected even if "authorized"
    assert "PATH_BOUNDARY" in p.stderr


def test_missing_diff_file_is_usage_error(loop, tmp_path):
    loop.write_packet("p1")
    oracle = tmp_path / "oracle.json"
    oracle.write_text('{"test_count": 1}')
    p = loop.run([PY, loop.harness("diffvalidator.py"), "--packet",
                  loop.data / "packets" / "p1.json", "--diff",
                  tmp_path / "nope.diff", "--oracle", oracle])
    assert p.returncode == 2
