from __future__ import annotations

import json
from pathlib import Path

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
    parse_since,
)


@pytest.fixture
def log_file(tmp_path: Path) -> Path:
    return tmp_path / "usage.jsonl"


@pytest.fixture
def sample_root(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "phase-manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "check-meta-sync.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "test.mdc").write_text("rule content", encoding="utf-8")
    return tmp_path


def test_append_event(log_file: Path) -> None:
    event = {"v": SCHEMA_VERSION, "cmd": "route", "task": "find baseUrl"}
    append_event(event, path=log_file)
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["cmd"] == "route"


def test_build_route_event_truncates_task(sample_root: Path) -> None:
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
        root=sample_root,
        decision=decision,
        tier_scan=[],
    )
    assert len(event["task"]) == 500
    assert event["task"].endswith("…")
    assert event["v"] == SCHEMA_VERSION
    assert "tier_scan" in event


def test_cursor_saved_tool(sample_root: Path) -> None:
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
    saved = cursor_saved_for(sample_root, "find baseUrl", 0, decision.target)
    assert saved > 0


def test_cursor_saved_cursor(sample_root: Path) -> None:
    saved = cursor_saved_for(sample_root, "refactor header", 8000, "cursor")
    assert saved == 0


def test_cursor_baseline_includes_overhead(sample_root: Path) -> None:
    baseline = cursor_baseline(sample_root, "task")
    assert baseline >= 6000


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


def test_format_report_empty() -> None:
    summary = aggregate_events([])
    text = format_report(summary)
    assert "No events yet" in text


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


def test_parse_since_variants() -> None:
    dt_7d = parse_since("7d")
    dt_24h = parse_since("24h")
    assert dt_7d.tzinfo is not None
    assert dt_24h > dt_7d
    dt_iso = parse_since("2026-01-01")
    assert dt_iso.year == 2026


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


def test_append_failure(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def fail_open(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", fail_open)
    append_event({"cmd": "route"}, path=Path("/tmp/x/usage.jsonl"))
    err = capsys.readouterr().err
    assert "usage log write failed" in err
