"""Edge-branch tests for MCP search tool (fail_under=100)."""

from __future__ import annotations

from pathlib import Path

import allure

from greedy_token.mcp import greedy_token_search

pytestmark = [
    allure.epic("MCP"),
    allure.parent_suite("MCP"),
    allure.feature("MCP tools"),
    allure.suite("MCP gaps"),
]


@allure.title("search tool: invalid context resets to default and no-hit skips summary")
def test_search_invalid_context_no_hits(minimal_workspace: Path) -> None:
    out = greedy_token_search("zzz-nonexistent-token-xyz", path="sample.js", context="bogus")
    assert "Greedy token" in out
    assert "hits:" not in out
