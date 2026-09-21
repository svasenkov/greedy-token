from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.crystal_ids import crystal_id_for_pattern
from tests.allure_reporting import attach_text
from tests.mcp_stdio_helpers import run_mcp, tool_text

pytest.importorskip("mcp")

pytestmark = [
    allure.epic("MCP"),
    allure.parent_suite("MCP"),
    allure.feature("MCP stdio server"),
    allure.suite("MCP stdio server"),
]


def _assert_greedy_token_footer(text: str) -> None:
    assert "Greedy token" in text
    assert "saved **~" in text
    assert "> spent ~" in text


def _assert_search_backend_billing(text: str) -> None:
    rg_billing = "ripgrep on disk — 0 LLM spend" in text
    python_billing = "script — 0 LLM spend" in text
    assert rg_billing or python_billing
    if rg_billing:
        assert "rg (disk search)" in text
    else:
        assert "python (script)" in text
        assert "rg (disk search)" not in text


@allure.story("Server handshake")
@allure.title("MCP stdio server advertises six greedy-token tools")
def test_mcp_stdio_lists_six_tools(minimal_workspace: Path) -> None:
    async def _list(session):
        tools = await session.list_tools()
        return [t.name for t in tools.tools]

    with allure.step("List MCP stdio tools"):
        names = run_mcp(minimal_workspace, _list)
        attach_text("tool names", "\n".join(names))
    with allure.step("Verify six greedy-token tools are advertised"):
        assert names == [
            "greedy_token_route",
            "greedy_token_rag",
            "greedy_token_search",
            "greedy_token_usage",
            "greedy_token_pipeline",
            "greedy_token_crystallize",
        ]


@allure.story("Route tool")
@allure.title("MCP stdio route tool returns tier decision with Greedy token footer")
def test_mcp_stdio_route_includes_greedy_token(minimal_workspace: Path) -> None:
    async def _call(session):
        return await session.call_tool(
            "greedy_token_route",
            {"task": "find baseUrl in sample.js"},
        )

    with allure.step("Call greedy_token_route via MCP stdio"):
        result = run_mcp(minimal_workspace, _call)
        text = tool_text(result)
        attach_text("route response", text)
    with allure.step("Verify TOOL route and Greedy token footer"):
        assert "Route: TOOL" in text
        _assert_greedy_token_footer(text)


@allure.story("Search tool")
@allure.title("MCP stdio search tool finds baseUrl in workspace file")
def test_mcp_stdio_search_finds_match(minimal_workspace: Path) -> None:
    async def _call(session):
        return await session.call_tool(
            "greedy_token_search",
            {"query": "baseUrl", "path": "sample.js"},
        )

    with allure.step("Call greedy_token_search via MCP stdio"):
        result = run_mcp(minimal_workspace, _call)
        text = tool_text(result)
        attach_text("search response", text)
    with allure.step("Verify baseUrl match and Greedy token footer"):
        assert "baseUrl" in text
        assert "free tier" in text
        _assert_greedy_token_footer(text)


@allure.story("RAG tool")
@allure.title("MCP stdio RAG tool returns doc hits with Greedy token footer")
def test_mcp_stdio_rag_returns_hits(minimal_workspace: Path) -> None:
    async def _call(session):
        return await session.call_tool(
            "greedy_token_rag",
            {"query": "baseUrl -D flag", "domain": "config"},
        )

    with allure.step("Call greedy_token_rag via MCP stdio"):
        result = run_mcp(minimal_workspace, _call)
        text = tool_text(result)
        attach_text("rag response", text)
    with allure.step("Verify RAG hits and Greedy token footer"):
        assert "RAG hits" in text or "test-baseurl" in text
        _assert_greedy_token_footer(text)


@allure.story("Pipeline tool")
@allure.title("MCP stdio pipeline dry-run includes per-step Greedy token footer")
def test_mcp_stdio_pipeline_dry_run_footer(minimal_workspace: Path) -> None:
    async def _call(session):
        return await session.call_tool(
            "greedy_token_pipeline",
            {"task": "check-meta-sync then rag baseUrl", "execute": False},
        )

    with allure.step("Call greedy_token_pipeline dry-run via MCP stdio"):
        result = run_mcp(minimal_workspace, _call)
        text = tool_text(result)
        attach_text("pipeline response", text)
    with allure.step("Verify per-step savings footer"):
        assert "Per-step savings" in text
        assert "Saved vs naive agent chat" in text


@allure.story("Pipeline tool")
@allure.title("MCP stdio pipeline execute=true runs allowlisted search+rag (not mock-only)")
def test_mcp_stdio_pipeline_execute_true(minimal_workspace: Path) -> None:
    async def _call(session):
        return await session.call_tool(
            "greedy_token_pipeline",
            {
                "task": "search baseUrl path=sample.js then rag baseUrl",
                "execute": True,
            },
        )

    with allure.step("Call greedy_token_pipeline with execute=true via MCP stdio"):
        result = run_mcp(minimal_workspace, _call)
        text = tool_text(result)
        attach_text("pipeline execute response", text)
    with allure.step("Verify steps ran and footer is present"):
        assert "[tool/ran]" in text or "ran]" in text
        assert "baseUrl" in text
        assert "Per-step savings" in text
        _assert_search_backend_billing(text)
        assert "(dry-run)" not in text.split("---")[0]


@allure.story("Usage tool")
@allure.title("MCP stdio usage tool reports empty log gracefully")
def test_mcp_stdio_usage_empty_log(minimal_workspace: Path, tmp_path: Path) -> None:
    log_file = tmp_path / "usage.jsonl"
    log_file.write_text("", encoding="utf-8")

    async def _call(session):
        return await session.call_tool("greedy_token_usage", {"since": "7d"})

    with allure.step("Call greedy_token_usage with empty log via MCP stdio"):
        result = run_mcp(minimal_workspace, _call, log_path=log_file)
        text = tool_text(result)
        attach_text("usage response", text)
        attach_text("log path", str(log_file))
    with allure.step("Verify empty log message"):
        assert "No events since 7d" in text
        assert f"Log: {log_file}" in text


