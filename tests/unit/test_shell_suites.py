# ============================================================================
# test_shell_suites.py — pytest wrapper for the bash test suites
# Purpose : One `python -m pytest tests/` run covers the shell tests too;
#           each suite is hermetic (mktemp + trap cleanup) and asserts via
#           exit codes per the F2 test standards.
# ============================================================================
import os
import subprocess
from pathlib import Path

import pytest

UNIT = Path(__file__).resolve().parent

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell suites")


@pytest.mark.parametrize("suite", ["test_worktree_pool.sh", "test_missing_check.sh"])
def test_shell_suite_passes(suite):
    p = subprocess.run(["bash", str(UNIT / suite)], capture_output=True,
                       text=True, timeout=180)
    assert p.returncode == 0, "%s failed:\n%s\n%s" % (suite, p.stdout, p.stderr)
    assert "ALL PASS" in p.stdout
