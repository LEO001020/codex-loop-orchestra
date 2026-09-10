"""Current Run View: deterministic projection of canonical state.

Covers acceptance gates C (view from canonical state with cursor), N
(prompt boundedness) and the fail-open contract of harness/run_view.py.
"""
from __future__ import annotations

import json

import pytest

import run_view


def write_ledger(loop, packets, cursor=1234):
    loop.set_ledger({"packets": packets, "event_cursor": cursor,
                     "loop_state": "planning", "schema":
                     "codex-loop-statemachine/v2"})


def make_entry(state="RUNNING", attempt=0, role="worker", task="t",
               paths=None, history=None, **extra):
    entry = {
        "state": state, "attempts": attempt, "role": role,
        "task_name": task, "authorized_paths": paths or ["src/a.py"],
        "cwd": "E:\\tmp\\wt", "history": history or [{"ts": 100.0, "to": state,
                                                      "via": "x"}],
    }
    entry.update(extra)
    return entry


def test_view_shows_active_work_with_scope_and_cursor(loop):
    write_ledger(loop, {
        "p-running": make_entry("RUNNING", task="event persistence",
                                paths=["harness/events/**"]),
        "p-review": make_entry("REPORTED", role="reviewer", task="review P17"),
    })
    view = run_view.render_view(str(loop.root))
    assert "state_cursor: 1234" in view
    assert "RUN CONTEXT" in view
    assert "p-running" in view and "RUNNING" in view
    assert "task: event persistence" in view
    assert "scope: harness/events/**" in view
    assert "p-review" in view and "REPORTED" in view
    # provenance: view names the ledger as the only authority
    assert "only" in view and "authority" in view


def test_view_terminal_and_awaiting_sections(loop):
    (loop.data / "dead_letters").mkdir(exist_ok=True)
    (loop.data / "dead_letters" / "p-dead.json").write_text(
        json.dumps({"packet_id": "p-dead", "reason": "off_table_event",
                    "detail": {}, "ts": 5}), encoding="utf-8")
    (loop.data / "reports" / "p-done").mkdir(parents=True, exist_ok=True)
    (loop.data / "reports" / "p-done" / "report.json").write_text(
        json.dumps({"packet_id": "p-done", "status": "done"}), encoding="utf-8")
    write_ledger(loop, {
        "p-dead": make_entry("DEAD_LETTER", history=[
            {"ts": 1, "to": "RUNNING", "via": "dispatched"},
            {"ts": 2, "to": "DEAD_LETTER", "via": "off_table_event"}]),
        "p-done": make_entry("DONE", history=[
            {"ts": 3, "to": "MERGED", "via": "merged"},
            {"ts": 4, "to": "DONE", "via": "release"}]),
        "p-adju": make_entry("SOL_ADJUDICATE"),
        "p-wave": make_entry("WAVE_DONE_READY"),
    })
    view = run_view.render_view(str(loop.root))
    assert "AWAITING ROOT DECISION" in view
    assert "p-adju" in view and "p-wave" in view
    assert "p-dead" in view and "reason: off_table_event" in view
    assert "p-done" in view and "data/reports/p-done/report.json" in view


def test_view_is_bounded(loop):
    packets = {("p%02d" % i): make_entry("RUNNING", task="t%d" % i)
               for i in range(30)}
    write_ledger(loop, packets)
    view = run_view.render_view(str(loop.root), max_active=10)
    assert "+20 more" in view
    # hard bound: header + counts + 10 items (2 lines each) + fixed tail
    assert len(view.splitlines()) < 40
    # worker block is bounded regardless of how many packets are active
    block = run_view.worker_context_block(str(loop.root), max_active=12)
    assert len(block.splitlines()) < 45


def test_snapshot_json_shape(loop):
    write_ledger(loop, {"p1": make_entry("RUNNING"),
                        "p2": make_entry("MERGED")})
    doc = run_view.snapshot(str(loop.root))
    assert doc["schema"] == "codex-loop-run-view/v1"
    assert doc["event_cursor"] == 1234
    assert [n["packet_id"] for n in doc["active"]] == ["p1"]
    assert [n["packet_id"] for n in doc["terminal_recent"]] == ["p2"]
    assert doc["counts"] == {"RUNNING": 1, "MERGED": 1}


def test_view_fail_open_on_corrupt_or_missing_state(loop):
    (loop.data / "progress_ledger.json").write_text("{corrupt", encoding="utf-8")
    view = run_view.render_view(str(loop.root))
    assert "RUN CONTEXT" in view and "no packets" in view
    empty = run_view.render_view(str(loop.root / "nonexistent-root"))
    assert "RUN CONTEXT" in empty


def test_roster_overlay_marks_live(loop):
    roster_dir = loop.data / "lifecycle"
    roster_dir.mkdir(exist_ok=True)
    (roster_dir / "exec_roster.json").write_text(json.dumps({
        "schema": "codex-loop-exec-roster/v2",
        "jobs": {"r1": {"packet_id": "p-live", "state": "running",
                        "started_at": 50.0, "run_id": "p-live-a0-x"}},
    }), encoding="utf-8")
    write_ledger(loop, {"p-live": make_entry("RUNNING")})
    doc = run_view.snapshot(str(loop.root))
    assert doc["active"][0]["live"] == "running"
    view = run_view.render_view(str(loop.root))
    assert "live:running" in view


def test_cli_json_and_text(loop):
    write_ledger(loop, {"p1": make_entry("RUNNING")})
    result = loop.run([loop.harness("run_view.py") and
                       __import__("sys").executable,
                       str(loop.root / "harness" / "run_view.py"),
                       "--root", str(loop.root), "--json"])
    assert result.returncode == 0
    doc = json.loads(result.stdout)
    assert doc["counts"].get("RUNNING") == 1
    result = loop.run([__import__("sys").executable,
                       str(loop.root / "harness" / "run_view.py"),
                       "--root", str(loop.root)])
    assert result.returncode == 0 and "ACTIVE WORK" in result.stdout
