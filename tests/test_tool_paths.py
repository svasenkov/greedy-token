from __future__ import annotations

import os
from pathlib import Path

import pytest

from greedy_token.code_search import search_code
from greedy_token.tool_paths import resolve_rg


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


def test_search_code_works_without_path_env(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path("/Users/stanislav/zero-design-system")
    cursor_rg = Path(
        "/Applications/Cursor.app/Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg"
    )
    if not cursor_rg.is_file():
        pytest.skip("Cursor bundled rg not installed")

    monkeypatch.setenv("PATH", "")
    out = search_code("baseUrl", root, path="configurator-option-presets.html")
    assert "command not found" not in out.text.lower()
    assert "configurator-option-presets.html" in out.text
