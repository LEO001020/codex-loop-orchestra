# ============================================================================
# test_smoke_gate_real.py — P0-2 regression tests: the smoke gate must issue
# REAL role starts, never four bare root-Sol `codex exec` calls.
# Cases: static — the gate script contains no bare `codex exec` invocation
#        (every exec carries -m), reads model/effort from the role TOML
#        instead of hand-copied names, and keeps the write-isolation OUTSIDE
#        dir out of /tmp (workspace-write whitelists /tmp -> fake verdicts);
#        behavioral (mock) — the --json event stream parsing accepts the
#        pinned model and REJECTS a run that actually landed on the root
#        model, and pre-filled historical meter lines can no longer make a
#        misrouted smoke run pass (the old test-suite blind spot).
# ============================================================================
import json
import os
import re

import pytest

from tests.conftest import MOCK, PKG

GATE = (PKG / "harness" / "smoke_gate.sh").read_text(encoding="utf-8")
ROLES = ["worker", "reviewer", "verifier", "duty_officer"]
MOCK_CODEX = MOCK / "bin" / "codex"
requires_posix_bash = pytest.mark.skipif(
    os.name == "nt", reason="behavioral smoke gate requires POSIX bash paths")


def pinned_model(role):
    text = (PKG / "agents" / ("%s.toml" % role)).read_text(encoding="utf-8")
    return re.search(r'^\s*model\s*=\s*"([^"]+)"', text, re.M).group(1)


def make_pkgroot(tmp_path):
    import os
    import shutil
    root = tmp_path / "pkgroot"
    (root / "data").mkdir(parents=True)
    if os.name == "nt":
        shutil.copytree(PKG / "agents", root / "agents")
    else:
        os.symlink(PKG / "agents", root / "agents")
    (root / "VERSIONS.lock").write_text('codex_cli_version = "0.0.0"\n')
    return root


def gate(loop, tmp_path, root, **extra_env):
    home = tmp_path / "codex-home"
    home.mkdir(exist_ok=True)
    outside = tmp_path / "outside-base"
    outside.mkdir(exist_ok=True)
    return loop.run(["bash", loop.harness("smoke_gate.sh"), root],
                    CODEX_BIN=str(MOCK_CODEX), CODEX_HOME=str(home),
                    SMOKE_OUTSIDE_BASE=str(outside), **extra_env)


# ---- static: no bare `codex exec` left in the gate ---------------------------

def test_gate_has_no_bare_codex_exec_invocation():
    """Every $CODEX_BIN exec invocation must carry the -m model override; a
    bare exec is exactly the P0-2 defect (four root-Sol runs)."""
    lines = GATE.splitlines()
    calls, i = [], 0
    while i < len(lines):
        if '"$CODEX_BIN" exec' in lines[i]:
            block = lines[i]
            while block.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                block += " " + lines[i]
            calls.append(block)
        i += 1
    assert calls, "gate must invoke $CODEX_BIN exec"
    for c in calls:
        assert re.search(r"-m\s+", c), "bare codex exec found (no -m): %s" % c[:120]
        assert "model_reasoning_effort=" in c, "exec without effort pin: %s" % c[:120]


def test_gate_reads_model_and_effort_from_toml():
    assert "toml_field" in GATE
    assert re.search(r'toml_field\s+"\$TOML"\s+model\b', GATE)
    assert re.search(r'toml_field\s+"\$TOML"\s+model_reasoning_effort', GATE)
    # no hand-copied model names anywhere in the gate
    assert "gpt-5.6" not in GATE


def test_gate_outside_dir_is_not_under_tmp():
    """workspace-write whitelists /tmp and $TMPDIR as writable roots, so an
    OUTSIDE dir from mktemp gives a fake verdict. Default base must be $HOME."""
    m = re.search(r'OUTSIDE_BASE="\$\{SMOKE_OUTSIDE_BASE:-([^}"]+)\}"', GATE)
    assert m, "gate must define OUTSIDE_BASE with a SMOKE_OUTSIDE_BASE override"
    assert m.group(1) == "$HOME"
    assert not re.search(r'OUTSIDE="?\$\(mktemp', GATE)


def test_gate_does_not_depend_on_subagent_start_hook():
    """SubagentStart never fires for exec top-level processes; the gate must
    not grep hook-written meter logs for its route assertion."""
    assert "METER_LOG" not in GATE
    assert "agent_role" not in GATE


# ---- behavioral: --json event stream parsing (mock mode) ----------------------

@requires_posix_bash
def test_route_assertion_parses_json_event_stream(loop, tmp_path):
    root = make_pkgroot(tmp_path)
    p = gate(loop, tmp_path, root)
    assert p.returncode == 0, p.stdout + p.stderr
    for role in ROLES:
        assert "PASS  route[%s]: --json confirms model=%s effort=" \
               % (role, pinned_model(role)) in p.stdout


@requires_posix_bash
def test_route_assertion_rejects_root_model_run(loop, tmp_path):
    """If the run actually lands on the root Sol model (MOCK_FORCE_MODEL
    simulates the -m override being ignored), route[*] must FAIL — the signal
    comes from Codex's own event stream, not from anything we can pre-fill."""
    root = make_pkgroot(tmp_path)
    p = gate(loop, tmp_path, root, MOCK_FORCE_MODEL="gpt-5.6")
    assert p.returncode == 1
    assert "FAIL  route[worker]" in p.stdout


@requires_posix_bash
def test_prefilled_meter_lines_cannot_fake_a_pass(loop, tmp_path):
    """Old blind spot: pre-filled events.ndjson meter records made the gate
    pass. Now: same pre-fill + misrouted runs must still FAIL."""
    root = make_pkgroot(tmp_path)
    lines = [json.dumps({"event": "SubagentStart", "ts_utc": "t",
                         "model": pinned_model(r), "cwd": "x",
                         "agent_role": r}) for r in ROLES]
    (root / "data" / "events.ndjson").write_text("\n".join(lines) + "\n")
    p = gate(loop, tmp_path, root, MOCK_FORCE_MODEL="gpt-5.6",
             METER_LOG=str(root / "data" / "events.ndjson"))
    assert p.returncode == 1
    assert "FAIL  route[worker]" in p.stdout
