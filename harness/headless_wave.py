#!/usr/bin/env python3
"""Launch a visible, lifecycle-supervised headless Codex wave.

This is the low-friction transport used when Desktop should remain a light
control plane.  It does not claim a birth from ``Popen`` alone: every task is
counted only after ``lifecycle_supervisor.py`` has published the matching
run-id as ``starting`` or ``running`` in ``exec_roster.json``.  The 8765
dashboard merges that roster with its WSL peer.

The input manifest is a JSON object with ``tasks``.  Each task needs
``task_id``, ``task_name``, ``prompt`` and ``cwd``; ``role`` defaults to
``worker``.  Concurrent workspace writes require explicitly distinct working
directories and ``allow_write=true``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

try:
    from dispatch_v2 import (DispatcherV2, ExecutionPlane, ipybox_enabled_for,
                             resolve_role_pin)
    from lifecycle_supervisor import Store
    from orchestration_common import LoopPaths, OrchestrationPolicy, read_json
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dispatch_v2 import (DispatcherV2, ExecutionPlane, ipybox_enabled_for,
                             resolve_role_pin)
    from lifecycle_supervisor import Store
    from orchestration_common import LoopPaths, OrchestrationPolicy, read_json


TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
ACTIVE = {"starting", "running"}
TERMINAL = {"completed", "failed", "timed_out", "cancelled", "spawn_failed", "lost"}
ROLE_SET = {"worker", "verifier", "reviewer", "plan_expander", "duty_officer"}


class WaveError(RuntimeError):
    pass


def load_manifest(path: Path) -> list[dict[str, Any]]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WaveError(f"manifest unreadable: {exc}") from exc
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    if not isinstance(tasks, list) or not tasks:
        raise WaveError("manifest.tasks must be a non-empty list")
    seen: set[str] = set()
    write_cwds: set[str] = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise WaveError(f"task {index} is not an object")
        task_id = str(task.get("task_id") or "")
        if not TASK_ID.fullmatch(task_id) or task_id in seen:
            raise WaveError(f"invalid or duplicate task_id {task_id!r}")
        seen.add(task_id)
        packet_id = task.get("packet_id")
        if packet_id is not None:
            if not isinstance(packet_id, str) or not TASK_ID.fullmatch(packet_id):
                raise WaveError(f"task {task_id}: invalid packet_id")
            required = ("task_name",)
        else:
            required = ("task_name", "prompt", "cwd")
        for key in required:
            if not isinstance(task.get(key), str) or not task[key].strip():
                raise WaveError(f"task {task_id}: missing {key}")
        role = str(task.get("role") or "worker")
        if role not in ROLE_SET:
            raise WaveError(f"task {task_id}: unsupported role {role!r}")
        sandbox = str(task.get("sandbox") or "read-only")
        if sandbox not in {"read-only", "workspace-write"}:
            raise WaveError(f"task {task_id}: unsupported sandbox {sandbox!r}")
        if packet_id is not None and any(
                key in task for key in ("prompt", "cwd", "sandbox", "allow_write")):
            raise WaveError(f"task {task_id}: packet tasks may not override execution details")
        if sandbox == "workspace-write":
            if task.get("allow_write") is not True:
                raise WaveError(f"task {task_id}: workspace-write needs allow_write=true")
            cwd = str(Path(task["cwd"]).resolve()).casefold()
            if cwd in write_cwds:
                raise WaveError("workspace-write tasks must use distinct cwd values")
            write_cwds.add(cwd)
    return tasks


def roster_item(data_dir: Path, packet_id: str) -> dict[str, Any]:
    doc = read_json(data_dir / "lifecycle" / "exec_roster.json", {}) or {}
    row = (doc.get("jobs") or {}).get(packet_id)
    return row if isinstance(row, dict) else {}


def process_start_token(pid: Any) -> int | None:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            if (not ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(code)) or code.value != 259):
                return None
            creation = wintypes.FILETIME(); exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME(); user = wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetProcessTimes(
                    handle, ctypes.byref(creation), ctypes.byref(exit_time),
                    ctypes.byref(kernel), ctypes.byref(user)):
                return None
            return ((int(creation.dwHighDateTime) << 32)
                    | int(creation.dwLowDateTime))
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        return int(Path("/proc/%d/stat" % pid).read_text(
            encoding="ascii").split()[21])
    except (OSError, ValueError, IndexError):
        return None


def process_alive(pid: Any, expected_start: Any = None) -> bool:
    actual = process_start_token(pid)
    if actual is None:
        return False
    if expected_start is None:
        return True
    try:
        return actual == int(expected_start)
    except (TypeError, ValueError):
        return False


def recover_live_supervisor(data_dir: Path, packet_id: str) -> dict[str, Any]:
    row = roster_item(data_dir, packet_id)
    published = row.get("published_report")
    if (row.get("state") == "lost" and row.get("exit_code") == 0
            and isinstance(published, str) and Path(published).is_file()):
        return Store(data_dir).update(
            packet_id, run_id=str(row.get("run_id") or ""),
            attempt=int(row.get("attempt", 0) or 0), state="completed",
            recovered_by="headless_wave_completed_report")
    if (row.get("state") not in ACTIVE and row.get("run_id")
            and process_alive(row.get("supervisor_pid"),
                              row.get("supervisor_proc_start_ticks"))
            and process_alive(row.get("os_pid"),
                              row.get("worker_proc_start_ticks"))):
        return Store(data_dir).update(
            packet_id, run_id=str(row["run_id"]),
            attempt=int(row.get("attempt", 0) or 0), state="running",
            heartbeat_at=time.time(), recovered_by="headless_wave")
    return row


def observed(data_dir: Path, packet_id: str, run_id: str) -> bool:
    row = roster_item(data_dir, packet_id)
    # Only RUNNING is effective concurrency.  ``starting`` merely reserves an
    # initializing slot and may be followed by spawn_failed milliseconds later.
    return row.get("run_id") == run_id and row.get("state") == "running"


def wait_observed(data_dir: Path, packet_id: str, run_id: str,
                  timeout: float = 5.0, stable_for: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    first_running: float | None = None
    while time.monotonic() < deadline:
        row = roster_item(data_dir, packet_id)
        if row.get("run_id") != run_id:
            first_running = None
        elif row.get("state") == "running":
            first_running = first_running or time.monotonic()
            if time.monotonic() - first_running >= stable_for:
                return True
        else:
            first_running = None
            if row.get("state") in {"completed", "failed", "timed_out", "cancelled",
                                    "spawn_failed"}:
                return False
        time.sleep(0.05)
    return False


def wait_generation_change(data_dir: Path, packet_id: str, prior_run_id: str,
                           timeout: float = 5.0) -> dict[str, Any]:
    """Wait until the packet-keyed roster is overwritten by the new birth."""
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = roster_item(data_dir, packet_id)
        run_id = str(latest.get("run_id") or "")
        if run_id and run_id != prior_run_id:
            return latest
        time.sleep(0.05)
    return latest


def _completion_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Project terminal lifecycle data into a bounded parent-facing result.

    The full report remains on disk.  Returning only its path and a bounded
    first line preserves the Fable return convention without injecting a
    completed wave's full context into the parent Desktop turn.
    """
    report = row.get("published_report") or row.get("report")
    result: dict[str, Any] = {}
    if isinstance(report, str) and report:
        result["report_path"] = report
        try:
            text = Path(report).read_text(encoding="utf-8", errors="replace")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines:
                result["summary"] = lines[0][:500]
        except OSError:
            pass
    if row.get("failure") is not None:
        result["failure"] = row.get("failure")
    return result


