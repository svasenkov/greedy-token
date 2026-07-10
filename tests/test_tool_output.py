from __future__ import annotations

import allure
import pytest

from greedy_token import __version__
from greedy_token.tool_output import filter_tool_output
from greedy_token.version import read_pyproject_version
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Tool output"),
    allure.parent_suite("Tool output"),
    allure.feature("Output filtering"),
    allure.suite("Output filtering"),
]


@allure.story("Package version")
@allure.title("Package version matches pyproject.toml")
def test_version_matches_pyproject() -> None:
    with allure.step("Read version from pyproject.toml"):
        expected = read_pyproject_version()
        attach_text("pyproject version", expected)
        attach_text("package __version__", __version__)
    with allure.step("Verify versions match"):
        assert __version__ == expected


@allure.story("Blank lines")
@allure.title("Tool output filter strips consecutive blank lines")
def test_filter_tool_output_strips_blank_lines() -> None:
    raw = "a\n\n\nb"
    with allure.step("Filter tool output with blank lines"):
        attach_text("raw input", raw)
        filtered = filter_tool_output(raw)
        attach_text("filtered output", filtered)
    with allure.step("Verify consecutive blank lines are collapsed"):
        assert filtered == "a\nb"
