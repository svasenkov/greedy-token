from __future__ import annotations

import json
from pathlib import Path

import allure
import pytest

from greedy_token.mcp import (
    greedy_token_crystallize,
    greedy_token_pipeline,
    greedy_token_rag,
    greedy_token_route,
    greedy_token_search,
    greedy_token_usage,
)
from greedy_token.usage import SCHEMA_VERSION
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("MCP"),
    allure.parent_suite("MCP"),
    allure.feature("MCP tools"),
    allure.suite("MCP tools"),
]


def _assert_greedy_token_footer(text: str) -> None:
    assert "Greedy token" in text
    assert "saved **~" in text
    assert "> spent ~" in text


@allure.story("Route tool")
@allure.title("MCP route tool returns tier decision with Greedy token footer")
def test_mcp_route_includes_greedy_token_footer(minimal_workspace: Path) -> None:
    with allure.step("Call greedy_token_route"):
        out = greedy_token_route("find baseUrl in sample.js")
        attach_text("route response", out)
    with allure.step("Verify tool route and Greedy token footer"):
        assert "tool" in out.lower() or "TOOL" in out
        _assert_greedy_token_footer(out)


@allure.story("Search tool")
@allure.title("MCP search tool finds matches and appends Greedy token footer")
def test_mcp_search_finds_match_in_workspace(minimal_workspace: Path) -> None:
    with allure.step("Call greedy_token_search"):
        out = greedy_token_search("baseUrl", path="sample.js")
        attach_text("search response", out)
    with allure.step("Verify match and Greedy token footer"):
        assert "baseUrl" in out
        assert "free tier" in out
        _assert_greedy_token_footer(out)


@allure.story("RAG tool")
@allure.title("MCP RAG tool returns hits with Greedy token footer")
def test_mcp_rag_returns_hits_with_footer(minimal_workspace: Path) -> None:
    with allure.step("Call greedy_token_rag"):
        out = greedy_token_rag("baseUrl -D flag", domain="config")
        attach_text("rag response", out)
    with allure.step("Verify RAG hits and Greedy token footer"):
        assert "RAG hits for:" in out or "test-baseurl" in out
        _assert_greedy_token_footer(out)


@allure.story("Pipeline tool")
@allure.title("MCP pipeline list returns named recipes")
def test_mcp_pipeline_list_recipes() -> None:
    with allure.step("Call greedy_token_pipeline list"):
        out = greedy_token_pipeline("list")
        attach_text("pipeline list response", out)
    with allure.step("Verify named recipes are listed"):
        assert "meta-audit" in out
        assert "search-rag" in out


@allure.story("Pipeline tool")
@allure.title("MCP pipeline dry-run includes per-step Greedy token footer")
def test_mcp_pipeline_dry_run_includes_footer(minimal_workspace: Path) -> None:
    with allure.step("Call greedy_token_pipeline dry-run"):
        out = greedy_token_pipeline("check-meta-sync then rag baseUrl")
        attach_text("pipeline response", out)
    with allure.step("Verify per-step savings footer"):
        assert "Per-step savings" in out
        assert "Saved vs naive Cursor chat" in out


@allure.story("Pipeline tool")
@allure.title("MCP pipeline execute=true runs allowlisted search+rag (not mock-only)")
def test_mcp_pipeline_execute_true(minimal_workspace: Path) -> None:
    with allure.step("Call greedy_token_pipeline with execute=true"):
        out = greedy_token_pipeline(
            "search baseUrl path=sample.js then rag baseUrl",
            execute=True,
        )
        attach_text("pipeline execute response", out)
    with allure.step("Verify steps ran and footer is present"):
        assert "[tool/ran]" in out or "ran]" in out
        assert "baseUrl" in out
        assert "Per-step savings" in out
        assert "ripgrep on disk — 0 LLM spend" in out
        assert "(dry-run)" not in out.split("---")[0]


@allure.story("Pipeline tool")
@allure.title("MCP pipeline defaults to dry-run when execute is omitted")
def test_mcp_pipeline_execute_false_by_default(minimal_workspace: Path) -> None:
    with allure.step("Call greedy_token_pipeline without execute flag"):
        out = greedy_token_pipeline("search baseUrl path=sample.js then rag baseUrl")
        attach_text("pipeline dry-run response", out)
    with allure.step("Verify dry-run (no real step execution)"):
        assert "(dry-run)" in out or "[tool/dry-run]" in out or "dry-run" in out
        assert "Per-step savings" in out


