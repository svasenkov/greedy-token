from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.mcp import (
    greedy_token_pipeline,
    greedy_token_rag,
    greedy_token_route,
    greedy_token_search,
    greedy_token_usage,
)
from greedy_token.usage import SCHEMA_VERSION

pytestmark = [
    allure.epic("MCP"),
    allure.parent_suite("MCP"),
    allure.feature("MCP tools"),
    allure.suite("MCP tools"),
]


def _assert_token_economy_footer(text: str) -> None:
    assert "Token economy" in text
    assert "Saved vs naive Cursor chat" in text


@allure.story("Route tool")
@allure.title("MCP route tool returns tier decision with Token economy footer")
def test_mcp_route_includes_token_economy_footer(minimal_workspace: Path) -> None:
    out = greedy_token_route("find baseUrl in sample.js")
    assert "tool" in out.lower() or "TOOL" in out
    _assert_token_economy_footer(out)


@allure.story("Search tool")
@allure.title("MCP search tool finds matches and appends Token economy footer")
def test_mcp_search_finds_match_in_workspace(minimal_workspace: Path) -> None:
    out = greedy_token_search("baseUrl", path="sample.js")
    assert "baseUrl" in out
    _assert_token_economy_footer(out)


@allure.story("RAG tool")
@allure.title("MCP RAG tool returns hits with Token economy footer")
def test_mcp_rag_returns_hits_with_footer(minimal_workspace: Path) -> None:
    out = greedy_token_rag("baseUrl -D flag", domain="e2e")
    assert "RAG hits for:" in out or "test-baseurl" in out
    _assert_token_economy_footer(out)


@allure.story("Pipeline tool")
@allure.title("MCP pipeline list returns named recipes")
def test_mcp_pipeline_list_recipes() -> None:
    out = greedy_token_pipeline("list")
    assert "meta-audit" in out
    assert "search-rag" in out


@allure.story("Pipeline tool")
@allure.title("MCP pipeline dry-run includes per-step Token economy footer")
def test_mcp_pipeline_dry_run_includes_footer(minimal_workspace: Path) -> None:
    out = greedy_token_pipeline("check-meta-sync then rag baseUrl")
    assert "Per-step savings" in out
    assert "Saved vs naive Cursor chat" in out


@patch("greedy_token.mcp.run_pipeline")
@allure.story("Pipeline tool")
@allure.title("MCP pipeline passes execute=true to run allowlisted steps")
def test_mcp_pipeline_execute_true(mock_run, minimal_workspace: Path) -> None:
    from greedy_token.pipeline import PipelineResult

    mock_run.return_value = PipelineResult(task="t", steps=[])
    greedy_token_pipeline("check-meta-sync then rag baseUrl", execute=True)
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs.get("execute") is True


@patch("greedy_token.mcp.run_pipeline")
@allure.story("Pipeline tool")
@allure.title("MCP pipeline defaults to dry-run when execute is omitted")
def test_mcp_pipeline_execute_false_by_default(mock_run, minimal_workspace: Path) -> None:
    from greedy_token.pipeline import PipelineResult

    mock_run.return_value = PipelineResult(task="t", steps=[])
    greedy_token_pipeline("pipeline: meta-audit configurator-boolean")
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs.get("execute") is False


@allure.story("Usage tool")
@allure.title("MCP usage tool aggregates log events and session totals")
def test_mcp_usage_aggregates_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = tmp_path / "usage.jsonl"
    event = {
        "v": SCHEMA_VERSION,
        "cmd": "route",
        "ts": "2026-07-07T12:00:00Z",
        "selected_tier": "tool",
        "est_tokens": 0,
        "cursor_baseline": 9600,
        "cursor_saved": 9000,
        "route_id": "tool-rg-search",
        "token_counter_method": "tiktoken/cl100k_base",
    }
    log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
    monkeypatch.setattr("greedy_token.mcp.log_path", lambda: log_file)

    out = greedy_token_usage("7d")
    assert "tool-rg-search" in out or "tool" in out.lower()
    assert "Session totals (this window)" in out
    assert "Saved vs naive Cursor (all events)" in out
    assert f"Log: {log_file}" in out


@allure.story("Usage tool")
@allure.title("MCP usage tool reports empty log gracefully")
def test_mcp_usage_empty_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = tmp_path / "usage.jsonl"
    log_file.write_text("", encoding="utf-8")
    monkeypatch.setattr("greedy_token.mcp.log_path", lambda: log_file)

    out = greedy_token_usage("7d")
    assert "No events since 7d" in out
    assert f"Log: {log_file}" in out
