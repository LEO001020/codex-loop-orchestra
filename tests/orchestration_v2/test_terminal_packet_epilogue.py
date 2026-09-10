import json
import os

from orchestration_common import LoopPaths, atomic_write_json
from terminal_packet_epilogue import normalize_legacy_reports
from terminal_packet_epilogue import route_terminal_retries
from terminal_packet_epilogue import run


def test_legacy_plain_report_requires_matching_completed_generation(tmp_path):
    paths = LoopPaths.resolve(tmp_path)
    packet = "p1"
    report = paths.data / "reports" / packet / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text("plain final", encoding="utf-8")
    atomic_write_json(paths.ledger, {"packets": {packet: {
        "state": "RUNNING", "current_run_id": "run-1",
        "history": [], "attempts": 0,
    }}})
    atomic_write_json(paths.data / "lifecycle" / "exec_roster.json", {
        "jobs": {packet: {"state": "completed", "exit_code": 0,
                           "run_id": "run-1"}}})

    assert normalize_legacy_reports(paths) == [packet]
    value = json.loads(report.read_text(encoding="utf-8"))
    assert value["packet_id"] == packet and value["run_id"] == "run-1"
    assert value["summary"] == "plain final"


def test_legacy_plain_report_rejects_stale_generation(tmp_path):
    paths = LoopPaths.resolve(tmp_path)
    report = paths.data / "reports" / "p1" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text("stale", encoding="utf-8")
    atomic_write_json(paths.ledger, {"packets": {"p1": {
        "state": "RUNNING", "current_run_id": "new",
        "history": [], "attempts": 1,
    }}})
    atomic_write_json(paths.data / "lifecycle" / "exec_roster.json", {
        "jobs": {"p1": {"state": "completed", "exit_code": 0,
                           "run_id": "old"}}})
    assert normalize_legacy_reports(paths) == []
    assert report.read_text(encoding="utf-8") == "stale"


def test_terminal_runner_consumes_durable_request_and_clears_marker(
        tmp_path, monkeypatch):
    paths = LoopPaths.resolve(tmp_path)
    orchestration = paths.data / "orchestration"
    orchestration.mkdir(parents=True)
    atomic_write_json(orchestration / "terminal_requests.json",
                      {"requested_seq": 2, "consumed_seq": 0})
    atomic_write_json(orchestration / "terminal_epilogue.pid",
                      {"pid": os.getpid(), "proc_start_ticks": 456})
    atomic_write_json(paths.ledger, {"packets": {}, "event_cursor": 0})
    monkeypatch.setattr("terminal_packet_epilogue.run_refill_once",
                        lambda root, dry_run=False: (0, {"status": "idle"}))
    result = run(tmp_path)
    requests = json.loads((orchestration / "terminal_requests.json").read_text())
    assert result["status"] == "PASS"
    assert requests["consumed_seq"] == requests["requested_seq"] == 2
    assert not (orchestration / "terminal_epilogue.pid").exists()


def test_terminal_runner_drains_request_arriving_during_first_pass(
        tmp_path, monkeypatch):
    paths = LoopPaths.resolve(tmp_path)
    orchestration = paths.data / "orchestration"
    orchestration.mkdir(parents=True)
    request_path = orchestration / "terminal_requests.json"
    atomic_write_json(request_path, {"requested_seq": 1, "consumed_seq": 0})
    atomic_write_json(orchestration / "terminal_epilogue.pid",
                      {"pid": os.getpid(), "proc_start_ticks": 456})
    atomic_write_json(paths.ledger, {"packets": {}, "event_cursor": 0})
    calls = []

    def refill(root, dry_run=False):
        calls.append(1)
        if len(calls) == 1:
            atomic_write_json(request_path,
                              {"requested_seq": 2, "consumed_seq": 0})
        return 0, {"status": "idle"}

    monkeypatch.setattr("terminal_packet_epilogue.run_refill_once", refill)
    result = run(tmp_path)
    requests = json.loads(request_path.read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert result["status"] == "PASS"
    assert requests["consumed_seq"] == requests["requested_seq"] == 2


def test_terminal_runner_does_not_clear_newer_runner_marker(tmp_path, monkeypatch):
    paths = LoopPaths.resolve(tmp_path)
    orchestration = paths.data / "orchestration"
    orchestration.mkdir(parents=True)
    atomic_write_json(orchestration / "terminal_requests.json",
                      {"requested_seq": 1, "consumed_seq": 0})
    atomic_write_json(orchestration / "terminal_epilogue.pid",
                      {"pid": os.getpid() + 1, "proc_start_ticks": 789})
    atomic_write_json(paths.ledger, {"packets": {}, "event_cursor": 0})
    monkeypatch.setattr("terminal_packet_epilogue.run_refill_once",
                        lambda root, dry_run=False: (0, {"status": "idle"}))
    run(tmp_path)
    marker = json.loads((orchestration / "terminal_epilogue.pid").read_text())
    assert marker["pid"] == os.getpid() + 1


def test_retry_router_only_invokes_failed_and_timed_out(tmp_path, monkeypatch):
    paths = LoopPaths.resolve(tmp_path)
    calls = []

    class Result:
        returncode = 0
        stdout = '{"action":"retry"}'
        stderr = ""

    monkeypatch.setattr("terminal_packet_epilogue.subprocess.run",
                        lambda argv, **kwargs: calls.append((argv, kwargs)) or Result())
    routed = route_terminal_retries(
        paths, {"failed": "FAILED", "timeout": "TIMED_OUT",
                "done": "REPORTED", "ready": "DISPATCHABLE"})
    assert [item["packet_id"] for item in routed] == ["failed", "timeout"]
    assert all("retry.py" in call[0][1] for call in calls)
