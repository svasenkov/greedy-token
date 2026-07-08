from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.code_search import resolve_search_path, search_code

pytestmark = [allure.epic("Code search"), allure.feature("Search path resolution")]


@allure.story("Monorepo")
@allure.title("Resolve search path by filename in monorepo")
def test_resolve_search_path_by_filename(monorepo_root: Path) -> None:
    p = resolve_search_path("configurator-option-presets.html", monorepo_root)
    assert p is not None
    assert p.name == "configurator-option-presets.html"


@allure.story("Minimal workspace")
@allure.title("Resolve search path by filename in minimal workspace")
def test_resolve_search_path_in_minimal_workspace(minimal_workspace: Path) -> None:
    p = resolve_search_path("sample.js", minimal_workspace)
    assert p is not None
    assert p.name == "sample.js"


@allure.story("Scoped search")
@allure.title("Scoped file search reports no matches when absent")
def test_search_code_scoped_file_no_match(monorepo_root: Path) -> None:
    out = search_code("baseUrl", monorepo_root, path="configurator-option-presets.html")
    assert "No matches" in out.text
    assert "configurator-option-presets.html" in out.text


@allure.story("Scoped search")
@allure.title("Scoped search finds baseUrl in sample.js")
def test_search_code_finds_in_minimal_workspace(minimal_workspace: Path) -> None:
    out = search_code("baseUrl", minimal_workspace, path="sample.js")
    assert "baseUrl" in out.text
    assert out.engine in ("rg", "python")


@allure.story("Global search")
@allure.title("Global search finds baseUrl across monorepo")
def test_search_code_global_finds_baseurl(monorepo_root: Path) -> None:
    out = search_code("baseUrl", monorepo_root, path="", limit=5)
    assert "baseUrl" in out.text or "baseurl" in out.text.lower()
