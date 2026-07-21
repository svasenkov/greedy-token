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
    build_script_override_event,
    format_report,
    load_events,
    logging_enabled,
    log_archive_paths,
    max_log_bytes,
    parse_since,
    rotate_log_if_needed,
)
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Greedy token"),
    allure.parent_suite("Greedy token"),
    allure.feature("Usage telemetry"),
    allure.suite("Usage telemetry"),
]


@pytest.fixture
def log_file(tmp_path: Path) -> Path:
    return tmp_path / "usage.jsonl"


@allure.story("Event logging")
@allure.title("Usage log append writes JSON line")
def test_append_event(log_file: Path) -> None:
    event = {"v": SCHEMA_VERSION, "cmd": "route", "task": "find baseUrl"}
    with allure.step("Append route event to usage log"):
        append_event(event, path=log_file)
        lines = log_file.read_text(encoding="utf-8").splitlines()
        attach_text("log lines", "\n".join(lines))
    with allure.step("Verify single JSON line written"):
        assert len(lines) == 1
        assert json.loads(lines[0])["cmd"] == "route"


@allure.story("Route events")
@allure.title("Route event builder truncates long task strings")
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
    with allure.step("Build route event from long task"):
        event = build_route_event(
            cmd="estimate",
            task=long_task,
            root=minimal_workspace,
            decision=decision,
            tier_scan=[],
        )
        attach_json("route event", {"task_length": len(event["task"]), "task_suffix": event["task"][-5:]})
    with allure.step("Verify task truncation and schema fields"):
        assert len(event["task"]) == 500
        assert event["task"].endswith("…")
        assert event["v"] == SCHEMA_VERSION
        assert "tier_scan" in event


@allure.story("Override events")
@allure.title("Script override event follows usage contract")
def test_build_script_override_event_shape(minimal_workspace: Path) -> None:
    with allure.step("Build script override event"):
        event = build_script_override_event(
            task="ssh check qaguru prod",
            selected_tier="cursor",
            previous_tier="python",
            crystal_id="python-ssh-check",
            root=minimal_workspace,
            reason="user_reask",
            window_sec=900,
            tags={"project": "infra-home"},
        )
        attach_json("override event", event)
    with allure.step("Verify override contract fields"):
        assert event["event"] == "script_override"
        assert event["selected_tier"] == "cursor"
        assert event["previous_tier"] == "python"
        assert event["crystal_id"] == "python-ssh-check"
        assert event["task_normalized"] == "ssh check qaguru prod"
        assert event["billing"]["spent_est"] == 0
        assert event["billing"]["saved_est"] == 0
        assert event["meta"]["reason"] == "user_reask"
        assert event["meta"]["window_sec"] == 900


@allure.story("Override events")
@allure.title("normalize_task collapses case and whitespace")
def test_normalize_task() -> None:
    from greedy_token.usage import normalize_task

    assert normalize_task("  SSH   check\tqaguru  ") == "ssh check qaguru"


@allure.story("Override events")
@allure.title("Auto script_override after cursor reask within window")
def test_auto_script_override_within_window(log_file: Path) -> None:
    from greedy_token.usage import OVERRIDE_WINDOW_SEC, append_event

    prior = {
        "v": SCHEMA_VERSION,
        "ts": "2026-07-14T12:00:00Z",
        "cmd": "route",
        "task": "SSH check   QAGURU prod",
        "selected_tier": "python",
        "route_id": "python-ssh-check",
        "root": "/tmp/ws",
    }
    append_event(prior, path=log_file, emit_auto_override=False)

    cursor = {
        "v": SCHEMA_VERSION,
        "ts": "2026-07-14T12:05:00Z",
        "cmd": "route",
        "task": "ssh check qaguru prod",
        "selected_tier": "cursor",
        "route_id": "cursor-fallback",
        "root": "/tmp/ws",
        "tags": {"project": "infra-home"},
    }
    append_event(cursor, path=log_file)

    lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    attach_json("log events", lines)
    assert len(lines) == 3
    override = lines[-1]
    assert override["event"] == "script_override"
    assert override["crystal_id"] == "python-ssh-check"
    assert override["previous_tier"] == "python"
    assert override["selected_tier"] == "cursor"
    assert override["meta"]["reason"] == "user_reask"
    assert override["meta"]["prior_usage_ts"] == "2026-07-14T12:00:00Z"
    assert override["meta"]["window_sec"] == OVERRIDE_WINDOW_SEC
    assert override["tags"]["project"] == "infra-home"