CRYSTAL_ID = crystal_id_for_pattern("summarize weekly spend report table")


@allure.story("Crystallize tool")
@allure.title("MCP stdio crystallize draft→promote→reject matches CLI safe-mode flow")
def test_mcp_stdio_crystallize_l3_flow(
    minimal_workspace: Path,
    crystal_home: Path,
    no_cheap_llm: None,
) -> None:
    async def _draft(session):
        return await session.call_tool(
            "greedy_token_crystallize",
            {"action": "draft", "crystal_id": CRYSTAL_ID},
        )

    async def _promote(session):
        return await session.call_tool(
            "greedy_token_crystallize",
            {"action": "promote", "crystal_id": CRYSTAL_ID},
        )

    async def _reject(session):
        return await session.call_tool(
            "greedy_token_crystallize",
            {"action": "reject", "crystal_id": CRYSTAL_ID},
        )

    with allure.step("Call greedy_token_crystallize draft via MCP stdio"):
        log_path = crystal_home / "usage.jsonl"
        draft = run_mcp(minimal_workspace, _draft, log_path=log_path)
        draft_text = tool_text(draft)
        attach_text("draft response", draft_text)
    with allure.step("Verify draft output matches CLI semantics"):
        assert "Draft crystal:" in draft_text
        assert "shadow until" in draft_text
        assert "scripts lint OK" in draft_text

    with allure.step("Call greedy_token_crystallize promote via MCP stdio"):
        promote = run_mcp(minimal_workspace, _promote, log_path=log_path)
        promote_text = tool_text(promote)
        attach_text("promote response", promote_text)
    with allure.step("Verify promote output"):
        assert "shadow → active" in promote_text

    with allure.step("Call greedy_token_crystallize reject via MCP stdio"):
        reject = run_mcp(minimal_workspace, _reject, log_path=log_path)
        reject_text = tool_text(reject)
        attach_text("reject response", reject_text)
    with allure.step("Verify reject output"):
        assert "Rejected" in reject_text
        assert "route removed=True" in reject_text


@allure.story("Telemetry contract")
@allure.title("MCP stdio route logs a recommendation, not an execution")
def test_mcp_stdio_route_telemetry_is_recommendation(
    minimal_workspace: Path, tmp_path: Path
) -> None:
    import json

    log = tmp_path / "usage.jsonl"

    async def _call(session):
        return await session.call_tool("greedy_token_route", {"task": "git log"})

    result = run_mcp(minimal_workspace, _call, log_path=log)
    text = tool_text(result)
    attach_text("route response", text)

    assert log.is_file(), "expected a telemetry record"
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    event = events[0]
    attach_text("route event", json.dumps(event, indent=2))
    with allure.step("recommendation: no execution, no credited savings"):
        assert event["phase"] == "recommended"
        assert event["executor"]["executed"] is False
        assert event["cursor_saved"] == 0
        assert event["savings_eligible"] is False
        assert event["savings_exclusion"] == "not_executed"
        assert event["operation_id"]
        assert "time_saved_ms" not in event
        assert "outcome" not in event
    with allure.step("routing evidence is the real decision's, not a synthetic one"):
        assert event["route_id"] == "python-git-recent"
        assert event["confidence"] < 1.0
        assert event["confidence_source"] == "formula"
        assert event["matched"]
        # Potential estimate kept separate, never as earned savings.
        assert event["cursor_saved_potential"] > 0
    with allure.step("footer does not claim the saved estimate"):
        assert "not executed" in text


@allure.story("Telemetry contract")
@allure.title("MCP stdio with GREEDY_TOKEN_LOG=0 writes no usage records")
def test_mcp_stdio_log_zero_writes_nothing(
    minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("GREEDY_TOKEN_HOME", raising=False)
    default_log = fake_home / ".greedy-token" / "usage.jsonl"

    async def _call(session):
        await session.call_tool("greedy_token_route", {"task": "git log"})
        await session.call_tool("greedy_token_search", {"query": "baseUrl"})
        return await session.call_tool(
            "greedy_token_pipeline", {"task": "check-meta-sync", "execute": True}
        )

    # log_path=None → helper exports GREEDY_TOKEN_LOG=0 to the subprocess.
    run_mcp(minimal_workspace, _call)
    assert not default_log.exists()


@allure.story("Telemetry contract")
@allure.title("MCP stdio pipeline emits correlated request/outcome per executed step")
def test_mcp_stdio_pipeline_operation_correlation(
    minimal_workspace: Path, tmp_path: Path
) -> None:
    import json

    log = tmp_path / "usage.jsonl"

    async def _call(session):
        return await session.call_tool(
            "greedy_token_pipeline",
            {"task": "rag zzzz-no-such-term", "execute": True},
        )

    run_mcp(minimal_workspace, _call, log_path=log)
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    attach_text("pipeline events", json.dumps(events, indent=2))
    assert len(events) == 2
    request, outcome = events
    with allure.step("one operation_id correlates request and outcome"):
        assert request["operation_id"] == outcome["operation_id"]
        assert request["operation_id"]
        assert request["parent_operation_id"] == outcome["parent_operation_id"]
        assert request["parent_operation_id"]
    with allure.step("empty rag result is not a success and earns nothing"):
        assert request["cursor_saved"] == 0
        assert outcome["event"] == "route_outcome"
        assert outcome["outcome"] == "failure"
        assert outcome["outcome_layer"] == "pipeline"
