#!/usr/bin/env python3
"""Lazy ipybox MCP: expose tools immediately, start Jupyter on first cell."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from ipybox.kernel_mgr.client import KernelClient
from ipybox.kernel_mgr.server import KernelGateway
from ipybox.mcp_server import MCPServer, extract_kernel_env, parse_args
from mcpygen import ToolServer


class _LazyKernelClient:
    def __init__(self, owner: "LazyMCPServer") -> None:
        self.owner = owner

    async def execute(self, code: str, timeout: float | None = None):
        client = await self.owner.ensure_runtime()
        return await client.execute(code, timeout=timeout)

    async def reset(self) -> None:
        client = await self.owner.ensure_runtime()
        await client.reset()


class LazyMCPServer(MCPServer):
    """Keep FastMCP cheap until a tool actually needs the persistent kernel."""

    def __init__(self, *args, **kwargs) -> None:
        self._runtime_lock = asyncio.Lock()
        self._runtime_stack: AsyncExitStack | None = None
        self._runtime_client: KernelClient | None = None
        super().__init__(*args, **kwargs)
        self._client = _LazyKernelClient(self)  # base tool methods keep their schemas

    @asynccontextmanager
    async def server_lifespan(self, _server):
        try:
            yield
        finally:
            await self.stop_runtime()

    async def ensure_runtime(self) -> KernelClient:
        if self._runtime_client is not None:
            return self._runtime_client
        async with self._runtime_lock:
            if self._runtime_client is not None:
                return self._runtime_client
            stack = AsyncExitStack()
            try:
                await stack.enter_async_context(ToolServer(
                    host=self.tool_server_host,
                    port=self.tool_server_port,
                    log_to_stderr=True,
                    log_level=self.log_level,
                ))
                await stack.enter_async_context(KernelGateway(
                    host=self.kernel_gateway_host,
                    port=self.kernel_gateway_port,
                    sandbox=self.sandbox,
                    sandbox_config=self.sandbox_config,
                    log_to_stderr=True,
                    log_level=self.log_level,
                    env=self.kernel_env | {
                        "TOOL_SERVER_HOST": self.tool_server_host,
                        "TOOL_SERVER_PORT": str(self.tool_server_port),
                    },
                ))
                client = await stack.enter_async_context(KernelClient(
                    host=self.kernel_gateway_host,
                    port=self.kernel_gateway_port,
                ))
            except BaseException:
                await stack.aclose()
                raise
            self._runtime_stack = stack
            self._runtime_client = client
            return client

    async def stop_runtime(self) -> None:
        async with self._runtime_lock:
            stack, self._runtime_stack = self._runtime_stack, None
            self._runtime_client = None
        if stack is not None:
            await stack.aclose()

    async def reset(self):
        """Reset an existing kernel; before first execution it is already clean."""
        if self._runtime_client is None:
            return None
        return await super().reset()


async def main() -> None:
    args = parse_args()
    user_bin = str(Path.home() / ".local" / "bin")
    os.environ["PATH"] = user_bin + os.pathsep + os.environ.get("PATH", "")
    os.makedirs(args.workspace, exist_ok=True)
    temp_dir = args.workspace.resolve() / ".ipybox-tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    os.environ["KERNEL_ENV_TMPDIR"] = str(temp_dir)
    os.chdir(args.workspace)
    load_dotenv(args.workspace.absolute() / ".env")
    sandbox_config: Path | None = None
    if args.sandbox_config:
        if args.sandbox_config.exists():
            sandbox_config = args.sandbox_config
        else:
            logging.getLogger(__name__).warning(
                "sandbox config %s does not exist; using default", args.sandbox_config)
    server = LazyMCPServer(
        tool_server_host=args.tool_server_host,
        tool_server_port=args.tool_server_port,
        kernel_gateway_host=args.kernel_gateway_host,
        kernel_gateway_port=args.kernel_gateway_port,
        sandbox=args.sandbox,
        sandbox_config=sandbox_config,
        log_level=args.log_level,
        kernel_env=extract_kernel_env(),
    )
    loop = asyncio.get_running_loop()

    def cancel_tasks() -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, cancel_tasks)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