@allure.story("Override events")
@allure.title("Auto script_override skips outside window and mismatched tasks")
def test_auto_script_override_guards(log_file: Path) -> None:
    from greedy_token.usage import append_event

    append_event(
        {
            "v": SCHEMA_VERSION,
            "ts": "2026-07-14T12:00:00Z",
            "task": "ssh check qaguru prod",
            "selected_tier": "python",
            "route_id": "python-ssh-check",
        },
        path=log_file,
        emit_auto_override=False,
    )
    # Outside 900s window
    append_event(
        {
            "v": SCHEMA_VERSION,
            "ts": "2026-07-14T12:20:00Z",
            "task": "ssh check qaguru prod",
            "selected_tier": "cursor",
            "route_id": "cursor-fallback",
        },
        path=log_file,
    )
    # Different task within window (no prior for this task)
    append_event(
        {
            "v": SCHEMA_VERSION,
            "ts": "2026-07-14T12:21:00Z",
            "task": "openapi diff stacks",
            "selected_tier": "cursor",
            "route_id": "cursor-fallback",
        },
        path=log_file,
    )

    rows = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    overrides = [r for r in rows if r.get("event") == "script_override"]
    attach_json("overrides", overrides)
    assert overrides == []


@allure.story("Override events")
@allure.title("Auto script_override attributes any cheap tier (tool/rag/ollama)")
@pytest.mark.parametrize(
    ("prior_tier", "prior_route"),
    [("tool", "tool-rg-search"), ("rag", "rag-cli"), ("ollama", "ollama-audit-skill")],
)
def test_auto_override_attributes_cheap_tiers(
    log_file: Path, prior_tier: str, prior_route: str
) -> None:
    from greedy_token.usage import append_event

    append_event(
        {
            "v": SCHEMA_VERSION,
            "ts": "2026-07-14T12:00:00Z",
            "task": "find baseUrl in stacks",
            "selected_tier": prior_tier,
            "route_id": prior_route,
        },
        path=log_file,
        emit_auto_override=False,
    )
    append_event(
        {
            "v": SCHEMA_VERSION,
            "ts": "2026-07-14T12:05:00Z",
            "task": "find baseUrl in stacks",
            "selected_tier": "cursor",
            "route_id": "cursor-fallback",
        },
        path=log_file,
    )

    rows = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    overrides = [r for r in rows if r.get("event") == "script_override"]
    attach_json("overrides", overrides)
    assert len(overrides) == 1
    assert overrides[0]["previous_tier"] == prior_tier
    assert overrides[0]["crystal_id"] == prior_route
    assert overrides[0]["meta"]["reason"] == "user_reask"


@allure.story("Savings estimate")
@allure.title("Cursor savings is reported for tool tier")
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
    with allure.step("Compute cursor savings for tool tier"):
        saved = cursor_saved_for(minimal_workspace, "find baseUrl", 0, decision.target)
        attach_text("saved tokens", str(saved))
    with allure.step("Verify positive savings"):
        assert saved > 0


@allure.story("Savings estimate")
@allure.title("Cursor savings is zero for cursor tier")
def test_cursor_saved_cursor(minimal_workspace: Path) -> None:
    with allure.step("Compute cursor savings for cursor tier"):
        saved = cursor_saved_for(minimal_workspace, "refactor header", 8000, "cursor")
        attach_text("saved tokens", str(saved))
    with allure.step("Verify zero savings"):
        assert saved == 0


@allure.story("Baseline")
@allure.title("Cursor baseline includes agent overhead tokens")
def test_cursor_baseline_includes_overhead(minimal_workspace: Path) -> None:
    with allure.step("Compute cursor baseline"):
        baseline = cursor_baseline(minimal_workspace, "task")
        attach_text("baseline tokens", str(baseline))
    with allure.step("Verify baseline includes agent overhead"):
        assert baseline >= 6000


@allure.story("Aggregation")
@allure.title("Event aggregator groups events by tier and route")
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
    with allure.step("Aggregate usage events by tier"):
        summary = aggregate_events(events)
        attach_json("summary", {
            "events": summary.events,
            "tool_count": summary.by_tier["tool"].count,
            "tool_saved": summary.by_tier["tool"].saved_vs_cursor,
            "rag_est_tokens": summary.by_tier["rag"].est_tokens,
            "top_route": summary.top_routes[0],
        })
    with allure.step("Verify tier and route grouping"):
        assert summary.events == 3
        assert summary.by_tier["tool"].count == 2
        assert summary.by_tier["tool"].saved_vs_cursor == 18000
        assert summary.by_tier["rag"].est_tokens == 1800
        assert summary.top_routes[0] == ("tool-rg-search", 2)


