# ============================================================================
# test_dispatch_model_pin.py — P0-1 regression tests for harness/dispatch.py
# The defect: dispatch_single built a bare `codex exec` (no -m, no -c), so
# every packet ran on the root Sol model. These tests pin the fix:
# Cases: cmd carries -m matching worker.toml's model; cmd carries
#        -c model_reasoning_effort matching the TOML; cmd carries --json;
#        different roles use their OWN TOML pins (reviewer=K3 max,
#        worker=the active execution profile); a TOML without a model pin fails visibly (rc 1,
#        never a silent root-model fallback); CSV mode validates the TOML and
#        pins agent_type + records the model in dispatched events.
# ============================================================================
import json
import os
import re

from tests.conftest import PKG, PY

TOML_RX = {
    "model": r'^\s*model\s*=\s*"([^"]+)"',
    "effort": r'^\s*model_reasoning_effort\s*=\s*"([^"]+)"',
    "context": r'^\s*model_context_window\s*=\s*(\d+)',
    "compact": r'^\s*model_auto_compact_token_limit\s*=\s*(\d+)',
}


def toml_pin(role, key):
    text = (PKG / "agents" / ("%s.toml" % role)).read_text(encoding="utf-8")
    return re.search(TOML_RX[key], text, re.M).group(1)


def seed_wave(loop, pids, state="DISPATCHABLE"):
    for pid in pids:
        loop.write_packet(pid)
    loop.write_dag(waves=[list(pids)])
    led = loop.ledger()
    for pid in pids:
        led["packets"][pid] = {"state": state, "history": [], "attempts": 0}
    loop.set_ledger(led)


def dry_run_cmd(loop, *extra):
    """Dispatch --dry-run and parse the full JSON argv it prints."""
    seed_wave(loop, ["w1-p1"])
    p = loop.run([PY, loop.harness("dispatch.py"), "--mode", "single",
                  "--dry-run", *extra])
    assert p.returncode == 0, p.stderr
    line = next(l for l in p.stdout.splitlines() if l.startswith("DRY-RUN"))
    return json.loads(line.split(": ", 1)[1])


# ---- cmd construction: worker pin --------------------------------------------

def test_cmd_contains_model_flag_matching_worker_toml(loop):
    cmd = dry_run_cmd(loop)
    assert "-m" in cmd, "P0-1: dispatch must pin the model explicitly"
    assert cmd[cmd.index("-m") + 1] == toml_pin("worker", "model")


def test_cmd_contains_reasoning_effort_matching_worker_toml(loop):
    cmd = dry_run_cmd(loop)
    want = "model_reasoning_effort=%s" % toml_pin("worker", "effort")
    assert "-c" in cmd
    assert cmd[cmd.index("-c") + 1] == want


def test_cmd_contains_json_flag_for_per_packet_token_capture(loop):
    cmd = dry_run_cmd(loop)
    assert "--json" in cmd, "turn.completed.usage is the per-packet token tap"


def test_worker_ipybox_follows_execution_plane(loop):
    cmd = dry_run_cmd(loop)
    overrides = [cmd[i + 1] for i, value in enumerate(cmd[:-1]) if value == "-c"]
    expected = ("mcp_servers.ipybox.enabled=false" if os.name == "nt" else
                "mcp_servers.ipybox.enabled=true")
    rejected = ("mcp_servers.ipybox.enabled=true" if os.name == "nt" else
                "mcp_servers.ipybox.enabled=false")
    assert expected in overrides
    assert ("mcp_servers.node_repl.enabled=false" in overrides) == (os.name == "nt")
    assert rejected not in overrides


def test_non_gpt_roles_carry_catalog_safe_context_and_800k_compaction(loop):
    for role in ("worker", "reviewer", "verifier", "duty_officer"):
        cmd = dry_run_cmd(loop, "--role", role)
        overrides = [cmd[i + 1] for i, value in enumerate(cmd[:-1]) if value == "-c"]
        assert "model_context_window=%s" % toml_pin(role, "context") in overrides
        assert "model_auto_compact_token_limit=%s" % toml_pin(role, "compact") in overrides
        expected_context = "1000000"
        assert "model_context_window=%s" % expected_context in overrides
        assert "model_auto_compact_token_limit=800000" in overrides


def test_cmd_sandbox_comes_from_worker_toml(loop):
    cmd = dry_run_cmd(loop)
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"


# ---- role-specific pins: V4 execution and K3 review stay separated -------------

def test_roles_use_their_own_toml_pins(loop):
    for role in ("worker", "reviewer", "verifier", "duty_officer"):
        cmd = dry_run_cmd(loop, "--role", role)
        assert cmd[cmd.index("-m") + 1] == toml_pin(role, "model"), role
        assert cmd[cmd.index("-c") + 1] == \
            "model_reasoning_effort=%s" % toml_pin(role, "effort"), role


