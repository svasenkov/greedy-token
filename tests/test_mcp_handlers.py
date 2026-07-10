from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest

pytest.importorskip("mcp")

pytestmark = [
    allure.epic("MCP"),
    allure.parent_suite("MCP"),
    allure.feature("MCP tool handlers"),
    allure.suite("MCP tool handlers"),
]


@allure.story("Usage tool")
@allure.title("greedy_token_usage appends session totals footer")
def test_greedy_token_usage_footer(tmp_path: Path, minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.mcp import greedy_token_usage

    log_file = tmp_path / "usage.jsonl"
    log_file.write_text(
        '{"cmd":"route","ts":"2030-01-01T00:00:00Z","selected_tier":"tool",'
        '"est_tokens":0,"cursor_baseline":9000,"cursor_saved":9000,'
        '"route_id":"tool-rg","token_counter_method":"tiktoken/cl100k_base"}\n'
        "bad json line\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log_file))
    out = greedy_token_usage("7d")
    assert "Session totals" in out
    assert "malformed log lines skipped" in out


def _assert_greedy_token_footer(text: str) -> None:
    assert "Greedy token" in text
    assert "Saved vs naive Cursor chat" in text


@allure.story("Pipeline tool")
@allure.title("greedy_token_pipeline list returns recipes")
def test_greedy_token_pipeline_list(minimal_workspace: Path) -> None:
    from greedy_token.mcp import greedy_token_pipeline

    out = greedy_token_pipeline("list")
    assert "meta-audit" in out
    assert "search-rag" in out


@allure.story("Search tool")
@allure.title("greedy_token_search finds matches in workspace file")
def test_greedy_token_search(minimal_workspace: Path) -> None:
    from greedy_token.mcp import greedy_token_search

    out = greedy_token_search("baseUrl", "sample.js")
    assert "baseUrl" in out
    assert "ripgrep on disk — 0 LLM spend" in out
    _assert_greedy_token_footer(out)


@allure.story("RAG tool")
@allure.title("greedy_token_rag searches with domain filter")
def test_greedy_token_rag_domain(minimal_workspace: Path) -> None:
    from greedy_token.mcp import greedy_token_rag

    out = greedy_token_rag("baseUrl", domain="config")
    assert "RAG hits for:" in out or "test-baseurl" in out
    _assert_greedy_token_footer(out)


@allure.story("Route tool")
@allure.title("greedy_token_route returns tier recommendation")
def test_greedy_token_route(minimal_workspace: Path) -> None:
    from greedy_token.mcp import greedy_token_route

    out = greedy_token_route("find baseUrl in sample.js")
    assert "tool" in out.lower() or "TOOL" in out
    _assert_greedy_token_footer(out)


@allure.story("Server main")
@allure.title("mcp.main applies Ollama env and runs server")
def test_mcp_main(monkeypatch: pytest.MonkeyPatch, minimal_workspace: Path) -> None:
    from greedy_token import mcp as mcp_mod

    monkeypatch.setattr(mcp_mod, "apply_ollama_env", lambda root: None)
    monkeypatch.setattr(mcp_mod.mcp, "run", lambda: None)
    mcp_mod.main()


@allure.story("Icons")
@allure.title("mcp_icons loads SVG from package when static dir missing")
def test_mcp_icons_package_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token import mcp as mcp_mod

    with patch.object(Path, "is_file", return_value=False):
        icons = mcp_mod.mcp_icons()
    assert icons[0].mimeType in ("image/png", "image/svg+xml")