@allure.story("Report")
@allure.title("Usage report handles empty event list")
def test_format_report_empty() -> None:
    with allure.step("Format report for empty events"):
        summary = aggregate_events([])
        text = format_report(summary)
        attach_text("report", text)
    with allure.step("Verify empty report message"):
        assert "No events yet" in text


@allure.story("Logging toggle")
@allure.title("Conditional append skips write when logging disabled")
def test_logging_disabled(monkeypatch: pytest.MonkeyPatch, log_file: Path) -> None:
    from argparse import Namespace

    from greedy_token.usage import maybe_append_event

    monkeypatch.setenv("GREEDY_TOKEN_LOG", "0")
    with allure.step("Attempt append with logging disabled"):
        enabled = logging_enabled()
        attach_text("logging enabled", str(enabled))
        args = Namespace(no_log=False)
        maybe_append_event(args, {"cmd": "route"})
        args_no_log = Namespace(no_log=True)
        maybe_append_event(args_no_log, {"cmd": "route"})
        attach_text("log file exists", str(log_file.exists()))
    with allure.step("Verify no log file was created"):
        assert enabled is False
        assert not log_file.exists()


@allure.story("Time filter")
@allure.title("Since parser accepts relative and ISO date strings")
def test_parse_since_variants() -> None:
    with allure.step("Parse relative and ISO since strings"):
        dt_7d = parse_since("7d")
        dt_24h = parse_since("24h")
        dt_iso = parse_since("2026-01-01")
        attach_text("7d", dt_7d.isoformat())
        attach_text("24h", dt_24h.isoformat())
        attach_text("iso", dt_iso.isoformat())
    with allure.step("Verify parsed datetimes"):
        assert dt_7d.tzinfo is not None
        assert dt_24h > dt_7d
        assert dt_iso.year == 2026


@allure.story("Log loading")
@allure.title("Event loader skips malformed JSON lines")
def test_load_events_skips_bad_lines(log_file: Path) -> None:
    log_file.write_text(
        '{"cmd":"route","ts":"2026-07-07T00:00:00Z","selected_tier":"tool"}\n'
        "not-json\n"
        '{"cmd":"estimate","ts":"2026-07-07T01:00:00Z","selected_tier":"rag"}\n',
        encoding="utf-8",
    )
    with allure.step("Load events from log with malformed line"):
        events, skipped = load_events(log_file)
        attach_text("loaded events", str(len(events)))
        attach_text("skipped lines", str(skipped))
    with allure.step("Verify valid events loaded and bad line skipped"):
        assert len(events) == 2
        assert skipped == 1


@allure.story("Error handling")
@allure.title("Usage log append logs warning when write fails")
def test_append_failure(monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path) -> None:
    target = tmp_path / "usage.jsonl"
    original_open = Path.open

    def guarded_open(self: Path, *args, **kwargs):
        if self.resolve() == target.resolve():
            raise OSError("disk full")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with allure.step("Attempt append with failing file write"):
        append_event({"cmd": "route"}, path=target)
        err = capsys.readouterr().err
        attach_text("stderr", err)
    with allure.step("Verify write failure warning"):
        assert "usage log write failed" in err


@allure.story("Rotation")
@allure.title("Log rotation archives file when over size limit")
def test_rotate_log_when_over_limit(log_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "80")
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_FILES", "3")
    log_file.write_text('{"cmd":"old","ts":"2026-07-07T00:00:00Z"}\n' * 3, encoding="utf-8")
    with allure.step("Rotate log when over size limit"):
        rotated = rotate_log_if_needed(log_file)
        attach_text("rotated", str(rotated))
        attach_text("archive exists", str(log_file.with_name("usage.jsonl.1").is_file()))
    with allure.step("Verify log was archived"):
        assert rotated is True
        assert log_file.with_name("usage.jsonl.1").is_file()
        assert not log_file.exists() or log_file.stat().st_size == 0


