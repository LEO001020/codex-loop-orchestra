# ============================================================================
# test_smoke_gate.py — Unit tests for harness/smoke_gate.sh (§7.4 three
# assertions + version comparison — P-01/P0-2 coverage, driven by mock_codex).
# Cases: normal — all assertions green with NO pre-filled meter log (the gate
#        now verifies the route from the --json event stream, Codex's own
#        persistence surface, never from hook-written logs); boundary —
#        missing codex binary fails the gate visibly (rc 1, never a silent
#        pass); failure injection — version drift prints the re-run WARN,
#        a simulated misroute (MOCK_FORCE_MODEL: the CLI ignores -m, i.e. the
#        root-Sol fallback that WAS the P0-2 defect) fails route[*] for every
#        role, an unpinned role TOML fails fail-visibly, and the breach
#        scenario fails the write-isolation assertion.
# ============================================================================
import os
import re

import pytest

from tests.conftest import MOCK, PKG

ROLES = ["worker", "reviewer", "verifier", "duty_officer"]
MOCK_CODEX = MOCK / "bin" / "codex"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX smoke-gate harness")


def pinned_model(role):
    text = (PKG / "agents" / ("%s.toml" % role)).read_text()
    return re.search(r'^\s*model\s*=\s*"([^"]+)"', text, re.M).group(1)


def make_pkgroot(tmp_path, lock_ver="0.0.0", agents="real"):
    """Scratch package root: agents/ symlinked to the real TOMLs (or a broken
    variant), VERSIONS.lock pinned to lock_ver. NO meter log is seeded — the
    rewritten gate must not need one (P0-2: hook logs are forgeable)."""
    root = tmp_path / "pkgroot"
    (root / "data").mkdir(parents=True)
    if agents == "real":
        os.symlink(PKG / "agents", root / "agents")
    elif agents == "unpinned":
        (root / "agents").mkdir()
        for r in ROLES:
            # name only — model/model_reasoning_effort deliberately missing
            (root / "agents" / ("%s.toml" % r)).write_text('name = "%s"\n' % r)
    (root / "VERSIONS.lock").write_text(
        'codex_cli_version = "%s"\n' % lock_ver)
    return root


def gate(loop, tmp_path, root, codex_bin=None, **extra_env):
    home = tmp_path / "empty-codex-home"           # forces $PKG_ROOT/agents
    home.mkdir(exist_ok=True)
    outside_base = tmp_path / "outside-base"       # hermetic ③ (default=$HOME)
    outside_base.mkdir(exist_ok=True)
    return loop.run(["bash", loop.harness("smoke_gate.sh"), root],
                    CODEX_BIN=str(codex_bin or MOCK_CODEX),
                    CODEX_HOME=str(home),
                    SMOKE_OUTSIDE_BASE=str(outside_base),
                    **extra_env)


# ---- normal: mock codex all green, no meter log needed (P0-2) ---------------

def test_all_assertions_pass_without_any_meter_log(loop, tmp_path):
    root = make_pkgroot(tmp_path)                  # lock matches mock 0.0.0
    p = gate(loop, tmp_path, root)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "SMOKE GATE: ALL ASSERTIONS PASS" in p.stdout
    assert "matches VERSIONS.lock" in p.stdout
    for role in ROLES:
        # ① spawn line names the pinned model + effort combination
        assert re.search(r"PASS  spawnable\[%s\]: .*-m %s" % (role, pinned_model(role)),
                         p.stdout)
        # ② route verified from the --json event stream, not a hook log
        assert "PASS  route[%s]: --json confirms model=%s effort=" \
               % (role, pinned_model(role)) in p.stdout
    assert "PASS  write-isolation" in p.stdout


def test_rollout_fallback_is_bounded_by_per_role_start_marker():
    source = (PKG / "harness" / "smoke_gate.sh").read_text(encoding="utf-8")
    assert 'ROLE_T0="$SMOKE_TMP/$ROLE.started"' in source
    assert '-newer "$ROLE_T0"' in source
    assert "T0-bounded rollout + current event" in source


# ---- boundary: no codex binary — gate fails visibly --------------------------

def test_missing_codex_binary_fails_gate(loop, tmp_path):
    root = make_pkgroot(tmp_path)
    p = gate(loop, tmp_path, root,
             codex_bin=tmp_path / "no-such-codex")
    assert p.returncode == 1                       # fail-visible, never silent
    assert "not-installed" in p.stdout             # version probe degraded
    for role in ROLES:
        assert "FAIL  spawnable[%s]" % role in p.stdout


# ---- failure injection --------------------------------------------------------

def test_version_drift_prints_rerun_warning(loop, tmp_path):
    root = make_pkgroot(tmp_path, lock_ver="0.147.0")   # mock reports 0.0.0
    p = gate(loop, tmp_path, root)
    assert "WARN  version drift" in p.stdout
    assert "must re-run smoke gate" in p.stdout
    assert p.returncode == 0                       # drift warns, gate still runs


def test_misroute_to_root_model_fails_route_assertion(loop, tmp_path):
    """P0-2 regression: if the CLI ignores -m and the run lands on the root
    Sol model (exactly the old bare-exec behavior), route[*] must FAIL."""
    root = make_pkgroot(tmp_path)
    p = gate(loop, tmp_path, root, MOCK_FORCE_MODEL="gpt-5.6")
    assert p.returncode == 1
    for role in ROLES:
        if pinned_model(role) == "gpt-5.6":
            continue                               # no role pins the root model
        assert "FAIL  route[%s]" % role in p.stdout
    assert "ASSERTION(S) FAILED" in p.stdout + p.stderr


def test_unpinned_role_toml_fails_visibly(loop, tmp_path):
    root = make_pkgroot(tmp_path, agents="unpinned")
    p = gate(loop, tmp_path, root)
    assert p.returncode == 1
    for role in ROLES:
        assert "FAIL  spawnable[%s]: TOML lacks model" % role in p.stdout
        assert "FAIL  route[%s]" % role in p.stdout


def test_breach_scenario_fails_write_isolation(loop, tmp_path):
    root = make_pkgroot(tmp_path)
    loop.set_scenario("breach")                    # mock escapes the worktree
    p = gate(loop, tmp_path, root)
    assert p.returncode == 1
    assert "FAIL  write-isolation" in p.stdout
