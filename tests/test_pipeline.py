from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.pipeline import format_pipeline_footer, parse_pipeline, run_pipeline

pytestmark = [allure.epic("Pipeline"), allure.feature("Multi-step chains")]


@allure.story("Named recipes")
@allure.title("Parse meta-audit named pipeline recipe")
def test_parse_named_pipeline_meta_audit() -> None:
    steps = parse_pipeline("pipeline: meta-audit configurator-boolean")
    assert len(steps) == 2
    assert steps[0].step_id == "check-meta-sync"
    assert steps[0].tier == "python"
    assert steps[1].step_id == "audit-skill"
    assert steps[1].tier == "ollama"
    assert "configurator-boolean" in steps[1].args


@allure.story("Named recipes")
@allure.title("Parse search-rag recipe and reuse query for RAG step")
def test_parse_named_pipeline_search_rag_reuses_query() -> None:
    steps = parse_pipeline("pipeline: search-rag baseUrl TestConfig")
    assert len(steps) == 2
    assert steps[0].step_id == "search"
    assert steps[0].args == "baseUrl\tTestConfig"
    assert steps[1].step_id == "rag"
    assert steps[1].args == "baseUrl"


@allure.story("Custom chain")
@allure.title("Parse custom then-chain pipeline syntax")
def test_parse_custom_chain() -> None:
    steps = parse_pipeline("check-meta-sync then rag baseUrl -D flag")
    assert steps[0].step_id == "check-meta-sync"
    assert steps[1].step_id == "rag"
    assert steps[1].args == "baseUrl -D flag"


@allure.story("Dry run")
@allure.title("Pipeline dry-run does not execute allowlisted steps")
def test_pipeline_dry_run(minimal_workspace: Path) -> None:
    result = run_pipeline(
        "check-meta-sync then rag baseUrl",
        minimal_workspace,
        execute=False,
    )
    assert len(result.steps) == 2
    assert result.steps[0].step.tier == "python"
    assert result.steps[1].step.tier == "rag"
    assert not result.steps[0].executed


@allure.story("Token footer")
@allure.title("Pipeline footer includes per-executor savings table")
def test_format_pipeline_footer_has_by_executor(minimal_workspace: Path) -> None:
    result = run_pipeline(
        "check-meta-sync then rag baseUrl",
        minimal_workspace,
        execute=False,
    )
    footer = format_pipeline_footer(result, minimal_workspace)
    assert "Per-step savings" in footer
    assert "Saved by executor" in footer
    assert "Saved vs naive Cursor chat" in footer


@patch("greedy_token.pipeline._run_step")
@allure.story("Error handling")
@allure.title("Pipeline stops early when a step fails")
def test_pipeline_stops_on_error(mock_run, minimal_workspace: Path) -> None:
    from greedy_token.pipeline import PipelineStep, StepResult

    ok_step = StepResult(
        step=PipelineStep("check-meta-sync", "python", "meta", command="echo"),
        ok=True,
        exit_code=0,
        output="ok",
        duration_ms=1,
        est_tokens=0,
        executed=True,
    )
    fail_step = StepResult(
        step=PipelineStep("audit-skill", "ollama", "audit", command="echo"),
        ok=False,
        exit_code=1,
        output="fail",
        duration_ms=1,
        est_tokens=0,
        executed=True,
    )
    mock_run.side_effect = [ok_step, fail_step]
    result = run_pipeline("meta-audit configurator-boolean", minimal_workspace, execute=True)
    assert result.stopped_early
    assert len(result.steps) == 2