@allure.story("Rotation")
@allure.title("Log rotation respects GREEDY_TOKEN_LOG_MAX_FILES")
def test_rotate_log_keeps_archives(log_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "40")
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_FILES", "2")
    with allure.step("Rotate log twice with max 2 archives"):
        log_file.write_text('{"cmd":"a","ts":"2026-07-07T00:00:00Z"}\n' * 2, encoding="utf-8")
        rotate_log_if_needed(log_file)
        log_file.write_text('{"cmd":"b","ts":"2026-07-07T01:00:00Z"}\n' * 2, encoding="utf-8")
        rotate_log_if_needed(log_file)
        attach_text("archive .1 exists", str(log_file.with_name("usage.jsonl.1").is_file()))
        attach_text("archive .2 exists", str(log_file.with_name("usage.jsonl.2").is_file()))
        attach_text("archive .3 exists", str(log_file.with_name("usage.jsonl.3").exists()))
    with allure.step("Verify archive count limit"):
        assert log_file.with_name("usage.jsonl.1").is_file()
        assert log_file.with_name("usage.jsonl.2").is_file()
        assert not log_file.with_name("usage.jsonl.3").exists()


@allure.story("Archives")
@allure.title("Event loader reads current log and rotated archives")
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
    with allure.step("Load events from current log and archive"):
        events, skipped = load_events(log_file)
        cmds = {e["cmd"] for e in events}
        attach_text("commands", ", ".join(sorted(cmds)))
        attach_text("skipped", str(skipped))
    with allure.step("Verify both archive and current events loaded"):
        assert cmds == {"archived", "current"}
        assert skipped == 0


@allure.story("Rotation")
@allure.title("Usage log append rotates before writing new event")
def test_append_rotates_before_write(log_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "60")
    log_file.write_text('{"cmd":"fill","ts":"2026-07-07T00:00:00Z"}\n' * 2, encoding="utf-8")
    with allure.step("Append event triggering rotation"):
        append_event({"cmd": "new", "ts": "2026-07-07T02:00:00Z"}, path=log_file)
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        attach_text("log lines", "\n".join(lines))
        attach_text("archive exists", str(log_file.with_name("usage.jsonl.1").is_file()))
    with allure.step("Verify rotation before write and single new event"):
        assert log_file.with_name("usage.jsonl.1").is_file()
        assert len(lines) == 1
        assert json.loads(lines[0])["cmd"] == "new"


@allure.story("Archives")
@allure.title("Log archive paths are returned in order")
def test_log_archive_paths_order(log_file: Path) -> None:
    with allure.step("Resolve log archive paths"):
        paths = log_archive_paths(log_file, max_files=3)
        attach_text("archive paths", "\n".join(str(p) for p in paths))
    with allure.step("Verify ordered archive path list"):
        assert paths[0] == log_file
        assert paths[1].name == "usage.jsonl.1"
        assert paths[2].name == "usage.jsonl.2"


@allure.story("Configuration")
@allure.title("Max log bytes reads GREEDY_TOKEN_LOG_MAX_BYTES env")
def test_max_log_bytes_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "1024")
    with allure.step("Read max log bytes from env"):
        max_bytes = max_log_bytes()
        attach_text("max log bytes", str(max_bytes))
    with allure.step("Verify env value is used"):
        assert max_bytes == 1024


