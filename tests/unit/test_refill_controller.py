import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


PKG = Path(__file__).resolve().parents[2]
HARNESS = PKG / "harness"
SPEC = importlib.util.spec_from_file_location("refill_controller_test", HARNESS / "refill_controller.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def write_config(root, target=16, low=12, cap=50,
                 k3_target=None, k3_low=None, threshold=None):
    cfg = root / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    lines = ["[agents]",
             "normal_wave_concurrency = %d" % target,
             "normal_wave_low_water = %d" % low,
             "max_concurrent_threads_per_session = %d" % cap]
    if k3_target is not None:
        lines.append("normal_k3_wave_concurrency = %d" % k3_target)
    if k3_low is not None:
        lines.append("normal_k3_wave_low_water = %d" % k3_low)
    if threshold is not None:
        lines.append("idle_reclaim_threshold = %d" % threshold)
    (cfg / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_queue(root, v4=0, k3=0):
    d = root / "data" / "refill"
    d.mkdir(parents=True, exist_ok=True)
    (d / "work_queue.json").write_text(json.dumps(
        {"schema": MOD.QUEUE_SCHEMA, "pools": {"v4": v4, "k3": k3}}), encoding="utf-8")


def write_roster(root, agents=None):
    """agents: {aid: model} (status running) or {aid: (model, status)}."""
    items = {}
    for aid, value in (agents or {}).items():
        if isinstance(value, (tuple, list)):
            model, status = value[0], value[1]
        else:
            model, status = value, "running"
        items[aid] = {"agent_id": aid, "model": model, "status": status}
    life = root / "data" / "lifecycle"
    life.mkdir(parents=True, exist_ok=True)
    (life / "native_roster.json").write_text(json.dumps(
        {"schema": "codex-loop-native-roster/v1", "pending": [], "agents": items}),
        encoding="utf-8")


def state(root):
    return json.loads((root / "data" / "refill" / "refill_state.json").read_text(encoding="utf-8"))


def events(root):
    p = root / "data" / "refill" / "events.ndjson"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x] if p.exists() else []


def test_config_reads_target_low_water_cap_with_defaults(tmp_path):
    ctl = MOD.RefillController(tmp_path)
    assert (ctl.target("v4"), ctl.low_water("v4"), ctl.cap()) == (16, 12, 50)
    assert (ctl.target("k3"), ctl.low_water("k3")) == (16, 12)
    write_config(tmp_path, target=16, low=12, cap=50)
    ctl = MOD.RefillController(tmp_path)
    assert (ctl.target("v4"), ctl.low_water("v4"), ctl.cap()) == (16, 12, 50)
    write_config(tmp_path, target=20, low=10, cap=60)
    ctl = MOD.RefillController(tmp_path)
    assert (ctl.target("v4"), ctl.low_water("v4"), ctl.cap()) == (20, 10, 60)
    write_config(tmp_path, target=16, low=99, cap=50)  # low water clamps to target
    assert MOD.RefillController(tmp_path).low_water("v4") == 16
    write_config(tmp_path, k3_target=20, k3_low=14)
    ctl = MOD.RefillController(tmp_path)
    assert (ctl.target("v4"), ctl.low_water("v4")) == (16, 12)
    assert (ctl.target("k3"), ctl.low_water("k3")) == (20, 14)


def test_first_wave_drop_rearms_refill_not_only_first_wave(tmp_path):
    write_config(tmp_path)
    write_queue(tmp_path, v4=30)
    ctl = MOD.RefillController(tmp_path)
    s0 = ctl.recompute()  # nothing running yet
    assert s0["refill_required"] is True
    assert s0["deficit"]["total"] == 16
    assert s0["model_pool"] == ["v4"]
    write_roster(tmp_path, {"a%d" % i: "provider-a/v4-executor" for i in range(16)})
    assert ctl.recompute()["refill_required"] is False
    # first wave drops back below the low-water mark: refill must re-arm
    write_roster(tmp_path, {"a%d" % i: "provider-a/v4-executor" for i in range(10)})
    s = ctl.recompute()
    assert s["refill_required"] is True
    assert s["deficit"]["total"] == 6
    assert s["model_pool"] == ["v4"]
    assert sum(1 for e in events(tmp_path) if e["event"] == "refill_required") == 2


