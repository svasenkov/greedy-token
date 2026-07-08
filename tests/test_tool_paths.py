from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.code_search import search_code
from greedy_token.tool_paths import resolve_rg
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Code search"),
    allure.parent_suite("Code search"),
    allure.feature("Ripgrep resolution"),
    allure.suite("Ripgrep resolution"),
]


@allure.story("Cursor bundle")
@allure.title("Ripgrep resolver finds Cursor-bundled binary")
def test_resolve_rg_finds_cursor_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor_rg = Path(
        "/Applications/Cursor.app/Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg"
    )
    if not cursor_rg.is_file():
        pytest.skip("Cursor bundled rg not installed")

    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    monkeypatch.setenv("PATH", "")
    with allure.step("Resolve ripgrep with empty PATH"):
        found = resolve_rg()
        attach_text("resolved rg path", str(found) if found else "(none)")
    with allure.step("Verify Cursor-bundled rg is found"):
        assert found is not None
        assert found.name == "rg"


@allure.story("Monorepo search")
@allure.title("Code search works when PATH is empty but Cursor rg exists")
def test_search_code_works_without_path_env(
    monkeypatch: pytest.MonkeyPatch,
    monorepo_root: Path,
) -> None:
    cursor_rg = Path(
        "/Applications/Cursor.app/Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg"
    )
    if not cursor_rg.is_file():
        pytest.skip("Cursor bundled rg not installed")

    monkeypatch.setenv("PATH", "")
    with allure.step("Search code with empty PATH"):
        out = search_code("baseUrl", monorepo_root, path="configurator-option-presets.html")
        attach_text("search output", out.text)
        attach_text("engine", out.engine)
    with allure.step("Verify search succeeds without PATH"):
        assert "command not found" not in out.text.lower()
        assert "configurator-option-presets.html" in out.text
