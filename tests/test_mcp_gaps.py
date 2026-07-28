"""Edge-branch tests for MCP search tool (fail_under=100)."""

from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.mcp import greedy_token_search

pytestmark = [
    allure.epic("MCP"),
    allure.parent_suite("MCP"),
    allure.feature("MCP tools"),
    allure.suite("MCP gaps"),
]


@allure.title("search tool: invalid context raises a clear error")
def test_search_invalid_context_raises(minimal_workspace: Path) -> None:
    with pytest.raises(ValueError, match=r"invalid context 'bogus'"):
        greedy_token_search("zzz-nonexistent-token-xyz", path="sample.js", context="bogus")
