from __future__ import annotations

from pathlib import Path

import pytest

from greedy_token.budget import format_savings_lines, format_tool_footer, wrap_mcp_response
from greedy_token.estimator import cursor_baseline


def test_format_savings_lines() -> None:
    lines = format_savings_lines(
        baseline=11607,
        spent=0,
        saved=11607,
        tier="tool",
        executor_sub="rg",
    )
    assert lines == [
        "Saved vs naive Cursor chat",
        "  Baseline (naive agent chat):  ~11,607",
        "  Spent (MCP executor, LLM tokens): ~0  (ripgrep on disk — no cloud LLM)",
        "  Saved:             ~11,607  (= baseline − spent)",
    ]


def test_format_tool_footer_detailed_breakdown() -> None:
    root = Path("/Users/stanislav/zero-design-system")
    task = "search: baseUrl in configurator-option-presets.html"
    footer = format_tool_footer(
        task,
        root,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
        duration_ms=42,
    )
    assert "This call" in footer
    assert "Executor: rg" in footer
    assert "Duration: 42 ms" in footer
    assert "Always-on rules:" in footer
    assert "Agent overhead:" in footer
    assert "Tier alternatives" in footer
    assert "rg (local search)" in footer
    assert "cursor (agent / cloud)" in footer
    assert "← this call" in footer
    assert "Baseline (naive agent chat):" in footer
    assert "Spent (MCP executor, LLM tokens):" in footer
    assert "ripgrep on disk — no cloud LLM" in footer
    assert "Saved:" in footer
    assert "(= baseline − spent)" in footer
    baseline = cursor_baseline(root, task)
    assert f"~{baseline:,}" in footer


def test_format_tool_footer_cursor_no_savings() -> None:
    root = Path("/Users/stanislav/zero-design-system")
    task = "refactor header layout"
    footer = format_tool_footer(
        task,
        root,
        tier="cursor",
        est_tokens=11000,
        route_id="cursor-wiring",
        executor_sub="cursor",
    )
    assert "Executor: cursor" in footer
    assert "Baseline (naive agent chat):" in footer
    assert "Saved:             ~0" in footer


def test_wrap_mcp_response_appends_footer() -> None:
    root = Path("/Users/stanislav/zero-design-system")
    out = wrap_mcp_response(
        "result line",
        task="search: baseUrl",
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        root=root,
        log=False,
        executor_sub="rg",
    )
    assert out.startswith("result line")
    assert "Token economy" in out
