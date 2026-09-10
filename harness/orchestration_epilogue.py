#!/usr/bin/env python3
"""Zero-model production epilogue for L2 drain and meter refresh.

The epilogue is deliberately fail-visible but non-fatal to the legacy state
machine: an integration sensor failure is persisted and blocks layered mode,
while cold-start execution keeps moving.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("LOOP_ROOT", HERE.parent)).resolve()
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "metering"))

from l2_consumer import L2Consumer, load_policy  # noqa: E402
from model_token_share_bridge import refresh as refresh_meter  # noqa: E402
from plan_consumer import run_once as run_plan_once  # noqa: E402
from refill_consumer_v2 import run_once as run_refill_once  # noqa: E402
from statemachine_v2 import StateMachine  # noqa: E402
from orchestration_common import LoopPaths, file_lock  # noqa: E402


def _schedule_plan_consumer(root: Path) -> dict[str, Any]:
    """Start the potentially long layered drain off the statemachine path.

    Request-level O_EXCL claims remain the exactly-once authority.  Extra
    epilogues may briefly start an empty drainer, but can never duplicate K3.
    """
    log_path = root / "data" / "plans" / "consumer.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    kwargs: dict[str, Any] = {
        "cwd": str(root), "env": {**os.environ, "LOOP_ROOT": str(root)},
        "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            [sys.executable, str(root / "harness" / "plan_consumer.py")], **kwargs)
        return {"status": "scheduled", "pid": proc.pid, "log": str(log_path)}
    finally:
        log.close()


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _run_epilogue_locked(root: Path, *, source: str) -> dict[str, Any]:
    root = Path(root).resolve()
    now = time.time()
    result: dict[str, Any] = {"schema": "codex-loop-epilogue/v2",
                              "ts": now, "source": source, "status": "PASS"}
    errors: list[str] = []
    try:
        result["state_machine"] = StateMachine(paths=LoopPaths.resolve(root)).step()
    except Exception as exc:
        errors.append("state_machine: %s: %s" % (type(exc).__name__, exc))
    try:
        policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
        mode = str(policy.get("routing", {}).get("mode", "cold_start"))
        if mode == "layered":
            plan_rc, plan = 0, _schedule_plan_consumer(root)
        else:
            # cold_start is zero work; shadow is a bounded local observation.
            plan_rc, plan = run_plan_once(root)
        result["plan"] = plan
        if plan_rc != 0:
            errors.append("plan: consumer rc=%d" % plan_rc)
    except Exception as exc:
        errors.append("plan: %s: %s" % (type(exc).__name__, exc))
    try:
        policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
        stats = L2Consumer(root, policy=policy).drain()
        result["l2"] = stats.__dict__
        if stats.errors:
            errors.extend("l2: %s" % item for item in stats.errors)
    except Exception as exc:
        errors.append("l2: %s: %s" % (type(exc).__name__, exc))
    sessions = Path(os.environ.get(
        "CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    try:
        if sessions.is_dir():
            result["meter"] = refresh_meter(root, sessions, force=False)
        else:
            result["meter"] = {"status": "SKIPPED", "why": "sessions_missing"}
    except Exception as exc:
        errors.append("meter: %s: %s" % (type(exc).__name__, exc))
    try:
        refill_rc, refill = run_refill_once(root, dry_run=False)
        result["refill"] = refill
        if refill_rc != 0:
            errors.append("refill: actuator rc=%d" % refill_rc)
    except Exception as exc:
        errors.append("refill: %s: %s" % (type(exc).__name__, exc))
    if errors:
        result["status"] = "FAIL_VISIBLE"
        result["errors"] = errors
    _write(root / "data" / "orchestration" / "epilogue_status.json", result)
    return result


def run_epilogue(root: Path | str = ROOT, *, source: str) -> dict[str, Any]:
    """Run one complete epilogue transaction under a cross-process lock.

    Both periodic state-machine reconciliation and terminal lifecycle edges
    may request this work.  Serializing the whole transaction keeps those
    complementary triggers without overlapping StateMachine/refill passes.
    """
    root = Path(root).resolve()
    with file_lock(root / "data" / "orchestration" / ".epilogue.lock"):
        return _run_epilogue_locked(root, source=source)


if __name__ == "__main__":
    # Lifecycle supervisors from before the terminal-packet split may still
    # launch this legacy CLI path.  Route that no-argument compatibility call
    # through the light state-machine+refill transaction; periodic/full
    # maintenance keeps using run_epilogue() as a Python API.
    from terminal_packet_epilogue import run as run_terminal_packet
    print(json.dumps(run_terminal_packet(ROOT), ensure_ascii=False))
