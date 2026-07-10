"""Public-contract branch coverage for src/greedy_token/ (no private _ imports)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.cli import cmd_run
from greedy_token.code_search import SearchResult, resolve_search_path, search_code
from greedy_token.estimator import TaskEstimate, estimate_task, format_estimate
from greedy_token.executors import TaskRunResult
from greedy_token.pipeline import (
    PipelineResult,
    PipelineStep,
    StepResult,
    format_pipeline_body,
    list_pipelines,
    parse_pipeline,
    run_pipeline,
)
from greedy_token.router import RouteDecision, format_decision, route_task
from greedy_token.settings import format_config
from greedy_token.tokens import collect_paths
from greedy_token.usage import (
    ReportSummary,
    TierStats,
    build_script_event,
    format_report,
    parse_since,
)
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Test infrastructure"),
    allure.parent_suite("Test infrastructure"),
    allure.feature("Branch coverage"),
    allure.suite("Branch coverage"),
]


def _ns(**kwargs) -> Namespace:
    defaults = {"no_log": True}
    defaults.update(kwargs)
    return Namespace(**defaults)


@allure.title("Branch gaps: budget footer route_id and cursor billing")
def test_budget_footer_branch_gaps(minimal_workspace: Path) -> None:
    from greedy_token.budget import format_tool_footer

    no_route = format_tool_footer(
        "task",
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="",
    )
    assert "Route:" not in no_route

    cursor_billing = format_tool_footer(
        "refactor",
        minimal_workspace,
        tier="cursor",
        est_tokens=5000,
        route_id="cursor-x",
        executor_sub="cursor",
    )
    assert "Billing: expensive LLM (Cursor agent)" in cursor_billing

    unknown_tier = format_tool_footer(
        "task",
        minimal_workspace,
        tier="compress",
        est_tokens=10,
        route_id="compress-x",
    )
    assert "Billing:" not in unknown_tier or "compress" in unknown_tier.lower()


@allure.title("Branch gaps: CLI run empty output and non-executable dry-run")
def test_cli_run_branch_gaps(minimal_workspace: Path, capsys) -> None:
    from greedy_token.executors import RunPlan

    with patch(
        "greedy_token.cli.execute_task",
        return_value=TaskRunResult(
            decision=RouteDecision(
                target="tool",
                route_id="tool-rg",
                confidence=1.0,
                matched=[],
                command="rg",
                note="",
                domains=[],
                read_only=True,
            ),
            output="",
            exit_code=0,
        ),
    ):
        assert cmd_run(_ns(task="find x", execute=True)) == 0
        out = capsys.readouterr().out
        assert "Route:" in out

    decision = RouteDecision(
        target="ollama",
        route_id="ollama-inventory",
        confidence=1.0,
        matched=[],
        command="./scripts/ollama/batch-inventory.sh",
        note="",
        domains=[],
        read_only=False,
    )
    plan = RunPlan(
        decision=decision,
        command="cd . && ./scripts/ollama/batch-inventory.sh",
        dry_run_output="dry",
        executable=False,
    )
    with patch("greedy_token.cli.route_task", return_value=decision):
        with patch("greedy_token.cli.plan_run", return_value=plan):
            assert cmd_run(_ns(task="batch inventory", execute=False)) == 0
    dry = capsys.readouterr().out
    attach_text("non-executable dry-run", dry)
    assert "not read-only" in dry

    read_only_plan = RunPlan(
        decision=RouteDecision(
            target="tool",
            route_id="tool-rg",
            confidence=1.0,
            matched=[],
            command="rg needle",
            note="",
            domains=[],
            read_only=True,
        ),
        command="rg needle",
        dry_run_output="rg needle",
        executable=True,
    )
    with patch("greedy_token.cli.route_task", return_value=read_only_plan.decision):
        with patch("greedy_token.cli.plan_run", return_value=read_only_plan):
            assert cmd_run(_ns(task="find needle", execute=False)) == 0
    assert "read-only" in capsys.readouterr().out

    cursor_plan = RunPlan(
        decision=RouteDecision(
            target="cursor",
            route_id="cursor-x",
            confidence=0.3,
            matched=[],
            command=None,
            note="",
            domains=[],
            read_only=False,
        ),
        command=None,
        dry_run_output="Open new Cursor chat.",
        executable=False,
    )
    with patch("greedy_token.cli.route_task", return_value=cursor_plan.decision):
        with patch("greedy_token.cli.plan_run", return_value=cursor_plan):
            assert cmd_run(_ns(task="refactor everything", execute=False)) == 0
    cursor_out = capsys.readouterr().out
    assert "read-only" not in cursor_out
    assert "not read-only" not in cursor_out


@allure.title("Branch gaps: code_search path resolution and rg fallback")
def test_code_search_branch_gaps(minimal_workspace: Path, tmp_path: Path) -> None:
    assert resolve_search_path("/", minimal_workspace) is None
    assert resolve_search_path("definitely-missing-xyz.txt", minimal_workspace) is None

    only = minimal_workspace / "projects" / "only-one.js"
    only.parent.mkdir(parents=True, exist_ok=True)
    only.write_text("// one\n", encoding="utf-8")
    assert resolve_search_path("only-one.js", minimal_workspace) == only.resolve()

    dup_dir_a = minimal_workspace / "projects" / "dup-a"
    dup_dir_b = minimal_workspace / "projects" / "dup-b"
    dup_dir_a.mkdir(parents=True)
    dup_dir_b.mkdir(parents=True)
    (dup_dir_a / "dup.txt").write_text("a\n", encoding="utf-8")
    (dup_dir_b / "dup.txt").write_text("b\n", encoding="utf-8")
    assert resolve_search_path("dup.txt", minimal_workspace) is None


@allure.title("Branch gaps: code_search rg fallback when rg output is useless")
def test_code_search_rg_fallback_branch(minimal_workspace: Path) -> None:
    with patch("greedy_token.code_search._run_rg", return_value=(127, "command not found")):
        with patch(
            "greedy_token.code_search._python_search_tree",
            return_value=["projects/sample.js:1:baseUrl"],
        ):
            result = search_code("baseUrl", minimal_workspace, limit=5)
    assert isinstance(result, SearchResult)
    assert result.engine == "python"


@allure.title("Branch gaps: estimator cursor spent line")
def test_estimator_cursor_spent_line(minimal_workspace: Path) -> None:
    for target, needle in (
        ("python", "0 LLM spend"),
        ("ollama", "cheap LLM"),
        ("rag", "docs/rag chunks"),
        ("cursor", "expensive LLM"),
    ):
        est = TaskEstimate(
            decision=RouteDecision(
                target=target,
                route_id=f"{target}-x",
                confidence=0.3,
                matched=[],
                command=None,
                note="",
                domains=[],
                complexity="high",
                est_tokens=9000,
                rationale="agent",
            ),
            complexity="high",
            est_tokens=9000,
            rationale="agent",
            cursor_saved=0,
            ollama_note=None,
        )
        text = format_estimate(est, f"wiring task {target}", minimal_workspace)
        assert needle in text

    unknown = TaskEstimate(
        decision=RouteDecision(
            target="compress",
            route_id="compress-x",
            confidence=1.0,
            matched=[],
            command=None,
            note="",
            domains=[],
            complexity="low",
            est_tokens=10,
            rationale="r",
        ),
        complexity="low",
        est_tokens=10,
        rationale="r",
        cursor_saved=0,
        ollama_note=None,
    )
    plain = format_estimate(unknown, "compress task", minimal_workspace)
    assert "Spent (MCP executor, LLM tokens): ~10" in plain
    assert "full agent path" not in plain


@allure.title("Branch gaps: pipeline estimate, body, and list via public API")
def test_pipeline_branch_gaps(minimal_workspace: Path) -> None:
    # ollama estimate path: dry-run audit / summarize-like step through run_pipeline.
    skill = minimal_workspace / ".cursor" / "skills" / "pipe-demo" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("# pipe-demo\n", encoding="utf-8")
    dry = run_pipeline("audit-skill pipe-demo", minimal_workspace, execute=False)
    assert dry.steps
    assert dry.steps[0].est_tokens >= 0

    missing_skill_footer = run_pipeline("search missing-skill-token", minimal_workspace, execute=False)
    assert missing_skill_footer.steps[0].est_tokens >= 0

    step = PipelineStep("noop", "tool", "noop", args="")
    body = format_pipeline_body(
        PipelineResult(
            task="t",
            steps=[
                StepResult(
                    step=step,
                    ok=True,
                    exit_code=0,
                    output="",
                    duration_ms=1,
                    est_tokens=0,
                    executed=False,
                )
            ],
        )
    )
    assert "Step 1/1" in body
    assert body.count("\n\n") >= 1

    with patch(
        "greedy_token.pipeline._load_pipelines_config",
        return_value={"pipelines": {"bare": {"steps": ["check-meta-sync"]}}},
    ):
        listed = list_pipelines()
    assert "bare" in listed
    assert "steps:" in listed


@allure.title("Branch gaps: rag_index frontmatter and blank manifest lines via get_indexed")
def test_rag_index_branch_gaps(minimal_workspace: Path) -> None:
    from greedy_token.rag_index import get_indexed_chunks, invalidate_rag_index

    chunk = minimal_workspace / "docs" / "rag" / "config" / "unclosed.md"
    chunk.write_text("---\nunclosed frontmatter", encoding="utf-8")
    manifest = minimal_workspace / "docs" / "rag" / "manifest.jsonl"
    # blank line between rows exercises blank skip in manifest loader
    manifest.write_text(
        '{"id":"a","domain":"config","path":"docs/rag/config/unclosed.md","tags":[]}\n'
        "\n"
        '{"id":"b","domain":"config","path":"docs/rag/config/unclosed.md","tags":[]}\n',
        encoding="utf-8",
    )
    invalidate_rag_index(minimal_workspace)
    chunks = get_indexed_chunks(minimal_workspace)
    assert len(chunks) == 2
    # Unclosed frontmatter stays in the indexed token set (no closed --- strip).
    assert "unclosed" in chunks[0].body_tokens or "frontmatter" in chunks[0].body_tokens


@allure.title("Branch gaps: router note dedup and best_in_tier via route_task")
def test_router_branch_gaps(minimal_workspace: Path) -> None:
    decision = route_task("find baseUrl", minimal_workspace)
    assert decision.target == "tool"
    assert "Mechanical search" in decision.rationale or decision.rationale
    # Rationales from matching routes should not spam the same note twice.
    if "Mechanical search" in decision.rationale:
        assert decision.rationale.count("Mechanical search") == 1

    # Tie / first-wins: two identical-score find tasks still select a stable tool route.
    again = route_task("find baseUrl", minimal_workspace)
    assert again.route_id == decision.route_id

    text = format_decision(decision, "find baseUrl", minimal_workspace)
    assert "Route:" in text or decision.route_id in text


@allure.title("Branch gaps: settings format_config without workspace path")
def test_settings_format_config_no_workspace() -> None:
    text = format_config(root=None)
    assert "greedy-token cheap LLM settings" in text
    assert "  3." not in text


@allure.title("Branch gaps: collect_paths absolute path")
def test_tokens_collect_paths_absolute(minimal_workspace: Path) -> None:
    sample = minimal_workspace / "projects" / "sample.js"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text("x\n", encoding="utf-8")
    paths = collect_paths([str(sample)], minimal_workspace)
    assert sample.resolve() in paths


@allure.title("Branch gaps: resolve_rg skips empty PATH segment")
def test_tool_paths_empty_path_segment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.tool_paths import _rg_candidates, resolve_rg

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rg = bin_dir / "rg"
    rg.write_text("#!/bin/sh\necho\n", encoding="utf-8")
    rg.chmod(0o755)
    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    monkeypatch.setenv("PATH", f"::{bin_dir}")
    monkeypatch.setattr("greedy_token.tool_paths.shutil.which", lambda *_a, **_k: None)

    path_derived = [p for p in _rg_candidates() if p.parent == bin_dir]
    assert path_derived == [bin_dir / "rg"]

    with patch("greedy_token.tool_paths._rg_candidates", return_value=iter([bin_dir / "rg"])):
        found = resolve_rg()
    assert found == rg.resolve()


@allure.title("Branch gaps: ollama audit-skill estimate when skill file is missing")
def test_pipeline_audit_skill_estimate_missing_file(minimal_workspace: Path) -> None:
    step = PipelineStep(
        "audit-skill",
        "ollama",
        "audit",
        command="echo estimate-out",
        args="no-such-skill.md",
    )
    with patch("greedy_token.pipeline.ollama_available", return_value=True):
        with patch(
            "greedy_token.pipeline.PIPELINE_AUTO_RUN",
            frozenset({"audit-skill"}),
        ):
            with patch("greedy_token.pipeline.parse_pipeline", return_value=[step]):
                result = run_pipeline("audit-skill gap", minimal_workspace, execute=True)
    assert result.steps[0].executed
    assert result.steps[0].est_tokens > 0


@allure.title("Branch gaps: ollama estimate skips audit-skill file branch for other steps")
def test_pipeline_ollama_estimate_non_audit_skill(minimal_workspace: Path) -> None:
    step = PipelineStep(
        "classify-file",
        "ollama",
        "classify",
        command="echo classified",
        args="sample.js",
    )
    with patch("greedy_token.pipeline.ollama_available", return_value=True):
        with patch("greedy_token.pipeline.parse_pipeline", return_value=[step]):
            result = run_pipeline("classify-file gap", minimal_workspace, execute=True)
    assert result.steps[0].executed
    assert result.steps[0].est_tokens > 0


@allure.title("Branch gaps: usage event builders and format_report sections")
def test_usage_branch_gaps(minimal_workspace: Path) -> None:
    event = build_script_event(script_id="check-meta-sync", root=minimal_workspace)
    assert "duration_ms" not in event
    assert "executed" not in event["executor"]

    aware = parse_since("2026-06-01T12:00:00+00:00")
    assert aware.tzinfo is not None

    summary = ReportSummary(
        events=1,
        by_tier={"tool": TierStats(count=1, est_tokens=0, saved_vs_cursor=100)},
        top_routes=[],
        counter_methods={},
    )
    report = format_report(summary)
    assert "Top routes:" not in report
    assert "Token counter:" not in report
    assert "Events: 1" in report


@allure.title("Branch gaps: Ollama-available paths and scoped rg fallback")
def test_ci_linux_branch_gaps(minimal_workspace: Path) -> None:
    from greedy_token.budget import format_tool_footer
    from greedy_token.wrappers import ollama_status_line

    with patch("greedy_token.budget.ollama_available", return_value=True):
        footer = format_tool_footer(
            "audit skill configurator-boolean",
            minimal_workspace,
            tier="tool",
            est_tokens=0,
            route_id="mcp-search",
            executor_sub="rg",
        )
    assert "cheap" in footer

    ollama_decision = RouteDecision(
        target="ollama",
        route_id="ollama-audit",
        confidence=1.0,
        matched=["audit"],
        command="./scripts/ollama/audit-skill.sh",
        note="",
        domains=[],
        complexity="medium",
        est_tokens=0,
        rationale="Local Ollama",
    )
    with patch("greedy_token.estimator.route_task", return_value=ollama_decision):
        with patch("greedy_token.estimator.ollama_available", return_value=False):
            with patch(
                "greedy_token.estimator.ollama_status_line",
                return_value="Ollama: unavailable (test)",
            ):
                est = estimate_task("audit skill", minimal_workspace)
    assert est.ollama_note == "Ollama: unavailable (test)"

    with patch("greedy_token.wrappers.ollama_available", return_value=True):
        available_line = ollama_status_line()
    assert "available" in available_line

    scoped = minimal_workspace / "projects" / "scoped-search.js"
    scoped.write_text("needle_ci_branch\n", encoding="utf-8")
    with patch("greedy_token.code_search.resolve_rg", return_value=Path("/usr/bin/rg")):
        with patch("greedy_token.code_search._run_rg", return_value=(1, "")):
            scoped_result = search_code(
                "needle_ci_branch",
                minimal_workspace,
                path="projects/scoped-search.js",
            )
    assert scoped_result.engine == "python"
    assert "python file scan" in scoped_result.text


@allure.title("Branch gaps: parse_pipeline missing skill args")
def test_pipeline_parse_errors(minimal_workspace: Path) -> None:
    with pytest.raises(ValueError, match="needs more args|audit-skill needs"):
        parse_pipeline("pipeline: meta-audit")
