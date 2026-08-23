#!/usr/bin/env python3
"""List or terminate orphan ipybox Jupyter trees, fail-closed by cmdline."""
from __future__ import annotations

from pathlib import Path

import argparse
import fcntl
import json
import os
import time

import psutil


VENV = str(Path(__file__).resolve().parents[1] / "venv")  # resolve from package root
# v2 intentionally differs from the original supervisor-owned lock.  Existing
# supervisor processes may still hold the v1 lock while launching a newly
# deployed reaper; reusing that pathname would self-deadlock across parent and
# child until the supervisor timeout.  New supervisors delegate all locking to
# this file.
REAPER_LOCK = "/tmp/codex-loop-ipybox-reaper-v2.lock"


def is_orphan_gateway(proc: psutil.Process) -> bool:
    try:
        cmd = " ".join(proc.cmdline())
        return (proc.ppid() == 1 and VENV in cmd
                and "jupyter-kernelgateway" in cmd)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def discover() -> list[psutil.Process]:
    return [proc for proc in psutil.process_iter() if is_orphan_gateway(proc)]


def identity(proc: psutil.Process) -> tuple[int, float] | None:
    try:
        return proc.pid, proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def same_process(proc: psutil.Process, expected: tuple[int, float] | None) -> bool:
    return expected is not None and identity(proc) == expected


def describe(roots: list[psutil.Process]) -> dict:
    descendants = []
    for root in roots:
        try:
            descendants.extend(root.children(recursive=True))
        except psutil.NoSuchProcess:
            pass
    return {
        "orphan_gateways": len(roots),
        "descendants": len({proc.pid for proc in descendants}),
        "gateway_pids": sorted(proc.pid for proc in roots),
    }


def terminate(roots: list[psutil.Process]) -> dict:
    targets: dict[int, tuple[psutil.Process, tuple[int, float] | None]] = {}
    for root in roots:
        root_identity = identity(root)
        if not same_process(root, root_identity) or not is_orphan_gateway(root):
            continue
        try:
            for child in root.children(recursive=True):
                targets[child.pid] = (child, identity(child))
        except psutil.NoSuchProcess:
            pass
        targets[root.pid] = (root, root_identity)
    live_targets = []
    for proc, expected in sorted(targets.values(), key=lambda item: item[0].pid, reverse=True):
        if not same_process(proc, expected):
            continue
        try:
            proc.terminate()
            live_targets.append(proc)
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(live_targets, timeout=5)
    for proc in alive:
        try:
            expected = targets.get(proc.pid, (proc, None))[1]
            if same_process(proc, expected):
                proc.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=2)
    time.sleep(0.2)
    result = describe(discover())
    result["terminated"] = len(live_targets)
    return result


def apply_until_stable() -> dict:
    total = 0
    result = describe(discover())
    for _ in range(3):
        roots = discover()
        if not roots:
            break
        result = terminate(roots)
        total += int(result.get("terminated", 0))
        time.sleep(0.1)
    result = describe(discover())
    result["terminated"] = total
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    lock_fd = os.open(REAPER_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = apply_until_stable() if args.apply else describe(discover())
    finally:
        # fdopen normally owns the descriptor; guard the exceptional path.
        try:
            os.close(lock_fd)
        except OSError:
            pass
    result["mode"] = "apply" if args.apply else "dry-run"
    print(json.dumps(result, sort_keys=True))
    return 0 if not args.apply or result["orphan_gateways"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