def test_portable_profile_separates_execution_and_review_models(loop):
    w = dry_run_cmd(loop, "--role", "worker")
    seed_wave(loop, ["w1-p1"])  # reseed (dry_run_cmd seeds again harmlessly)
    r = dry_run_cmd(loop, "--role", "reviewer")
    w_model, r_model = w[w.index("-m") + 1], r[r.index("-m") + 1]
    assert w_model == toml_pin("worker", "model"), "worker must use TOML-pinned model"
    assert r_model == toml_pin("reviewer", "model"), "reviewer must use TOML-pinned model"
    assert w_model == "gpt-5.6-terra"
    assert r_model == "gpt-5.6"
    assert w_model != r_model
    assert w[w.index("-c") + 1] == (
        "model_reasoning_effort=%s" % toml_pin("worker", "effort"))
    assert r[r.index("-c") + 1] == (
        "model_reasoning_effort=%s" % toml_pin("reviewer", "effort"))


# ---- fail-visible: unpinned TOML never falls back to the root model ------------

def test_toml_without_model_fails_visibly(loop):
    agents = loop.root / "agents"
    (agents / "worker.toml").write_text('name = "worker"\n'
                                        'model_reasoning_effort = "low"\n')
    seed_wave(loop, ["w1-p1"])
    p = loop.run([PY, loop.harness("dispatch.py"), "--mode", "single", "--dry-run"])
    assert p.returncode == 1
    assert "missing model" in p.stderr
    assert "DRY-RUN" not in p.stdout          # nothing dispatched on root model


def test_missing_toml_fails_visibly(loop, monkeypatch):
    agents = loop.root / "agents"
    (agents / "worker.toml").unlink()
    # a role whose TOML we point away from the package via CODEX_HOME=empty —
    # the package fallback still exists, so instead assert the local override
    # with a *broken* file wins and fails.
    (agents / "worker.toml").write_text("not valid toml = [ oops\n")
    seed_wave(loop, ["w1-p1"])
    p = loop.run([PY, loop.harness("dispatch.py"), "--mode", "single", "--dry-run"])
    assert p.returncode == 1
    assert "unreadable" in p.stderr


# ---- CSV batch mode also carries the pin ---------------------------------------

def test_csv_mode_validates_toml_and_pins_agent_type(loop):
    seed_wave(loop, ["w1-p1", "w1-p2"])
    p = loop.run([PY, loop.harness("dispatch.py"), "--mode", "csv", "--dry-run"])
    assert p.returncode == 0, p.stderr
    call = json.loads((loop.data / "dispatch" / "batch_w0.call.json").read_text())
    assert call["agent_type"] == "worker"     # in-session spawn loads the TOML
    evs = [e for e in loop.events() if e["event"] == "dispatch_dry_run"]
    assert all(e["detail"]["model"] == toml_pin("worker", "model") for e in evs)
    assert all(e["detail"]["reasoning_effort"] == toml_pin("worker", "effort")
               for e in evs)


def test_csv_mode_with_unpinned_toml_fails_visibly(loop):
    agents = loop.root / "agents"
    (agents / "worker.toml").write_text('name = "worker"\n')
    seed_wave(loop, ["w1-p1"])
    p = loop.run([PY, loop.harness("dispatch.py"), "--mode", "csv", "--dry-run"])
    assert p.returncode == 1
    assert "missing model" in p.stderr


def test_sol_budget_does_not_reclassify_explicit_child_by_model(loop):
    (loop.root / "agents" / "reviewer.toml").write_text(
        'name="reviewer"\nmodel="gpt-5.6"\nmodel_reasoning_effort="high"\n')
    seed_wave(loop, ["w1-p1"])
    usage = loop.data / "usage"
    usage.mkdir()
    (usage / "model_token_share.json").write_text(json.dumps({"windows": {
        "rolling_24h": {"status": "BLOCK"},
        "rolling_7d": {"status": "OK"}}}))
    p = loop.run([PY, loop.harness("dispatch.py"), "--mode", "single",
                  "--dry-run", "--role", "reviewer", "--force"])
    assert p.returncode == 0, p.stderr
    assert "DRY-RUN" in p.stdout
    assert not any(e["event"] == "sol_budget_blocked" for e in loop.events())


def test_sol_budget_block_keeps_child_dispatch_available(loop):
    for role in ("worker", "verifier"):
        seed_wave(loop, ["w1-p1"])
        usage = loop.data / "usage"
        usage.mkdir(exist_ok=True)
        (usage / "model_token_share.json").write_text(json.dumps({"windows": {
            "cumulative_since_f2": {"status": "BLOCK"}}}))
        p = loop.run([PY, loop.harness("dispatch.py"), "--mode", "single",
                      "--dry-run", "--role", role])
        assert p.returncode == 0, role + p.stderr
        assert "DRY-RUN" in p.stdout


def test_sol_budget_block_allows_release_finalize_explicit_sol_role(loop):
    (loop.root / "agents" / "reviewer.toml").write_text(
        'name="reviewer"\nmodel="gpt-5.6"\nmodel_reasoning_effort="high"\n')
    seed_wave(loop, ["w1-p1"], state="MERGED")
    usage = loop.data / "usage"
    usage.mkdir()
    (usage / "model_token_share.json").write_text(json.dumps({"windows": {
        "cumulative_since_f2": {"status": "BLOCK"}}}))
    p = loop.run([PY, loop.harness("dispatch.py"), "--mode", "single",
                  "--dry-run", "--role", "reviewer", "--force"])
    assert p.returncode == 0, p.stderr
    assert "DRY-RUN" in p.stdout
