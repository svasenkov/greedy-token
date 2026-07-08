from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token import __version__
from greedy_token.tool_output import filter_tool_output
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
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if ln.startswith("version = "))
        expected = line.split("=", 1)[1].strip().strip('"')
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