def test_continuous_refill_keeps_writing_until_queue_empty(tmp_path):
    write_config(tmp_path)
    write_queue(tmp_path, v4=40)
    ctl = MOD.RefillController(tmp_path)
    waves = 0
    for _ in range(3):
        write_roster(tmp_path, {"a%d" % i: "provider-a/v4-executor" for i in range(8)})
        s = ctl.recompute()
        assert s["refill_required"] is True
        assert s["deficit"]["total"] == 8
        waves += 1
        write_roster(tmp_path, {"a%d" % i: "provider-a/v4-executor" for i in range(16)})
        assert ctl.recompute()["refill_required"] is False
    write_queue(tmp_path, v4=0)
    s = ctl.recompute()
    assert s["refill_required"] is False
    assert s["queue_empty"] is True
    assert s["deficit"]["total"] == 0
    assert s["debt_held"]["total"] == 0
    assert sum(1 for e in events(tmp_path) if e["event"] == "refill_required") == waves == 3


def test_queue_empty_release_and_explicit_release_finalize(tmp_path):
    write_config(tmp_path)
    write_queue(tmp_path, v4=10)
    ctl = MOD.RefillController(tmp_path)
    assert ctl.recompute()["refill_required"] is True
    write_roster(tmp_path, {("i%d" % i): ("provider-a/v4-executor", "idle")
                            for i in range(6)})
    s = MOD.RefillController(tmp_path).recompute()
    assert s["refill_required"] is True
    assert s["reuse_required"] is True and s["reuse"]["v4"] == [
        "i0", "i1", "i2", "i3", "i4", "i5"]
    ctl.queue_clear()
    s = ctl.recompute()
    assert s["refill_required"] is False and s["queue_empty"] is True
    assert s["deficit"]["total"] == 0 and s["debt_held"]["total"] == 0
    assert s["reuse_required"] is False
    assert s["idle_reclaim_required"] is False
    assert s["host_close_agent"] == []
    assert s["reason"] == "queue_empty"

    write_queue(tmp_path, v4=10)
    assert ctl.recompute()["refill_required"] is True
    s = ctl.release_finalize()
    assert s["refill_required"] is False and s["finalized"] is True
    assert s["reason"] == "release_finalize"
    assert s["deficit"]["total"] == 0 and s["debt_held"]["total"] == 0
    assert s["reuse_required"] is False and s["idle_reclaim_required"] is False
    assert ctl.recompute()["refill_required"] is False  # sticky until resumed
    ctl.resume()
    assert ctl.recompute()["refill_required"] is True


def test_eleven_idle_among_sixteen_yield_deficit_eleven(tmp_path):
    write_config(tmp_path)
    write_queue(tmp_path, v4=30)
    roster = {"r%d" % i: "provider-a/v4-executor" for i in range(5)}
    roster.update({"i%d" % i: ("provider-a/v4-executor", "idle")
                   for i in range(11)})
    write_roster(tmp_path, roster)
    s = MOD.RefillController(tmp_path).recompute()
    assert s["active"] == {"total": 5, "v4": 5, "k3": 0}
    assert s["effective_concurrency"] == 5
    assert s["idle"] == {"total": 11, "v4": 11, "k3": 0}
    assert s["refill_required"] is True
    assert s["deficit"]["total"] == 11  # target 16 - 5 running; idle counts 0
    assert s["debt_held"]["total"] == 11
    assert s["reuse_required"] is True
    assert len(s["reuse"]["v4"]) == 11  # idle agents suggested for assignment


def test_reuse_capped_by_pending_and_excess_idle_is_reclaimed(tmp_path):
    write_config(tmp_path)
    write_queue(tmp_path, v4=5)
    roster = {"i%d" % i: ("provider-a/v4-executor", "idle") for i in range(11)}
    write_roster(tmp_path, roster)
    s = MOD.RefillController(tmp_path).recompute()
    assert s["reuse_required"] is True
    assert len(s["reuse"]["v4"]) == 5          # only 5 pending items
    assert s["idle_reclaim_required"] is True  # 6 excess idle beyond threshold 0
    assert len(s["host_close_agent"]) == 6
    assert set(s["host_close_agent"]) == {"i%d" % i for i in range(5, 11)}


def test_completed_and_shutdown_pending_do_not_count_and_are_reclaimed(tmp_path):
    write_config(tmp_path)
    write_queue(tmp_path, v4=20)
    roster = {"r%d" % i: "provider-a/v4-executor" for i in range(10)}
    roster.update({"c%d" % i: ("provider-a/v4-executor", "completed")
                   for i in range(4)})
    roster.update({"s%d" % i: ("provider-a/v4-executor", "shutdown_pending")
                   for i in range(2)})
    write_roster(tmp_path, roster)
    s = MOD.RefillController(tmp_path).recompute()
    assert s["active"]["total"] == 10
    assert s["completed"] == {"total": 4, "v4": 4, "k3": 0}
    assert s["shutdown_pending"] == {"total": 2, "v4": 2, "k3": 0}
    assert s["deficit"]["total"] == 6  # completed/shutdown_pending count 0
    assert s["idle_reclaim_required"] is True
    assert sorted(s["host_close_agent"]) == (
        ["c0", "c1", "c2", "c3", "s0", "s1"])


