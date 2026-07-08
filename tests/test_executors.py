from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.executors import execute_task

pytestmark = [
    allure.epic("Routing"),
    allure.parent_suite("Routing"),
    allure.feature("Task execution"),
    allure.suite("Task execution"),
]




@allure.story("Tool tier")
@allure.title("Execute task runs ripgrep and returns matches without RAG fallback")
def test_execute_task_tool_finds_baseurl(minimal_workspace: Path) -> None:
    result = execute_task("find baseUrl in sample.js", minimal_workspace)
    assert result.decision.target == "tool"
    assert result.used_rag_fallback is False
    assert result.exit_code == 0
    assert "baseUrl" in result.output
    assert "sample.js" in result.output


@allure.story("RAG tier")
@allure.title("Execute task on RAG route returns formatted doc hits")
def test_execute_task_rag_route_returns_hits(minimal_workspace: Path) -> None:
    result = execute_task("какой -D flag для baseUrl", minimal_workspace)
    assert result.decision.target == "rag"
    assert "RAG hits" in result.output or "baseUrl" in result.output
    assert result.used_rag_fallback is False


@allure.story("Execute safety")
@allure.title("Execute plan refuses non-read-only Ollama route")
def test_execute_plan_refuses_non_readonly(minimal_workspace: Path) -> None:
    from greedy_token.executors import execute_plan, plan_run
    from greedy_token.router import route_task

    with patch("greedy_token.router.ollama_available", return_value=True):
        decision = route_task("audit skill configurator-boolean", minimal_workspace)
    if decision.target != "ollama":
        pytest.skip("Ollama route not selected in this environment")
    plan = plan_run(decision, "audit skill configurator-boolean", minimal_workspace)
    code, out = execute_plan(plan)
    assert code == 1
    assert "Refusing --execute" in out


@patch("greedy_token.executors._rag_fallback_output")
@patch("greedy_token.executors.execute_plan")
@patch("greedy_token.executors.route_task")
@allure.story("RAG fallback")
@allure.title("Task executor falls back to RAG when ripgrep output is empty")
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
@allure.title("Task executor returns empty output for cursor tier")
def test_execute_task_cursor_returns_empty(minimal_workspace: Path) -> None:
    result = execute_task("refactor monolithic header shell layout", minimal_workspace)
    assert result.decision.target == "cursor"
    assert result.output == ""
