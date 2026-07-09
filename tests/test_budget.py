from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.budget import format_savings_lines, format_tool_footer, wrap_mcp_response
from greedy_token.estimator import cursor_baseline
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Token economy"),
    allure.parent_suite("Token economy"),
    allure.feature("Token budget"),
    allure.suite("Token budget"),
]


@allure.story("Savings footer")
@allure.title("Format savings lines for MCP tool footer")
def test_format_savings_lines() -> None:
    with allure.step("Format savings lines"):
        lines = format_savings_lines(
            baseline=11607,
            spent=0,
            saved=11607,
            tier="tool",
            executor_sub="rg",
        )
        attach_text("footer lines", "\n".join(lines))
    with allure.step("Verify canonical Token economy block"):
        assert lines == [
            "Saved vs naive Cursor chat",
            "  Baseline (naive agent chat):  ~11,607",
            "  Spent (MCP executor, LLM tokens): ~0  (ripgrep on disk — 0 LLM spend)",
            "  Saved:             ~11,607  (= baseline − spent)",
        ]


@allure.story("Tool footer")
@allure.title("Tool footer includes detailed token breakdown")
def test_format_tool_footer_detailed_breakdown(minimal_workspace: Path) -> None:
    task = "search: baseUrl in configurator-option-presets.html"
    with allure.step("Format tool footer for search task"):
        footer = format_tool_footer(
            task,
            minimal_workspace,
            tier="tool",
            est_tokens=0,
            route_id="mcp-search",
            executor_sub="rg",
            duration_ms=42,
        )
        attach_text("footer", footer)
    with allure.step("Verify detailed token breakdown sections"):
        assert "This call" in footer
        assert "Executor: rg" in footer
        assert "Duration: 42 ms" in footer
        assert "Always-on rules:" in footer
        assert "Agent overhead:" in footer
        assert "Tier alternatives" in footer
        assert "rg (disk search)" in footer
        assert "cursor (expensive LLM)" in footer
        assert "← this call" in footer
        assert "Baseline (naive agent chat):" in footer
        assert "Spent (MCP executor, LLM tokens):" in footer
        assert "ripgrep on disk — 0 LLM spend" in footer
        assert "Saved:" in footer
        assert "(= baseline − spent)" in footer
        baseline = cursor_baseline(minimal_workspace, task)
        assert f"~{baseline:,}" in footer


@allure.story("Tool footer")
@allure.title("Cursor tier footer shows zero savings")
def test_format_tool_footer_cursor_no_savings(minimal_workspace: Path) -> None:
    task = "refactor header layout"
    with allure.step("Format tool footer for cursor tier"):
        footer = format_tool_footer(
            task,
            minimal_workspace,
            tier="cursor",
            est_tokens=11000,
            route_id="cursor-wiring",
            executor_sub="cursor",
        )
        attach_text("footer", footer)
    with allure.step("Verify zero savings for cursor tier"):
        assert "Executor: cursor" in footer
        assert "Baseline (naive agent chat):" in footer
        assert "Saved:             ~0" in footer


@allure.story("MCP response")
@allure.title("MCP response wrapper appends Token economy footer")
def test_wrap_mcp_response_appends_footer(minimal_workspace: Path) -> None:
    with allure.step("Wrap MCP response with Token economy footer"):
        out = wrap_mcp_response(
            "result line",
            task="search: baseUrl",
            tier="tool",
            est_tokens=0,
            route_id="mcp-search",
            root=minimal_workspace,
            log=False,
            executor_sub="rg",
        )
        attach_text("wrapped response", out)
    with allure.step("Verify footer is appended"):
        assert out.startswith("result line")
        assert "Token economy" in out


@allure.story("RAG tokens")
@allure.title("rag_est_tokens counts body and file paths")
def test_rag_est_tokens(minimal_workspace: Path) -> None:
    from greedy_token.budget import rag_est_tokens
    from greedy_token.rag_search import RagHit

    hits = [
        RagHit("id1", "docs/rag/config/test-chunk.md", "config", 1.0, "excerpt", body="baseUrl text"),
        RagHit("id2", "missing.md", "config", 1.0, "excerpt only"),
    ]
    total = rag_est_tokens(hits, minimal_workspace)
    assert total > 0


@allure.story("Spent hints")
@allure.title("spent_hint covers all executor tiers")
def test_spent_hint_all_tiers() -> None:
    from greedy_token.budget import spent_hint

    assert "ripgrep" in spent_hint("tool", 0, "rg")
    assert "script" in spent_hint("python", 0)
    assert "cheap LLM" in spent_hint("ollama", 100)
    assert "docs/rag" in spent_hint("rag", 100)
    assert "expensive LLM" in spent_hint("cursor", 100)
    assert spent_hint("unknown", 0) == ""


@allure.story("Tool footer")
@allure.title("format_tool_footer covers ollama and rag billing lines")
def test_format_tool_footer_ollama_rag(minimal_workspace: Path) -> None:
    ollama_footer = format_tool_footer(
        "audit skill",
        minimal_workspace,
        tier="ollama",
        est_tokens=2500,
        route_id="ollama-audit",
        executor_sub="ollama",
        ollama_eval_tokens=100,
    )
    assert "cheap LLM" in ollama_footer

    rag_footer = format_tool_footer(
        "rag query",
        minimal_workspace,
        tier="rag",
        est_tokens=1800,
        route_id="mcp-rag",
        executor_sub="rag",
        rag_hits=3,
    )
    assert "docs/rag" in rag_footer

