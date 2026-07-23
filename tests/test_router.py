from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from greedy_token.router import TIER_ORDER, route_task, route_task_all_tiers
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Routing"),
    allure.parent_suite("Routing"),
    allure.feature("Task router"),
    allure.suite("Task router"),
]


@allure.story("Invariants")
@allure.title("route_task never raises and always yields a valid tier for arbitrary input")
@given(
    task=st.text(max_size=120)
    | st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=120)
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_route_task_total_on_arbitrary_input(minimal_workspace: Path, task: str) -> None:
    # ollama pinned unavailable => deterministic, network-free routing.
    with patch("greedy_token.router.ollama_available", return_value=False):
        decision = route_task(task, minimal_workspace)
    assert decision.target in set(TIER_ORDER)
    assert decision.target
    assert decision.est_tokens >= 0
    assert isinstance(decision.route_id, str) and decision.route_id


@allure.story("Invariants")
@allure.title("route_task stays valid when the cheap LLM tier is available")
@given(task=st.text(max_size=120))
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_route_task_total_with_ollama_available(minimal_workspace: Path, task: str) -> None:
    with patch("greedy_token.router.ollama_available", return_value=True):
        decision = route_task(task, minimal_workspace)
    assert decision.target in set(TIER_ORDER)
    assert decision.est_tokens >= 0


@allure.story("Scoring")
@allure.title("_score_patterns accumulates per-match weight and length bonus")
def test_score_patterns_accumulates() -> None:
    from greedy_token.router import _score_patterns

    with allure.step("Two matching patterns accumulate (score is not overwritten)"):
        score_both, matched = _score_patterns("alpha beta", ["alpha", "beta"])
        score_one, _ = _score_patterns("alpha beta", ["alpha"])
        assert matched == ["alpha", "beta"]
        assert score_both > score_one
    with allure.step("Exact weight: each match adds 1.0 + min(len/20, 2.0)"):
        expected = (1.0 + min(len("alpha") / 20.0, 2.0)) + (1.0 + min(len("beta") / 20.0, 2.0))
        assert score_both == expected
    with allure.step("Length bonus is capped at 2.0 for long patterns"):
        long_pat = "x" * 100
        score_long, _ = _score_patterns(long_pat, [long_pat])
        assert score_long == 1.0 + 2.0
    with allure.step("No match yields a zero score and empty match list"):
        assert _score_patterns("zzz", ["alpha"]) == (0.0, [])


@allure.story("Tool tier")
@allure.title("Route find task to tool tier with read-only plan")
def test_route_find_goes_to_tool(minimal_workspace: Path) -> None:
    with allure.step("Route find task"):
        decision = route_task("find baseUrl in sample.js", minimal_workspace)
        attach_json("decision", {"target": decision.target, "read_only": decision.read_only, "route_id": decision.route_id})
        attach_text("command", decision.command or "")
    with allure.step("Verify tool tier with read-only plan"):
        assert decision.target == "tool"
        assert decision.read_only is True
        assert decision.command is not None


@allure.story("RAG tier")
@allure.title("Route documentation question to RAG tier")
def test_route_rag_question(minimal_workspace: Path) -> None:
    with allure.step("Route documentation question"):
        decision = route_task("which -D flag for baseUrl", minimal_workspace)
        attach_json("decision", {"target": decision.target, "route_id": decision.route_id})
    with allure.step("Verify RAG tier selection"):
        assert decision.target == "rag"


@allure.story("Cursor tier")
@allure.title("Route open-ended task to cursor fallback")
def test_route_cursor_fallback(minimal_workspace: Path) -> None:
    with allure.step("Route open-ended explain task"):
        decision = route_task("explain quantum foam in repository layout", minimal_workspace)
        attach_json("decision", {"target": decision.target, "route_id": decision.route_id})
    with allure.step("Verify cursor fallback route"):
        assert decision.target == "cursor"
        assert decision.route_id == "cursor-fallback"


