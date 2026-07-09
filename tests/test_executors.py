from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.executors import execute_task
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Routing"),
    allure.parent_suite("Routing"),
    allure.feature("Task execution"),
    allure.suite("Task execution"),
]


@allure.story("Tool tier")
@allure.title("Execute task runs ripgrep and returns matches without RAG fallback")
def test_execute_task_tool_finds_baseurl(minimal_workspace: Path) -> None:
    projects = minimal_workspace / "projects"
    for idx in range(3):
        (projects / f"sample-{idx}.js").write_text(
            f"const baseUrl = 'http://localhost/{idx}';\n",
            encoding="utf-8",
        )
    with allure.step("Execute find task via tool tier"):
        result = execute_task("find baseUrl in sample.js", minimal_workspace)
        attach_text("output", result.output)
        attach_json("decision", {"target": result.decision.target, "exit_code": result.exit_code})
    with allure.step("Verify ripgrep match without RAG fallback"):
        assert result.decision.target == "tool"
        assert result.used_rag_fallback is False
        assert result.exit_code == 0
        assert "baseUrl" in result.output
        assert "sample.js" in result.output


@allure.story("RAG tier")
@allure.title("Execute task on RAG route returns formatted doc hits")
def test_execute_task_rag_route_returns_hits(minimal_workspace: Path) -> None:
    with allure.step("Execute RAG-routed documentation question"):
        result = execute_task("which -D flag for baseUrl", minimal_workspace)
        attach_text("output", result.output)
        attach_json("decision", {"target": result.decision.target, "used_rag_fallback": result.used_rag_fallback})
    with allure.step("Verify RAG hits in output"):
        assert result.decision.target == "rag"
        assert "RAG hits" in result.output or "baseUrl" in result.output
        assert result.used_rag_fallback is False


@allure.story("Execute safety")
@allure.title("Execute plan refuses non-read-only Ollama route")
def test_execute_plan_refuses_non_readonly(minimal_workspace: Path) -> None:
    from greedy_token.executors import execute_plan, plan_run
    from greedy_token.router import route_task

    with allure.step("Route audit task to Ollama tier"):
        with patch("greedy_token.router.ollama_available", return_value=True):
            decision = route_task("audit skill configurator-boolean", minimal_workspace)
        attach_json("decision", {"target": decision.target, "read_only": decision.read_only})
    if decision.target != "ollama":
        pytest.skip("Ollama route not selected in this environment")
    with allure.step("Attempt execute on non-read-only Ollama plan"):
        plan = plan_run(decision, "audit skill configurator-boolean", minimal_workspace)
        code, out = execute_plan(plan)
        attach_text("execute output", out)
        attach_text("exit code", str(code))
    with allure.step("Verify execute is refused"):
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
    with allure.step("Execute find task with empty ripgrep output"):
        result = execute_task("find baseUrl in missing-file.html", minimal_workspace)
        attach_text("output", result.output)
        attach_text("used_rag_fallback", str(result.used_rag_fallback))
    with allure.step("Verify RAG fallback was used"):
        assert result.used_rag_fallback is True
        assert "fallback RAG" in result.output or "RAG hits" in result.output


@allure.story("Cursor tier")
@allure.title("Task executor returns empty output for cursor tier")
def test_execute_task_cursor_returns_empty(minimal_workspace: Path) -> None:
    with allure.step("Execute refactor task routed to cursor"):
        result = execute_task("refactor monolithic header shell layout", minimal_workspace)
        attach_json("decision", {"target": result.decision.target})
        attach_text("output", result.output or "(empty)")
    with allure.step("Verify cursor tier returns empty output"):
        assert result.decision.target == "cursor"
        assert result.output == ""


@allure.story("Plan run")
@allure.title("plan_run builds python tier command with wrapper read_only")
def test_plan_run_python(minimal_workspace: Path) -> None:
    from greedy_token.executors import plan_run
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="python",
        route_id="script-check-meta-sync",
        confidence=1.0,
        matched=["meta"],
        command="./scripts/check-meta-sync.sh",
        note="",
        domains=[],
        read_only=True,
    )
    plan = plan_run(decision, "check meta", minimal_workspace)
    assert plan.executable is True
    assert "check-meta-sync" in plan.command


