# ============================================================================
# test_release_review_route.py -- minimal release-review hard-route tests
# Cases: pin fail-closed (TOML drift), dry-run pins the active review profile,
#        real dispatch idempotent per wave + controlled record, success and
#        failure both return to SOL_ADJUDICATE, verdict provenance matches
#        the dispatch record.
# ============================================================================
import json
import tomllib

from tests.conftest import PY


def seed_wave(loop, pids):
    for pid in pids:
        loop.write_packet(pid)
    loop.write_dag(waves=[list(pids)])
    led = loop.ledger()
    for pid in pids:
        led["packets"][pid] = {"state": "DISPATCHABLE", "history": [], "attempts": 0}
    loop.set_ledger(led)


def dispatch(loop, *args, **env):
    return loop.run([PY, loop.harness("dispatch.py"), *args], **env)


def expected_review_pin(loop):
    with (loop.root / "config" / "orchestration_policy_v2.toml").open("rb") as handle:
        models = tomllib.load(handle)["models"]
    return models["k3_model"], models["k3_reasoning"]


def test_release_review_pin_mismatch_fails_closed(loop):
    (loop.root / "agents" / "reviewer.toml").write_text(
        'name="reviewer"\nmodel="gpt-5.6"\nmodel_reasoning_effort="max"\n')
    p = dispatch(loop, "--release-review", "--dry-run")
    assert p.returncode == 1
    assert "pin mismatch" in p.stderr
    assert "DRY-RUN" not in p.stdout          # nothing dispatched unpinned


def test_release_review_sandbox_drift_fails_closed(loop):
    model, effort = expected_review_pin(loop)
    (loop.root / "agents" / "reviewer.toml").write_text(
        'name="reviewer"\nmodel=%s\nmodel_reasoning_effort=%s\n'
        'sandbox_mode="workspace-write"\n' %
        (json.dumps(model), json.dumps(effort)))
    p = dispatch(loop, "--release-review", "--dry-run")
    assert p.returncode == 1
    assert "pin mismatch" in p.stderr
    assert "DRY-RUN" not in p.stdout


def test_release_review_dry_run_pins_active_review_profile(loop):
    seed_wave(loop, ["w1-p1"])
    expected_model, expected_effort = expected_review_pin(loop)
    p = dispatch(loop, "--release-review", "--dry-run", "--wave", "0")
    assert p.returncode == 0, p.stderr
    line = next(l for l in p.stdout.splitlines() if l.startswith("DRY-RUN"))
    cmd = json.loads(line.split(": ", 1)[1])
    assert cmd[cmd.index("-m") + 1] == expected_model
    assert cmd[cmd.index("-c") + 1] == \
        "model_reasoning_effort=%s" % expected_effort
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    evs = [e for e in loop.events() if e["event"] == "dispatch_dry_run"]
    assert len(evs) == 1
    assert evs[0]["detail"]["mode"] == "release_review"
    assert evs[0]["detail"]["model"] == expected_model
    assert evs[0]["detail"]["reasoning_effort"] == expected_effort
    assert not (loop.data / "release_review" / "w0.json").exists()  # audit-only


def test_release_review_idempotent_when_record_exists(loop):
    """A persisted per-wave dispatch record makes later calls no-ops."""
    seed_wave(loop, ["w1-p1"])
    record = loop.data / "release_review" / "w0.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({
        "schema": "codex-loop-release-review-record/v1", "wave": 0,
        "packet_id": "rr-wave0", "run_id": "rr-run-1",
        "role": "reviewer", "model": "provider-b/independent-reviewer", "effort": "max",
        "mode": "release_review", "status": "dispatched", "ts": 0.0}))
    p = dispatch(loop, "--release-review", "--dry-run", "--wave", "0")
    assert p.returncode == 0, p.stderr
    assert "already dispatched" in p.stdout
    assert not [e for e in loop.events() if e["event"] == "dispatch_dry_run"]
    assert json.loads(record.read_text())["run_id"] == "rr-run-1"  # untouched


def test_release_review_launching_record_blocks_second_birth(loop):
    """The pre-birth ownership state is as idempotent as dispatched."""
    seed_wave(loop, ["w1-p1"])
    record = loop.data / "release_review" / "w0.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({
        "schema": "codex-loop-release-review-record/v1", "wave": 0,
        "packet_id": "rr-wave0", "run_id": "rr-launching",
        "role": "reviewer", "model": "provider-b/independent-reviewer", "effort": "max",
        "mode": "release_review", "status": "launching", "ts": 0.0}))
    p = dispatch(loop, "--release-review", "--wave", "0")
    assert p.returncode == 0, p.stderr
    assert "already dispatched" in p.stdout
    assert json.loads(record.read_text())["status"] == "launching"
    assert not [e for e in loop.events() if e["event"] == "dispatched"]


def test_release_review_success_returns_to_sol_adjudicate(loop):
    pid = "rr-wave0"
    led = loop.ledger()
    led["packets"][pid] = {"state": "DISPATCHABLE", "history": [], "attempts": 0,
                           "release_review": True, "release_review_wave": 0}
    loop.set_ledger(led)
    loop.write_report(pid)
    for ev in ("dispatched", "subagent_stop", "review_verdict_pass"):
        loop.append_event(pid, ev)
    rc, states = loop.step()
    assert rc == 0
    assert states[pid] == "SOL_ADJUDICATE"     # never a direct release
    assert loop.history(pid)[-1]["t"] == 24
    assert loop.sol_wakes() == []


def test_release_review_failure_returns_to_sol_adjudicate(loop):
    pid = "rr-wave0"
    led = loop.ledger()
    led["packets"][pid] = {"state": "RUNNING", "history": [], "attempts": 0,
                           "release_review": True, "release_review_wave": 0}
    loop.set_ledger(led)
    loop.append_event(pid, "exec_failed", {"why": "reviewer crashed"})
    rc, states = loop.step()
    assert rc == 0                              # no dead letter, no Sol wake
    assert states[pid] == "SOL_ADJUDICATE"
    assert loop.history(pid)[-1]["t"] == 26


def test_verdict_provenance_must_match_dispatch_record(loop, tmp_path):
    rec = tmp_path / "record.json"
    rec.write_text(json.dumps({"run_id": "rr-run-1", "model": "provider-b/independent-reviewer",
                               "effort": "max", "wave": 0,
                               "status": "dispatched"}))
    ok = tmp_path / "verdict_ok.json"
    ok.write_text(json.dumps({"verdict": "APPROVED", "findings": [],
                              "provenance": {"run_id": "rr-run-1",
                                             "model": "provider-b/independent-reviewer",
                                             "effort": "max", "wave": 0}}))
    p = loop.run([PY, loop.harness("verdict_check.py"), "--verdict", ok,
                  "--dispatch-record", rec])
    assert p.returncode == 0, p.stderr
    bad = tmp_path / "verdict_bad.json"
    bad.write_text(json.dumps({"verdict": "APPROVED", "findings": [],
                               "provenance": {"run_id": "other-run",
                                              "model": "provider-b/independent-reviewer",
                                              "effort": "max", "wave": 0}}))
    p = loop.run([PY, loop.harness("verdict_check.py"), "--verdict", bad,
                  "--dispatch-record", rec])
    assert p.returncode == 1
    assert "PROVENANCE_MISMATCH" in p.stderr   # fail-closed on drift
