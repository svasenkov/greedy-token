from __future__ import annotations

from pathlib import Path

import pytest

from greedy_token import __version__
from greedy_token.tool_output import filter_tool_output


def test_version_matches_pyproject() -> None:
    assert __version__ == "0.4.2"


def test_filter_tool_output_strips_blank_lines() -> None:
    assert filter_tool_output("a\n\n\nb") == "a\nb"
