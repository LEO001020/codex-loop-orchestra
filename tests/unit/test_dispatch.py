# ============================================================================
# test_dispatch.py — Unit tests for harness/dispatch.py
# Cases: single-mode dry run (normal), CSV batch pack (normal), state filter
#        + --force (boundary), nothing dispatchable (boundary, rc=2), packet
#        missing required field (failure, rc=1), missing wave (failure, rc=1),
#        real single spawn against the mock codex binary (B-level).
# ============================================================================
import csv
import json
import time

from conftest import PY, MOCK


def seed_wave(loop, pids, state="DISPATCHABLE"):
    for pid in pids:
        loop.write_packet(pid, paths=["src/alpha/"] if pid.endswith("1") else ["src/beta/"])
    loop.write_dag(waves=[list(pids)])
    led = loop.ledger()
    for pid in pids:
        led["packets"][pid] = {"state": state, "history": [], "attempts": 0}
    loop.set_ledger(led)


def dispatch(loop, *args, **env):
    return loop.run([PY, loop.harness("dispatch.py"), *args], **env)


def test_single_mode_dry_run_appends_dispatched_events(loop):
    seed_wave(loop, ["w1-p1", "w1-p2"])
    p = dispatch(loop, "--mode", "single", "--dry-run")
    assert p.returncode == 0, p.stderr
    assert p.stdout.count("DRY-RUN") == 2
    evs = [e for e in loop.events() if e["event"] == "dispatched"]
    assert {e["packet_id"] for e in evs} == {"w1-p1", "w1-p2"}
    assert all(e["detail"]["mode"] == "single" for e in evs)


def test_csv_mode_emits_batch_pack(loop):
    seed_wave(loop, ["w1-p1", "w1-p2"])
    p = dispatch(loop, "--mode", "csv", "--dry-run")
    assert p.returncode == 0, p.stderr
    csv_path = loop.data / "dispatch" / "batch_w0.csv"
    call_path = loop.data / "dispatch" / "batch_w0.call.json"
    assert csv_path.exists() and call_path.exists()
    rows = list(csv.DictReader(csv_path.open()))
    assert [r["packet_id"] for r in rows] == ["w1-p1", "w1-p2"]
    assert set(rows[0]) == {"packet_id", "goal", "authorized_paths",
                            "acceptance", "constraints", "worktree"}
    call = json.loads(call_path.read_text())
    assert call["tool"] == "spawn_agents_on_csv"
    assert call["id_column"] == "packet_id"
    assert "{goal}" in call["instruction"] and "{worktree}" in call["instruction"]
    assert call["max_concurrency"] <= 4          # config [agents] cap
    assert set(call["output_schema"]) == {"status", "summary", "report_path"}


def test_state_filter_skips_non_dispatchable_and_force_overrides(loop):
    seed_wave(loop, ["w1-p1"], state="PLANNED")  # not yet DISPATCHABLE
    p = dispatch(loop, "--dry-run")
    assert p.returncode == 2
    assert "nothing dispatchable" in p.stdout
    p = dispatch(loop, "--dry-run", "--force")
    assert p.returncode == 0


def test_packet_missing_required_field_is_error(loop):
    (loop.data / "packets" / "bad.json").write_text('{"packet_id": "bad", "goal": "g"}')
    loop.write_dag(waves=[["bad"]])
    p = dispatch(loop, "--dry-run", "--force")
    assert p.returncode == 1
    assert "missing required field" in p.stderr


def test_missing_wave_is_error(loop):
    loop.write_dag(waves=[])
    p = dispatch(loop, "--dry-run", "--force", "--wave", "3")
    assert p.returncode == 1
    assert "no wave 3" in p.stderr


def test_single_spawn_with_mock_codex_lands_report(repo_loop):
    """B-level: real dispatch path, `codex` resolved to the mock binary."""
    loop = repo_loop
    seed_wave(loop, ["w1-p1"])
    path = "%s:%s" % (MOCK / "bin", loop.env()["PATH"])
    p = dispatch(loop, "--mode", "single", PATH=path)
    assert p.returncode == 0, p.stderr
    report = loop.data / "reports" / "w1-p1" / "report.json"
    deadline = time.time() + 15                  # Popen is fire-and-forget
    while time.time() < deadline and not report.exists():
        time.sleep(0.1)
    assert report.exists(), "mock codex should land the report file"
    assert json.loads(report.read_text())["status"] == "done"
    ev_names = [e["event"] for e in loop.events() if e["packet_id"] == "w1-p1"]
    assert "dispatched" in ev_names
    assert "subagent_stop" in ev_names           # written by mock codex
    # worktree was physically allocated for isolation
    assert (loop.wt_dir / "w1-p1").is_dir()
