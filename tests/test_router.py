from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.router import route_task, route_task_all_tiers
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Routing"),
    allure.parent_suite("Routing"),
    allure.feature("Task router"),
    allure.suite("Task router"),
]


@allure.story("Tool tier")
@allure.title("Route find task to tool tier with read-only plan")
def test_route_find_goes_to_tool(minimal_workspace: Path) -> None:
    with allure.step("Route find task"):
        decision = route_task("find baseUrl in sample.js", minimal_workspace)
        attach_json("decision", {"target": decision.target, "read_only": decision.read_only, "route_id": decision.route_id})
        attach_text("command", decision.command or "")
    with allure.step("Verify tool tier with read-only plan"):
        assert decision.target == "tool"
        assert decision.read_only is True
        assert decision.command is not None


@allure.story("RAG tier")
@allure.title("Route documentation question to RAG tier")
def test_route_rag_question(minimal_workspace: Path) -> None:
    with allure.step("Route documentation question"):
        decision = route_task("which -D flag for baseUrl", minimal_workspace)
        attach_json("decision", {"target": decision.target, "route_id": decision.route_id})
    with allure.step("Verify RAG tier selection"):
        assert decision.target == "rag"


@allure.story("Cursor tier")
@allure.title("Route open-ended task to cursor fallback")
def test_route_cursor_fallback(minimal_workspace: Path) -> None:
    with allure.step("Route open-ended explain task"):
        decision = route_task("explain quantum foam in repository layout", minimal_workspace)
        attach_json("decision", {"target": decision.target, "route_id": decision.route_id})
    with allure.step("Verify cursor fallback route"):
        assert decision.target == "cursor"
        assert decision.route_id == "cursor-fallback"


@patch("greedy_token.router.ollama_available", return_value=False)
@allure.story("Ollama availability")
@allure.title("Route skips Ollama tier when server is unavailable")
def test_route_skips_unavailable_ollama(mock_ollama, minimal_workspace: Path) -> None:
    with allure.step("Route audit task with Ollama unavailable"):
        decision = route_task("audit skill configurator-boolean", minimal_workspace)
        attach_json("decision", {"target": decision.target, "route_id": decision.route_id})
    with allure.step("Verify Ollama tier is skipped"):
        assert decision.target != "ollama"


@allure.story("Tier scan")
@allure.title("Full tier scan returns five executor rows")
def test_route_task_all_tiers_has_five_rows(minimal_workspace: Path) -> None:
    with allure.step("Run full tier scan for find task"):
        tiers = route_task_all_tiers("find baseUrl", minimal_workspace)
        attach_json("tier scan", [{"tier": t[0], "label": t[1]} for t in tiers])
    with allure.step("Verify five executor rows in order"):
        assert len(tiers) == 5
        assert [t[0] for t in tiers] == ["tool", "python", "ollama", "rag", "cursor"]