def test_idle_occupying_full_cap_allows_reclaim_then_refill(tmp_path):
    write_config(tmp_path)
    write_queue(tmp_path, v4=20)
    write_roster(tmp_path, {"i%d" % i: ("provider-a/v4-executor", "idle")
                            for i in range(50)})
    s = MOD.RefillController(tmp_path).recompute()
    assert s["cap"] == 50
    assert s["active"]["total"] == 0 and s["idle"]["total"] == 50
    assert s["occupied"]["total"] == 50
    assert s["spawn_capacity_now"] == 0
    assert s["refill_required"] is True
    assert s["deficit"]["total"] == 0    # no free slot until idle is reclaimed
    assert s["debt_held"]["total"] == 0  # demand stays visible via refill_required
    assert s["reclaim_first"] is True    # close idle slots first, then refill
    assert s["idle_reclaim_required"] is True
    assert len(s["host_close_agent"]) == 30  # 20 reused, 30 excess idle closed
    # after reclaiming every idle slot, the full deficit reappears
    write_roster(tmp_path, {})
    s = MOD.RefillController(tmp_path).recompute()
    assert s["deficit"]["total"] == 16


def test_idle_reclaim_threshold_is_configurable(tmp_path):
    write_config(tmp_path, target=16, low=12, cap=50)
    write_queue(tmp_path, v4=0, k3=10)
    write_roster(tmp_path, {"i%d" % i: ("provider-b/k3-reviewer", "idle")
                            for i in range(11)})  # idle k3 with pending k3 work
    s = MOD.RefillController(tmp_path).recompute()
    assert s["refill_required_by_pool"] == {"v4": False, "k3": True}
    assert s["reuse_required"] is True and len(s["reuse"]["k3"]) == 10
    assert s["idle_reclaim_required"] is True       # 1 excess idle > threshold 0
    assert s["idle_reclaim_required_by_pool"] == {"v4": False, "k3": True}
    assert len(s["host_close_agent"]) == 1
    write_config(tmp_path, target=16, low=12, cap=50, threshold=5)
    s = MOD.RefillController(tmp_path).recompute()
    assert s["idle_reclaim_threshold"] == 5
    assert s["idle_reclaim_required"] is False     # 1 excess idle <= threshold 5
    assert s["host_close_agent_required"] is False


def test_v4_k3_pool_split_and_model_classification(tmp_path):
    write_config(tmp_path)
    write_queue(tmp_path, v4=8, k3=4)
    write_roster(tmp_path, {
        "w1": "provider-a/v4-executor", "w2": "provider-a/v4-executor",
        "w3": "provider-a/v4-executor", "w4": "provider-a/v4-executor",
    })
    s = MOD.RefillController(tmp_path).recompute()
    assert s["active"] == {"total": 4, "v4": 4, "k3": 0}
    assert s["refill_required"] is True
    assert s["deficit"] == {"total": 12, "v4": 8, "k3": 4}


def test_dual_pool_independent_watermarks_reach_target_32(tmp_path):
    write_config(tmp_path)
    write_queue(tmp_path, v4=30, k3=30)
    s = MOD.RefillController(tmp_path).recompute()
    assert s["target"] == {"v4": 16, "k3": 16}
    assert s["target_total"] == 32
    assert s["refill_required_by_pool"] == {"v4": True, "k3": True}
    assert s["refill_required"] is True
    assert s["deficit"] == {"total": 32, "v4": 16, "k3": 16}
    assert s["debt_held"] == {"total": 32, "v4": 16, "k3": 16}
    assert s["model_pool"] == ["k3", "v4"]
    assert s["active"]["total"] + s["deficit"]["total"] <= s["cap"] == 50


def test_k3_empty_queue_refills_only_v4(tmp_path):
    write_config(tmp_path)
    write_queue(tmp_path, v4=30, k3=0)
    s = MOD.RefillController(tmp_path).recompute()
    assert s["refill_required_by_pool"] == {"v4": True, "k3": False}
    assert s["deficit"] == {"total": 16, "v4": 16, "k3": 0}
    assert s["model_pool"] == ["v4"]
    assert s["debt_held"] == {"total": 16, "v4": 16, "k3": 0}