@allure.story("Usage tool")
@allure.title("MCP usage tool aggregates log events and session totals")
def test_mcp_usage_aggregates_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    log_file = tmp_path / "usage.jsonl"
    event = {
        "v": SCHEMA_VERSION,
        "cmd": "route",
        "ts": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "selected_tier": "tool",
        "est_tokens": 0,
        "cursor_baseline": 9600,
        "cursor_saved": 9000,
        "route_id": "tool-rg-search",
        "token_counter_method": "tiktoken/cl100k_base",
    }
    log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
    monkeypatch.setattr("greedy_token.mcp.log_path", lambda: log_file)

    with allure.step("Call greedy_token_usage with populated log"):
        out = greedy_token_usage("7d")
        attach_text("usage response", out)
        attach_text("log path", str(log_file))
    with allure.step("Verify aggregated session totals"):
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

    with allure.step("Call greedy_token_usage with empty log"):
        out = greedy_token_usage("7d")
        attach_text("usage response", out)
        attach_text("log path", str(log_file))
    with allure.step("Verify empty log message"):
        assert "No events since 7d" in out
        assert f"Log: {log_file}" in out


CRYSTAL_ID = "script-summarize-weekly-spend-report-table"


@allure.story("Crystallize tool")
@allure.title("MCP crystallize draft returns CLI-equivalent text")
def test_mcp_crystallize_draft(
    minimal_workspace: Path,
    crystal_home: Path,
    no_cheap_llm: None,
) -> None:
    with allure.step("Call greedy_token_crystallize draft"):
        out = greedy_token_crystallize("draft", CRYSTAL_ID)
        attach_text("draft response", out)
    with allure.step("Verify draft text"):
        assert "Draft crystal:" in out
        assert "shadow until" in out
        assert "scripts lint OK" in out


@allure.story("Crystallize tool")
@allure.title("MCP crystallize promote and reject follow draft")
def test_mcp_crystallize_promote_reject(
    minimal_workspace: Path,
    crystal_home: Path,
    no_cheap_llm: None,
) -> None:
    greedy_token_crystallize("draft", CRYSTAL_ID)
    with allure.step("Call greedy_token_crystallize promote"):
        out = greedy_token_crystallize("promote", CRYSTAL_ID)
        attach_text("promote response", out)
    with allure.step("Verify promote text"):
        assert "shadow → active" in out

    greedy_token_crystallize("draft", CRYSTAL_ID)
    with allure.step("Call greedy_token_crystallize reject"):
        out = greedy_token_crystallize("reject", CRYSTAL_ID)
        attach_text("reject response", out)
    with allure.step("Verify reject text"):
        assert "route removed=True" in out


@allure.story("Crystallize tool")
@allure.title("MCP crystallize draft includes lint failures in text output")
def test_mcp_crystallize_draft_lint_failed(
    minimal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from greedy_token.crystallize_l3 import DraftResult
    from pathlib import Path as PathCls

    result = DraftResult(
        crystal_id=CRYSTAL_ID,
        pattern="bad pattern",
        hits=3,
        draft_path=PathCls("draft.py"),
        config_path=PathCls("cfg.yaml"),
        shadow_until="2030-01-01T00:00:00Z",
        source="template",
        lint_ok=False,
        lint_violations=[{"id": "script-missing", "detail": "no script on disk"}],
    )
    monkeypatch.setattr(
        "greedy_token.mcp.draft_crystal",
        lambda cid, *, root, since: result,
    )
    with allure.step("Call greedy_token_crystallize draft with lint failure"):
        out = greedy_token_crystallize("draft", CRYSTAL_ID)
        attach_text("draft response", out)
    with allure.step("Verify lint failure is surfaced"):
        assert "Lint:    FAILED" in out
        assert "script-missing" in out


@allure.story("Crystallize tool")
@allure.title("MCP crystallize surfaces draft/promote errors and unknown action")
def test_mcp_crystallize_errors(minimal_workspace: Path) -> None:
    with allure.step("draft missing crystal raises"):
        with pytest.raises(ValueError, match="crystallize draft:"):
            greedy_token_crystallize("draft", "script-missing")
    with allure.step("promote without draft raises"):
        with pytest.raises(ValueError, match="crystallize promote:"):
            greedy_token_crystallize("promote", CRYSTAL_ID)
    with allure.step("unknown action raises"):
        with pytest.raises(ValueError, match="unknown action"):
            greedy_token_crystallize("bogus", CRYSTAL_ID)
