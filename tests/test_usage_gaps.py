"""Unit tests for usage event build / override attribution edges (fail_under=100)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import allure
import pytest

import greedy_token.usage as usage
from greedy_token.router import RouteDecision

pytestmark = [
    allure.epic("Usage"),
    allure.parent_suite("Usage"),
    allure.feature("Event build"),
    allure.suite("Usage gaps"),
]


def _decision(**kw) -> RouteDecision:
    base = dict(
        target="cursor", route_id="r", confidence=0.9, matched=["m"], command=None,
        note="", domains=[], complexity="medium", est_tokens=100, rationale="",
    )
    base.update(kw)
    return RouteDecision(**base)


@allure.title("executor_from_decision falls back to ollama settings on resolve failure")
def test_executor_ollama_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "greedy_token.model_select.resolve_model",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("no model")),
    )
    out = usage.executor_from_decision(_decision(target="ollama"), root=None)
    assert out["kind"] == "ollama" and "model" in out


@allure.title("build_route_event surfaces shadow, escalation, and tags")
def test_build_route_event_optionals(minimal_workspace: Path) -> None:
    dec = _decision(shadow_route_id="shadow-1")
    event = usage.build_route_event(
        cmd="route",
        task="find baseUrl in sample.js",
        root=minimal_workspace,
        decision=dec,
        escalated_from="fast",
        llm_tags={"project": "tms"},
    )
    assert event["shadow_route_id"] == "shadow-1" and event["shadow"] is True
    assert event["escalated_from"] == "fast"
    assert event["tags"] == {"project": "tms"}


@allure.title("build_script_override_event omits empty crystal/window/tags")
def test_build_script_override_minimal() -> None:
    event = usage.build_script_override_event(
        task="retry task", selected_tier="cursor", previous_tier="python",
        crystal_id="", window_sec=None, tags=None,
    )
    assert "crystal_id" not in event
    assert "window_sec" not in event["meta"]
    assert "tags" not in event


@allure.title("find_prior_script_hit skips junk and picks nearest prior hit in window")
def test_find_prior_script_hit(tmp_path: Path) -> None:
    log = tmp_path / "usage.jsonl"
    assert usage.find_prior_script_hit(log, "", datetime.now(timezone.utc)) is None

    task = "find base url in config"
    norm = usage.normalize_task(task)
    when = datetime.now(timezone.utc)

    def row(delta_s: int, *, tier: str = "python", event: str | None = None, ts: str | None = "auto") -> str:
        r: dict = {"selected_tier": tier, "task": task}
        if event:
            r["event"] = event
        if ts == "auto":
            r["ts"] = (when + timedelta(seconds=delta_s)).isoformat()
        elif ts is not None:
            r["ts"] = ts
        return json.dumps(r)

    lines = [
        "",
        "{ not json",
        row(-10, event="script_override"),   # skipped: override
        row(+10),                            # skipped: ts >= when
        row(-5000),                          # skipped: outside window
        row(-200),                           # candidate (best)
        row(-50),                            # newer → replaces best
        row(-300),                           # older than best → no replace
        json.dumps({"selected_tier": "cursor", "task": task, "ts": (when - timedelta(seconds=30)).isoformat()}),  # wrong tier
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    best = usage.find_prior_script_hit(log, norm, when, window_sec=900)
    assert best is not None


@allure.title("maybe_emit_auto_script_override early-returns on non-eligible events")
def test_maybe_emit_early_returns(tmp_path: Path) -> None:
    log = tmp_path / "usage.jsonl"
    log.write_text("", encoding="utf-8")

    usage.maybe_emit_auto_script_override({"event": "script_override"}, path=log)
    usage.maybe_emit_auto_script_override({"selected_tier": "python"}, path=log)
    usage.maybe_emit_auto_script_override({"selected_tier": "cursor", "task": "   "}, path=log)
    usage.maybe_emit_auto_script_override({"selected_tier": "cursor", "task": "real task", "ts": "garbage"}, path=log)
    assert log.read_text(encoding="utf-8") == ""


@allure.title("format_report swallows budget-line errors")
def test_format_report_budget_error(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "greedy_token.budget_ledger.format_budget_line",
        lambda **k: (_ for _ in ()).throw(OSError("io")),
    )
    event = usage.build_route_event(
        cmd="route", task="find baseUrl in sample.js", root=minimal_workspace, decision=_decision()
    )
    summary = usage.aggregate_events([event])
    out = usage.format_report(summary)
    assert summary.events == 1
    assert isinstance(out, str) and out
