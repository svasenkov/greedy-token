from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.executors import execute_task

pytestmark = [allure.epic("Routing"), allure.feature("Task execution")]


@patch("greedy_token.executors._rag_fallback_output")
@patch("greedy_token.executors.execute_plan")
@patch("greedy_token.executors.route_task")
@allure.story("RAG fallback")
@allure.title("execute_task falls back to RAG when ripgrep output is empty")
def test_execute_task_rag_fallback_on_weak_rg(
    mock_route,
    mock_execute,
    mock_rag_fallback,
    minimal_workspace: Path,
) -> None:
    from greedy_token.router import RouteDecision

    mock_route.return_value = RouteDecision(
        target="tool",
        route_id="tool-rg-search",
        confidence=0.9,
        matched=["find"],
        command="rg ...",
        note="",
        domains=[],
        read_only=True,
        tool="rg",
    )
    mock_execute.return_value = (0, "")
    mock_rag_fallback.return_value = "RAG hits for: baseUrl\n\n1. chunk"

    result = execute_task("find baseUrl in missing-file.html", minimal_workspace)
    assert result.used_rag_fallback is True
    assert "fallback RAG" in result.output or "RAG hits" in result.output


@allure.story("Cursor tier")
@allure.title("execute_task returns empty output for cursor tier")
def test_execute_task_cursor_returns_empty(minimal_workspace: Path) -> None:
    result = execute_task("refactor monolithic header shell layout", minimal_workspace)
    assert result.decision.target == "cursor"
    assert result.output == ""
