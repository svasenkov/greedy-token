from __future__ import annotations

from pathlib import Path

import pytest

from greedy_token.executors import _tool_output_weak
from greedy_token.router import _build_tool_command, _extract_search_query
from greedy_token.tool_output import filter_tool_output


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
def test_extract_search_query(task: str, expected: str) -> None:
    assert _extract_search_query(task) == expected


def test_build_tool_command_uses_identifier_not_phrase(minimal_workspace: Path) -> None:
    cmd = _build_tool_command(
        {"tool": "rg"},
        "find baseUrl in e2e configurator",
        minimal_workspace,
    )
    assert "-F baseUrl" in cmd or "-F 'baseUrl'" in cmd
    assert "baseUrl in e2e configurator" not in cmd
    assert "!.cursor/hooks/**" in cmd


def test_filter_tool_output_drops_hooks_readme() -> None:
    raw = ".cursor/hooks/README.md:49:echo find baseUrl\nprojects/foo.js:1:baseUrl"
    filtered = filter_tool_output(raw)
    assert ".cursor/hooks" not in filtered
    assert "projects/foo.js" in filtered


def test_tool_output_weak_when_empty() -> None:
    assert _tool_output_weak("", 1) is True
    assert _tool_output_weak("projects/x:1:baseUrl", 0) is False
