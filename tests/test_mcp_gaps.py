"""Edge-branch tests for MCP search tool (fail_under=100)."""

from __future__ import annotations

from pathlib import Path

import pytest

import allure
from greedy_token.mcp import greedy_token_search

pytestmark = [
    allure.epic("MCP"),
    allure.parent_suite("MCP"),
    allure.feature("MCP tools"),
    allure.suite("MCP gaps"),
]


@allure.title("search tool: invalid context raises a clear error")
def test_search_invalid_context_raises(minimal_workspace: Path) -> None:
    with pytest.raises(ValueError, match=r"invalid context 'bogus'"):
        greedy_token_search("zzz-nonexistent-token-xyz", path="sample.js", context="bogus")


@allure.title("route tool records the observed transition for a cursor decision")
def test_route_tool_cursor_transition(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    import greedy_token.mcp as mcp
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="cursor",
        route_id="cursor-fallback",
        confidence=0.5,
        matched=[],
        command=None,
        note="",
        domains=[],
        complexity="high",
        est_tokens=9000,
        rationale="complex",
    )
    spy = Mock()
    monkeypatch.setattr(mcp, "find_workspace_root", lambda: minimal_workspace)
    monkeypatch.setattr(
        mcp,
        "estimate_task",
        lambda task, root: SimpleNamespace(decision=decision, est_tokens=9000),
    )
    monkeypatch.setattr("greedy_token.cheap_llm.observe_transition", spy)
    mcp.greedy_token_route("refactor the whole subsystem")
    spy.assert_called_once_with(
        "direct->cursor",
        request_kind="route_decision",
        route_id="cursor-fallback",
    )


@allure.title("usage tool emits the machine envelope when machine mode is on")
def test_usage_tool_machine_mode(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as _json
    from types import SimpleNamespace

    import greedy_token.mcp as mcp

    monkeypatch.setattr(mcp, "log_path", lambda: minimal_workspace / "usage.jsonl")
    monkeypatch.setattr(mcp, "load_events", lambda path, since: ([], 0))
    monkeypatch.setattr(
        mcp,
        "aggregate_events",
        lambda events, since_label: SimpleNamespace(by_tier={}),
    )
    monkeypatch.setattr(mcp, "format_report", lambda summary: "REPORT")
    monkeypatch.setattr(mcp, "format_savings_lines", lambda **kw: ["L1"])
    monkeypatch.setattr(mcp, "find_workspace_root", lambda: minimal_workspace)
    monkeypatch.setattr(mcp, "_machine_mode", lambda root: True)
    out = mcp.greedy_token_usage("7d")
    doc = _json.loads(out)
    assert doc["op"] == "usage"
    assert doc["count"] == 0
    assert doc["skipped_lines"] == 0


@allure.title("pipeline tool emits the machine envelope when machine mode is on")
def test_pipeline_tool_machine_mode(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as _json
    from types import SimpleNamespace

    import greedy_token.mcp as mcp

    step_result = SimpleNamespace(
        step=SimpleNamespace(step_id="search", tier="tool"),
        ok=True,
        exit_code=0,
        executed=True,
        result_status="produced",
        duration_ms=12,
    )
    result = SimpleNamespace(
        task="pipeline: x",
        all_ok=True,
        steps=[step_result],
        stopped_early=False,
    )
    monkeypatch.setattr(mcp, "find_workspace_root", lambda: minimal_workspace)
    monkeypatch.setattr(mcp, "run_pipeline", lambda *a, **k: result)
    monkeypatch.setattr(mcp, "format_pipeline_response", lambda r, root: "OUT")
    monkeypatch.setattr(mcp, "_machine_mode", lambda root: True)
    out = mcp.greedy_token_pipeline("pipeline: check-meta-sync")
    doc = _json.loads(out)
    assert doc["op"] == "pipeline"
    assert doc["ok"] is True
    assert doc["step_count"] == 1
    assert doc["steps"][0]["step_id"] == "search"
    assert doc["result_status"] == "produced"


@allure.title("crystallize reject emits the machine envelope when machine mode is on")
def test_crystallize_machine_result(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as _json
    from types import SimpleNamespace

    import greedy_token.mcp as mcp

    monkeypatch.setattr(mcp, "find_workspace_root", lambda: minimal_workspace)
    monkeypatch.setattr(mcp, "_machine_mode", lambda root: True)
    monkeypatch.setattr(
        mcp,
        "reject_crystal",
        lambda crystal_id, root, actor, reason: SimpleNamespace(ok=True),
    )
    monkeypatch.setattr(
        mcp, "format_reject_result", lambda result, crystal_id: "REJECTED"
    )
    out = mcp.greedy_token_crystallize("reject", crystal_id="c-1", reason="no")
    doc = _json.loads(out)
    assert doc["ok"] is True
    assert doc["result_status"] == "produced"
    assert doc["output"] == "REJECTED"
