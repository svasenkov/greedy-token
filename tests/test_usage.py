from __future__ import annotations

import json
from pathlib import Path

import allure
import pytest

from greedy_token.estimator import cursor_baseline, cursor_saved_for
from greedy_token.router import RouteDecision
from greedy_token.usage import (
    SCHEMA_VERSION,
    aggregate_events,
    append_event,
    build_route_event,
    format_report,
    load_events,
    logging_enabled,
    log_archive_paths,
    max_log_bytes,
    parse_since,
    rotate_log_if_needed,
)

pytestmark = [
    allure.epic("Token economy"),
    allure.parent_suite("Token economy"),
    allure.feature("Usage telemetry"),
    allure.suite("Usage telemetry"),
]


@pytest.fixture
def log_file(tmp_path: Path) -> Path:
    return tmp_path / "usage.jsonl"


@allure.story("Event logging")
@allure.title("append_event writes JSON line to usage log")
def test_append_event(log_file: Path) -> None:
    event = {"v": SCHEMA_VERSION, "cmd": "route", "task": "find baseUrl"}
    append_event(event, path=log_file)
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["cmd"] == "route"


@allure.story("Route events")
@allure.title("build_route_event truncates long task strings")
def test_build_route_event_truncates_task(minimal_workspace: Path) -> None:
    long_task = "x" * 600
    decision = RouteDecision(
        target="tool",
        route_id="tool-rg-search",
        confidence=0.59,
        matched=["find"],
        command="rg baseUrl",
        note="",
        domains=[],
        complexity="low",
        est_tokens=0,
        rationale="search",
        read_only=True,
        tool="rg",
    )
    event = build_route_event(
        cmd="estimate",
        task=long_task,
        root=minimal_workspace,
        decision=decision,
        tier_scan=[],
    )
    assert len(event["task"]) == 500
    assert event["task"].endswith("…")
    assert event["v"] == SCHEMA_VERSION
    assert "tier_scan" in event


@allure.story("Savings estimate")
@allure.title("cursor_saved_for reports savings for tool tier")
def test_cursor_saved_tool(minimal_workspace: Path) -> None:
    decision = RouteDecision(
        target="tool",
        route_id="tool-rg-search",
        confidence=0.59,
        matched=["find"],
        command=None,
        note="",
        domains=[],
        complexity="low",
        est_tokens=0,
        rationale="search",
    )
    saved = cursor_saved_for(minimal_workspace, "find baseUrl", 0, decision.target)
    assert saved > 0


@allure.story("Savings estimate")
@allure.title("cursor_saved_for is zero for cursor tier")
def test_cursor_saved_cursor(minimal_workspace: Path) -> None:
    saved = cursor_saved_for(minimal_workspace, "refactor header", 8000, "cursor")
    assert saved == 0


@allure.story("Baseline")
@allure.title("cursor_baseline includes agent overhead tokens")
def test_cursor_baseline_includes_overhead(minimal_workspace: Path) -> None:
    baseline = cursor_baseline(minimal_workspace, "task")
    assert baseline >= 6000


@allure.story("Aggregation")
@allure.title("aggregate_events groups events by tier and route")
def test_aggregate_by_tier() -> None:
    events = [
        {
            "selected_tier": "tool",
            "est_tokens": 0,
            "cursor_saved": 10000,
            "route_id": "tool-rg-search",
            "token_counter_method": "tiktoken/cl100k_base",
        },
        {
            "selected_tier": "tool",
            "est_tokens": 0,
            "cursor_saved": 8000,
            "route_id": "tool-rg-search",
            "token_counter_method": "tiktoken/cl100k_base",
        },
        {
            "selected_tier": "rag",
            "est_tokens": 1800,
            "cursor_saved": 5000,
            "route_id": "rag-cli",
            "token_counter_method": "tiktoken/cl100k_base",
        },
    ]
    summary = aggregate_events(events)
    assert summary.events == 3
    assert summary.by_tier["tool"].count == 2
    assert summary.by_tier["tool"].saved_vs_cursor == 18000
    assert summary.by_tier["rag"].est_tokens == 1800
    assert summary.top_routes[0] == ("tool-rg-search", 2)


@allure.story("Report")
@allure.title("format_report handles empty event list")
def test_format_report_empty() -> None:
    summary = aggregate_events([])
    text = format_report(summary)
    assert "No events yet" in text


