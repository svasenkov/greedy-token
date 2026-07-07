from __future__ import annotations

from pathlib import Path

import pytest

from greedy_token.code_search import resolve_search_path, search_code


def test_resolve_search_path_by_filename() -> None:
    root = Path("/Users/stanislav/zero-design-system")
    p = resolve_search_path(
        "configurator-option-presets.html",
        root,
    )
    assert p is not None
    assert p.name == "configurator-option-presets.html"


def test_search_code_scoped_file_no_match() -> None:
    root = Path("/Users/stanislav/zero-design-system")
    out = search_code("baseUrl", root, path="configurator-option-presets.html")
    assert "No matches" in out.text
    assert "configurator-option-presets.html" in out.text


def test_search_code_global_finds_baseurl() -> None:
    root = Path("/Users/stanislav/zero-design-system")
    out = search_code("baseUrl", root, path="", limit=5)
    assert "baseUrl" in out.text or "baseurl" in out.text.lower()
