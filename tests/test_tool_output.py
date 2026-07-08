from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token import __version__
from greedy_token.tool_output import filter_tool_output

pytestmark = [
    allure.epic("Tool output"),
    allure.parent_suite("Tool output"),
    allure.feature("Output filtering"),
    allure.suite("Output filtering"),
]


@allure.story("Package version")
@allure.title("Package __version__ matches pyproject.toml")
def test_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("version = "))
    expected = line.split("=", 1)[1].strip().strip('"')
    assert __version__ == expected


@allure.story("Blank lines")
@allure.title("filter_tool_output strips consecutive blank lines")
def test_filter_tool_output_strips_blank_lines() -> None:
    assert filter_tool_output("a\n\n\nb") == "a\nb"
