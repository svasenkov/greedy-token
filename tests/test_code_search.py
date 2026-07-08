from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.code_search import resolve_search_path, search_code
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Code search"),
    allure.parent_suite("Code search"),
    allure.feature("Search path resolution"),
    allure.suite("Search path resolution"),
]


@allure.story("Monorepo")
@allure.title("Resolve search path by filename in monorepo")
def test_resolve_search_path_by_filename(monorepo_root: Path) -> None:
    with allure.step("Resolve configurator-option-presets.html in monorepo"):
        p = resolve_search_path("configurator-option-presets.html", monorepo_root)
        attach_text("resolved path", str(p) if p else "(none)")
    with allure.step("Verify filename match"):
        assert p is not None
        assert p.name == "configurator-option-presets.html"


@allure.story("Minimal workspace")
@allure.title("Resolve search path by filename in minimal workspace")
def test_resolve_search_path_in_minimal_workspace(minimal_workspace: Path) -> None:
    with allure.step("Resolve sample.js in minimal workspace"):
        p = resolve_search_path("sample.js", minimal_workspace)
        attach_text("resolved path", str(p) if p else "(none)")
    with allure.step("Verify filename match"):
        assert p is not None
        assert p.name == "sample.js"


@allure.story("Scoped search")
@allure.title("Scoped file search reports no matches when absent")
def test_search_code_scoped_file_no_match(monorepo_root: Path) -> None:
    with allure.step("Search baseUrl in scoped file with no match"):
        out = search_code("baseUrl", monorepo_root, path="configurator-option-presets.html")
        attach_text("search output", out.text)
        attach_text("engine", out.engine)
    with allure.step("Verify no matches message"):
        assert "No matches" in out.text
        assert "configurator-option-presets.html" in out.text


@allure.story("Scoped search")
@allure.title("Scoped search finds baseUrl in sample.js")
def test_search_code_finds_in_minimal_workspace(minimal_workspace: Path) -> None:
    with allure.step("Search baseUrl in sample.js"):
        out = search_code("baseUrl", minimal_workspace, path="sample.js")
        attach_text("search output", out.text)
        attach_text("engine", out.engine)
    with allure.step("Verify baseUrl match"):
        assert "baseUrl" in out.text
        assert out.engine in ("rg", "python")


@allure.story("Global search")
@allure.title("Global search finds baseUrl across monorepo")
def test_search_code_global_finds_baseurl(monorepo_root: Path) -> None:
    with allure.step("Search baseUrl globally in monorepo"):
        out = search_code("baseUrl", monorepo_root, path="", limit=5)
        attach_text("search output", out.text)
        attach_text("engine", out.engine)
    with allure.step("Verify baseUrl found"):
        assert "baseUrl" in out.text or "baseurl" in out.text.lower()
