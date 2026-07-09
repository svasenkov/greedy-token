from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.executors import _tool_output_weak
from greedy_token.router import _build_tool_command, _extract_search_query
from greedy_token.tool_output import filter_tool_output
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Routing"),
    allure.parent_suite("Routing"),
    allure.feature("Tool search helpers"),
    allure.suite("Tool search helpers"),
]


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("find baseUrl in test config", "baseUrl"),
        ("find baseUrl in configurator presets", "baseUrl"),
        ("grep for ConfigReader in tests", "ConfigReader"),
        ('search for "healthCheck path"', "healthCheck path"),
        ("find phase-manifest.json", "phase-manifest.json"),
    ],
)
@allure.story("Query extraction")
@allure.title("Extract search query `{expected}` from task")
def test_extract_search_query(task: str, expected: str) -> None:
    with allure.step("Extract search query from task"):
        attach_text("task", task)
        attach_text("expected", expected)
        result = _extract_search_query(task)
        attach_text("extracted", result)
    with allure.step("Verify extracted query matches expected"):
        assert result == expected


@allure.story("Ripgrep command")
@allure.title("Tool command builder uses identifier not full phrase")
def test_build_tool_command_uses_identifier_not_phrase(minimal_workspace: Path) -> None:
    with allure.step("Build ripgrep command for find task"):
        cmd = _build_tool_command(
            {"tool": "rg"},
            "find baseUrl in configurator presets",
            minimal_workspace,
        )
        attach_text("tool command", cmd)
    with allure.step("Verify identifier search not full phrase"):
        assert "-F baseUrl" in cmd or "-F 'baseUrl'" in cmd
        assert "baseUrl in configurator presets" not in cmd
        assert "!.cursor/hooks/**" in cmd


@allure.story("Output filter")
@allure.title("Tool output filter drops .cursor/hooks paths")
def test_filter_tool_output_drops_hooks_readme() -> None:
    raw = ".cursor/hooks/README.md:49:echo find baseUrl\nprojects/foo.js:1:baseUrl"
    with allure.step("Filter tool output with hooks path"):
        attach_text("raw output", raw)
        filtered = filter_tool_output(raw)
        attach_text("filtered output", filtered)
    with allure.step("Verify hooks paths are dropped"):
        assert ".cursor/hooks" not in filtered
        assert "projects/foo.js" in filtered


@allure.story("Weak output")
@allure.title("Weak tool output detector flags empty ripgrep results")
def test_tool_output_weak_when_empty() -> None:
    with allure.step("Evaluate weak output detector"):
        empty_weak = _tool_output_weak("", 1)
        match_weak = _tool_output_weak("projects/x:1:baseUrl", 0)
        attach_text("empty output weak", str(empty_weak))
        attach_text("match output weak", str(match_weak))
    with allure.step("Verify empty results are weak"):
        assert empty_weak is True
        assert match_weak is False
