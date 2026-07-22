"""Baseline calibration: source priority, calibrate CLI, and footer source labels."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import allure
import pytest
import yaml

from greedy_token import baseline as B
from greedy_token.baseline import (
    BASE_CURSOR_OVERHEAD,
    METHOD_MANUAL,
    METHOD_MEASURED,
    SOURCE_CALIBRATED,
    SOURCE_DEFAULT,
    SOURCE_MEASURED,
    baseline_source,
    cursor_overhead,
    get_baseline_settings,
    write_baseline_config,
)
from greedy_token import settings
from greedy_token.cli import cmd_calibrate
from tests.allure_reporting import attach_text


def user_config_path() -> Path:
    """Via the settings module attribute so the conftest isolation patch applies."""
    return settings.user_config_path()

pytestmark = [
    allure.epic("Token economy"),
    allure.parent_suite("Token economy"),
    allure.feature("Baseline calibration"),
    allure.suite("Baseline calibration"),
]


def _calibrate_args(**kw) -> Namespace:
    base = {"overhead": None, "from_file": None, "json": False, "no_log": True}
    base.update(kw)
    return Namespace(**base)


@allure.story("Source priority")
@allure.title("No user config → default-estimate with BASE_CURSOR_OVERHEAD")
def test_default_estimate_without_config() -> None:
    settings = get_baseline_settings()
    assert settings == B.BaselineSettings(
        overhead_tokens=BASE_CURSOR_OVERHEAD, source=SOURCE_DEFAULT
    )
    assert cursor_overhead() == BASE_CURSOR_OVERHEAD
    assert baseline_source() == SOURCE_DEFAULT


@allure.story("Source priority")
@allure.title("Calibrated config wins over the default-estimate constant")
def test_calibrated_config_beats_default() -> None:
    write_baseline_config(9500, method=METHOD_MANUAL)
    settings = get_baseline_settings()
    assert settings.overhead_tokens == 9500
    assert settings.source == SOURCE_CALIBRATED
    assert settings.method == METHOD_MANUAL
    assert settings.calibrated_at
    assert cursor_overhead() == 9500


@allure.story("Source priority")
@allure.title("method: measured labels the source as measured")
def test_measured_method_labels_source_measured() -> None:
    write_baseline_config(8000, method=METHOD_MEASURED)
    settings = get_baseline_settings()
    assert settings.source == SOURCE_MEASURED
    assert settings.method == METHOD_MEASURED


@allure.story("Source priority")
@allure.title("Malformed baseline section falls back to default-estimate")
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"baseline": "not-a-dict"}, id="section-not-dict"),
        pytest.param({"baseline": {}}, id="missing-overhead"),
        pytest.param({"baseline": {"overhead_tokens": "abc"}}, id="overhead-not-int"),
        pytest.param({"baseline": {"overhead_tokens": 0}}, id="overhead-zero"),
        pytest.param({"baseline": {"overhead_tokens": -5}}, id="overhead-negative"),
    ],
)
def test_malformed_baseline_falls_back(payload: dict) -> None:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    settings = get_baseline_settings()
    assert settings.overhead_tokens == BASE_CURSOR_OVERHEAD
    assert settings.source == SOURCE_DEFAULT


@allure.story("Config write")
@allure.title("write_baseline_config merges with existing config sections")
def test_write_preserves_other_sections() -> None:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"cheap_llm": {"provider": "ollama", "model": "m"}}),
        encoding="utf-8",
    )
    written = write_baseline_config(7000, method=METHOD_MANUAL)
    assert written == path
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["cheap_llm"] == {"provider": "ollama", "model": "m"}
    assert data["baseline"]["overhead_tokens"] == 7000
    assert data["baseline"]["method"] == METHOD_MANUAL
    assert data["baseline"]["calibrated_at"]


@allure.story("Source priority")
@allure.title("estimator.cursor_baseline uses the calibrated overhead")
def test_cursor_baseline_uses_calibrated_overhead(minimal_workspace: Path) -> None:
    from greedy_token.context_audit import audit_context
    from greedy_token.estimator import cursor_baseline
    from greedy_token.tokens import count_tokens

    task = "wire the header"
    rules = sum(i.estimate.tokens for i in audit_context(minimal_workspace) if i.always_on)
    default_baseline = cursor_baseline(minimal_workspace, task)
    assert default_baseline == rules + count_tokens(task).tokens + BASE_CURSOR_OVERHEAD

    write_baseline_config(1234, method=METHOD_MANUAL)
    assert cursor_baseline(minimal_workspace, task) == rules + count_tokens(task).tokens + 1234


@allure.story("Source priority")
@allure.title("Router cursor-tier estimate uses the calibrated overhead")
def test_router_estimate_uses_calibrated_overhead(minimal_workspace: Path) -> None:
    from greedy_token.router import _token_estimate_for_route
    from greedy_token.tokens import count_tokens

    task = "wire the header"
    write_baseline_config(2222, method=METHOD_MANUAL)
    from greedy_token.context_audit import audit_context

    rules = sum(i.estimate.tokens for i in audit_context(minimal_workspace) if i.always_on)
    _, est, _ = _token_estimate_for_route("cursor", task=task, root=minimal_workspace)
    assert est == rules + count_tokens(task).tokens + 2222


@allure.story("Calibrate CLI")
@allure.title("calibrate without flags prints status and writes nothing")
def test_calibrate_status_view(minimal_workspace: Path, capsys: pytest.CaptureFixture) -> None:
    code = cmd_calibrate(_calibrate_args())
    out = capsys.readouterr().out
    attach_text("status", out)
    assert code == 0
    assert "Agent overhead" in out
    assert SOURCE_DEFAULT in out
    assert "--overhead N" in out
    assert "--from-file" in out
    assert not user_config_path().is_file()


@allure.story("Calibrate CLI")
@allure.title("calibrate --json status includes source and null calibrated_at")
def test_calibrate_status_json(minimal_workspace: Path, capsys: pytest.CaptureFixture) -> None:
    code = cmd_calibrate(_calibrate_args(json=True))
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == SOURCE_DEFAULT
    assert payload["overhead_tokens"] == BASE_CURSOR_OVERHEAD
    assert payload["calibrated_at"] is None
    assert payload["rules_tokens"] > 0
    assert not user_config_path().is_file()


@allure.story("Calibrate CLI")
@allure.title("calibrate --overhead N writes baseline: to the user config")
def test_calibrate_overhead_writes_config(
    minimal_workspace: Path, capsys: pytest.CaptureFixture
) -> None:
    code = cmd_calibrate(_calibrate_args(overhead=9500))
    out = capsys.readouterr().out
    attach_text("calibrate output", out)
    assert code == 0
    assert "~9,500" in out
    assert SOURCE_CALIBRATED in out
    data = yaml.safe_load(user_config_path().read_text(encoding="utf-8"))
    assert data["baseline"]["overhead_tokens"] == 9500
    assert data["baseline"]["method"] == METHOD_MANUAL
    assert data["baseline"]["calibrated_at"]


@allure.story("Calibrate CLI")
@allure.title("calibrate --from-file measures the dump and writes method: measured")
def test_calibrate_from_file_measures(
    minimal_workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    dump = tmp_path / "agent-context-dump.md"
    dump.write_text("system prompt and tool schemas " * 50, encoding="utf-8")
    code = cmd_calibrate(_calibrate_args(from_file=str(dump), json=True))
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == SOURCE_MEASURED
    assert payload["method"] == METHOD_MEASURED
    assert payload["overhead_tokens"] > 0
    data = yaml.safe_load(user_config_path().read_text(encoding="utf-8"))
    assert data["baseline"]["overhead_tokens"] == payload["overhead_tokens"]
    assert data["baseline"]["method"] == METHOD_MEASURED


@allure.story("Calibrate CLI")
@allure.title("calibrate rejects invalid inputs with exit code 2")
def test_calibrate_invalid_inputs(
    minimal_workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    with allure.step("--overhead and --from-file together"):
        assert cmd_calibrate(_calibrate_args(overhead=100, from_file="x")) == 2
        assert "not both" in capsys.readouterr().err
    with allure.step("--overhead 0"):
        assert cmd_calibrate(_calibrate_args(overhead=0)) == 2
        assert "positive" in capsys.readouterr().err
    with allure.step("--from-file missing"):
        assert cmd_calibrate(_calibrate_args(from_file=str(tmp_path / "nope.md"))) == 2
        assert "not found" in capsys.readouterr().err
    with allure.step("--from-file empty"):
        empty = tmp_path / "empty.md"
        empty.write_text("", encoding="utf-8")
        assert cmd_calibrate(_calibrate_args(from_file=str(empty))) == 2
        assert "empty" in capsys.readouterr().err
    with allure.step("nothing was written"):
        assert not user_config_path().is_file()


@allure.story("Footer source label")
@allure.title("Tool footer marks Saved with default-estimate, then calibrated")
def test_tool_footer_source_label(minimal_workspace: Path) -> None:
    from greedy_token.budget import format_tool_footer

    task = "search: baseUrl"
    footer = format_tool_footer(task, minimal_workspace, tier="tool", est_tokens=0, style="compact")
    attach_text("compact default", footer)
    assert f"(baseline: {SOURCE_DEFAULT})" in footer

    write_baseline_config(9500, method=METHOD_MANUAL)
    footer = format_tool_footer(task, minimal_workspace, tier="tool", est_tokens=0, style="compact")
    attach_text("compact calibrated", footer)
    assert f"(baseline: {SOURCE_CALIBRATED})" in footer

    full = format_tool_footer(task, minimal_workspace, tier="tool", est_tokens=0, style="full")
    attach_text("full calibrated", full)
    assert f"Agent overhead:  ~9,500  ({SOURCE_CALIBRATED})" in full
    assert f"(= baseline − spent; baseline: {SOURCE_CALIBRATED})" in full


@allure.story("Footer source label")
@allure.title("estimate footer marks Baseline and Saved with the source")
def test_estimate_footer_source_label(minimal_workspace: Path) -> None:
    from greedy_token.estimator import estimate_task, format_estimate

    task = "find baseUrl"
    text = format_estimate(estimate_task(task, minimal_workspace), task, minimal_workspace)
    attach_text("estimate default", text)
    assert f"(= baseline − spent; baseline: {SOURCE_DEFAULT})" in text

    write_baseline_config(8000, method=METHOD_MEASURED)
    text = format_estimate(estimate_task(task, minimal_workspace), task, minimal_workspace)
    attach_text("estimate measured", text)
    assert f"(= baseline − spent; baseline: {SOURCE_MEASURED})" in text


@allure.story("Footer source label")
@allure.title("route decision marks Saved est with the source")
def test_route_footer_source_label(minimal_workspace: Path) -> None:
    from greedy_token.router import format_decision, route_task

    task = "find baseUrl"
    decision = route_task(task, minimal_workspace)
    text = format_decision(decision, task, minimal_workspace)
    assert f"(baseline: {SOURCE_DEFAULT})" in text

    write_baseline_config(9000, method=METHOD_MANUAL)
    text = format_decision(decision, task, minimal_workspace)
    assert f"(baseline: {SOURCE_CALIBRATED})" in text


@allure.story("Footer source label")
@allure.title("pipeline footer marks per-step and total savings with the source")
def test_pipeline_footer_source_label(minimal_workspace: Path) -> None:
    from greedy_token.pipeline import format_pipeline_footer, run_pipeline

    write_baseline_config(9500, method=METHOD_MANUAL)
    result = run_pipeline("check-meta-sync then rag baseUrl", minimal_workspace, execute=False)
    footer = format_pipeline_footer(result, minimal_workspace)
    attach_text("pipeline footer", footer)
    assert (
        "Per-step savings (if each step were a separate naive Cursor chat; "
        f"baseline: {SOURCE_CALIBRATED}):"
    ) in footer
    assert f"Saved by executor (sum of per-step savings; baseline: {SOURCE_CALIBRATED}):" in footer
    assert f"Agent overhead:  ~9,500  ({SOURCE_CALIBRATED})" in footer
    assert f"baseline: {SOURCE_CALIBRATED})" in footer


@allure.story("Report source label")
@allure.title("report marks saved_vs_cursor with the baseline source (text + JSON)")
def test_report_source_label(minimal_workspace: Path) -> None:
    from greedy_token.usage import ReportSummary, TierStats, aggregate_events, format_report

    summary = aggregate_events([
        {"selected_tier": "tool", "est_tokens": 0, "cursor_baseline": 9000, "cursor_saved": 9000},
    ])
    text = format_report(summary)
    attach_text("report default", text)
    assert f"Baseline source: {SOURCE_DEFAULT}" in text
    assert f"agent overhead ~{BASE_CURSOR_OVERHEAD:,} tokens" in text
    assert "run greedy-token calibrate" in text
    payload = summary.to_dict()
    assert payload["baseline"] == {
        "overhead_tokens": BASE_CURSOR_OVERHEAD,
        "source": SOURCE_DEFAULT,
    }

    write_baseline_config(9500, method=METHOD_MEASURED)
    text = format_report(summary)
    attach_text("report measured", text)
    assert f"Baseline source: {SOURCE_MEASURED} (agent overhead ~9,500 tokens)" in text
    assert "run greedy-token calibrate" not in text
    assert summary.to_dict()["baseline"] == {"overhead_tokens": 9500, "source": SOURCE_MEASURED}
