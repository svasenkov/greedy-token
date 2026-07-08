from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.router import route_task, route_task_all_tiers

pytestmark = [allure.epic("Routing"), allure.feature("Task router")]


@allure.story("Tool tier")
@allure.title("Route find task to tool tier with read-only plan")
def test_route_find_goes_to_tool(minimal_workspace: Path) -> None:
    decision = route_task("find baseUrl in sample.js", minimal_workspace)
    assert decision.target == "tool"
    assert decision.read_only is True
    assert decision.command is not None


@allure.story("RAG tier")
@allure.title("Route documentation question to RAG tier")
def test_route_rag_question(minimal_workspace: Path) -> None:
    decision = route_task("какой -D flag для baseUrl", minimal_workspace)
    assert decision.target == "rag"


@allure.story("Cursor tier")
@allure.title("Route open-ended task to cursor fallback")
def test_route_cursor_fallback(minimal_workspace: Path) -> None:
    decision = route_task("explain quantum foam in repository layout", minimal_workspace)
    assert decision.target == "cursor"
    assert decision.route_id == "cursor-fallback"


@patch("greedy_token.router.ollama_available", return_value=False)
@allure.story("Ollama availability")
@allure.title("Route skips ollama tier when server is unavailable")
def test_route_skips_unavailable_ollama(mock_ollama, minimal_workspace: Path) -> None:
    decision = route_task("audit skill configurator-boolean", minimal_workspace)
    assert decision.target != "ollama"


@allure.story("Tier scan")
@allure.title("route_task_all_tiers returns five executor rows")
def test_route_task_all_tiers_has_five_rows(minimal_workspace: Path) -> None:
    tiers = route_task_all_tiers("find baseUrl", minimal_workspace)
    assert len(tiers) == 5
    assert [t[0] for t in tiers] == ["tool", "python", "ollama", "rag", "cursor"]
