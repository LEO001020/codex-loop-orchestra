#!/usr/bin/env python3
"""Fault-inject an abrupt MCP-client death and verify full ipybox teardown."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import psutil


VENV = "/home/codexloop/.venvs/codex-loop-f2/"
SUPERVISOR = "/home/codexloop/bin/ipybox-supervised"
WORKSPACE = "/home/codexloop/codex-loop-s-f2/data"
SANDBOX_CONFIG = "/home/codexloop/codex-loop-s-f2/config/ipybox_sandbox.json"


def matching_pids(fragment: str) -> set[int]:
    found: set[int] = set()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = " ".join(proc.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if VENV in command and fragment in command:
            found.add(proc.info["pid"])
    return found


async def client() -> int:
    params = StdioServerParameters(
        command=SUPERVISOR,
        args=["--workspace", WORKSPACE, "--sandbox", "--sandbox-config",
              SANDBOX_CONFIG, "--log-level", "WARNING"],
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.call_tool(
                "execute_ipython_cell", {"code": "parent_death_smoke = 73"})
            assert not result.isError, result
            print("CLIENT_READY", flush=True)
            await asyncio.sleep(300)
    return 0


def controller() -> int:
    baseline_gateways = matching_pids("jupyter-kernelgateway")
    command = [sys.executable, str(Path(__file__).resolve()), "--client"]
    helper = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, start_new_session=True,
    )
    try:
        assert helper.stdout is not None
        deadline = time.monotonic() + 30
        ready = False
        while time.monotonic() < deadline:
            line = helper.stdout.readline()
            if line == "":
                if helper.poll() is not None:
                    raise RuntimeError("fault-injection client exited before ready")
                continue
            if line.strip() == "CLIENT_READY":
                ready = True
                break
        if not ready:
            raise TimeoutError("fault-injection client did not become ready")
        assert len(matching_pids("jupyter-kernelgateway") - baseline_gateways) == 1

        # Simulate an uncatchable Codex/MCP-client crash.  Its supervisor becomes
        # PPID 1; the supervisor's parent watcher must tear down the SRT/Jupyter
        # process group without touching gateway chains from other live tasks.
        helper.kill()
        helper.wait(timeout=5)
        for _ in range(80):
            if matching_pids("jupyter-kernelgateway") == baseline_gateways:
                break
            time.sleep(0.1)
        assert matching_pids("jupyter-kernelgateway") == baseline_gateways
        print("IPYBOX_PARENT_DEATH_OK orphan_delta=0 gateway_delta=0")
        return 0
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(client()) if "--client" in sys.argv else controller())
