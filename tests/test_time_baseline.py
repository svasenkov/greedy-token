"""Wall-clock baseline: naive agent ms, time_saved, footer/report wiring."""

from __future__ import annotations

from pathlib import Path

import allure
import pytest
import yaml

from greedy_token import settings
from greedy_token.baseline import (
    BASE_AGENT_OVERHEAD_MS,
    METHOD_MANUAL,
    MS_PER_1K_BASELINE_TOKENS,
    SOURCE_CALIBRATED,
    SOURCE_DEFAULT,
    SOURCE_MEASURED,
    format_duration_short,
    get_time_baseline_settings,
    naive_agent_ms,
    time_saved_ms,
    write_baseline_config,
)
from greedy_token.budget import format_tool_footer
from greedy_token.hub.api import handle_api
from greedy_token.router import route_task
from greedy_token.usage import (
    aggregate_events,
    append_event,
    build_compress_event,
    build_route_event,
    build_script_event,
    format_report,
)
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Token economy"),
    allure.parent_suite("Token economy"),
    allure.feature("Time baseline"),
    allure.suite("Time baseline"),
]


@allure.story("Defaults")
@allure.title("Default time knobs match built-in constants")
def test_default_time_baseline() -> None:
    settings_t = get_time_baseline_settings()
    assert settings_t.overhead_ms == BASE_AGENT_OVERHEAD_MS
    assert settings_t.ms_per_1k_tokens == MS_PER_1K_BASELINE_TOKENS
    assert settings_t.source == SOURCE_DEFAULT
    assert naive_agent_ms(10_000) == BASE_AGENT_OVERHEAD_MS + (
        10_000 * MS_PER_1K_BASELINE_TOKENS
    ) // 1000


@allure.story("Config")
@allure.title("baseline.overhead_ms calibrates time source")
def test_calibrated_time_overhead() -> None:
    write_baseline_config(7000, method=METHOD_MANUAL, overhead_ms=20_000, ms_per_1k_tokens=500)
    settings_t = get_time_baseline_settings()
    assert settings_t.overhead_ms == 20_000
    assert settings_t.ms_per_1k_tokens == 500
    assert settings_t.source == SOURCE_CALIBRATED
    assert naive_agent_ms(2000) == 20_000 + 1000


@allure.story("Config")
@allure.title("Time baseline parser fails safe and supports measured rate-only config")
def test_time_baseline_config_parsing_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "_read_yaml", lambda _path: {})
    assert get_time_baseline_settings().source == SOURCE_DEFAULT

    monkeypatch.setattr(
        settings,
        "_read_yaml",
        lambda _path: {
            "baseline": {
                "overhead_ms": "invalid",
                "ms_per_1k_tokens": object(),
            }
        },
    )
    invalid = get_time_baseline_settings()
    assert invalid.overhead_ms == BASE_AGENT_OVERHEAD_MS
    assert invalid.ms_per_1k_tokens == MS_PER_1K_BASELINE_TOKENS
    assert invalid.source == SOURCE_DEFAULT

    monkeypatch.setattr(
        settings,
        "_read_yaml",
        lambda _path: {
            "baseline": {
                "overhead_ms": 1234,
                "time_method": "measured",
                "time_calibrated_at": "2026-07-30",
            }
        },
    )
    measured = get_time_baseline_settings()
    assert measured.overhead_ms == 1234
    assert measured.source == SOURCE_MEASURED
    assert measured.calibrated_at == "2026-07-30"

    monkeypatch.setattr(
        settings,
        "_read_yaml",
        lambda _path: {
            "baseline": {
                "ms_per_1k_tokens": 321,
                "method": "measured",
                "calibrated_at": "2026-07-29",
            }
        },
    )
    rate_only = get_time_baseline_settings()
    assert rate_only.ms_per_1k_tokens == 321
    assert rate_only.source == SOURCE_MEASURED
    assert rate_only.calibrated_at == "2026-07-29"


