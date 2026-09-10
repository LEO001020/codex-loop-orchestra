#!/usr/bin/env python3
"""One real MCP stdio round-trip through the ipybox process supervisor."""
from __future__ import annotations

import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import psutil


def gateway_pids() -> set[int]:
    found = set()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = " ".join(proc.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if ("/home/codexloop/.venvs/codex-loop-f2/" in command
                and "jupyter-kernelgateway" in command):
            found.add(proc.info["pid"])
    return found


async def main() -> int:
    original = gateway_pids()
    params = StdioServerParameters(
        command="/home/codexloop/bin/ipybox-supervised",
        args=["--workspace", "/home/codexloop/codex-loop-s-f2/data",
              "--sandbox", "--sandbox-config",
              "/home/codexloop/codex-loop-s-f2/config/ipybox_sandbox.json",
              "--log-level", os.environ.get("IPYBOX_SMOKE_LOG_LEVEL", "WARNING")],
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            # Supervisor startup may legitimately reap stale PPID-1 gateways;
            # measure the lazy-start invariant after that cleanup has run.
            baseline = gateway_pids()
            tools = await session.list_tools()
            names = sorted(tool.name for tool in tools.tools)
            assert names == ["execute_ipython_cell", "install_package",
                             "register_mcp_server", "reset"], names
            assert gateway_pids() == baseline, "tools/list eagerly started Jupyter"
            result = await session.call_tool(
                "execute_ipython_cell", {"code": "supervisor_smoke = 42; supervisor_smoke"})
            assert not result.isError, result
            assert len(gateway_pids() - baseline) == 1, "first cell did not lazily start one gateway"
            boundary = await session.call_tool("execute_ipython_cell", {"code": """
from pathlib import Path
import socket
try:
    Path('/tmp/ipybox-outside-workspace').write_text('blocked')
    print('WRITE_OUTSIDE_ALLOWED')
except Exception:
    print('WRITE_OUTSIDE_BLOCKED')
try:
    socket.create_connection(('example.com', 80), timeout=1).close()
    print('NETWORK_ALLOWED')
except Exception:
    print('NETWORK_BLOCKED')
"""})
            boundary_text = "\n".join(getattr(item, "text", "") for item in boundary.content)
            assert "WRITE_OUTSIDE_BLOCKED" in boundary_text, boundary_text
            assert "NETWORK_BLOCKED" in boundary_text, boundary_text
            print("IPYBOX_LAZY_OK tools=4 gateway_on_first_cell=1")
    for _ in range(20):
        if gateway_pids() == baseline:
            break
        await asyncio.sleep(0.1)
    assert gateway_pids() == baseline, "supervisor left a gateway after disconnect"
    assert not (gateway_pids() - original), "smoke introduced an unexpected gateway"
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
