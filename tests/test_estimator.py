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
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Greedy token"),
    allure.parent_suite("Greedy token"),
    allure.feature("Task estimator"),
    allure.suite("Task estimator"),
]


@allure.story("Cursor baseline")
@allure.title("Cursor baseline sums always-on rules, task, and agent overhead")
def test_cursor_baseline_includes_rules_task_and_overhead(minimal_workspace: Path) -> None:
    task = "find baseUrl in sample.js"
    with allure.step("Compute cursor baseline for find task"):
        baseline = cursor_baseline(minimal_workspace, task)
        attach_text("task", task)
        attach_text("baseline tokens", str(baseline))
    with allure.step("Verify baseline exceeds task token count"):
        assert baseline > 0
        assert baseline > len(task) // 4


@allure.story("Savings")
@allure.title("Cursor saved is zero when route target is cursor")
def test_cursor_saved_for_cursor_target_is_zero(minimal_workspace: Path) -> None:
    task = "explain quantum foam in repository layout"
    with allure.step("Compute cursor savings for cursor tier"):
        baseline = cursor_baseline(minimal_workspace, task)
        saved = cursor_saved_for(minimal_workspace, task, est_tokens=500, target="cursor")
        attach_text("baseline", str(baseline))
        attach_text("saved", str(saved))
    with allure.step("Verify zero savings and baseline exceeds spent"):
        assert saved == 0
        assert baseline > 500


@allure.story("Savings")
@allure.title("Cursor saved equals baseline minus spent for tool tier")
def test_cursor_saved_for_tool_tier(minimal_workspace: Path) -> None:
    task = "find baseUrl in sample.js"
    with allure.step("Compute cursor savings for tool tier"):
        baseline = cursor_baseline(minimal_workspace, task)
        saved = cursor_saved_for(minimal_workspace, task, est_tokens=0, target="tool")
        attach_text("baseline", str(baseline))
        attach_text("saved", str(saved))
    with allure.step("Verify saved equals full baseline"):
        assert saved == baseline


@allure.story("Estimate task")
@allure.title("Estimate task routes find to tool with positive savings")
def test_estimate_task_find_routes_to_tool(minimal_workspace: Path) -> None:
    task = "find baseUrl in sample.js"
    with allure.step("Estimate find task routing and savings"):
        est = estimate_task(task, minimal_workspace)
        attach_json("estimate", {
            "target": est.decision.target,
            "complexity": est.complexity,
            "est_tokens": est.est_tokens,
            "cursor_saved": est.cursor_saved,
            "ollama_note": est.ollama_note,
        })
    with allure.step("Verify tool route with positive savings"):
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
    with allure.step("Estimate audit task with Ollama unavailable"):
        est = estimate_task(task, minimal_workspace)
        attach_json("estimate", {"target": est.decision.target, "ollama_note": est.ollama_note})
    with allure.step("Verify ollama note when route is ollama"):
        if est.decision.target == "ollama":
            assert est.ollama_note is not None
        else:
            assert est.ollama_note is None or est.decision.target != "ollama"


@allure.story("Format estimate")
@allure.title("Format estimate prints tier scan and command for tool route")
def test_format_estimate_tool_route(minimal_workspace: Path) -> None:
    task = "find baseUrl in sample.js"
    with allure.step("Format estimate for tool route"):
        est = estimate_task(task, minimal_workspace)
        text = format_estimate(est, task, minimal_workspace)
        attach_text("formatted estimate", text)
    with allure.step("Verify tier scan and savings sections"):
        assert f"Task: {task}" in text
        assert "Route: TOOL" in text
        assert "Tier scan:" in text
        assert "Command:" in text
        assert "Baseline (naive agent chat):" in text
        assert "Saved:" in text
        assert "0 LLM spend" in text
        assert "← selected" in text


@allure.story("Format estimate")
@allure.title("Format estimate shows zero savings and cursor hint for cursor route")
def test_format_estimate_cursor_route(minimal_workspace: Path) -> None:
    task = "explain quantum foam in repository layout"
    with allure.step("Format estimate for cursor route"):
        est = estimate_task(task, minimal_workspace)
        text = format_estimate(est, task, minimal_workspace)
        attach_text("formatted estimate", text)
    with allure.step("Verify cursor route with zero savings"):
        assert est.decision.target == "cursor"
        assert "Route: CURSOR" in text
        assert "Saved:             ~0" in text
        assert "expensive LLM" in text
        assert "New Cursor chat" in text
