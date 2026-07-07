from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from greedy_token.router import route_task, route_task_all_tiers


def test_route_find_goes_to_tool(minimal_workspace: Path) -> None:
    decision = route_task("find baseUrl in sample.js", minimal_workspace)
    assert decision.target == "tool"
    assert decision.read_only is True
    assert decision.command is not None


def test_route_rag_question(minimal_workspace: Path) -> None:
    decision = route_task("какой -D flag для baseUrl", minimal_workspace)
    assert decision.target == "rag"


def test_route_cursor_fallback(minimal_workspace: Path) -> None:
    decision = route_task("explain quantum foam in repository layout", minimal_workspace)
    assert decision.target == "cursor"
    assert decision.route_id == "cursor-fallback"


@patch("greedy_token.router.ollama_available", return_value=False)
def test_route_skips_unavailable_ollama(mock_ollama, minimal_workspace: Path) -> None:
    decision = route_task("audit skill configurator-boolean", minimal_workspace)
    assert decision.target != "ollama"


def test_route_task_all_tiers_has_five_rows(minimal_workspace: Path) -> None:
    tiers = route_task_all_tiers("find baseUrl", minimal_workspace)
    assert len(tiers) == 5
    assert [t[0] for t in tiers] == ["tool", "python", "ollama", "rag", "cursor"]