def wait_for_terminal(data_dir: Path, results: list[dict[str, Any]],
                      timeout: float) -> None:
    """Block the parent wave until its observed generations are terminal.

    This is deliberately a deterministic supervisor wait, not an LLM/Sol
    polling round.  Parent-bound waves use it by default so the calling
    Desktop turn remains open long enough to receive child reports.  A caller
    that intentionally wants detached background work must pass ``--detach``.
    """
    pending = {
        index for index, result in enumerate(results)
        if result.get("status") in {"running", "already_active"}
        and result.get("packet_id") and result.get("run_id")
    }
    if not pending:
        return
    deadline = time.monotonic() + max(0.0, float(timeout))
    while pending:
        progressed = False
        for index in tuple(pending):
            result = results[index]
            row = recover_live_supervisor(data_dir, str(result["packet_id"]))
            expected_run = result.get("run_id")
            evidence = row if row.get("run_id") == expected_run else {}
            if not evidence:
                # A newer generation may already own the packet-keyed live
                # row.  Terminal history is keyed by immutable run_id and is
                # therefore the authoritative ABA-safe completion source.
                for history_row in reversed(row.get("history") or []):
                    if (isinstance(history_row, dict)
                            and history_row.get("run_id") == expected_run
                            and history_row.get("state") in TERMINAL):
                        evidence = history_row
                        break
            state = str(evidence.get("state") or "")
            if state not in TERMINAL:
                continue
            result["status"] = state
            result.update(_completion_fields(evidence))
            pending.remove(index)
            progressed = True
        if not pending:
            return
        if time.monotonic() >= deadline:
            for index in pending:
                results[index]["status"] = "wait_timeout"
                results[index]["failure"] = {
                    "reason": "parent_wave_wait_timeout",
                    "timeout_seconds": timeout,
                }
            return
        if not progressed:
            time.sleep(0.20)