@allure.story("Logging paths")
@allure.title("log_path honors GREEDY_TOKEN_LOG env")
def test_log_path_custom(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from greedy_token.usage import log_path

    custom = tmp_path / "custom.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(custom))
    assert log_path() == custom


@allure.story("Event builders")
@allure.title("build_script_event and build_compress_event populate fields")
def test_build_script_and_compress_events(minimal_workspace: Path) -> None:
    from greedy_token.usage import build_compress_event, build_script_event, executor_from_decision
    from greedy_token.router import RouteDecision

    script_event = build_script_event(
        script_id="check-meta-sync",
        root=minimal_workspace,
        duration_ms=10,
        executed=True,
    )
    assert script_event["executor"]["script_id"] == "check-meta-sync"
    assert script_event["duration_ms"] == 10

    compress_event = build_compress_event(
        text="long prompt text",
        short="short",
        use_ollama=False,
        duration_ms=5,
    )
    assert compress_event["compressor"] == "heuristic"

    tool_exec = executor_from_decision(
        RouteDecision(
            target="tool",
            route_id="tool-rg",
            confidence=1.0,
            matched=[],
            command=None,
            note="",
            domains=[],
            tool="rg",
        )
    )
    assert tool_exec["kind"] == "rg"

    py_exec = executor_from_decision(
        RouteDecision(
            target="python",
            route_id="script-check-meta-sync",
            confidence=1.0,
            matched=[],
            command=None,
            note="",
            domains=[],
        )
    )
    assert py_exec["kind"] == "script"


@allure.story("Event builders")
@allure.title("executor_from_decision uses workspace cheap_llm model for ollama tier")
def test_executor_from_decision_uses_workspace_model(minimal_workspace: Path) -> None:
    from greedy_token.usage import build_route_event, executor_from_decision
    from greedy_token.router import RouteDecision

    ws_cfg = minimal_workspace / ".greedy-token.yaml"
    ws_cfg.write_text(
        "cheap_llm:\n  provider: ollama\n  url: http://localhost:11434\n"
        "  model: workspace-only-model\n",
        encoding="utf-8",
    )
    decision = RouteDecision(
        target="ollama",
        route_id="ollama-audit-skill",
        confidence=1.0,
        matched=["audit"],
        command=None,
        note="",
        domains=[],
    )
    assert executor_from_decision(decision, minimal_workspace)["model"] == "workspace-only-model"
    event = build_route_event(
        cmd="route",
        task="audit skill x",
        root=minimal_workspace,
        decision=decision,
        tier_scan=[],
    )
    assert event["executor"]["model"] == "workspace-only-model"


@allure.story("Tier scan helper")
@allure.title("build_tier_scan returns rows for all tiers")
def test_build_tier_scan(minimal_workspace: Path) -> None:
    from greedy_token.usage import build_tier_scan

    rows = build_tier_scan("find baseUrl", minimal_workspace)
    assert len(rows) == 5
    assert rows[0]["tier"] == "tool"


@allure.story("Report")
@allure.title("format_report includes tier table and skipped lines")
def test_format_report_with_events() -> None:
    from greedy_token.usage import ReportSummary, TierStats, format_report

    summary = ReportSummary(events=2, since="7d", skipped_lines=1)
    summary.by_tier = {
        "tool": TierStats(count=1, est_tokens=0, cursor_baseline=9000, saved_vs_cursor=9000),
        "ollama": TierStats(count=1, est_tokens=2000, cursor_baseline=9000, saved_vs_cursor=7000),
        "unknown": TierStats(count=1, est_tokens=1, cursor_baseline=1, saved_vs_cursor=0),
    }
    summary.top_routes = [("tool-rg", 1)]
    summary.counter_methods = {"tiktoken/cl100k_base": 2}
    text = format_report(summary)
    assert "greedy-token usage" in text
    assert "cheap LLM" in text
    assert "malformed lines skipped" in text


@allure.story("Route quality")
@allure.title("format_report renders quality block with empty by-tier / crystals")
def test_format_report_quality_without_by_tier() -> None:
    from greedy_token.usage import ReportSummary, format_report

    summary = ReportSummary(events=1, since="7d")
    # script_hits truthy so the quality block renders, but no per-tier breakdown
    # and no override-heavy crystals → both optional sub-sections are skipped.
    summary.quality = {
        "override_rate_7d": 0.0,
        "disable_threshold": 0.5,
        "cheap_hold_rate": 1.0,
        "override_events": 0,
        "script_hits": 3,
        "cheap_hits_by_tier": {},
        "by_crystal": [],
    }
    text = format_report(summary)
    assert "Route quality (not ML accuracy)" in text
    assert "cheap hits by tier" not in text
    assert "worst crystals by override" not in text


@allure.story("Time filter")
@allure.title("parse_since rejects invalid values")
def test_parse_since_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid --since"):
        parse_since("not-a-date")


@allure.story("Log loading")
@allure.title("load_events skips events before since and bad timestamps")
def test_load_events_since_filter(log_file: Path) -> None:
    log_file.write_text(
        '{"cmd":"old","ts":"2020-01-01T00:00:00Z","selected_tier":"tool"}\n'
        '{"cmd":"bad-ts","ts":"broken","selected_tier":"tool"}\n'
        '{"cmd":"new","ts":"2030-01-01T00:00:00Z","selected_tier":"rag"}\n',
        encoding="utf-8",
    )
    since = parse_since("2025-01-01")
    events, skipped = load_events(log_file, since=since)
    assert len(events) == 1
    assert events[0]["cmd"] == "new"
    assert skipped >= 1


@allure.story("Rotation")
@allure.title("rotate_log_if_needed returns false when file missing or small")
def test_rotate_log_noop(log_file: Path) -> None:
    assert rotate_log_if_needed(log_file) is False
    log_file.write_text("{}\n", encoding="utf-8")
    assert rotate_log_if_needed(log_file) is False


def _script_hit(route_id: str, tier: str = "python") -> dict:
    return {"v": 2, "cmd": "route", "selected_tier": tier, "route_id": route_id}


def _override(crystal_id: str) -> dict:
    return {
        "v": 2,
        "event": "script_override",
        "selected_tier": "cursor",
        "previous_tier": "python",
        "crystal_id": crystal_id,
        "route_id": crystal_id,
    }


@allure.story("Route quality")
@allure.title("quality_metrics computes override_rate and cheap_hold_rate")
def test_quality_metrics_rates() -> None:
    from greedy_token.usage import quality_metrics

    events = [
        _script_hit("script-a"),
        _script_hit("script-a"),
        _script_hit("script-a"),
        _script_hit("script-b"),
        _override("script-a"),
        {"selected_tier": "cursor", "route_id": "cursor-fallback"},
    ]
    q = quality_metrics(events, since_label="7d")
    assert q["script_hits"] == 4
    assert q["override_events"] == 1
    assert q["override_rate_7d"] == round(1 / 4, 4)
    assert q["cheap_hold_rate"] == round(1 - 1 / 4, 4)
    attach_json("quality", q)


@allure.story("Route quality")
@allure.title("quality_metrics flags worst crystal above disable threshold")
def test_quality_metrics_worst_crystal() -> None:
    from greedy_token.usage import OVERRIDE_DISABLE_THRESHOLD, quality_metrics

    events = [
        _script_hit("script-bad"),
        _script_hit("script-bad"),
        _override("script-bad"),
        _override("script-bad"),
        _script_hit("script-good"),
        _script_hit("script-good"),
        _script_hit("script-good"),
    ]
    q = quality_metrics(events)
    worst = q["by_crystal"][0]
    assert worst["crystal_id"] == "script-bad"
    assert worst["override_rate"] == 1.0
    assert worst["override_rate"] >= OVERRIDE_DISABLE_THRESHOLD
    assert worst["reuse_action"] == "disable/re-shadow"
    good = next(c for c in q["by_crystal"] if c["crystal_id"] == "script-good")
    assert good["override_count"] == 0
    assert good["reuse_action"] is None


@allure.story("Route quality")
@allure.title("quality_metrics counts every cheap tier in the hold denominator")
def test_quality_metrics_all_cheap_tiers() -> None:
    from greedy_token.usage import quality_metrics

    events = [
        _script_hit("tool-rg", tier="tool"),
        _script_hit("mcp-rag", tier="rag"),
        {"selected_tier": "ollama", "route_id": "ollama-x"},
        _script_hit("script-a"),
    ]
    q = quality_metrics(events)
    # All four cheap tiers now count toward the denominator (no fake 100%).
    assert q["script_hits"] == 4
    assert q["cheap_hits"] == 4
    assert q["cheap_hits_by_tier"] == {"ollama": 1, "python": 1, "rag": 1, "tool": 1}
    assert q["cheap_hold_rate"] == 1.0
    scope = q["signal_scope"]
    assert scope["no_signal_yet"] == {}
    assert scope["with_override_signal"] == sorted({"tool", "python", "ollama", "rag", "script"})


@allure.story("Route quality")
@allure.title("quality_metrics attributes overrides across cheap tiers")
def test_quality_metrics_cheap_tier_override_rate() -> None:
    from greedy_token.usage import quality_metrics

    events = [
        _script_hit("tool-rg", tier="tool"),
        _script_hit("tool-rg", tier="tool"),
        _script_hit("rag-cli", tier="rag"),
        _script_hit("rag-cli", tier="rag"),
        _override("tool-rg"),
        _override("rag-cli"),
    ]
    q = quality_metrics(events)
    assert q["cheap_hits"] == 4
    assert q["override_events"] == 2
    assert q["override_rate_7d"] == round(2 / 4, 4)
    assert q["cheap_hold_rate"] == round(1 - 2 / 4, 4)
    worst = q["by_crystal"][0]
    assert worst["override_rate"] == 0.5


@allure.story("Route quality")
@allure.title("quality_metrics is empty-safe and surfaced in aggregate_events")
def test_quality_metrics_empty_and_summary() -> None:
    from greedy_token.usage import aggregate_events, quality_metrics

    q = quality_metrics([])
    assert q["override_rate_7d"] == 0.0
    assert q["cheap_hold_rate"] == 1.0
    assert q["by_crystal"] == []

    summary = aggregate_events([_script_hit("script-a"), _override("script-a")], since_label="7d")
    payload = summary.to_dict()
    assert payload["quality"]["override_rate_7d"] == 1.0
    text = format_report(summary)
    assert "Route quality" in text

