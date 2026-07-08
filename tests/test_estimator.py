from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.estimator import (
    cursor_baseline,
    cursor_saved_for,
    estimate_task,
    format_estimate,
)

pytestmark = [
    allure.epic("Token economy"),
    allure.parent_suite("Token economy"),
    allure.feature("Task estimator"),
    allure.suite("Task estimator"),
]


@allure.story("Cursor baseline")
@allure.title("Cursor baseline sums always-on rules, task, and agent overhead")
def test_cursor_baseline_includes_rules_task_and_overhead(minimal_workspace: Path) -> None:
    task = "find baseUrl in sample.js"
    baseline = cursor_baseline(minimal_workspace, task)
    assert baseline > 0
    assert baseline > len(task) // 4


@allure.story("Savings")
@allure.title("Cursor saved is zero when route target is cursor")
def test_cursor_saved_for_cursor_target_is_zero(minimal_workspace: Path) -> None:
    task = "explain quantum foam in repository layout"
    baseline = cursor_baseline(minimal_workspace, task)
    assert cursor_saved_for(minimal_workspace, task, est_tokens=500, target="cursor") == 0
    assert baseline > 500


@allure.story("Savings")
@allure.title("Cursor saved equals baseline minus spent for tool tier")
def test_cursor_saved_for_tool_tier(minimal_workspace: Path) -> None:
    task = "find baseUrl in sample.js"
    baseline = cursor_baseline(minimal_workspace, task)
    saved = cursor_saved_for(minimal_workspace, task, est_tokens=0, target="tool")
    assert saved == baseline


@allure.story("Estimate task")
@allure.title("Estimate task routes find to tool with positive savings")
def test_estimate_task_find_routes_to_tool(minimal_workspace: Path) -> None:
    task = "find baseUrl in sample.js"
    est = estimate_task(task, minimal_workspace)
    assert est.decision.target == "tool"
    assert est.complexity
    assert est.est_tokens >= 0
    assert est.rationale
    assert est.cursor_saved > 0
    assert est.ollama_note is None


@allure.story("Estimate task")
@allure.title("Estimate task sets ollama note when Ollama route is unavailable")
@patch("greedy_token.estimator.ollama_available", return_value=False)
def test_estimate_task_ollama_unavailable_note(
    _mock_ollama: object,
    minimal_workspace: Path,
) -> None:
    task = "audit skill configurator-boolean"
    est = estimate_task(task, minimal_workspace)
    if est.decision.target == "ollama":
        assert est.ollama_note is not None
    else:
        assert est.ollama_note is None or est.decision.target != "ollama"


@allure.story("Format estimate")
@allure.title("Format estimate prints tier scan and command for tool route")
def test_format_estimate_tool_route(minimal_workspace: Path) -> None:
    task = "find baseUrl in sample.js"
    est = estimate_task(task, minimal_workspace)
    text = format_estimate(est, task, minimal_workspace)
    assert f"Task: {task}" in text
    assert "Route: TOOL" in text
    assert "Tier scan:" in text
    assert "Command:" in text
    assert "Baseline (naive agent chat):" in text
    assert "Saved:" in text
    assert "local — no cloud LLM" in text
    assert "← selected" in text


@allure.story("Format estimate")
@allure.title("Format estimate shows zero savings and cursor hint for cursor route")
def test_format_estimate_cursor_route(minimal_workspace: Path) -> None:
    task = "explain quantum foam in repository layout"
    est = estimate_task(task, minimal_workspace)
    assert est.decision.target == "cursor"
    text = format_estimate(est, task, minimal_workspace)
    assert "Route: CURSOR" in text
    assert "Saved:             ~0" in text
    assert "full agent path" in text
    assert "Новый Cursor-чат" in text
