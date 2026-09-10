"""Run Evidence Entry: stable index over existing durable evidence.

Covers acceptance gates D (worker historical recovery), E (historical
disposition reachable + raw evidence retained), F (reviewer recovery),
G (root decision durability) — all programmatic, zero LLM.
"""
from __future__ import annotations

import json

import run_evidence


def write_ledger(loop, packets, cursor=777):
    loop.set_ledger({"packets": packets, "event_cursor": cursor,
                     "loop_state": "planning"})


def test_index_links_reports_and_prior_attempts(loop):
    reports = loop.data / "reports" / "p1"
    reports.mkdir(parents=True)
    (reports / "report.json").write_text(
        json.dumps({"packet_id": "p1", "status": "done",
                    "summary": "the unique fact"}), encoding="utf-8")
    prev = reports / "previous"
    prev.mkdir()
    (prev / "attempt-0.json").write_text(
        json.dumps({"packet_id": "p1", "status": "failed"}), encoding="utf-8")
    write_ledger(loop, {"p1": {"state": "REPORTED", "attempts": 1,
                               "role": "worker", "task_name": "t",
                               "history": [
                                   {"ts": 1, "to": "RUNNING", "via": "x"},
                                   {"ts": 2, "to": "REPORTED", "via": "y"}]}})
    index = run_evidence.build_index(str(loop.root))
    node = index["packets"]["p1"]
    # all index paths anchor at the control root (one stable resolver)
    assert node["report"] == "data/reports/p1/report.json"
    assert node["prior_attempts"] == [
        "data/reports/p1/previous/attempt-0.json"]
    # the current disposition is part of the index (later state reachable)
    assert node["state"] == "REPORTED"
    assert node["last_transition"]["to"] == "REPORTED"
    assert index["event_cursor"] == 777
    assert (loop.data / "evidence" / "index.json").exists()


def test_dead_letter_disposition_and_raw_evidence_retained(loop):
    (loop.data / "dead_letters").mkdir(exist_ok=True)
    (loop.data / "dead_letters" / "p2.json").write_text(
        json.dumps({"packet_id": "p2", "reason": "timeout_retry_exhausted",
                    "detail": {}, "ts": 9}), encoding="utf-8")
    write_ledger(loop, {"p2": {"state": "DEAD_LETTER", "attempts": 2,
                               "history": [
                                   {"ts": 1, "to": "RUNNING", "via": "x"},
                                   {"ts": 2, "to": "TIMED_OUT", "via": "timeout"},
                                   {"ts": 3, "to": "DISPATCHABLE", "via": "retry_dispatch"},
                                   {"ts": 4, "to": "DEAD_LETTER", "via": "y"}]}})
    index = run_evidence.build_index(str(loop.root))
    node = index["packets"]["p2"]
    assert node["dead_letter"]["reason"] == "timeout_retry_exhausted"
    # full lineage visible: attempt -> timeout -> retry -> final disposition
    tos = [h["to"] for h in node["history_tail"]]
    assert tos == ["RUNNING", "TIMED_OUT", "DISPATCHABLE", "DEAD_LETTER"]


def test_record_decision_appends_and_index_picks_it_up(loop):
    write_ledger(loop, {"p3": {"state": "REPORTED", "attempts": 0,
                               "history": []}})
    ledger_before = (loop.data / "progress_ledger.json").read_text(
        encoding="utf-8")
    row = run_evidence.append_decision(
        str(loop.root), "p3", "accept", note="evidence-backed",
        refs=["reports/p3/report.json"])
    assert row["packet_id"] == "p3" and row["decision"] == "accept"
    # durable, append-only, outside the ledger (no second task authority)
    rows = [json.loads(l) for l in
            (loop.data / "decisions" / "decisions.ndjsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1 and rows[0]["evidence_refs"] == [
        "reports/p3/report.json"]
    assert (loop.data / "progress_ledger.json").read_text(
        encoding="utf-8") == ledger_before
    index = run_evidence.build_index(str(loop.root))
    assert index["packets"]["p3"]["decisions"][0]["decision"] == "accept"
    assert index["counts"]["decisions"] == 1
    # idempotent second build does not duplicate
    index = run_evidence.build_index(str(loop.root))
    assert len(index["packets"]["p3"]["decisions"]) == 1


def test_index_rebuildable_and_fail_open(loop):
    write_ledger(loop, {"p4": {"state": "RUNNING", "attempts": 0,
                               "history": []}})
    index = run_evidence.build_index(str(loop.root))
    assert index["packets"]["p4"]["state"] == "RUNNING"
    # rebuildable: identical rebuild from the same underlying files
    again = run_evidence.build_index(str(loop.root))
    assert again["packets"]["p4"] == index["packets"]["p4"]
    # fail-open: missing ledger still yields a (empty) index document
    empty = run_evidence.build_index(str(loop.root / "nope"))
    assert empty["packets"] == {}


def test_roster_rows_linked(loop):
    lifecycle = loop.data / "lifecycle"
    lifecycle.mkdir(exist_ok=True)
    (lifecycle / "exec_roster.json").write_text(json.dumps({
        "jobs": {"r9": {"packet_id": "p5", "run_id": "p5-a0-x",
                        "state": "failed", "attempt": 0, "exit_code": 2,
                        "published_report": "", "stderr_path": "s",
                        "stdout_path": "o", "started_at": 10.0}}}),
        encoding="utf-8")
    write_ledger(loop, {"p5": {"state": "RUNNING", "attempts": 0,
                               "history": []}})
    index = run_evidence.build_index(str(loop.root))
    runs = index["packets"]["p5"]["exec_runs"]
    assert runs and runs[0]["run_id"] == "p5-a0-x"
    assert runs[0]["stderr_path"] == "s"


def test_cli_build_and_show(loop):
    write_ledger(loop, {"p6": {"state": "REPORTED", "attempts": 0,
                               "history": []}})
    result = loop.run([loop.harness("run_evidence.py") and
                       __import__("sys").executable,
                       str(loop.root / "harness" / "run_evidence.py"),
                       "--root", str(loop.root), "build"])
    assert result.returncode == 0
    assert "RUN EVIDENCE ENTRY" in result.stdout
    result = loop.run([__import__("sys").executable,
                       str(loop.root / "harness" / "run_evidence.py"),
                       "--root", str(loop.root), "show"])
    assert result.returncode == 0 and "packets indexed: 1" in result.stdout