@allure.story("Logging toggle")
@allure.title("maybe_append_event skips write when logging disabled")
def test_logging_disabled(monkeypatch: pytest.MonkeyPatch, log_file: Path) -> None:
    from argparse import Namespace

    from greedy_token.usage import maybe_append_event

    monkeypatch.setenv("GREEDY_TOKEN_LOG", "0")
    assert logging_enabled() is False
    args = Namespace(no_log=False)
    maybe_append_event(args, {"cmd": "route"})
    assert not log_file.exists()
    args_no_log = Namespace(no_log=True)
    maybe_append_event(args_no_log, {"cmd": "route"})
    assert not log_file.exists()


@allure.story("Time filter")
@allure.title("parse_since accepts relative and ISO date strings")
def test_parse_since_variants() -> None:
    dt_7d = parse_since("7d")
    dt_24h = parse_since("24h")
    assert dt_7d.tzinfo is not None
    assert dt_24h > dt_7d
    dt_iso = parse_since("2026-01-01")
    assert dt_iso.year == 2026


@allure.story("Log loading")
@allure.title("load_events skips malformed JSON lines")
def test_load_events_skips_bad_lines(log_file: Path) -> None:
    log_file.write_text(
        '{"cmd":"route","ts":"2026-07-07T00:00:00Z","selected_tier":"tool"}\n'
        "not-json\n"
        '{"cmd":"estimate","ts":"2026-07-07T01:00:00Z","selected_tier":"rag"}\n',
        encoding="utf-8",
    )
    events, skipped = load_events(log_file)
    assert len(events) == 2
    assert skipped == 1


@allure.story("Error handling")
@allure.title("append_event logs warning when write fails")
def test_append_failure(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def fail_open(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", fail_open)
    append_event({"cmd": "route"}, path=Path("/tmp/x/usage.jsonl"))
    err = capsys.readouterr().err
    assert "usage log write failed" in err


@allure.story("Rotation")
@allure.title("rotate_log_if_needed archives log when over size limit")
def test_rotate_log_when_over_limit(log_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "80")
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_FILES", "3")
    log_file.write_text('{"cmd":"old","ts":"2026-07-07T00:00:00Z"}\n' * 3, encoding="utf-8")
    assert rotate_log_if_needed(log_file) is True
    assert log_file.with_name("usage.jsonl.1").is_file()
    assert not log_file.exists() or log_file.stat().st_size == 0


@allure.story("Rotation")
@allure.title("rotate_log_if_needed respects GREEDY_TOKEN_LOG_MAX_FILES")
def test_rotate_log_keeps_archives(log_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "40")
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_FILES", "2")
    log_file.write_text('{"cmd":"a","ts":"2026-07-07T00:00:00Z"}\n' * 2, encoding="utf-8")
    rotate_log_if_needed(log_file)
    log_file.write_text('{"cmd":"b","ts":"2026-07-07T01:00:00Z"}\n' * 2, encoding="utf-8")
    rotate_log_if_needed(log_file)
    assert log_file.with_name("usage.jsonl.1").is_file()
    assert log_file.with_name("usage.jsonl.2").is_file()
    assert not log_file.with_name("usage.jsonl.3").exists()


@allure.story("Archives")
@allure.title("load_events reads current log and rotated archives")
def test_load_events_reads_archives(log_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = log_file.with_name("usage.jsonl.1")
    archive.write_text(
        '{"cmd":"archived","ts":"2026-07-07T00:00:00Z","selected_tier":"tool"}\n',
        encoding="utf-8",
    )
    log_file.write_text(
        '{"cmd":"current","ts":"2026-07-07T01:00:00Z","selected_tier":"rag"}\n',
        encoding="utf-8",
    )
    events, skipped = load_events(log_file)
    cmds = {e["cmd"] for e in events}
    assert cmds == {"archived", "current"}
    assert skipped == 0


@allure.story("Rotation")
@allure.title("append_event rotates log before writing new event")
def test_append_rotates_before_write(log_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "60")
    log_file.write_text('{"cmd":"fill","ts":"2026-07-07T00:00:00Z"}\n' * 2, encoding="utf-8")
    append_event({"cmd": "new", "ts": "2026-07-07T02:00:00Z"}, path=log_file)
    assert log_file.with_name("usage.jsonl.1").is_file()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["cmd"] == "new"


@allure.story("Archives")
@allure.title("log_archive_paths returns ordered archive paths")
def test_log_archive_paths_order(log_file: Path) -> None:
    paths = log_archive_paths(log_file, max_files=3)
    assert paths[0] == log_file
    assert paths[1].name == "usage.jsonl.1"
    assert paths[2].name == "usage.jsonl.2"


@allure.story("Configuration")
@allure.title("max_log_bytes reads GREEDY_TOKEN_LOG_MAX_BYTES env")
def test_max_log_bytes_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "1024")
    assert max_log_bytes() == 1024
