from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.code_search import search_code
from greedy_token.tool_paths import resolve_rg

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
    found = resolve_rg()
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
    out = search_code("baseUrl", monorepo_root, path="configurator-option-presets.html")
    assert "command not found" not in out.text.lower()
    assert "configurator-option-presets.html" in out.text
