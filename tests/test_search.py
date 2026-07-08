from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.executors import _tool_output_weak
from greedy_token.router import _build_tool_command, _extract_search_query
from greedy_token.tool_output import filter_tool_output

pytestmark = [allure.epic("Routing"), allure.feature("Tool search helpers")]


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("find baseUrl in e2e config", "baseUrl"),
        ("find baseUrl in e2e configurator", "baseUrl"),
        ("grep for ConfigReader in tests", "ConfigReader"),
        ('search for "healthCheck path"', "healthCheck path"),
        ("find phase-manifest.json", "phase-manifest.json"),
    ],
)
@allure.story("Query extraction")
@allure.title("Extract search query `{expected}` from task")
def test_extract_search_query(task: str, expected: str) -> None:
    assert _extract_search_query(task) == expected


@allure.story("Ripgrep command")
@allure.title("build_tool_command uses identifier not full phrase")
def test_build_tool_command_uses_identifier_not_phrase(minimal_workspace: Path) -> None:
    cmd = _build_tool_command(
        {"tool": "rg"},
        "find baseUrl in e2e configurator",
        minimal_workspace,
    )
    assert "-F baseUrl" in cmd or "-F 'baseUrl'" in cmd
    assert "baseUrl in e2e configurator" not in cmd
    assert "!.cursor/hooks/**" in cmd


@allure.story("Output filter")
@allure.title("filter_tool_output drops .cursor/hooks paths")
def test_filter_tool_output_drops_hooks_readme() -> None:
    raw = ".cursor/hooks/README.md:49:echo find baseUrl\nprojects/foo.js:1:baseUrl"
    filtered = filter_tool_output(raw)
    assert ".cursor/hooks" not in filtered
    assert "projects/foo.js" in filtered


@allure.story("Weak output")
@allure.title("tool_output_weak detects empty ripgrep results")
def test_tool_output_weak_when_empty() -> None:
    assert _tool_output_weak("", 1) is True
    assert _tool_output_weak("projects/x:1:baseUrl", 0) is False
