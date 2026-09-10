#!/usr/bin/env python3
"""Light terminal transaction: consume lifecycle events, then refill packets."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("LOOP_ROOT", HERE.parent)).resolve()
sys.path.insert(0, str(HERE))

from orchestration_common import LoopPaths, atomic_write_json, file_lock  # noqa: E402
from orchestration_common import read_json  # noqa: E402
from refill_consumer_v2 import run_once as run_refill_once  # noqa: E402
from statemachine_v2 import StateMachine  # noqa: E402


def _failure_text(paths: LoopPaths, packet_id: str, state: str) -> str:
    roster = read_json(paths.data / "lifecycle" / "exec_roster.json",
                       {"jobs": {}}) or {"jobs": {}}
    job = (roster.get("jobs") or {}).get(packet_id, {})
    failure = job.get("failure") if isinstance(job, dict) else {}
    parts = [state]
    if isinstance(failure, dict):
        parts.extend(str(failure.get(key) or "")
                     for key in ("why", "error", "stderr_tail"))
    if isinstance(job, dict):
        parts.extend(str(job.get(key) or "")
                     for key in ("stop_reason", "exit_code"))
        stderr_path = job.get("stderr_path")
        if stderr_path:
            try:
                candidate = Path(str(stderr_path)).resolve()
                candidate.relative_to(paths.root)
                parts.append(candidate.read_text(
                    encoding="utf-8", errors="replace")[-8192:])
            except (OSError, ValueError):
                pass
    text = "\n".join(part for part in parts if part).strip()
    return text[:12000] or state


def route_terminal_retries(paths: LoopPaths,
                           states: dict[str, str]) -> list[dict[str, object]]:
    """Run the deterministic retry table for newly failed canonical packets."""
    routed: list[dict[str, object]] = []
    for packet_id, state in states.items():
        if state not in {"FAILED", "TIMED_OUT"}:
            continue
        proc = subprocess.run(
            [sys.executable, str(paths.root / "harness" / "retry.py"),
             "--packet", packet_id, "--error",
             _failure_text(paths, packet_id, state)],
            cwd=str(paths.root), env={**os.environ, "LOOP_ROOT": str(paths.root)},
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=30.0, check=False)
        routed.append({"packet_id": packet_id, "from_state": state,
                       "rc": proc.returncode,
                       "decision": proc.stdout.strip()[-2000:],
                       "stderr": proc.stderr.strip()[-2000:]})
    return routed


def normalize_legacy_reports(paths: LoopPaths) -> list[str]:
    """Wrap plain reports from supervisors born before report envelopes.

    The exec roster and ledger run_id must agree and the child must have
    completed with exit code zero.  This makes the compatibility path no less
    strict than a freshly started supervisor.
    """
    ledger = read_json(paths.ledger, {"packets": {}}) or {"packets": {}}
    roster = read_json(paths.data / "lifecycle" / "exec_roster.json",
                       {"jobs": {}}) or {"jobs": {}}
    jobs = roster.get("jobs") if isinstance(roster, dict) else {}
    jobs = jobs if isinstance(jobs, dict) else {}
    wrapped: list[str] = []
    for packet_id, entry in (ledger.get("packets") or {}).items():
        if not isinstance(entry, dict) or entry.get("state") != "RUNNING":
            continue
        job = jobs.get(packet_id)
        if (not isinstance(job, dict) or job.get("state") != "completed"
                or int(job.get("exit_code", 1) or 0) != 0
                or not job.get("run_id")
                or str(job.get("run_id")) != str(entry.get("current_run_id"))):
            continue
        report = paths.data / "reports" / packet_id / "report.json"
        if not report.is_file():
            continue
        raw = report.read_bytes()
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            value = None
        if isinstance(value, dict) and value.get("packet_id") == packet_id:
            continue
        atomic_write_json(report, {
            "packet_id": packet_id, "status": "done",
            "run_id": str(job["run_id"]),
            "summary": raw.decode("utf-8", errors="replace"),
            "compatibility_envelope": True,
        })
        wrapped.append(packet_id)
    return wrapped


def run(root: Path = ROOT) -> dict[str, object]:
    result: dict[str, object] = {"schema": "codex-loop-terminal-packet/v1",
                                "ts": time.time(), "status": "PASS"}
    errors: list[str] = []
    orchestration = root / "data" / "orchestration"
    requests = orchestration / "terminal_requests.json"
    marker = orchestration / "terminal_epilogue.pid"
    claim = orchestration / ".terminal_epilogue.lock"
    with file_lock(orchestration / ".terminal_packet.lock"):
        paths = LoopPaths.resolve(root)
        while True:
            request_state = read_json(requests, {"requested_seq": 1,
                                                  "consumed_seq": 0}) or {}
            requested = int(request_state.get("requested_seq", 1) or 1)
            try:
                result["legacy_reports_wrapped"] = normalize_legacy_reports(paths)
            except Exception as exc:
                errors.append("report_normalize: %s: %s" %
                              (type(exc).__name__, exc))
            try:
                states = StateMachine(paths).step()
                result["state_machine"] = states
                result["retry_routes"] = route_terminal_retries(paths, states)
                for route in result["retry_routes"]:
                    if route.get("rc") not in {0, 4, 5, 6}:
                        errors.append("retry_route: packet=%s rc=%s stderr=%s" %
                                      (route.get("packet_id"), route.get("rc"),
                                       route.get("stderr")))
                if result["retry_routes"]:
                    result["state_machine_after_retry"] = StateMachine(paths).step()
            except Exception as exc:
                errors.append("state_machine: %s: %s" % (type(exc).__name__, exc))
            try:
                rc, refill = run_refill_once(root, dry_run=False)
                result["refill"] = refill
                if rc:
                    errors.append("refill: actuator rc=%d" % rc)
            except Exception as exc:
                errors.append("refill: %s: %s" % (type(exc).__name__, exc))
            with file_lock(claim):
                latest = read_json(requests, None)
                if not isinstance(latest, dict):
                    raise RuntimeError("terminal request state unreadable; refusing to consume edge")
                latest["consumed_seq"] = max(
                    int(latest.get("consumed_seq", 0) or 0), requested)
                latest["consumed_at"] = time.time()
                atomic_write_json(requests, latest)
                if int(latest.get("requested_seq", 0) or 0) <= requested:
                    marker_value = read_json(marker, {}) or {}
                    if (isinstance(marker_value, dict)
                            and int(marker_value.get("pid", -1) or -1) == os.getpid()):
                        try:
                            marker.unlink()
                        except FileNotFoundError:
                            pass
                    result["requested_seq"] = requested
                    result["consumed_seq"] = latest["consumed_seq"]
                    break
    if errors:
        result["status"] = "FAIL_VISIBLE"
        result["errors"] = errors
    atomic_write_json(root / "data" / "orchestration" /
                      "terminal_packet_status.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
