import csv
import json
import subprocess
import sys
from pathlib import Path


PKG = Path(__file__).resolve().parents[2]
SCRIPT = PKG / "harness" / "csv_reconcile.py"


def write_csv(path, fieldnames, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader(); writer.writerows(records)


def events(data):
    return [json.loads(line) for line in (data / "events.ndjson").read_text().splitlines()]


def test_success_copies_report_and_emits_generation_stop(tmp_path):
    data, wt = tmp_path / "data", tmp_path / "wt"
    local = wt / "data" / "reports" / "p1" / "report.json"
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"packet_id": "p1", "status": "done"}))
    batch, results = tmp_path / "batch.csv", tmp_path / "results.csv"
    write_csv(batch, ["packet_id", "worktree", "local_report", "attempt", "run_id"],
              [{"packet_id": "p1", "worktree": wt, "local_report": local,
                "attempt": 2, "run_id": "p1-a2"}])
    write_csv(results, ["packet_id", "status", "summary"],
              [{"packet_id": "p1", "status": "done", "summary": "ok"}])
    result = subprocess.run([sys.executable, str(SCRIPT), "--batch-csv", str(batch),
        "--results-csv", str(results), "--data-dir", str(data)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (data / "reports" / "p1" / "report.json").exists()
    assert events(data)[0]["run_id"] == "p1-a2"
    assert events(data)[0]["event"] == "subagent_stop"


def test_missing_result_is_visible_and_idempotent(tmp_path):
    data, wt = tmp_path / "data", tmp_path / "wt"
    batch, results = tmp_path / "batch.csv", tmp_path / "results.csv"
    write_csv(batch, ["packet_id", "worktree", "local_report", "attempt", "run_id"],
              [{"packet_id": "p2", "worktree": wt,
                "local_report": wt / "data" / "reports" / "p2" / "report.json",
                "attempt": 0, "run_id": "p2-a0"}])
    write_csv(results, ["packet_id", "status", "summary"], [])
    command = [sys.executable, str(SCRIPT), "--batch-csv", str(batch),
               "--results-csv", str(results), "--data-dir", str(data)]
    assert subprocess.run(command).returncode == 1
    assert subprocess.run(command).returncode == 1
    assert [e["event"] for e in events(data)] == ["exec_failed"]


def test_stale_batch_generation_is_ignored(tmp_path):
    data, wt = tmp_path / "data", tmp_path / "wt"
    data.mkdir()
    (data / "progress_ledger.json").write_text(json.dumps({"packets": {
        "p3": {"state": "RUNNING", "attempts": 2}}}))
    local = wt / "data" / "reports" / "p3" / "report.json"
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"packet_id": "p3", "status": "done"}))
    batch, results = tmp_path / "batch.csv", tmp_path / "results.csv"
    write_csv(batch, ["packet_id", "worktree", "local_report", "attempt", "run_id"],
              [{"packet_id": "p3", "worktree": wt, "local_report": local,
                "attempt": 1, "run_id": "p3-a1"}])
    write_csv(results, ["packet_id", "status", "summary"],
              [{"packet_id": "p3", "status": "done", "summary": "late"}])
    result = subprocess.run([sys.executable, str(SCRIPT), "--batch-csv", str(batch),
        "--results-csv", str(results), "--data-dir", str(data)], capture_output=True, text=True)
    assert result.returncode == 0
    assert not (data / "events.ndjson").exists()
    assert not (data / "reports" / "p3" / "report.json").exists()


def test_result_outside_batch_fails_closed_before_events(tmp_path):
    data, wt = tmp_path / "data", tmp_path / "wt"
    batch, results = tmp_path / "batch.csv", tmp_path / "results.csv"
    write_csv(batch, ["packet_id", "worktree", "local_report", "attempt", "run_id"],
              [{"packet_id": "p1", "worktree": wt,
                "local_report": wt / "data" / "reports" / "p1" / "report.json",
                "attempt": 0, "run_id": "p1-a0"}])
    write_csv(results, ["packet_id", "status", "summary"],
              [{"packet_id": "p1", "status": "done", "summary": "ok"},
               {"packet_id": "p9", "status": "done", "summary": "rogue"}])
    result = subprocess.run([sys.executable, str(SCRIPT), "--batch-csv", str(batch),
        "--results-csv", str(results), "--data-dir", str(data)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "absent from batch" in result.stderr
    assert not (data / "events.ndjson").exists()


def test_report_packet_id_mismatch_aborts_before_publish(tmp_path):
    data, wt = tmp_path / "data", tmp_path / "wt"
    local = wt / "data" / "reports" / "p1" / "report.json"
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"packet_id": "other", "status": "done"}))
    batch, results = tmp_path / "batch.csv", tmp_path / "results.csv"
    write_csv(batch, ["packet_id", "worktree", "local_report", "attempt", "run_id"],
              [{"packet_id": "p1", "worktree": wt, "local_report": local,
                "attempt": 0, "run_id": "p1-a0"}])
    write_csv(results, ["packet_id", "status", "summary"],
              [{"packet_id": "p1", "status": "done", "summary": "ok"}])
    result = subprocess.run([sys.executable, str(SCRIPT), "--batch-csv", str(batch),
        "--results-csv", str(results), "--data-dir", str(data)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "report packet_id mismatch" in result.stderr
    assert not (data / "reports" / "p1" / "report.json").exists()
    assert not (data / "events.ndjson").exists()


def test_stamp_fingerprints_current_batch_and_results(tmp_path):
    data, wt = tmp_path / "data", tmp_path / "wt"
    local = wt / "data" / "reports" / "p1" / "report.json"
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"packet_id": "p1", "status": "done"}))
    batch, results, stamp = tmp_path / "batch.csv", tmp_path / "results.csv", tmp_path / "stamp.json"
    write_csv(batch, ["packet_id", "worktree", "local_report", "attempt", "run_id"],
              [{"packet_id": "p1", "worktree": wt, "local_report": local,
                "attempt": 0, "run_id": "p1-a0"}])
    write_csv(results, ["packet_id", "status", "summary"],
              [{"packet_id": "p1", "status": "done", "summary": "ok"}])
    result = subprocess.run([sys.executable, str(SCRIPT), "--batch-csv", str(batch),
        "--results-csv", str(results), "--data-dir", str(data), "--stamp", str(stamp)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    doc = json.loads(stamp.read_text(encoding="utf-8"))
    assert doc["schema"] == "codex-loop-csv-reconcile-stamp/v1"
    assert doc["results_size"] == results.stat().st_size
    assert doc["summary"] == {"completed": 1, "failed": 0}
    assert len(doc["batch_sha256"]) == len(doc["results_sha256"]) == 64