@patch("greedy_token.router.ollama_available", return_value=False)
@allure.story("Ollama availability")
@allure.title("Route skips Ollama tier when server is unavailable")
def test_route_skips_unavailable_ollama(mock_ollama, minimal_workspace: Path) -> None:
    with allure.step("Route audit task with Ollama unavailable"):
        decision = route_task("audit skill configurator-boolean", minimal_workspace)
        attach_json("decision", {"target": decision.target, "route_id": decision.route_id})
    with allure.step("Verify Ollama tier is skipped"):
        assert decision.target != "ollama"


@patch("greedy_token.router.ollama_available", return_value=True)
@allure.story("Ollama routes")
@allure.title("Draft-rag phrases no longer hit removed ollama-rag-draft stub")
def test_draft_rag_does_not_hit_removed_null_route(mock_ollama, minimal_workspace: Path) -> None:
    from greedy_token.executors import plan_run

    with allure.step("Route former phantom draft-rag phrase"):
        decision = route_task("draft rag chunk", minimal_workspace)
        plan = plan_run(decision, "draft rag chunk", minimal_workspace)
        attach_json(
            "decision",
            {
                "target": decision.target,
                "route_id": decision.route_id,
                "command": decision.command,
                "dry_run": plan.dry_run_output[:120],
            },
        )
    with allure.step("Verify no null-command ollama-rag-draft / No executor"):
        assert decision.route_id != "ollama-rag-draft"
        assert plan.dry_run_output != "No executor."
        if decision.target == "ollama":
            assert decision.command


@allure.story("Tier scan")
@allure.title("Full tier scan returns five executor rows")
def test_route_task_all_tiers_has_five_rows(minimal_workspace: Path) -> None:
    with allure.step("Run full tier scan for find task"):
        tiers = route_task_all_tiers("find baseUrl", minimal_workspace)
        attach_json("tier scan", [{"tier": t[0], "label": t[1]} for t in tiers])
    with allure.step("Verify five executor rows in order"):
        assert len(tiers) == 5
        assert [t[0] for t in tiers] == ["tool", "python", "ollama", "rag", "cursor"]


@allure.story("Shadow routes")
@allure.title("Disabled shadow route does not execute script tier")
def test_disabled_shadow_route_is_skipped(minimal_workspace: Path) -> None:
    # Pin "now" inside the configured shadow window so the assertion does not
    # depend on the wall clock (the shadow_until date would otherwise expire).
    from datetime import datetime, timezone

    fixed_now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with allure.step("Route task matching access-diag shadow route"), patch(
        "greedy_token.router._now", return_value=fixed_now
    ):
        decision = route_task("access diag", minimal_workspace)
        attach_json(
            "decision",
            {
                "target": decision.target,
                "route_id": decision.route_id,
                "shadow_route_id": decision.shadow_route_id,
            },
        )
    with allure.step("Verify disabled shadow route is skipped but logged"):
        assert decision.route_id != "python-access-diag"
        assert decision.target == "cursor"
        assert decision.shadow_route_id == "python-access-diag"


@allure.story("Token estimate")
@allure.title("Ollama available route reports non-zero est_tokens")
@patch("greedy_token.router.ollama_available", return_value=True)
def test_ollama_est_tokens_nonzero(mock_ollama, minimal_workspace: Path) -> None:
    from greedy_token.router import _token_estimate_for_route

    with allure.step("Estimate tokens for available ollama route"):
        complexity, est, rationale = _token_estimate_for_route(
            "ollama",
            task="audit skill configurator-boolean",
            root=minimal_workspace,
        )
        attach_json("estimate", {"complexity": complexity, "est_tokens": est, "rationale": rationale})
    with allure.step("Verify est_tokens is positive cheap-LLM spend"):
        assert est > 0
        assert "Cheap LLM" in rationale
        assert "0 API spend" not in rationale