def test_v4_full_k3_dropped_to_five_refills_only_k3(tmp_path):
    write_config(tmp_path)
    write_queue(tmp_path, v4=30, k3=30)
    roster = {"v%d" % i: "provider-a/v4-executor" for i in range(16)}
    roster.update({"k%d" % i: "provider-b/k3-reviewer" for i in range(5)})
    write_roster(tmp_path, roster)
    s = MOD.RefillController(tmp_path).recompute()
    assert s["refill_required_by_pool"] == {"v4": False, "k3": True}
    assert s["deficit"] == {"total": 11, "v4": 0, "k3": 11}
    assert s["model_pool"] == ["k3"]
    assert s["debt_held"] == {"total": 11, "v4": 0, "k3": 11}


def test_cap_50_read_from_config_and_ceiling_enforced(tmp_path):
    write_config(tmp_path, target=16, low=12, cap=50)
    write_queue(tmp_path, v4=100)
    write_roster(tmp_path, {"a%d" % i: "provider-a/v4-executor" for i in range(10)})
    s = MOD.RefillController(tmp_path).recompute()
    assert s["cap"] == 50
    assert s["deficit"]["total"] == 6  # min(100, 16-10, 50-10)
    assert s["active"]["total"] + s["deficit"]["total"] <= s["cap"]

    write_config(tmp_path, target=16, low=12, cap=14)
    s = MOD.RefillController(tmp_path).recompute()
    assert s["cap"] == 14
    assert s["deficit"]["total"] == 4  # ceiling binds: min(100, 6, 14-10)
    assert s["active"]["total"] + s["deficit"]["total"] <= 14


def test_fail_visible_keeps_debt_and_never_pretends_refilled(tmp_path):
    write_config(tmp_path)  # no refill_direct_spawn -> host spawn unavailable
    write_queue(tmp_path, v4=30)
    ctl = MOD.RefillController(tmp_path)
    s = ctl.recompute()
    assert s["refill_required"] is True
    assert s["spawn_mechanism"] == "host_spawn_required"
    assert s["host_spawn_available"] is False
    assert s["refilled"] is False
    assert s["debt_held"]["total"] == s["deficit"]["total"] == 16

    ctl.spawn_intent(6, "v4")
    s = ctl.read_state()
    assert s["refill_required"] is True and s["deficit"]["total"] == 16
    assert s["debt_held"]["total"] == 16  # intent never clears debt
    intent = [e for e in events(tmp_path) if e["event"] == "spawn_intent"]
    assert intent[0]["count"] == 6 and intent[0]["debt_cleared"] is False

    # only observed running agents reduce the deficit
    write_roster(tmp_path, {"a%d" % i: "provider-a/v4-executor" for i in range(6)})
    s = ctl.recompute()
    assert s["refill_required"] is True and s["deficit"]["total"] == 10
    assert s["debt_held"]["total"] == 10


def test_queue_sync_ledger_counts_ready_packets(tmp_path):
    write_config(tmp_path)
    led = tmp_path / "data"
    led.mkdir(parents=True, exist_ok=True)
    (led / "progress_ledger.json").write_text(json.dumps({"packets": {
        "p1": {"state": "DISPATCHABLE"}, "p2": {"state": "DISPATCHABLE"},
        "p3": {"state": "IN_PROGRESS"}, "p4": {"state": "DONE"}}}), encoding="utf-8")
    ctl = MOD.RefillController(tmp_path)
    ctl.queue_sync_ledger("v4")
    s = ctl.recompute()
    assert s["pending"] == {"total": 2, "v4": 2, "k3": 0}
    assert s["refill_required"] is True and s["deficit"]["total"] == 2


def test_cli_status_recompute_and_release_finalize(tmp_path):
    write_config(tmp_path)
    write_queue(tmp_path, v4=8)
    env = {**os.environ, "LOOP_ROOT": str(tmp_path)}
    p = subprocess.run([sys.executable, str(HARNESS / "refill_controller.py"), "--recompute"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["refill_required"] is True and out["deficit"]["total"] == 8
    p = subprocess.run([sys.executable, str(HARNESS / "refill_controller.py"), "--release-finalize"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["refill_required"] is False and out["finalized"] is True
    p = subprocess.run([sys.executable, str(HARNESS / "refill_controller.py"), "--status"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["finalized"] is True
