"""Helpers for MCP stdio E2E tests (real greedy-token-mcp subprocess)."""

from __future__ import annotations

import asyncio
import os
import sys
import sysconfig
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

pytest = __import__("pytest")
mcp = pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402 — needs importorskip above
from mcp.client.stdio import stdio_client  # noqa: E402


def tool_text(result: Any) -> str:
    """All response content blocks joined — never only the first block."""
    blocks = getattr(result, "content", None) or []
    return "\n".join(getattr(block, "text", str(block)) for block in blocks)


def mcp_env(workspace: Path, *, log_path: Path | None = None) -> dict[str, str]:
    env = {
        **os.environ,
        "GREEDY_TOKEN_ROOT": str(workspace),
        "PYTHONUTF8": "1",
    }
    if log_path is not None:
        env["GREEDY_TOKEN_LOG"] = str(log_path)
    else:
        env["GREEDY_TOKEN_LOG"] = "0"
    return env


def _site_packages() -> list[str]:
    """site-packages dirs the `-S` child cannot discover on its own."""
    paths = sysconfig.get_paths()
    return sorted(
        {paths[key] for key in ("purelib", "platlib") if paths.get(key)}
    )


def observed_mcp_env(
    workspace: Path,
    *,
    ledger_path: Path,
    run_id: str,
    case_id: str,
) -> dict[str, str]:
    """Hermetic env for an observed MCP stdio child — allowlist, no parent leak."""
    from greedy_token.cheap_llm import (
        OBSERVE_CASE_ENV,
        OBSERVE_HTTP_ALLOW_ENV,
        OBSERVE_LEDGER_ENV,
        OBSERVE_RUN_ENV,
    )

    src = Path(__file__).resolve().parents[1] / "src"
    ollama_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(workspace / "_observed_home"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        # `-S` skips site.py entirely — the first code inside the child is
        # the observer bootstrap, not site imports.
        "PYTHONPATH": os.pathsep.join([str(src), *_site_packages()]),
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "GREEDY_TOKEN_ROOT": str(workspace),
        "GREEDY_TOKEN_LOG": "0",
        "GREEDY_TOKEN_HOME": str(workspace / "_observed_gt_home"),
        "OLLAMA_URL": ollama_url,
        OBSERVE_LEDGER_ENV: str(ledger_path),
        OBSERVE_RUN_ENV: run_id,
        OBSERVE_CASE_ENV: case_id,
        OBSERVE_HTTP_ALLOW_ENV: ollama_url,
    }


def mcp_server_params(
    workspace: Path,
    *,
    log_path: Path | None = None,
    observe: dict[str, str] | None = None,
) -> StdioServerParameters:
    if observe is not None:
        from greedy_token.cheap_llm import OBSERVED_MODULE_ENTRY

        return StdioServerParameters(
            command=sys.executable,
            args=["-S", "-c", OBSERVED_MODULE_ENTRY, "greedy_token.mcp"],
            env=observed_mcp_env(workspace, **observe),
            cwd=str(workspace),
        )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "greedy_token.mcp"],
        env=mcp_env(workspace, log_path=log_path),
    )


async def with_mcp_session[T](
    workspace: Path,
    fn: Callable[[ClientSession], Awaitable[T]],
    *,
    log_path: Path | None = None,
    observe: dict[str, str] | None = None,
    timeout: float | None = None,
) -> T:
    """One MCP session; `timeout` covers spawn + initialize + fn + cleanup."""
    params = mcp_server_params(workspace, log_path=log_path, observe=observe)

    async def _run() -> T:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)

    if timeout is None:
        return await _run()
    async with asyncio.timeout(timeout):
        return await _run()


def run_mcp[T](
    workspace: Path,
    fn: Callable[[ClientSession], Awaitable[T]],
    *,
    log_path: Path | None = None,
    observe: dict[str, str] | None = None,
    timeout: float | None = None,
) -> T:
    return asyncio.run(
        with_mcp_session(
            workspace, fn, log_path=log_path, observe=observe, timeout=timeout
        )
    )