def resolve_codex_binary() -> str:
    explicit = os.environ.get("CODEX_HEADLESS_BIN")
    if explicit:
        path = Path(explicit).resolve()
        if path.is_file():
            return str(path)
        raise WaveError(f"CODEX_HEADLESS_BIN is not a file: {path}")
    if os.name == "nt":
        npm_vendor = (Path.home() / "AppData" / "Roaming" / "npm" / "node_modules"
                      / "@openai" / "codex" / "node_modules" / "@openai"
                      / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc"
                      / "bin" / "codex.exe")
        if npm_vendor.is_file():
            return str(npm_vendor)
        candidate = shutil.which("codex.exe")
    else:
        candidate = shutil.which("codex")
    if not candidate:
        raise WaveError("headless codex executable not found")
    return candidate


def opencodex_healthy() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:10100/healthz", timeout=2.0) as response:
            value = json.loads(response.read(65536).decode("utf-8"))
        return response.status == 200 and value.get("status") == "ok"
    except Exception:
        return False


def provider_birth_allowed(root: Path, model: str) -> bool:
    from provider_health import backoff_active
    blocked, _ = backoff_active(root, model)
    return not blocked


def launch_supervisor(command: list[str], root: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    kwargs: dict[str, Any] = {
        "cwd": str(root), "stdin": subprocess.DEVNULL,
        "stdout": log, "stderr": subprocess.STDOUT, "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(command, **kwargs)
        return proc.pid
    finally:
        log.close()


def task_key(manifest: Path, task_id: str) -> str:
    digest = hashlib.sha256(str(manifest.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"adhoc-{digest}-{task_id}"[:96]


def build_commands(task: dict[str, Any], manifest: Path, paths: LoopPaths,
                   policy: OrchestrationPolicy, timeout: float) -> tuple[str, str, list[str], list[str], Path]:
    role = str(task.get("role") or "worker")
    pin = resolve_role_pin(role, paths, policy)
    sandbox = str(task.get("sandbox") or "read-only")
    packet_id = task_key(manifest, str(task["task_id"]))
    run_id = f"{packet_id}-{uuid.uuid4().hex}"
    report_dir = paths.data / "reports" / "headless-wave" / packet_id / run_id
    report = report_dir / "last_message.txt"
    events = report_dir / "events.jsonl"
    stderr = report_dir / "stderr.log"
    ipybox = ipybox_enabled_for(role, ExecutionPlane.WSL_HEADLESS, policy, task)
    prompt = (
        "You are a leaf LOOP agent. Do not spawn, delegate to, create, or "
        "message any subagent/thread, and do not call multi-agent tools. "
        "Complete this bounded packet yourself and return directly to the "
        "orchestrator.\n" + str(task["prompt"])
    )
    if not prompt.lstrip().startswith("任务名："):
        prompt = f"任务名：{task['task_name']}\n{prompt}"
    worker = [resolve_codex_binary(), "exec", "--skip-git-repo-check", "--sandbox", sandbox,
              *pin.cli_overrides(), "-c",
              f"mcp_servers.ipybox.enabled={'true' if ipybox else 'false'}"]
    # The Desktop Windows config contains a complete node_repl transport and
    # needs an explicit headless disable.  WSL has no node_repl table; adding
    # only `enabled=false` would create an invalid transport at config-parse
    # time, so absence already is the desired disabled state.
    if os.name == "nt":
        worker += ["-c", "mcp_servers.node_repl.enabled=false"]
    # Headless source/audit packets do not need the Desktop Apps/plugin plane.
    # Disabling it avoids remote plugin/MCP authentication retries on every
    # birth. A bounded manifest may opt in for a task that genuinely needs it.
    if task.get("enable_apps") is not True:
        worker += ["-c", "features.apps=false",
                   "-c", "features.plugins=false",
                   "-c", "features.recommended_plugins=false",
                   "-c", "features.remote_plugin=false"]
    worker += ["--json", "-o", str(report), prompt]
    supervisor = [sys.executable, str(paths.root / "harness" / "lifecycle_supervisor.py"),
                  "--data-dir", str(paths.data), "--packet", packet_id,
                  "--run-id", run_id, "--attempt", "0",
                  *(["--parent-session-id", str(task["parent_session_id"])]
                    if task.get("parent_session_id") else []),
                  *(["--manifest-id", str(task["manifest_id"])]
                    if task.get("manifest_id") else []),
                  "--task-name", str(task["task_name"])[:160], "--role", role,
                  "--model", pin.model, "--plane",
                  "Windows CLI" if os.name == "nt" else "WSL CLI",
                  "--cwd", str(Path(task["cwd"]).resolve()),
                  "--stdout", str(events), "--stderr", str(stderr),
                  "--report", str(report), "--timeout", str(timeout), "--", *worker]
    return packet_id, run_id, supervisor, worker, report_dir


def run(args: argparse.Namespace) -> int:
    manifest = args.manifest.resolve()
    paths = LoopPaths.resolve(args.root)
    policy = OrchestrationPolicy.load(paths)
    tasks = load_manifest(manifest)
    detach = bool(getattr(args, "detach", False))
    explicit_wait = bool(getattr(args, "wait_all", False))
    if detach and explicit_wait:
        raise WaveError("--detach and --wait-all are mutually exclusive")
    # Parent-bound waves restore the Fable/native wait-all contract by
    # default.  Unparented operational waves retain the old detached behavior
    # unless the caller opts into --wait-all explicitly.
    wait_all = explicit_wait or (
        not detach and any(task.get("parent_session_id") for task in tasks))
    interval = max(0.0, args.spawn_interval_ms / 1000.0)
    results: list[dict[str, Any]] = []
    dispatcher = DispatcherV2(paths)
    for index, task in enumerate(tasks, 1):
        task_id = str(task["task_id"])
        if task.get("packet_id") is not None:
            packet_id = str(task["packet_id"])
            role = str(task.get("role") or "worker")
            ledger = read_json(paths.ledger, {"packets": {}}) or {"packets": {}}
            entry = (ledger.get("packets") or {}).get(packet_id)
            allowed_states = {"DISPATCHABLE"}
            if not isinstance(entry, dict) or entry.get("state") not in allowed_states:
                results.append({"task_id": task["task_id"],
                                "status": "ledger_state_blocked",
                                "packet_id": packet_id,
                                "ledger_state": (entry or {}).get("state")
                                if isinstance(entry, dict) else None})
                continue
            expected_model = resolve_role_pin(role, paths, policy).model
            prior = recover_live_supervisor(paths.data, packet_id)
            if prior.get("state") in ACTIVE and not (
                    process_alive(prior.get("supervisor_pid"),
                                  prior.get("supervisor_proc_start_ticks"))
                    and process_alive(prior.get("os_pid"),
                                      prior.get("worker_proc_start_ticks"))):
                results.append({"task_id": task["task_id"],
                                "status": "stale_active_roster",
                                "packet_id": packet_id,
                                "run_id": prior.get("run_id")})
                continue
            if prior.get("state") in ACTIVE and (
                    prior.get("role") != role or prior.get("model") != expected_model):
                results.append({"task_id": task["task_id"],
                                "status": "active_route_conflict",
                                "packet_id": packet_id,
                                "observed_role": prior.get("role"),
                                "observed_model": prior.get("model")})
                continue
            if prior.get("state") == "running":
                results.append({"task_id": task["task_id"], "status": "already_active",
                                "packet_id": packet_id, "run_id": prior.get("run_id")})
                continue
            if prior.get("state") == "starting":
                prior_run = str(prior.get("run_id") or "")
                if prior_run and wait_observed(paths.data, packet_id, prior_run,
                                               args.observe_timeout):
                    results.append({"task_id": task["task_id"],
                                    "status": "already_active",
                                    "packet_id": packet_id, "run_id": prior_run})
                else:
                    final = roster_item(paths.data, packet_id)
                    results.append({"task_id": task["task_id"],
                                    "status": str(final.get("state") or "initializing"),
                                    "packet_id": packet_id, "run_id": prior_run,
                                    "failure": final.get("failure")})
                continue
            if args.dry_run:
                rc = dispatcher.dispatch([packet_id], role=role, dry_run=True,
                                         wave_idx=index - 1)
                results.append({"task_id": task["task_id"],
                                "status": "dry_run" if rc == 0 else "dispatch_refused",
                                "packet_id": packet_id, "role": role})
                continue
            # Backoff is a birth gate, not a status/read-only gate: dry-runs
            # and already-active generations above remain observable.
            if not provider_birth_allowed(paths.root, expected_model):
                results.append({"task_id": task_id, "status": "provider_backoff",
                                "model": expected_model})
                continue
            if index > 1 and interval:
                time.sleep(interval)
            if index == 1 or (index - 1) % max(1, args.health_every) == 0:
                if not opencodex_healthy():
                    results.append({"task_id": task["task_id"],
                                    "status": "health_blocked",
                                    "packet_id": packet_id})
                    continue
            prior_run = str(prior.get("run_id") or "")
            rc = dispatcher.dispatch([packet_id], role=role, dry_run=False,
                                     wave_idx=index - 1)
            fresh = (wait_generation_change(paths.data, packet_id, prior_run,
                                            args.observe_timeout)
                     if rc == 0 else roster_item(paths.data, packet_id))
            run_id = str(fresh.get("run_id") or "")
            if rc == 0 and run_id and run_id != prior_run and wait_observed(
                    paths.data, packet_id, run_id, args.observe_timeout):
                results.append({"task_id": task["task_id"], "status": "running",
                                "packet_id": packet_id, "run_id": run_id})
            else:
                final = roster_item(paths.data, packet_id)
                results.append({"task_id": task["task_id"],
                                "status": ("dispatch_refused" if rc else
                                           str(final.get("state") or "unobserved")),
                                "packet_id": packet_id, "run_id": run_id,
                                "failure": final.get("failure")})
            continue
        packet_id, run_id, supervisor, worker, report_dir = build_commands(
            task, manifest, paths, policy, args.timeout)
        prior = recover_live_supervisor(paths.data, packet_id)
        if prior.get("state") == "completed":
            results.append({"task_id": task["task_id"], "status": "already_completed",
                            "packet_id": packet_id, "run_id": prior.get("run_id")})
            continue
        if prior.get("state") == "running":
            results.append({"task_id": task["task_id"], "status": "already_active",
                            "packet_id": packet_id, "run_id": prior.get("run_id")})
            continue
        if prior.get("state") == "starting":
            prior_run = str(prior.get("run_id") or "")
            if prior_run and wait_observed(paths.data, packet_id, prior_run,
                                           args.observe_timeout):
                results.append({"task_id": task["task_id"], "status": "already_active",
                                "packet_id": packet_id, "run_id": prior_run})
            else:
                final = roster_item(paths.data, packet_id)
                results.append({"task_id": task["task_id"],
                                "status": str(final.get("state") or "initializing"),
                                "packet_id": packet_id, "run_id": prior_run,
                                "failure": final.get("failure")})
            continue
        if args.dry_run:
            results.append({"task_id": task["task_id"], "status": "dry_run",
                            "packet_id": packet_id, "model": worker[worker.index("-m") + 1]})
            continue
        expected_model = worker[worker.index("-m") + 1]
        if not provider_birth_allowed(paths.root, expected_model):
            results.append({"task_id": task_id, "status": "provider_backoff",
                            "packet_id": packet_id, "model": expected_model})
            continue
        if index > 1 and interval:
            time.sleep(interval)
        if index == 1 or (index - 1) % max(1, args.health_every) == 0:
            if not opencodex_healthy():
                results.append({"task_id": task["task_id"], "status": "health_blocked",
                                "packet_id": packet_id})
                continue
        supervisor_pid = launch_supervisor(
            supervisor, paths.root, report_dir / "supervisor.log")
        if wait_observed(paths.data, packet_id, run_id, args.observe_timeout):
            results.append({"task_id": task["task_id"], "status": "running",
                            "packet_id": packet_id, "run_id": run_id,
                            "supervisor_pid": supervisor_pid})
        else:
            # Unknown is deliberately not counted as a successful handoff and
            # must not clear any external refill debt.  Do not auto-retry: the
            # supervisor may still be starting, and retrying could duplicate.
            final = roster_item(paths.data, packet_id)
            results.append({"task_id": task["task_id"],
                            "status": str(final.get("state") or "unobserved"),
                            "packet_id": packet_id, "run_id": run_id,
                            "supervisor_pid": supervisor_pid,
                            "failure": final.get("failure")})
    if wait_all and not args.dry_run:
        wait_for_terminal(paths.data, results, args.timeout + 30.0)
    print(json.dumps({"manifest": str(manifest), "target_requested": len(tasks),
                      "observed_running": sum(r["status"] in {"running", "already_active"}
                                              for r in results),
                      "wait_all": wait_all,
                      "results": results}, ensure_ascii=False))
    return 0 if all(r["status"] in {"running", "already_active", "already_completed",
                                       "completed", "dry_run"}
                    for r in results) else 3


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Launch a lifecycle-visible headless Codex wave")
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("--observe-timeout", type=float, default=5.0)
    ap.add_argument("--spawn-interval-ms", type=int, default=1000)
    ap.add_argument("--health-every", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait-all", action="store_true",
                    help="wait for all observed generations to reach terminal state")
    ap.add_argument("--detach", action="store_true",
                    help="return after birth observation; only for intentional background waves")
    return ap


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
