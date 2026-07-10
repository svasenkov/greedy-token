from __future__ import annotations

from pathlib import Path

import allure
import pytest

from tests.allure_reporting import attach_text
from tests.mcp_stdio_helpers import run_mcp, tool_text

pytest.importorskip("mcp")

pytestmark = [
    allure.epic("MCP"),
    allure.parent_suite("MCP"),
    allure.feature("MCP stdio server"),
    allure.suite("MCP stdio server"),
]


def _assert_greedy_token_footer(text: str) -> None:
    assert "Greedy token" in text
    assert "Saved vs naive Cursor chat" in text


@allure.story("Server handshake")
@allure.title("MCP stdio server advertises five greedy-token tools")
def test_mcp_stdio_lists_five_tools(minimal_workspace: Path) -> None:
    async def _list(session):
        tools = await session.list_tools()
        return [t.name for t in tools.tools]

    with allure.step("List MCP stdio tools"):
        names = run_mcp(minimal_workspace, _list)
        attach_text("tool names", "\n".join(names))
    with allure.step("Verify five greedy-token tools are advertised"):
        assert names == [
            "greedy_token_route",
            "greedy_token_rag",
            "greedy_token_search",
            "greedy_token_usage",
            "greedy_token_pipeline",
        ]


@allure.story("Route tool")
@allure.title("MCP stdio route tool returns tier decision with Greedy token footer")
def test_mcp_stdio_route_includes_greedy_token(minimal_workspace: Path) -> None:
    async def _call(session):
        return await session.call_tool(
            "greedy_token_route",
            {"task": "find baseUrl in sample.js"},
        )

    with allure.step("Call greedy_token_route via MCP stdio"):
        result = run_mcp(minimal_workspace, _call)
        text = tool_text(result)
        attach_text("route response", text)
    with allure.step("Verify TOOL route and Greedy token footer"):
        assert "Route: TOOL" in text
        _assert_greedy_token_footer(text)


@allure.story("Search tool")
@allure.title("MCP stdio search tool finds baseUrl in workspace file")
def test_mcp_stdio_search_finds_match(minimal_workspace: Path) -> None:
    async def _call(session):
        return await session.call_tool(
            "greedy_token_search",
            {"query": "baseUrl", "path": "sample.js"},
        )

    with allure.step("Call greedy_token_search via MCP stdio"):
        result = run_mcp(minimal_workspace, _call)
        text = tool_text(result)
        attach_text("search response", text)
    with allure.step("Verify baseUrl match and Greedy token footer"):
        assert "baseUrl" in text
        _assert_greedy_token_footer(text)


@allure.story("RAG tool")
@allure.title("MCP stdio RAG tool returns doc hits with Greedy token footer")
def test_mcp_stdio_rag_returns_hits(minimal_workspace: Path) -> None:
    async def _call(session):
        return await session.call_tool(
            "greedy_token_rag",
            {"query": "baseUrl -D flag", "domain": "config"},
        )

    with allure.step("Call greedy_token_rag via MCP stdio"):
        result = run_mcp(minimal_workspace, _call)
        text = tool_text(result)
        attach_text("rag response", text)
    with allure.step("Verify RAG hits and Greedy token footer"):
        assert "RAG hits" in text or "test-baseurl" in text
        _assert_greedy_token_footer(text)


@allure.story("Pipeline tool")
@allure.title("MCP stdio pipeline dry-run includes per-step Greedy token footer")
def test_mcp_stdio_pipeline_dry_run_footer(minimal_workspace: Path) -> None:
    async def _call(session):
        return await session.call_tool(
            "greedy_token_pipeline",
            {"task": "check-meta-sync then rag baseUrl", "execute": False},
        )

    with allure.step("Call greedy_token_pipeline dry-run via MCP stdio"):
        result = run_mcp(minimal_workspace, _call)
        text = tool_text(result)
        attach_text("pipeline response", text)
    with allure.step("Verify per-step savings footer"):
        assert "Per-step savings" in text
        assert "Saved vs naive Cursor chat" in text


@allure.story("Usage tool")
@allure.title("MCP stdio usage tool reports empty log gracefully")
def test_mcp_stdio_usage_empty_log(minimal_workspace: Path, tmp_path: Path) -> None:
    log_file = tmp_path / "usage.jsonl"
    log_file.write_text("", encoding="utf-8")

    async def _call(session):
        return await session.call_tool("greedy_token_usage", {"since": "7d"})

    with allure.step("Call greedy_token_usage with empty log via MCP stdio"):
        result = run_mcp(minimal_workspace, _call, log_path=log_file)
        text = tool_text(result)
        attach_text("usage response", text)
        attach_text("log path", str(log_file))
    with allure.step("Verify empty log message"):
        assert "No events since 7d" in text
        assert f"Log: {log_file}" in text