@allure.story("Format decision")
@allure.title("format_decision includes command, domains, and cursor hint")
def test_format_decision_full(minimal_workspace: Path) -> None:
    from greedy_token.router import RouteDecision, format_decision

    rag_decision = RouteDecision(
        target="rag",
        route_id="rag-lookup",
        confidence=0.9,
        matched=["rag"],
        command=None,
        note="extra note",
        domains=["config"],
        complexity="low",
        est_tokens=100,
        rationale="lookup docs",
    )
    rag_out = format_decision(rag_decision, "baseUrl flag", minimal_workspace)
    assert "RAG domains" in rag_out
    assert "greedy-token rag" in rag_out

    tool_decision = RouteDecision(
        target="tool",
        route_id="tool-rg",
        confidence=0.9,
        matched=["find"],
        command="rg needle",
        note="",
        domains=[],
        complexity="low",
        est_tokens=0,
        rationale="search",
        read_only=True,
    )
    tool_out = format_decision(tool_decision, "find needle", minimal_workspace)
    assert "Command:" in tool_out
    assert "read-only" in tool_out

    cursor_out = format_decision(
        RouteDecision(
            target="cursor",
            route_id="cursor-fallback",
            confidence=0.3,
            matched=[],
            command=None,
            note="",
            domains=[],
            complexity="high",
            est_tokens=9000,
            rationale="wiring",
        ),
        "refactor header",
        minimal_workspace,
    )
    assert "New agent chat" in cursor_out


@allure.story("Explainable routing")
@allure.title("explain_route returns reason, matched, saved_est and runner_up")
def test_explain_route_structure(minimal_workspace: Path) -> None:
    from greedy_token.router import explain_route

    decision = route_task("find baseUrl in sample.js", minimal_workspace)
    exp = explain_route(decision, "find baseUrl in sample.js", minimal_workspace)
    attach_json("explanation", exp)
    assert exp["selected_tier"] == decision.target
    assert exp["route_id"] == decision.route_id
    assert exp["reason"]
    assert exp["matched"] == list(decision.matched)
    assert isinstance(exp["saved_est"], int)
    # runner_up is either a cheaper/alternative tier or the cursor fallback
    assert exp["runner_up"] is None or exp["runner_up"]["tier"] != decision.target


@allure.story("Explainable routing")
@allure.title("format_decision surfaces Why line for a matched route")
def test_format_decision_why_line(minimal_workspace: Path) -> None:
    from greedy_token.router import format_decision

    out = format_decision(
        route_task("find baseUrl in sample.js", minimal_workspace),
        "find baseUrl in sample.js",
        minimal_workspace,
    )
    assert "Why:" in out


@allure.story("Explainable routing")
@allure.title("explain_route on cursor fallback names the fallback reason")
def test_explain_route_cursor_fallback(minimal_workspace: Path) -> None:
    from greedy_token.router import explain_route

    task = "explain quantum foam in repository layout"
    decision = route_task(task, minimal_workspace)
    exp = explain_route(decision, task, minimal_workspace)
    assert decision.route_id == "cursor-fallback"
    assert "fallback" in exp["reason"].lower()


@allure.story("Explainable routing")
@allure.title("explain_route: rationale fallback, budget_policy note, saved-est error")
def test_explain_route_edge_branches(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import greedy_token.estimator as estimator
    from greedy_token.router import RouteDecision, explain_route

    # No matched patterns and not the cursor fallback → reason uses rationale;
    # a budget_policy note is appended; cursor_saved_for failure → saved_est 0.
    decision = RouteDecision(
        target="python",
        route_id="script-check-meta-sync",
        confidence=1.0,
        matched=[],
        command="python scripts/meta-sync-check.py",
        note="budget_policy: cheap tier forced by daily cap",
        domains=[],
        rationale="python tier chosen by policy",
    )

    def boom(*a, **k):
        raise ValueError("estimator down")

    monkeypatch.setattr(estimator, "cursor_saved_for", boom)
    exp = explain_route(decision, "some policy-driven task", minimal_workspace)
    attach_json("explanation", exp)
    assert exp["reason"].startswith("python tier chosen by policy")
    assert "budget_policy" in exp["reason"]
    assert exp["saved_est"] == 0


@allure.story("Explainable routing")
@allure.title("explain_route: empty rationale falls back to generic tier reason")
def test_explain_route_generic_reason(minimal_workspace: Path) -> None:
    from greedy_token.router import RouteDecision, explain_route

    decision = RouteDecision(
        target="rag",
        route_id="rag-lookup",
        confidence=0.5,
        matched=[],
        command=None,
        note="",
        domains=[],
        rationale="",
    )
    exp = explain_route(decision, "lookup something", minimal_workspace)
    assert exp["reason"] == "rag tier, no explicit pattern"