@allure.story("Plan run")
@allure.title("plan_run returns RAG dry-run output")
def test_plan_run_rag(minimal_workspace: Path) -> None:
    from greedy_token.executors import plan_run
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="rag",
        route_id="rag-lookup",
        confidence=1.0,
        matched=["rag"],
        command=None,
        note="",
        domains=["config"],
    )
    plan = plan_run(decision, "baseUrl -D flag", minimal_workspace)
    assert plan.executable is False
    assert "RAG hits" in plan.dry_run_output or "No RAG hits" in plan.dry_run_output


@allure.story("Plan run")
@allure.title("plan_run returns cursor guidance")
def test_plan_run_cursor(minimal_workspace: Path) -> None:
    from greedy_token.executors import plan_run
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="cursor",
        route_id="cursor-fallback",
        confidence=0.3,
        matched=[],
        command=None,
        note="",
        domains=[],
    )
    plan = plan_run(decision, "refactor everything", minimal_workspace)
    assert "Cursor chat" in plan.dry_run_output


@allure.story("Plan run")
@allure.title("plan_run returns fallback for unknown target")
def test_plan_run_unknown(minimal_workspace: Path) -> None:
    from greedy_token.executors import plan_run
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="unknown",
        route_id="x",
        confidence=0.0,
        matched=[],
        command=None,
        note="",
        domains=[],
    )
    plan = plan_run(decision, "task", minimal_workspace)
    assert plan.dry_run_output == "No executor."


@patch("greedy_token.executors._rag_fallback_output")
@patch("greedy_token.executors.execute_plan")
@patch("greedy_token.executors.route_task")
@allure.story("Filtered output")
@allure.title("Task executor appends RAG when filtered rg output is short")
def test_execute_task_filtered_short_rg(
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
    mock_execute.return_value = (0, ".cursor/hooks/noise\nbaseUrl\n")
    mock_rag_fallback.return_value = "RAG hits\n\n1. chunk"
    result = execute_task("find baseUrl", minimal_workspace)
    assert result.used_rag_fallback is True
    assert "Additional RAG" in result.output or "RAG hits" in result.output


@patch("greedy_token.executors.execute_plan")
@patch("greedy_token.executors.route_task")
@allure.story("Filtered output")
@allure.title("Task executor returns filtered rg output without RAG when sufficient")
def test_execute_task_filtered_sufficient(
    mock_route,
    mock_execute,
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
    lines = "\n".join(f"line{i}: baseUrl" for i in range(5))
    mock_execute.return_value = (0, ".cursor/hooks/noise\n" + lines)
    result = execute_task("find baseUrl", minimal_workspace)
    assert result.used_rag_fallback is False
    assert "baseUrl" in result.output


@patch("greedy_token.executors._rag_fallback_output", return_value=None)
@patch("greedy_token.executors.execute_plan")
@patch("greedy_token.executors.route_task")
@allure.story("RAG fallback")
@allure.title("Task executor returns raw output when RAG fallback empty")
def test_execute_task_weak_rg_no_fallback(
    mock_route,
    mock_execute,
    mock_rag,
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
    mock_execute.return_value = (2, "")
    result = execute_task("find baseUrl", minimal_workspace)
    assert result.used_rag_fallback is False


@allure.story("RAG domains")
@allure.title("_infer_rag_domains detects config and stacks tokens")
def test_infer_rag_domains() -> None:
    from greedy_token.executors import _infer_rag_domains

    config_domains = _infer_rag_domains("explain baseUrl in testconfig")
    assert config_domains == ["config"]
    stacks = _infer_rag_domains("openapi spring stack flows")
    assert "stacks" in stacks
    analytics = _infer_rag_domains("allure dashboard quality gate")
    assert analytics == ["analytics"]
    assert _infer_rag_domains("random question") is None

