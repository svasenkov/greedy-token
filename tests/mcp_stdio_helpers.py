"""Helpers for MCP stdio E2E tests (real greedy-token-mcp subprocess)."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

pytest = __import__("pytest")
mcp = pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

T = TypeVar("T")


def tool_text(result: Any) -> str:
    if not result.content:
        return ""
    block = result.content[0]
    return getattr(block, "text", str(block))


def mcp_env(workspace: Path, *, log_path: Path | None = None) -> dict[str, str]:
    env = {**os.environ, "GREEDY_TOKEN_ROOT": str(workspace)}
    if log_path is not None:
        env["GREEDY_TOKEN_LOG"] = str(log_path)
    else:
        env["GREEDY_TOKEN_LOG"] = "0"
    return env


def mcp_server_params(workspace: Path, *, log_path: Path | None = None) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "greedy_token.mcp"],
        env=mcp_env(workspace, log_path=log_path),
    )


async def with_mcp_session(
    workspace: Path,
    fn: Callable[[ClientSession], Awaitable[T]],
    *,
    log_path: Path | None = None,
) -> T:
    params = mcp_server_params(workspace, log_path=log_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await fn(session)


def run_mcp(
    workspace: Path,
    fn: Callable[[ClientSession], Awaitable[T]],
    *,
    log_path: Path | None = None,
) -> T:
    return asyncio.run(with_mcp_session(workspace, fn, log_path=log_path))
