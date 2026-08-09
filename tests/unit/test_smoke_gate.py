# ============================================================================
# test_smoke_gate.py — Unit tests for harness/smoke_gate.sh (§7.4 three
# assertions + version comparison — P-01 coverage, driven by mock_codex).
# Cases: normal — all three assertions green against the mock codex with the
#        DEFAULT meter path data/events.ndjson (P-02 regression: the same
#        file the SubagentStart hook writes); boundary — missing codex
#        binary fails the gate visibly (rc 1, never a silent pass); failure
#        injection — version drift prints the re-run WARN, an empty meter
#        log fails assertion ② for every role, and the breach scenario
#        fails the write-isolation assertion.
# ============================================================================
import json
import os
import re

from conftest import MOCK, PKG

ROLES = ["worker", "reviewer", "verifier", "duty_officer"]
MOCK_CODEX = MOCK / "bin" / "codex"


def pinned_model(role):
    text = (PKG / "agents" / ("%s.toml" % role)).read_text()
    return re.search(r'^\s*model\s*=\s*"([^"]+)"', text, re.M).group(1)


def make_pkgroot(tmp_path, lock_ver="0.0.0", with_meter=True):
    """Scratch package root: agents/ symlinked to the real TOMLs, VERSIONS.lock
    pinned to lock_ver, and (optionally) a data/events.ndjson exactly like the
    SubagentStart meter hook writes — smoke_gate must find it by DEFAULT."""
    root = tmp_path / "pkgroot"
    (root / "data").mkdir(parents=True)
    os.symlink(PKG / "agents", root / "agents")
    (root / "VERSIONS.lock").write_text(
        'codex_cli_version = "%s"\n' % lock_ver)
    if with_meter:
        lines = [json.dumps({"event": "SubagentStart", "ts_utc": "t",
                             "model": pinned_model(r), "cwd": "x",
                             "agent_role": r}) for r in ROLES]
        (root / "data" / "events.ndjson").write_text("\n".join(lines) + "\n")
    return root


def gate(loop, tmp_path, root, codex_bin=None):
    home = tmp_path / "empty-codex-home"           # forces $PKG_ROOT/agents
    home.mkdir(exist_ok=True)
    return loop.run(["bash", loop.harness("smoke_gate.sh"), root],
                    CODEX_BIN=str(codex_bin or MOCK_CODEX),
                    CODEX_HOME=str(home))


# ---- normal: mock codex all green, DEFAULT meter path (P-02) ----------------

def test_all_assertions_pass_with_default_meter_path(loop, tmp_path):
    root = make_pkgroot(tmp_path)                  # lock matches mock 0.0.0
    p = gate(loop, tmp_path, root)                 # NO METER_LOG env set
    assert p.returncode == 0, p.stdout + p.stderr
    assert "SMOKE GATE: ALL ASSERTIONS PASS" in p.stdout
    assert "matches VERSIONS.lock" in p.stdout
    for role in ROLES:                             # ② read data/events.ndjson
        assert "PASS  route[%s]" % role in p.stdout
    assert "PASS  write-isolation" in p.stdout


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


def test_empty_meter_log_fails_route_assertion(loop, tmp_path):
    root = make_pkgroot(tmp_path, with_meter=False)
    p = gate(loop, tmp_path, root)
    assert p.returncode == 1
    for role in ROLES:
        assert "FAIL  route[%s]" % role in p.stdout
    assert "ASSERTION(S) FAILED" in p.stdout + p.stderr


def test_breach_scenario_fails_write_isolation(loop, tmp_path):
    root = make_pkgroot(tmp_path)
    loop.set_scenario("breach")                    # mock escapes the worktree
    p = gate(loop, tmp_path, root)
    assert p.returncode == 1
    assert "FAIL  write-isolation" in p.stdout