@allure.story("Config")
@allure.title("Token recalibrate preserves time knobs")
def test_write_baseline_preserves_time_knobs() -> None:
    write_baseline_config(7000, method=METHOD_MANUAL, overhead_ms=15_000, ms_per_1k_tokens=900)
    write_baseline_config(8000, method=METHOD_MANUAL)
    path = settings.user_config_path()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["baseline"]["overhead_tokens"] == 8000
    assert data["baseline"]["overhead_ms"] == 15_000
    assert data["baseline"]["ms_per_1k_tokens"] == 900


@allure.story("Savings")
@allure.title("time_saved_ms is baseline − duration for cheap tiers")
def test_time_saved_ms_math() -> None:
    baseline_tokens = 1000
    naive = naive_agent_ms(baseline_tokens)
    assert time_saved_ms(baseline_tokens, 500, "tool") == naive - 500
    assert time_saved_ms(baseline_tokens, 500, "cursor") == 0
    assert time_saved_ms(baseline_tokens, None, "tool") is None


@pytest.mark.parametrize(
    ("ms", "label"),
    [
        (42, "42ms"),
        (1500, "1.5s"),
        (12_000, "12s"),
        (65_000, "1m05s"),
    ],
)
@allure.story("Formatting")
@allure.title("format_duration_short covers ms/s/m")
def test_format_duration_short(ms: int, label: str) -> None:
    assert format_duration_short(ms) == label


@allure.story("Footer")
@allure.title("Compact footer includes time saved when duration is known")
def test_compact_footer_includes_time_saved(minimal_workspace: Path) -> None:
    footer = format_tool_footer(
        "search: baseUrl",
        minimal_workspace,
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        executor_sub="rg",
        duration_ms=42,
    )
    attach_text("footer", footer)
    assert "42ms" in footer
    assert "saved **~" in footer
    # "~12s" / "~23s" style — at least one short duration after saved
    assert " · ~" in footer.split("saved", 1)[1]


@allure.story("Telemetry")
@allure.title("Route events log cursor_baseline_ms and time_saved_ms")
def test_route_event_time_fields(tmp_path: Path, monkeypatch, minimal_workspace: Path) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    decision = route_task("find baseUrl", minimal_workspace)
    event = build_route_event(
        cmd="mcp",
        task="find baseUrl",
        root=minimal_workspace,
        decision=decision,
        duration_ms=100,
    )
    assert event["cursor_baseline_ms"] == naive_agent_ms(event["cursor_baseline"])
    if decision.target != "cursor":
        assert event["time_saved_ms"] == event["cursor_baseline_ms"] - 100
    append_event(event, path=log)
    summary = aggregate_events([event], since_label="7d")
    report = format_report(summary)
    attach_text("report", report)
    assert "time_saved" in report
    assert "Time baseline:" in report


@allure.story("Telemetry")
@allure.title("Event builders omit time_saved_ms when savings are unavailable")
def test_event_builders_omit_unavailable_time_saved(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("greedy_token.usage.time_saved_ms", lambda *_args: None)
    decision = route_task("find baseUrl", minimal_workspace)
    route_event = build_route_event(
        cmd="route",
        task="find baseUrl",
        root=minimal_workspace,
        decision=decision,
        duration_ms=10,
    )
    script_event = build_script_event(
        script_id="check-meta-sync",
        root=minimal_workspace,
        duration_ms=10,
    )
    compress_event = build_compress_event(
        text="long input text",
        short="short",
        use_ollama=False,
        duration_ms=10,
    )
    assert all(
        "time_saved_ms" not in event
        for event in (route_event, script_event, compress_event)
    )


@allure.story("Hub")
@allure.title("Summary metrics expose time_saved_ms")
def test_hub_summary_time_saved(tmp_path: Path, monkeypatch, minimal_workspace: Path) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    decision = route_task("find baseUrl", minimal_workspace)
    append_event(
        build_route_event(
            cmd="route",
            task="find baseUrl",
            root=minimal_workspace,
            decision=decision,
            duration_ms=1,
        ),
        path=log,
    )
    status, payload = handle_api("/api/summary?since=7d")
    assert status == 200
    metrics = payload["metrics"]
    assert "time_saved_ms" in metrics
    assert metrics["duration_samples"] == 1
    if decision.target != "cursor":
        assert metrics["time_saved_ms"] > 0
