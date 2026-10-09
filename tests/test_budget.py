from __future__ import annotations

from pathlib import Path

import pytest

import allure
from greedy_token.baseline import cursor_overhead
from greedy_token.budget import format_savings_lines, format_tool_footer, wrap_mcp_response
from greedy_token.estimator import cursor_baseline
from greedy_token.usage import TurnSkipEvidence
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Greedy token"),
    allure.parent_suite("Greedy token"),
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
    with allure.step("Verify canonical Greedy token block"):
        assert lines == [
            "Saved vs naive agent chat (baseline: default-estimate)",
            "  Baseline (naive agent chat):  ~11,607  (default-estimate)",
            "  Spent (est. payload tokens): ~0  (ripgrep on disk)",
            "  Saved:             ~11,607  (= baseline − spent; baseline: default-estimate)",
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
            style="full",
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
        assert "Spent (est. payload tokens):" in footer
        assert "ripgrep on disk" in footer
        assert "Saved:" in footer
        assert "unknown" in footer
        assert "potential" in footer
        baseline = cursor_baseline(minimal_workspace, task)
        assert f"~{baseline:,}" in footer


@allure.story("Savings eligibility")
@allure.title("Failed tool outcome shows zero token savings and no time savings")
def test_format_tool_footer_failed_outcome(minimal_workspace: Path) -> None:
    footer = format_tool_footer(
        "find absent marker",
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
        duration_ms=42,
        task_success=False,
        style="full",
    )
    assert "Saved:             ~0" in footer
    assert "  Time saved:" not in footer


@allure.story("Tool footer")
@allure.title("Cursor tier footer keeps earned savings unknown without a source")
def test_format_tool_footer_cursor_no_savings(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("greedy_token.budget._policy_footer_lines", lambda root: [])
    monkeypatch.setattr("greedy_token.budget.route_task_all_tiers", lambda *a: [])
    task = "refactor header layout"
    with allure.step("Format tool footer for cursor tier"):
        footer = format_tool_footer(
            task,
            minimal_workspace,
            tier="cursor",
            est_tokens=11000,
            route_id="cursor-wiring",
            executor_sub="cursor",
            style="full",
        )
        attach_text("footer", footer)
    with allure.step("Verify unknown earned savings and zero formula potential"):
        assert "Executor: cursor" in footer
        assert "Baseline (naive agent chat):" in footer
        assert "Saved:             unknown" in footer
        assert "potential ~0 only if the agent turn was skipped" in footer


@allure.story("MCP response")
@allure.title("MCP response wrapper appends Greedy token footer")
def test_wrap_mcp_response_appends_footer(minimal_workspace: Path) -> None:
    with allure.step("Wrap MCP response with Greedy token footer"):
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
        assert "Greedy token" in out


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
    assert "docs/rag chunks read" in spent_hint("rag", 100)
    assert "no chunks counted" in spent_hint("rag", 0)
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
        style="full",
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
        style="full",
    )
    assert "docs/rag" in rag_footer


@allure.story("Tool footer")
@allure.title("RAG Tier alternatives ← this call uses actual Spent, not router estimate")
def test_format_tool_footer_rag_tier_alternatives_match_spent(
    minimal_workspace: Path,
) -> None:
    spent = 9091
    footer = format_tool_footer(
        "rag: false-green",
        minimal_workspace,
        tier="rag",
        est_tokens=spent,
        route_id="mcp-rag",
        executor_sub="rag",
        rag_hits=5,
        style="full",
    )
    attach_text("rag footer", footer)
    # Selected tier row must echo Spent, not router RAG_READ_TOKENS_FALLBACK (~1800).
    assert "← this call" in footer
    rag_line = next(ln for ln in footer.splitlines() if "rag (docs/rag read)" in ln)
    assert "9,091" in rag_line and "← this call" in rag_line
    assert f"Spent (est. payload tokens): ~{spent:,}" in footer


@allure.story("Footer style")
@allure.title("Compact footer is default and shorter than full")
def test_format_tool_footer_compact_default(minimal_workspace: Path) -> None:
    task = "search: baseUrl"
    compact = format_tool_footer(
        task,
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
        duration_ms=42,
    )
    full = format_tool_footer(
        task,
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
        duration_ms=42,
        style="full",
    )
    attach_text("compact footer", compact)
    assert "**Greedy token**" in compact
    assert "`rg`" in compact
    assert "Tier alternatives" not in compact
    assert len(compact) < len(full) // 2


@allure.story("Footer style")
@allure.title("Markdown footer includes token table")
def test_format_tool_footer_markdown(minimal_workspace: Path) -> None:
    footer = format_tool_footer(
        "search: test",
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
        duration_ms=10,
        style="markdown",
    )
    attach_text("markdown footer", footer)
    assert "### Greedy token" in footer
    assert "| spent |" in footer
    assert "| **saved** (baseline: default-estimate) |" in footer


@allure.story("Footer style")
@allure.title("Footer style resolves from workspace config and env")
def test_footer_style_config(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.settings import get_footer_settings

    assert get_footer_settings(minimal_workspace).style == "compact"

    (minimal_workspace / ".greedy-token.yaml").write_text(
        "footer:\n  style: markdown\n",
        encoding="utf-8",
    )
    assert get_footer_settings(minimal_workspace).style == "markdown"

    monkeypatch.setenv("GREEDY_TOKEN_FOOTER_STYLE", "full")
    assert get_footer_settings(minimal_workspace).style == "full"


@allure.story("Telemetry contract")
@allure.title("wrap_mcp_response recommendation: logged event never claims execution")
def test_wrap_mcp_response_recommendation_event(
    minimal_workspace: Path, tmp_path: Path, monkeypatch
) -> None:
    import json

    from greedy_token.router import RouteDecision

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    decision = RouteDecision(
        target="python",
        route_id="python-git-recent",
        confidence=0.61,
        confidence_source="formula",
        raw_score=1.35,
        matched=["git log"],
        command=None,
        note="",
        domains=[],
        est_tokens=100,
    )
    out = wrap_mcp_response(
        "Route: PYTHON",
        task="git log",
        tier="python",
        est_tokens=100,
        route_id="python-git-recent",
        root=minimal_workspace,
        executed=False,
        decision=decision,
    )
    attach_text("wrapped", out)
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    event = events[0]
    with allure.step("recommendation telemetry keeps real evidence, no execution"):
        assert event["phase"] == "recommended"
        assert event["executor"]["executed"] is False
        assert event["cursor_saved"] == 0
        assert event["savings_exclusion"] == "not_executed"
        assert event["confidence"] == 0.61
        assert event["confidence_source"] == "formula"
        assert event["raw_score"] == 1.35
        assert event["matched"] == ["git log"]
        assert event["operation_id"]
    with allure.step("footer does not claim savings"):
        assert "not executed" in out


@allure.story("Telemetry contract")
@allure.title("wrap_mcp_response outcome: request and outcome share one operation_id")
def test_wrap_mcp_response_outcome_correlation(
    minimal_workspace: Path, tmp_path: Path, monkeypatch
) -> None:
    import json

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    wrap_mcp_response(
        "ok",
        task="search: baseUrl",
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        root=minimal_workspace,
        outcome="success",
        outcome_layer="executor",
    )
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 2
    request, outcome_ev = events
    assert request["operation_id"] == outcome_ev["operation_id"]
    assert outcome_ev["event"] == "route_outcome"
    assert outcome_ev["est_tokens"] == 0
    assert outcome_ev["cursor_saved"] == 0


@allure.story("Telemetry contract")
@allure.title("Full-style footer surfaces the not-executed savings note")
def test_full_footer_not_executed_note(minimal_workspace: Path) -> None:
    footer = format_tool_footer(
        "git log",
        minimal_workspace,
        tier="python",
        est_tokens=100,
        route_id="python-git-recent",
        executed=False,
        style="full",
    )
    attach_text("footer", footer)
    assert "not executed" in footer
    assert "Saved note:" in footer


@allure.story("Savings footer")
@allure.title("undeclared origin keeps earned savings unknown, potential labelled")
def test_footer_saved_scoped_under_advisory(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = "probe task"
    baseline = cursor_baseline(minimal_workspace, task)
    monkeypatch.setenv("GREEDY_HOOK_MODE", "advisory")
    footer = format_tool_footer(
        task,
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
    )
    attach_text("advisory footer", footer)
    assert "saved **unknown**" in footer
    assert f"potential ~{baseline:,} only if the agent turn was skipped" in footer

    monkeypatch.setenv("GREEDY_HOOK_MODE", "gate")
    footer = format_tool_footer(
        task,
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
    )
    attach_text("gate footer", footer)
    assert f"potential ~{baseline:,} only if the agent turn was skipped" in footer


@allure.story("Savings footer")
@allure.title("configured intercept alone does not lift the claim")
def test_footer_saved_scoped_under_configured_intercept(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mode=intercept is config, not evidence: without a declared turn-skip
    origin with an authoritative source the earned figure stays unknown."""
    task = "probe task"
    baseline = cursor_baseline(minimal_workspace, task)
    monkeypatch.setenv("GREEDY_HOOK_MODE", "intercept")
    footer = format_tool_footer(
        task,
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
    )
    attach_text("intercept footer", footer)
    assert "saved **unknown**" in footer
    assert f"potential ~{baseline:,} only if the agent turn was skipped" in footer


@allure.story("Savings footer")
@allure.title("bare hook skip and synthetic DTO both keep earned savings unknown")
@pytest.mark.parametrize("source", ["not-a-source", "host-ledger:r1", "", " ", "host-ledger"])
@pytest.mark.parametrize("style", ["compact", "markdown", "full"])
def test_footer_saved_full_under_hook_turn_replacement(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, source: str, style: str,
) -> None:
    monkeypatch.setattr("greedy_token.budget._policy_footer_lines", lambda root: [])
    monkeypatch.setattr("greedy_token.budget.ollama_available", lambda: False)
    monkeypatch.setattr("greedy_token.router.ollama_available", lambda: False)
    task = "probe task"
    baseline = cursor_baseline(minimal_workspace, task)
    floor = baseline - cursor_overhead()
    unproven = format_tool_footer(
        task,
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
        invocation="hook",
        turn_replaced=True,
    )
    attach_text("hook footer (self-declared)", unproven)
    # A bare turn_replaced=True is the boundary's own claim — without a host
    # evidence source the earned figure stays unknown.
    assert "saved **unknown**" in unproven
    assert f"potential ~{baseline:,} only if the agent turn was skipped" in unproven

    referenced = format_tool_footer(
        task,
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
        invocation="hook",
        turn_replaced=True,
        turn_evidence="host-ledger:req-77",
    )
    attach_text("hook footer (reference string)", referenced)
    # A reference string is metadata, never proof — the qualifier stays.
    assert "saved **unknown**" in referenced
    assert f"potential ~{baseline:,} only if the agent turn was skipped" in referenced

    footer = format_tool_footer(
        task,
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
        invocation="hook",
        turn_replaced=True,
        turn_evidence=TurnSkipEvidence(
            source=source, saved_tokens=4_321, saved_ms=123,
        ),
        duration_ms=42,
        style=style,
    )
    attach_text("hook footer (synthetic reported DTO)", footer)
    # The caller-reported figure is not a source measurement; earned stays
    # unknown and the formula deltas remain separate labelled potentials.
    assert "unknown" in footer
    assert "measured by" not in footer
    assert "4,321" not in footer
    assert "123ms" not in footer
    assert f"potential ~{baseline:,} only if the agent turn was skipped" in footer
    assert floor >= 0


@allure.story("Savings footer")
@allure.title("formula potentials never validate earned footer savings for any metadata form")
@pytest.mark.parametrize("potential", [0, 4_321])
@pytest.mark.parametrize("style", ["compact", "markdown", "full"])
@pytest.mark.parametrize(
    "evidence",
    [None, "host-ledger:r1", " ", TurnSkipEvidence(source="host-ledger", saved_tokens=0, saved_ms=0)],
    ids=["none", "reference", "whitespace", "dto"],
)
def test_footer_reported_turn_evidence_zero_potential(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch,
    potential: int, style: str, evidence: str | TurnSkipEvidence | None,
) -> None:
    from greedy_token import budget

    monkeypatch.setattr(budget, "cursor_saved_for", lambda *a: potential)
    monkeypatch.setattr(budget, "_policy_footer_lines", lambda root: [])
    monkeypatch.setattr(budget, "route_task_all_tiers", lambda *a: [])
    kwargs = dict(
        tier="tool", est_tokens=0, task_success=True,
        invocation="hook", turn_replaced=True, duration_ms=42,
        turn_evidence=evidence,
    )
    ctx = budget._build_tool_footer_context("probe task", minimal_workspace, **kwargs)
    assert ctx.savings_scope == "unknown"
    assert ctx.saved is None
    assert ctx.saved_source == ""
    assert ctx.saved_potential == potential
    assert ctx.time_measured is False
    assert ctx.time_saved == budget.time_saved_ms(ctx.baseline, 42, "tool")
    assert ctx.time_saved != 0
    footer = budget.format_tool_footer("probe task", minimal_workspace, style=style, **kwargs)
    assert "unknown" in footer
    assert f"potential ~{potential:,} only if the agent turn was skipped" in footer
    assert "measured by" not in footer
    assert "saved **~0**" not in footer
    assert "Saved:             ~0" not in footer


@allure.story("Savings footer")
@allure.title("MCP origin footer marks the shared agent turn")
def test_footer_saved_shared_under_mcp(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREEDY_HOOK_MODE", "intercept")
    task = "probe task"
    baseline = cursor_baseline(minimal_workspace, task)
    floor = baseline - cursor_overhead()
    footer = format_tool_footer(
        task,
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
        invocation="mcp",
    )
    attach_text("mcp footer", footer)
    # MCP is an entrypoint fact, not a proven model-turn skip: earned stays
    # unknown and the shared-turn formula delta is a labelled assumption.
    assert "saved **unknown**" in footer
    assert (
        f"potential ~{floor:,} if the call shared a live agent turn"
        " — assumed, not observed" in footer
    )
    assert floor < baseline


@allure.story("Savings footer")
@allure.title("legacy threshold env alone does not lift the scope qualifier")
def test_footer_saved_scoped_under_legacy_threshold(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GREEDY_HOOK_MODE", raising=False)
    monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", "0.9")
    footer = format_tool_footer(
        "probe task",
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
    )
    assert "only if the agent turn was skipped" in footer


@allure.story("Savings footer")
@allure.title("no saved claim → no scope qualifier even in advisory mode")
def test_footer_saved_dual_suppressed_without_savings(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREEDY_HOOK_MODE", "advisory")
    footer = format_tool_footer(
        "probe task",
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
        executed=False,
    )
    attach_text("not-executed footer", footer)
    assert "not executed" in footer
    assert "scope unknown" not in footer
    assert "agent turn" not in footer
