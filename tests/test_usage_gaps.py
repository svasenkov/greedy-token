"""Unit tests for usage event build / override attribution edges (fail_under=100)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import allure
import greedy_token.usage as usage
from greedy_token.result_gate import GateDecision
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
    assert usage.find_prior_script_hit(log, "", datetime.now(UTC)) is None

    task = "find base url in config"
    norm = usage.normalize_task(task)
    when = datetime.now(UTC)

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


@allure.title("_dedupe_identical_events keeps an unserializable event instead of crashing")
def test_dedupe_unserializable_event() -> None:
    """A circular record defeats ``json.dumps`` even with ``default=str`` —
    the dedupe must pass it through, not drop the event or raise."""
    row: dict = {
        "ts": datetime.now(UTC).isoformat(),
        "operation_id": "op-circular",
        "selected_tier": "tool",
        "est_tokens": 3,
        "cursor_saved": 7,
    }
    row["loop"] = row  # circular reference → ValueError inside json.dumps
    summary = usage.aggregate_events([row, row])
    # Both copies survive: unserializable events are passed through as unique.
    assert summary.events == 2
    assert summary.operations == 1  # same operation_id — one operation


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


# ---------------------------------------------------------------------------
# Mutation-hardening: exact-contract asserts for usage event builders/report.
# ---------------------------------------------------------------------------


def _gate(
    *, status: str = "produced", action: str = "accepted",
    reason: str = "accepted", eligible: bool = True, exclusion: str = "",
) -> GateDecision:
    return GateDecision(
        action=action, reason=reason, result_status=status, tier="tool",
        may_answer=True, continue_chain=True, succeeded=True,
        savings_eligible=eligible, savings_exclusion=exclusion,
        outcome="success",
    )


def _rich_decision() -> RouteDecision:
    return RouteDecision(
        target="tool", route_id="mcp-search", confidence=0.91234,
        matched=["find", "search"], command=None,
        note="n", domains=["d"], est_tokens=123,
        tool="rg", shadow_route_id="shadow-1",
        raw_score=0.45678, confidence_source="formula",
        calibration_n=7, calibration_segment="seg-1",
    )


@allure.title("build_route_event emits the full exact schema")
def test_build_route_event_golden(minimal_workspace: Path) -> None:

    root = minimal_workspace
    task = "find files"
    baseline = usage.cursor_baseline(root, task)
    e = usage.build_route_event(
        cmd="search", task=task, root=root,
        decision=_rich_decision(), executed=True, est_tokens_override=7,
        rag_hits=3, duration_ms=42, tier_scan=[{"tier": "tool"}],
        outcome_success=True, operation_id="op-1",
        parent_operation_id="op-0", authorized=True,
        gate=_gate(), spend_ref="r1", input_tokens=55,
        model_id="m1", model_billing="metered", cost_usd=0.001234,
        llm_attempts=["a", "b"], llm_tags={"k": "v"},
        profile="p", escalated_from="cheap", billing_tier="metered",
    )
    assert e.pop("ts").endswith("Z")
    assert e.pop("cursor_baseline") == baseline
    assert e.pop("cursor_baseline_ms") == usage.naive_agent_ms(baseline)
    expected = {
        "v": usage.SCHEMA_VERSION,
        "cmd": "search",
        "task": task,
        "root": str(root),
        "selected_tier": "tool",
        "route_id": "mcp-search",
        "confidence": round(0.91234, 4),
        "task_language": usage.detect_task_language(task),
        "est_tokens": 7,
        "cursor_saved": usage.cursor_saved_for(root, task, 7, "tool"),
        "token_counter_method": usage.count_tokens(task).method,
        "tier_scan": [{"tier": "tool"}],
        "executor": {
            "kind": "rg", "rag_hits": 3, "executed": True, "model_id": "m1",
        },
        "phase": "executed",
        "result_status": "produced",
        "gate_action": "accepted",
        "gate_reason": "accepted",
        "operation_id": "op-1",
        "parent_operation_id": "op-0",
        "authorized": True,
        "confidence_source": "formula",
        "raw_score": round(0.45678, 4),
        "calibration_n": 7,
        "bucket": usage.bucket_label(usage.bucket_index(0.45678)),
        "calibration_segment": "seg-1",
        "matched": ["find", "search"],
        "shadow_route_id": "shadow-1",
        "shadow": True,
        "duration_ms": 42,
        "time_saved_ms": usage.time_saved_ms(baseline, 42, "tool"),
        "profile": "p",
        "escalated_from": "cheap",
        "billing_tier": "metered",
        "cost_usd": 0.001234,
        "input_tokens": 55,
        "llm_attempts": ["a", "b"],
        "spend_ref": "r1",
        "tags": {"k": "v"},
        "billing": {"tier": "metered", "cost_usd": 0.001234, "model_id": "m1"},
    }
    assert e == expected


@allure.title("build_route_event exclusions and cursor billing block")
def test_build_route_event_variants(minimal_workspace: Path) -> None:
    root = minimal_workspace
    decision = RouteDecision(
        target="cursor", route_id="r", confidence=0.5,
        matched=[], command=None, note="", domains=[], est_tokens=10,
    )
    # failed executed call → task_failed exclusion, cursor tier → saved 0 anyway
    e = usage.build_route_event(
        cmd="c", task="t", root=root, decision=decision, executed=True,
        outcome_success=False,
    )
    assert e["savings_eligible"] is False
    assert e["savings_exclusion"] == "task_failed"
    assert e["cursor_saved"] == 0
    assert e["billing"]["tier"] == "cursor_estimate"
    # cursor tier has no potential savings → no potential column even unexecuted
    e = usage.build_route_event(
        cmd="c", task="t", root=root, decision=decision, executed=False,
    )
    assert e["savings_exclusion"] == "not_executed"
    assert "cursor_saved_potential" not in e
    assert e["phase"] == "recommended"
    # non-cursor tier, not executed → not_executed exclusion + potential column
    cheap = RouteDecision(
        target="ollama", route_id="r2", confidence=0.5,
        matched=[], command=None, note="", domains=[], est_tokens=10,
    )
    e = usage.build_route_event(
        cmd="c", task="t", root=root, decision=cheap, executed=False,
    )
    assert e["savings_exclusion"] == "not_executed"
    assert e["cursor_saved_potential"] == e["cursor_baseline"] - 10
    # gate with result_status=None but a GateDecision still lands fields
    gate = _gate(eligible=False, exclusion="unverified_result")
    e = usage.build_route_event(
        cmd="c", task="t", root=root, decision=decision, executed=True,
        gate=gate,
    )
    assert e["result_status"] == "produced"
    assert e["savings_exclusion"] == "unverified_result"


@allure.title("build_outcome_event emits the full exact schema and validates args")
def test_build_outcome_event_golden(minimal_workspace: Path) -> None:
    e = usage.build_outcome_event(
        task="My Task", root=minimal_workspace, decision=_rich_decision(),
        outcome="success", layer="executor", duration_ms=42, attempts=3,
        retries=1, escalations=["ollama"], exit_code=0,
        operation_id="op-1", parent_operation_id="op-0", gate=_gate(),
    )
    assert e.pop("ts").endswith("Z")
    assert e == {
        "v": usage.SCHEMA_VERSION,
        "event": "route_outcome",
        "cmd": "outcome",
        "task": "My Task",
        "task_normalized": "my task",
        "task_language": usage.detect_task_language("My Task"),
        "root": str(minimal_workspace),
        "selected_tier": "tool",
        "route_id": "mcp-search",
        "raw_score": round(0.45678, 4),
        "confidence": round(0.91234, 4),
        "confidence_source": "formula",
        "outcome": "success",
        "outcome_layer": "executor",
        "attempts": 3,
        "retries": 1,
        "escalations": ["ollama"],
        "est_tokens": 0,
        "cursor_baseline": 0,
        "cursor_saved": 0,
        "savings_eligible": True,
        "result_status": "produced",
        "gate_action": "accepted",
        "gate_reason": "accepted",
        "operation_id": "op-1",
        "parent_operation_id": "op-0",
        "calibration_segment": "seg-1",
        "duration_ms": 42,
        "exit_code": 0,
    }
    dec = _rich_decision()
    with pytest.raises(
        ValueError,
        match=r"outcome must be one of: escalated, failure, success, unknown",
    ):
        usage.build_outcome_event(
            task="t", root=minimal_workspace, decision=dec,
            outcome="bad", layer="executor",
        )
    with pytest.raises(
        ValueError,
        match=r"outcome layer must be one of: agent, escalation, executor, pipeline, retrieval",
    ):
        usage.build_outcome_event(
            task="t", root=minimal_workspace, decision=dec,
            outcome="success", layer="bad",
        )
    with pytest.raises(ValueError, match=r"outcome attempts must be >= 1"):
        usage.build_outcome_event(
            task="t", root=minimal_workspace, decision=dec,
            outcome="success", layer="executor", attempts=0,
        )
    with pytest.raises(
        ValueError, match=r"outcome retries must be >= 0 and < attempts"
    ):
        usage.build_outcome_event(
            task="t", root=minimal_workspace, decision=dec,
            outcome="success", layer="executor", attempts=2, retries=2,
        )


@allure.title("build_script_event emits the exact schema incl. exclusions")
def test_build_script_event_golden(minimal_workspace: Path) -> None:
    root = minimal_workspace
    baseline = usage.cursor_baseline(root, "scripts --run meta-sync-check")
    e = usage.build_script_event(
        script_id="meta-sync-check", root=root, duration_ms=15,
        executed=True, outcome_success=True, operation_id="op-9",
    )
    assert e.pop("ts").endswith("Z")
    assert e.pop("cursor_baseline") == baseline
    assert e == {
        "v": usage.SCHEMA_VERSION,
        "cmd": "scripts",
        "task": "scripts --run meta-sync-check",
        "root": str(root),
        "selected_tier": "python",
        "route_id": usage.wrapper_route_id("meta-sync-check"),
        "confidence": 1.0,
        "confidence_source": "fixed",
        "est_tokens": 0,
        "cursor_saved": baseline,
        "token_counter_method": usage.count_tokens(
            "scripts --run meta-sync-check"
        ).method,
        "tier_scan": [],
        "executor": {"kind": "script", "script_id": "meta-sync-check",
                     "executed": True},
        "phase": "executed",
        "operation_id": "op-9",
        "duration_ms": 15,
        "time_saved_ms": usage.time_saved_ms(baseline, 15, "python"),
        "cursor_baseline_ms": usage.naive_agent_ms(baseline),
    }
    # dry run → not executed exclusion + potential
    e = usage.build_script_event(script_id="meta-sync-check", root=root,
                                 executed=False)
    assert e["phase"] == "planned"
    assert e["savings_exclusion"] == "not_executed"
    assert e["cursor_saved_potential"] == baseline
    assert e["cursor_saved"] == 0
    # executed but failed
    e = usage.build_script_event(script_id="meta-sync-check", root=root,
                                 executed=True, outcome_success=False)
    assert e["savings_exclusion"] == "task_failed"
    # executed with rejecting gate
    e = usage.build_script_event(
        script_id="meta-sync-check", root=root, executed=True,
        gate=_gate(eligible=False, exclusion="invalid_result"),
    )
    assert e["savings_exclusion"] == "invalid_result"
    assert e["gate_action"] == "accepted"


@allure.title("build_script_override_event emits the exact schema")
def test_build_script_override_golden(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_ROOT", raising=False)
    e = usage.build_script_override_event(
        task="Do Thing", selected_tier="cursor", previous_tier="python",
        crystal_id="cr-7", root=minimal_workspace, reason="user_reask",
        prior_usage_ts="2026-01-01T00:00:00Z", window_sec=900,
        tags={"k": "v"}, operation_id="op-2",
    )
    assert e.pop("ts").endswith("Z")
    assert e == {
        "v": usage.SCHEMA_VERSION,
        "event": "script_override",
        "cmd": "override",
        "task": "Do Thing",
        "task_normalized": "do thing",
        "root": str(minimal_workspace),
        "selected_tier": "cursor",
        "previous_tier": "python",
        "est_tokens": 0,
        "cursor_baseline": 0,
        "cursor_saved": 0,
        "billing": {
            "spent_est": 0,
            "saved_est": 0,
            "note": "override — prior cheap hit rejected by user/agent",
        },
        "meta": {
            "reason": "user_reask",
            "prior_usage_ts": "2026-01-01T00:00:00Z",
            "window_sec": 900,
        },
        "operation_id": "op-2",
        "crystal_id": "cr-7",
        "route_id": "cr-7",
        "tags": {"k": "v"},
    }
    # no root → env fallback
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", "/env/root")
    e = usage.build_script_override_event(
        task="t", selected_tier="cursor", previous_tier="ollama",
    )
    assert e["root"] == "/env/root"
    assert "crystal_id" not in e and "route_id" not in e


@allure.title("build_compress_event emits the exact schema for both compressors")
def test_build_compress_event_golden() -> None:
    text = "a much longer body of text to compress"
    short = "short"
    before = usage.count_tokens(text)
    after = usage.count_tokens(short)
    e = usage.build_compress_event(
        text=text, short=short, use_ollama=True, duration_ms=30,
        eval_tokens=11, operation_id="op-3",
    )
    assert e.pop("ts").endswith("Z")
    assert e == {
        "v": usage.SCHEMA_VERSION,
        "cmd": "compress",
        "task": text,
        "root": __import__("os").environ.get("GREEDY_TOKEN_ROOT", ""),
        "selected_tier": "ollama",
        "route_id": "compress-ollama",
        "confidence": 1.0,
        "confidence_source": "fixed",
        "est_tokens": after.tokens,
        "cursor_baseline": before.tokens,
        "cursor_saved": before.tokens - after.tokens,
        "token_counter_method": before.method,
        "tier_scan": [],
        "executor": {"kind": "compress", "compressor": "ollama",
                     "eval_tokens": 11},
        "tokens_before": before.tokens,
        "tokens_after": after.tokens,
        "compressor": "ollama",
        "phase": "executed",
        "operation_id": "op-3",
        "cursor_baseline_ms": usage.naive_agent_ms(before.tokens),
        "duration_ms": 30,
        "time_saved_ms": usage.time_saved_ms(before.tokens, 30, "ollama"),
    }
    e = usage.build_compress_event(text=text, short=short, use_ollama=False)
    assert e["selected_tier"] == "python"
    assert e["route_id"] == "compress-heuristic"
    assert e["executor"] == {"kind": "compress", "compressor": "heuristic"}
    assert "duration_ms" not in e and "time_saved_ms" not in e
    assert "operation_id" not in e


@allure.title("find_prior_cheap_hit returns the nearest cheap hit inside the window")
def test_find_prior_cheap_hit_golden(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    log = tmp_path / "usage.jsonl"
    now = datetime.now(UTC).replace(microsecond=0)

    def iso(dt: datetime) -> str:
        return dt.isoformat().replace("+00:00", "Z")
    rows = [
        {"selected_tier": "python", "task_normalized": "probe task",
         "ts": iso(now - timedelta(seconds=300)), "route_id": "cr-old"},
        {"event": "script_override", "selected_tier": "python",
         "task_normalized": "probe task", "route_id": "cr-skip",
         "ts": iso(now - timedelta(seconds=10))},
        {"selected_tier": "cursor", "task_normalized": "probe task",
         "ts": iso(now - timedelta(seconds=40))},
        {"selected_tier": "ollama", "task_normalized": "other task",
         "ts": iso(now - timedelta(seconds=30))},
        {"selected_tier": "python", "task_normalized": "probe task",
         "ts": iso(now + timedelta(seconds=10)), "route_id": "cr-future"},
        {"selected_tier": "python", "task_normalized": "probe task",
         "ts": iso(now - timedelta(seconds=2000)), "route_id": "cr-stale"},
        {"selected_tier": "ollama", "task_normalized": "probe task",
         "ts": iso(now - timedelta(seconds=100)), "route_id": "cr-best"},
        "{not json",
        "",
    ]
    log.write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows),
        encoding="utf-8",
    )
    hit = usage.find_prior_cheap_hit(log, "probe task", now, window_sec=900)
    assert hit is not None and hit["route_id"] == "cr-best"
    # empty normalized task / missing file → None
    assert usage.find_prior_cheap_hit(log, "", now) is None
    assert usage.find_prior_cheap_hit(tmp_path / "nope.jsonl", "probe task", now) is None


@allure.title("maybe_emit_auto_script_override attributes a prior cheap hit")
def test_maybe_emit_auto_script_override_golden(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    log = tmp_path / "usage.jsonl"
    now = datetime.now(UTC).replace(microsecond=0)
    prior_ts = (now - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    log.write_text(json.dumps({
        "selected_tier": "python", "task_normalized": "probe task",
        "task": "Probe Task", "ts": prior_ts, "route_id": "python-x",
        "root": "/r",
    }) + "\n", encoding="utf-8")
    event = {
        "selected_tier": "cursor", "task": "Probe Task",
        "ts": now.isoformat().replace("+00:00", "Z"), "root": "/r",
        "tags": {"src": "t"},
    }
    usage.maybe_emit_auto_script_override(event, path=log)
    rows = [json.loads(line) for line in log.read_text().splitlines() if line]
    override = rows[-1]
    assert override["event"] == "script_override"
    assert override["selected_tier"] == "cursor"
    assert override["previous_tier"] == "python"
    assert override["crystal_id"] == "python-x"
    assert override["meta"]["reason"] == "user_reask"
    assert override["meta"]["prior_usage_ts"] == prior_ts
    assert override["meta"]["window_sec"] == 900
    assert override["tags"] == {"src": "t"}
    assert override["root"] == "/r"
    # early returns: non-cursor, blank task, no ts, no prior hit
    for ev in (
        {"selected_tier": "tool", "task": "x", "ts": now.isoformat()},
        {"selected_tier": "cursor", "task": "   ", "ts": now.isoformat()},
        {"selected_tier": "cursor", "task": "x"},
        {"selected_tier": "cursor", "task": "unknown task", "ts": now.isoformat()},
    ):
        before = log.read_text()
        usage.maybe_emit_auto_script_override(dict(ev), path=log)
        assert log.read_text() == before


@allure.title("aggregate_events produces the exact summary shape")
def test_aggregate_events_golden(minimal_workspace: Path) -> None:
    events = [
        {"selected_tier": "tool", "est_tokens": 10, "cursor_baseline": 100,
         "cursor_saved": 90, "duration_ms": 40, "time_saved_ms": 1000,
         "route_id": "r1", "token_counter_method": "tiktoken",
         "operation_id": "o1"},
        {"selected_tier": "tool", "est_tokens": 5, "cursor_baseline": 50,
         "cursor_saved": 45, "route_id": "r1",
         "token_counter_method": "tiktoken", "operation_id": "o1"},
        {"selected_tier": "ollama", "est_tokens": 20, "cursor_baseline": 60,
         "cursor_saved": 40, "duration_ms": 70, "route_id": "r2",
         "token_counter_method": "fallback"},
        {"event": "route_outcome", "selected_tier": "tool", "outcome": "success",
         "operation_id": "o1"},
        {"event": "route_outcome", "selected_tier": "tool", "outcome": "failure",
         "operation_id": "o2"},
        {"selected_tier": "mytier", "est_tokens": 1, "route_id": "r3",
         "token_counter_method": "fallback"},
        # byte-identical duplicate must dedupe away
        {"selected_tier": "mytier", "est_tokens": 1, "route_id": "r3",
         "token_counter_method": "fallback"},
    ]
    summary = usage.aggregate_events(events, since_label="7d")
    assert summary.events == 6  # the duplicate row is dropped
    assert summary.since == "7d"
    assert summary.operations == 3
    assert summary.outcome_records == 2
    tool = summary.by_tier["tool"]
    assert (tool.count, tool.est_tokens, tool.cursor_baseline,
            tool.saved_vs_cursor, tool.duration_ms, tool.duration_samples,
            tool.time_saved_ms) == (2, 15, 150, 135, 40, 1, 1000)
    assert summary.by_tier["ollama"].est_tokens == 20
    assert summary.by_tier["mytier"].count == 1
    assert list(summary.by_tier) == ["tool", "ollama", "mytier"]
    assert summary.top_routes == [("r1", 2), ("r2", 1), ("r3", 1)]
    assert summary.counter_methods == {"tiktoken": 2, "fallback": 2}
    assert summary.quality["explicit_outcomes"] == 2
    assert summary.quality["successful_outcomes"] == 1
    assert summary.quality["failed_outcomes"] == 1
    assert "override_hold_calibration" in summary.quality
    assert "outcome_calibration" in summary.quality
    assert summary.quality["since"] == "7d"


@allure.title("quality_metrics emits the exact metrics dict")
def test_quality_metrics_golden() -> None:
    events = [
        {"selected_tier": "python", "route_id": "c1"},
        {"selected_tier": "python", "route_id": "c1"},
        {"selected_tier": "ollama", "route_id": "c2"},
        {"event": "script_override", "crystal_id": "c1"},
        {"event": "route_outcome", "selected_tier": "tool", "outcome": "success"},
        {"event": "route_outcome", "selected_tier": "tool", "outcome": "failure"},
        {"event": "route_outcome", "selected_tier": "tool", "outcome": "unknown"},
        {"selected_tier": "cursor", "route_id": "x"},
    ]
    q = usage.quality_metrics(events, since_label="7d")
    assert q == {
        "since": "7d",
        "override_rate_7d": round(1 / 3, 4),
        "override_hold_rate": round(1 - 1 / 3, 4),
        "cheap_hold_rate": round(1 - 1 / 3, 4),
        "script_hits": 3,
        "cheap_hits": 3,
        "cheap_hits_by_tier": {"ollama": 1, "python": 2},
        "override_events": 1,
        "explicit_outcomes": 2,
        "successful_outcomes": 1,
        "failed_outcomes": 1,
        "other_outcomes": 1,
        "task_success_rate": 0.5,
        "outcomes_by_tier": {
            "tool": {"success": 1, "failure": 1, "other": 1},
        },
        "disable_threshold": 0.3,
        "by_crystal": [
            {"crystal_id": "c1", "script_hits": 2, "override_count": 1,
             "override_rate": 0.5, "reuse_action": "disable/re-shadow"},
            {"crystal_id": "c2", "script_hits": 1, "override_count": 0,
             "override_rate": 0.0, "reuse_action": None},
        ],
        "signal_scope": {
            "with_override_signal": sorted(usage.CHEAP_TIERS),
            "no_signal_yet": {},
            "override_hold_is_correctness": False,
            "outcome_event": usage.OUTCOME_EVENT,
        },
    }
    # empty input → zeroed metrics
    q = usage.quality_metrics([])
    assert q["script_hits"] == 0
    assert q["override_rate_7d"] == 0.0
    assert q["task_success_rate"] is None
    assert q["since"] is None


_BUDGET_BLOCK = """Budget (Sep)
  Metered API:    $0.0000 / $50.00 (0.0%) — hard cap
    cheap bulk:   $0.0000 · expensive: $0.0000
  Cursor estimate: ~$7.71 / $30.00 (25.7%) — soft limit"""


@allure.title("format_report renders the exact full report text")
def test_format_report_golden(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    monkeypatch.setattr(
        "greedy_token.baseline.get_baseline_settings",
        lambda: types.SimpleNamespace(overhead_tokens=17043, source="measured"),
    )
    monkeypatch.setattr(
        "greedy_token.baseline.get_time_baseline_settings",
        lambda: types.SimpleNamespace(
            overhead_ms=12000, ms_per_1k_tokens=800, source="default-estimate"
        ),
    )
    monkeypatch.setattr(
        "greedy_token.baseline.uncalibrated_nudge", lambda: ""
    )
    monkeypatch.setattr(
        "greedy_token.budget_ledger.format_budget_line",
        lambda **kw: _BUDGET_BLOCK,
    )
    summary = usage.ReportSummary(
        events=5, operations=3, outcome_records=2, skipped_lines=1, since="7d",
        by_tier={
            "tool": usage.TierStats(
                count=2, est_tokens=10, saved_vs_cursor=12000,
                time_saved_ms=1000, duration_samples=2,
            ),
            "ollama": usage.TierStats(
                count=3, est_tokens=45, saved_vs_cursor=300,
            ),
        },
        top_routes=[("mcp-search", 4)],
        counter_methods={"fallback": 2, "tiktoken": 3},
        quality={
            "override_rate_7d": 0.4, "disable_threshold": 0.5,
            "override_hold_rate": 0.6, "cheap_hold_rate": 0.6,
            "override_events": 2, "script_hits": 5,
            "cheap_hits_by_tier": {"python": 3, "ollama": 2},
            "by_crystal": [{
                "crystal_id": "cr-1", "override_count": 1, "script_hits": 2,
                "override_rate": 0.5, "reuse_action": "disable/re-shadow",
            }],
            "explicit_outcomes": 4, "successful_outcomes": 3,
            "failed_outcomes": 1, "other_outcomes": 1,
            "task_success_rate": 0.75,
            "outcome_calibration": [{
                "segment_type": "tier", "segment": "tool", "bucket": "high",
                "n": 5, "predicted": 0.9, "observed_success_rate": 0.8,
                "calibrated": True,
            }],
        },
    )
    assert usage.format_report(summary) == (
        "== greedy-token usage (since 7d) ==\n"
        "Events: 5  (operations 3 · outcome records 2)\n"
        "\n"
        "By tier:\n"
        "  tier        count   est_tokens  saved_vs_cursor   time_saved\n"
        "  tool            2           10           12,000         1.0s\n"
        "  ollama          3           45              300"
        "            —  (cheap LLM)\n"
        "\n"
        "Baseline source: measured (agent overhead ~17,043 tokens) — "
        "saved_vs_cursor is an estimate vs this baseline\n"
        "Time baseline: default-estimate (overhead ~12s + 800ms/1k tokens) — "
        "time_saved ~1.0s across 2 timed events\n"
        "\n"
        "Top routes:\n"
        "  mcp-search                      4\n"
        "\n"
        "Route quality — override/hold signal (not correctness):\n"
        "  override_rate   40%  (threshold 50%)\n"
        "  override_hold_rate 60%  (2 overrides / 5 cheap hits)\n"
        "  cheap hits by tier: ollama 2, python 3\n"
        "  worst crystals by override:\n"
        "    cr-1                           50% (1/2)  <- disable/re-shadow\n"
        "\n"
        "Observed task outcomes (explicit route_outcome events):\n"
        "  task_success_rate 75%  (3 success / 1 failure; 1 escalation/unknown)\n"
        "  measured outcomes 4; missing outcomes remain unknown\n"
        "\n"
        "Outcome confidence calibration (explicit success/failure; min n=20):\n"
        "  segment                      bucket           n  predicted"
        "  observed  status\n"
        "  tier:tool                    high             5        90%"
        "       80%  calibrated\n"
        "\n"
        "Token counter: fallback (2/5), tiktoken (3/5)\n"
        "\n"
        "Budget (Sep)\n"
        "  Metered API:    $0.0000 / $50.00 (0.0%) — hard cap\n"
        "    cheap bulk:   $0.0000 · expensive: $0.0000\n"
        "  Cursor estimate: ~$7.71 / $30.00 (25.7%) — soft limit\n"
        "\n"
        "(1 malformed lines skipped)"
    )


@allure.title("format_report empty-summary variants")
def test_format_report_empty_variants() -> None:
    assert usage.format_report(usage.ReportSummary()) == "No events yet."
    assert usage.format_report(
        usage.ReportSummary(since="24h", skipped_lines=2)
    ) == "No events since 24h. (2 malformed lines skipped)"


# ---------------------------------------------------------------------------
# Mutation-hardening: env helpers, rotation, sessions, misc helpers.
# ---------------------------------------------------------------------------


@allure.title("logging_enabled honors no_log and the env kill-switch")
def test_logging_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_LOG", raising=False)
    assert usage.logging_enabled() is True
    assert usage.logging_enabled(no_log=True) is False
    for off in ("0", "false", " OFF ", "No"):
        monkeypatch.setenv("GREEDY_TOKEN_LOG", off)
        assert usage.logging_enabled() is False
    for on in ("1", "yes", "on", "2"):
        monkeypatch.setenv("GREEDY_TOKEN_LOG", on)
        assert usage.logging_enabled() is True


@allure.title("_ensure_log_dir creates the dir once and skips on the flag")
def test_ensure_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage, "_log_dir_ready", False)
    target = tmp_path / "deep" / "nested" / "usage.jsonl"
    usage._ensure_log_dir(target)
    assert usage._log_dir_ready is True
    assert target.parent.is_dir()
    # second call is a no-op: remove the dir, it must NOT be recreated
    target.parent.rmdir()
    target.parent.parent.rmdir()
    usage._ensure_log_dir(target)
    assert not target.parent.exists()


@allure.title("_truncate_task and normalize_task exact behavior")
def test_task_helpers() -> None:
    assert usage._truncate_task("  pad  ") == "pad"
    exact = "x" * usage.TASK_MAX_LEN
    assert usage._truncate_task(exact) == exact
    long_task = "y" * (usage.TASK_MAX_LEN + 5)
    out = usage._truncate_task(long_task)
    assert len(out) == usage.TASK_MAX_LEN
    assert out == "y" * (usage.TASK_MAX_LEN - 1) + "…"
    assert usage.normalize_task("  A  B\tC\nD ") == "a b c d"
    assert usage.normalize_task("") == ""


@allure.title("_utc_now_iso emits second-precision UTC Z-suffix")
def test_utc_now_iso() -> None:
    ts = usage._utc_now_iso()
    assert ts.endswith("Z") and "." not in ts
    assert datetime.fromisoformat(ts.replace("Z", "+00:00")).tzinfo is not None


@allure.title("build_tier_scan maps every routed tier to the row schema")
def test_build_tier_scan(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dec = RouteDecision(
        target="tool", route_id="rx", confidence=0.5, matched=["m"],
        command=None, note="", domains=[], est_tokens=9,
    )
    rows = [("tool", dec), ("cursor", dec)]
    monkeypatch.setattr(
        usage, "route_task_all_tiers", lambda task, root: iter(rows)
    )
    scan = usage.build_tier_scan("task", minimal_workspace)
    assert scan == [
        {"tier": "tool", "route_id": "rx", "matched": True, "est_tokens": 9},
        {"tier": "cursor", "route_id": "rx", "matched": True, "est_tokens": 9},
    ]
    dec2 = RouteDecision(
        target="tool", route_id="rx", confidence=0.5, matched=[],
        command=None, note="", domains=[], est_tokens=9,
    )
    monkeypatch.setattr(
        usage, "route_task_all_tiers", lambda task, root: iter([("tool", dec2)])
    )
    assert usage.build_tier_scan("t", minimal_workspace)[0]["matched"] is False


@allure.title("executor_from_decision maps each tier to its executor dict")
def test_executor_from_decision(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def dec(target: str, **kw) -> RouteDecision:
        return RouteDecision(
            target=target, route_id=kw.pop("route_id", "r"),
            confidence=0.5, matched=[], command=None, note="",
            domains=[], est_tokens=1, **kw,
        )

    assert usage.executor_from_decision(dec("tool")) == {"kind": "rg"}
    assert usage.executor_from_decision(dec("tool", tool="fd")) == {"kind": "fd"}
    assert usage.executor_from_decision(
        dec("python", route_id="python-check-meta-sync")
    ) == {"kind": "script", "script_id": "check-meta-sync"}
    assert usage.executor_from_decision(dec("python")) == {"kind": "script"}
    assert usage.executor_from_decision(dec("rag")) == {"kind": "rag"}
    assert usage.executor_from_decision(dec("cursor")) == {"kind": "cursor"}
    assert usage.executor_from_decision(dec("unknown-tier")) == {"kind": "cursor"}
    # ollama: resolved model path
    import types as _t

    import greedy_token.model_select as ms

    monkeypatch.setattr(
        ms, "resolve_model",
        lambda *a, **k: _t.SimpleNamespace(
            settings=_t.SimpleNamespace(model="mdl"), model_id="mid"
        ),
    )
    assert usage.executor_from_decision(dec("ollama"), minimal_workspace) == {
        "kind": "ollama", "model": "mdl", "model_id": "mid",
        "eval_tokens": None,
    }
    # ollama: resolve_model failure → settings fallback
    def _raise(*a: object, **k: object) -> object:
        raise ValueError("no models")

    monkeypatch.setattr(ms, "resolve_model", _raise)
    from greedy_token.settings import get_ollama_settings

    e = usage.executor_from_decision(dec("ollama"), minimal_workspace)
    assert e == {
        "kind": "ollama", "model": get_ollama_settings(minimal_workspace).model,
        "eval_tokens": None,
    }


@allure.title("wrapper_for_route_id matches wrapper ids inside route ids")
def test_wrapper_for_route_id() -> None:
    w = usage.wrapper_for_route_id("python-check-meta-sync")
    assert w is not None and w.id == "check-meta-sync"
    assert usage.wrapper_for_route_id("x-check-meta-sync-y") is w  # substring
    assert usage.wrapper_for_route_id("zzz-no-match") is None


@allure.title("_cap_matched caps entries and total chars with ellipsis")
def test_cap_matched() -> None:
    many = [f"p{i}" for i in range(usage.MATCHED_MAX_ENTRIES + 3)]
    out = usage._cap_matched(many)
    assert out == many[: usage.MATCHED_MAX_ENTRIES]
    # total char budget: entries of 200 chars each — second overflows
    pats = ["a" * 200, "b" * 200, "c" * 10]
    out = usage._cap_matched(pats)
    assert out[0] == "a" * 200
    assert out[1] == "b" * (usage.MATCHED_MAX_CHARS - 200 - 1) + "…"
    assert len(out) == 2  # budget exhausted → third dropped
    assert usage._cap_matched([]) == []


@allure.title("session/session_file/env_tag resolve env, file, and truncation")
def test_session_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_SESSION", raising=False)
    monkeypatch.delenv("GREEDY_TOKEN_SESSION_FILE", raising=False)
    monkeypatch.delenv("GREEDY_TOKEN_TAG", raising=False)
    assert usage.session_file() == usage.DEFAULT_SESSION_FILE
    sf = tmp_path / "sess"
    monkeypatch.setenv("GREEDY_TOKEN_SESSION_FILE", str(sf))
    assert usage.session_file() == sf
    sf.write_text("line1\nline2\n", encoding="utf-8")
    assert usage.session_id() == "line1"
    # env beats file, truncated to SESSION_ID_MAX_LEN
    monkeypatch.setenv("GREEDY_TOKEN_SESSION", "e" * 200)
    sid = usage.session_id()
    assert len(sid) == usage.SESSION_ID_MAX_LEN
    # missing file → ""
    monkeypatch.delenv("GREEDY_TOKEN_SESSION")
    monkeypatch.setenv("GREEDY_TOKEN_SESSION_FILE", str(tmp_path / "nope"))
    assert usage.session_id() == ""
    # env_tag strips and truncates
    monkeypatch.setenv("GREEDY_TOKEN_TAG", "  lesson-3  ")
    assert usage.env_tag() == "lesson-3"
    monkeypatch.setenv("GREEDY_TOKEN_TAG", "t" * 100)
    assert len(usage.env_tag()) == usage.TAG_MAX_LEN


@allure.title("max_log_bytes/max_rotated_files parse env with digit guard")
def test_log_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GREEDY_TOKEN_LOG_MAX_BYTES", "GREEDY_TOKEN_LOG_MAX_FILES"):
        monkeypatch.delenv(var, raising=False)
    assert usage.max_log_bytes() == usage.DEFAULT_MAX_LOG_BYTES
    assert usage.max_rotated_files() == usage.DEFAULT_MAX_ROTATED
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", " 1024 ")
    assert usage.max_log_bytes() == 1024
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "0")
    assert usage.max_log_bytes() == 1  # max(1, 0)
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "-5")
    assert usage.max_log_bytes() == usage.DEFAULT_MAX_LOG_BYTES
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "abc")
    assert usage.max_log_bytes() == usage.DEFAULT_MAX_LOG_BYTES
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_FILES", "3")
    assert usage.max_rotated_files() == 3
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_FILES", "0")
    assert usage.max_rotated_files() == 1


@allure.title("log_archive_paths orders active log then numbered archives")
def test_log_archive_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_FILES", "2")
    assert usage.log_archive_paths(p) == [
        p, tmp_path / "usage.jsonl.1", tmp_path / "usage.jsonl.2",
    ]
    assert usage.log_archive_paths(p, max_files=1) == [p, tmp_path / "usage.jsonl.1"]


@allure.title("rotate_log_if_needed shifts numbered archives and drops the oldest")
def test_rotate_log_if_needed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "10")
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_FILES", "2")
    assert usage.rotate_log_if_needed(p) is False  # missing file
    p.write_text("12345", encoding="utf-8")
    assert usage.rotate_log_if_needed(p) is False  # below limit
    # over limit with existing archives → shift .1→.2, drop old .2
    (tmp_path / "usage.jsonl.1").write_text("A", encoding="utf-8")
    (tmp_path / "usage.jsonl.2").write_text("OLD", encoding="utf-8")
    p.write_text("x" * 20, encoding="utf-8")
    assert usage.rotate_log_if_needed(p) is True
    assert not p.exists()
    assert (tmp_path / "usage.jsonl.1").read_text() == "x" * 20
    assert (tmp_path / "usage.jsonl.2").read_text() == "A"


@allure.title("append_event honors opt-out, tag, session_id, and writes compact LF")
def test_append_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    target = tmp_path / "usage.jsonl"
    monkeypatch.delenv("GREEDY_TOKEN_LOG", raising=False)
    monkeypatch.delenv("GREEDY_TOKEN_TAG", raising=False)
    monkeypatch.delenv("GREEDY_TOKEN_SESSION", raising=False)
    monkeypatch.setenv("GREEDY_TOKEN_SESSION_FILE", str(tmp_path / "nope"))
    monkeypatch.setattr(usage, "_log_dir_ready", False)
    spy = []
    monkeypatch.setattr(
        usage, "maybe_emit_auto_script_override",
        lambda event, *, path: spy.append(event),
    )
    usage.append_event({"a": "é"}, path=target)
    line = target.read_bytes()
    # hook_mode is stamped on every appended event (savings attribution).
    assert line == '{"a":"é","hook_mode":"advisory"}\n'.encode()
    assert spy == [{"a": "é", "hook_mode": "advisory"}]
    # emit_auto_override=False skips the override emitter
    usage.append_event({"b": 1}, path=target, emit_auto_override=False)
    assert len(spy) == 1
    # env tag injected when absent; existing tag kept
    monkeypatch.setenv("GREEDY_TOKEN_TAG", "lab")
    usage.append_event({"c": 1}, path=target, emit_auto_override=False)
    usage.append_event({"c": 1, "tag": "orig"}, path=target, emit_auto_override=False)
    rows = [json.loads(x) for x in target.read_text().splitlines()]
    assert rows[-2]["tag"] == "lab" and rows[-1]["tag"] == "orig"
    # session_id injected unless a session key already present (incl. in tags)
    monkeypatch.setenv("GREEDY_TOKEN_SESSION", "sess-1")
    usage.append_event({"d": 1}, path=target, emit_auto_override=False)
    usage.append_event({"e": 1, "tags": {"sid": "x"}}, path=target,
                       emit_auto_override=False)
    rows = [json.loads(x) for x in target.read_text().splitlines()]
    assert rows[-2]["session_id"] == "sess-1"
    assert "session_id" not in rows[-1]
    # opt-out: nothing written at all
    monkeypatch.setenv("GREEDY_TOKEN_LOG", "0")
    before = target.read_bytes()
    usage.append_event({"f": 1}, path=target)
    assert target.read_bytes() == before
    # OSError on write → stderr note, no raise
    monkeypatch.setenv("GREEDY_TOKEN_LOG", "1")
    usage.append_event({"g": 1}, path=tmp_path)  # a directory → IsADirectoryError
    assert "usage log write failed" in capsys.readouterr().err


@allure.title("maybe_append_event respects args.no_log")
def test_maybe_append_event(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    monkeypatch.delenv("GREEDY_TOKEN_LOG", raising=False)
    calls: list[dict] = []
    monkeypatch.setattr(usage, "append_event", lambda e: calls.append(e))
    usage.maybe_append_event(types.SimpleNamespace(no_log=True), {"x": 1})
    assert calls == []
    usage.maybe_append_event(types.SimpleNamespace(no_log=False), {"x": 2})
    usage.maybe_append_event(types.SimpleNamespace(), {"x": 3})  # attr missing
    assert calls == [{"x": 2}, {"x": 3}]


@allure.title("parse_since handles unbounded, shorthand, ISO, and errors")
def test_parse_since() -> None:
    assert usage.parse_since(None) is None
    for word in ("all", " LIFETIME ", "Total"):
        assert usage.parse_since(word) is None
    now = datetime.now(UTC)
    for value, delta in (("7d", timedelta(days=7)),
                         ("24h", timedelta(hours=24)),
                         ("30m", timedelta(minutes=30))):
        got = usage.parse_since(value)
        assert got is not None and abs((now - got) - delta) < timedelta(seconds=60)
    assert usage.parse_since("2026-01-02T03:04:05Z") == datetime(
        2026, 1, 2, 3, 4, 5, tzinfo=UTC
    )
    naive = usage.parse_since("2026-01-02 03:04:05")
    assert naive is not None and naive.tzinfo == UTC
    with pytest.raises(ValueError, match=r"Invalid --since 'garbage'"):
        usage.parse_since("garbage")


@allure.title("_parse_event_ts parses Z/naive and rejects garbage")
def test_parse_event_ts() -> None:
    assert usage._parse_event_ts({}) is None
    assert usage._parse_event_ts({"ts": ""}) is None
    assert usage._parse_event_ts({"ts": "2026-01-02T03:04:05Z"}) == datetime(
        2026, 1, 2, 3, 4, 5, tzinfo=UTC
    )
    naive = usage._parse_event_ts({"ts": "2026-01-02 03:04:05"})
    assert naive is not None and naive.tzinfo == UTC
    assert usage._parse_event_ts({"ts": "garbage"}) is None


@allure.title("load_events merges archives, counts malformed, filters by since")
def test_load_events(tmp_path: Path) -> None:
    p = tmp_path / "usage.jsonl"
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat().replace("+00:00", "Z")
    new = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    p.write_text("\n".join([
        json.dumps({"id": "active", "ts": new}),
        "{malformed",
        "null",
        json.dumps({"id": "old", "ts": old}),
        json.dumps({"id": "no-ts"}),
        "",
    ]), encoding="utf-8")
    (tmp_path / "usage.jsonl.1").write_text(
        json.dumps({"id": "arc1"}) + "\n", encoding="utf-8"
    )
    events, skipped = usage.load_events(p)
    assert [e["id"] for e in events] == ["active", "old", "no-ts", "arc1"]
    assert skipped == 2  # malformed + bare null
    # include_archives=False → active only
    events, skipped = usage.load_events(p, include_archives=False)
    assert [e["id"] for e in events] == ["active", "old", "no-ts"]
    # since filter: old dropped silently, missing ts counts as skipped
    events, skipped = usage.load_events(p, since=datetime.now(UTC) - timedelta(days=1))
    ids = [e["id"] for e in events]
    assert ids == ["active"]
    assert skipped == 4  # malformed + null + no-ts + ts-less archive row


@allure.title("count_operations counts distinct ids plus unlabelled requests")
def test_count_operations() -> None:
    events = [
        {"operation_id": "o1"},
        {"operation_id": "o1"},
        {"operation_id": "o2"},
        {},
        {"operation_id": 5},  # non-str → unlabelled
        {"event": "route_outcome"},  # outcome rows never counted
        {"event": "script_override"},
        {"event": "route_outcome", "operation_id": "o1"},
    ]
    assert usage.count_operations(events) == 4


@allure.title("ReportSummary.to_dict emits the exact report schema")
def test_report_summary_to_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    monkeypatch.setattr(
        "greedy_token.baseline.get_baseline_settings",
        lambda: types.SimpleNamespace(overhead_tokens=111, source="bsrc"),
    )
    monkeypatch.setattr(
        "greedy_token.baseline.get_time_baseline_settings",
        lambda: types.SimpleNamespace(
            overhead_ms=222, ms_per_1k_tokens=33, source="tsrc"
        ),
    )
    summary = usage.ReportSummary(
        events=3, operations=2, outcome_records=1, skipped_lines=4, since="7d",
        by_tier={
            "tool": usage.TierStats(
                count=2, est_tokens=10, cursor_baseline=100,
                saved_vs_cursor=90, duration_ms=5, time_saved_ms=50,
                duration_samples=1,
            ),
        },
        top_routes=[("r1", 2)],
        counter_methods={"tiktoken": 3},
        quality={"k": "v"},
    )
    assert summary.to_dict() == {
        "events": 3,
        "operations": 2,
        "outcome_records": 1,
        "skipped_lines": 4,
        "since": "7d",
        "baseline": {
            "overhead_tokens": 111, "source": "bsrc", "overhead_ms": 222,
            "ms_per_1k_tokens": 33, "time_source": "tsrc",
        },
        "by_tier": {
            "tool": {
                "count": 2, "est_tokens": 10, "saved_vs_cursor": 90,
                "duration_ms": 5, "time_saved_ms": 50, "duration_samples": 1,
            },
        },
        "totals": {
            "cursor_baseline": 100, "est_tokens": 10, "saved_vs_cursor": 90,
            "duration_ms": 5, "time_saved_ms": 50, "duration_samples": 1,
        },
        "top_routes": [{"route_id": "r1", "count": 2}],
        "counter_methods": {"tiktoken": 3},
        "quality": {"k": "v"},
    }


# ---------------------------------------------------------------------------
# Mutation round 2: deeper kills for surviving mutants.
# ---------------------------------------------------------------------------


@allure.title("quality_metrics covers unknown fallbacks, accumulation, ties")
def test_quality_metrics_edges() -> None:
    events = [
        # outcome rows: 2 success + 2 failure + 2 other on one tier,
        # plus a row with no outcome field at all and one with no tier
        {"event": "route_outcome", "selected_tier": "tool", "outcome": "success"},
        {"event": "route_outcome", "selected_tier": "tool", "outcome": "success"},
        {"event": "route_outcome", "selected_tier": "tool", "outcome": "success"},
        {"event": "route_outcome", "selected_tier": "tool", "outcome": "failure"},
        {"event": "route_outcome", "selected_tier": "tool", "outcome": "failure"},
        {"event": "route_outcome", "selected_tier": "tool"},
        {"event": "route_outcome", "selected_tier": "tool"},
        {"event": "route_outcome", "outcome": "failure"},
        # overrides: crystal via route_id fallback, and no keys at all
        {"event": "script_override", "route_id": "cr-fb"},
        {"event": "script_override"},
        # override for a crystal that never had a cheap hit (×2 → rate 2.0)
        {"event": "script_override", "crystal_id": "cr-orphan"},
        {"event": "script_override", "crystal_id": "cr-orphan"},
        # cheap hits: threshold boundary 3/10, tie-break 1/2 twice,
        # precision 1/3, missing route_id, missing tier
        *[{"selected_tier": "python", "route_id": "cr-t"} for _ in range(10)],
        {"selected_tier": "python", "route_id": "cr-a"},
        {"selected_tier": "python", "route_id": "cr-a"},
        {"selected_tier": "python", "route_id": "cr-b"},
        {"selected_tier": "python", "route_id": "cr-b"},
        {"selected_tier": "python", "route_id": "cr-b"},
        {"selected_tier": "python", "route_id": "cr-b"},
        *[{"selected_tier": "python", "route_id": "cr-p"} for _ in range(3)],
        {"selected_tier": "python"},
        {"route_id": "cr-notier"},
    ] + [
        {"event": "script_override", "crystal_id": c}
        for c in (["cr-t"] * 3 + ["cr-a", "cr-b", "cr-b", "cr-p"])
    ]
    q = usage.quality_metrics(events)
    assert q["successful_outcomes"] == 3
    assert q["failed_outcomes"] == 3
    assert q["other_outcomes"] == 2  # two rows with no outcome field
    assert q["explicit_outcomes"] == 6
    # 3/6 = 0.5 — hmm need non-4-decimal: use a second metrics call
    assert q["task_success_rate"] == 0.5
    q2 = usage.quality_metrics([
        {"event": "route_outcome", "outcome": "success"},
        {"event": "route_outcome", "outcome": "success"},
        {"event": "route_outcome", "outcome": "failure"},
    ])
    assert q2["task_success_rate"] == round(2 / 3, 4)  # 0.6667 not 0.66667
    assert q["outcomes_by_tier"]["tool"] == {
        "success": 3, "failure": 2, "other": 2,
    }
    assert q["outcomes_by_tier"]["unknown"] == {
        "success": 0, "failure": 1, "other": 0,
    }
    crystals = {c["crystal_id"]: c for c in q["by_crystal"]}
    # route_id fallback crystal, and the no-keys "unknown" bucket
    assert crystals["cr-fb"]["override_count"] == 1
    assert crystals["unknown"]["override_count"] >= 1  # no-keys override + hit
    # orphan crystal: 2 overrides, 0 hits → max(1,0) → rate 2.0
    assert crystals["cr-orphan"]["script_hits"] == 0
    assert crystals["cr-orphan"]["override_rate"] == 2.0
    # threshold boundary: 3/10 == 0.3 → reuse_action set
    assert crystals["cr-t"]["override_rate"] == 0.3
    assert crystals["cr-t"]["reuse_action"] == "disable/re-shadow"
    # precision: 1/3 → 0.3333 (not 0.33333)
    assert crystals["cr-p"]["override_rate"] == round(1 / 3, 4)
    # same rate 0.5, different counts → -override_count sorts cr-b first
    assert crystals["cr-a"]["override_rate"] == 0.5  # 1/2
    assert crystals["cr-b"]["override_rate"] == 0.5  # 2/4
    idx = {c["crystal_id"]: i for i, c in enumerate(q["by_crystal"])}
    assert idx["cr-b"] < idx["cr-a"]
    one_hit = [
        c for c in q["by_crystal"]
        if c["script_hits"] == 1 and c["override_count"] == 1
    ]
    assert one_hit and all(c["override_rate"] == 1.0 for c in one_hit)


@allure.title("aggregate_events pins tier order, defaults, accumulation, top-10")
def test_aggregate_events_edges() -> None:
    events = [
        # {} first: the unordered tail must come after the canonical six
        {},
    ] + [
        {"selected_tier": t, "route_id": f"route-{t}"}
        for t in ("cursor", "compress", "rag", "ollama", "python", "tool")
    ]
    events += [
        {"selected_tier": "tool", "duration_ms": 40, "time_saved_ms": 5},
        {"selected_tier": "tool", "duration_ms": 30, "time_saved_ms": 6},
        {"event": "route_outcome", "outcome": "success"},
    ] + [
        {"selected_tier": "tool", "route_id": f"zz-{i:02d}"} for i in range(11)
    ]
    # tie routes inserted in reverse name order → sorted by name asc
    # (non-identical rows so dedupe keeps both copies)
    events += [
        {"selected_tier": "tool", "route_id": "zz-b", "est_tokens": 1},
        {"selected_tier": "tool", "route_id": "zz-b", "est_tokens": 2},
        {"selected_tier": "tool", "route_id": "zz-a", "est_tokens": 1},
        {"selected_tier": "tool", "route_id": "zz-a", "est_tokens": 2},
    ]
    summary = usage.aggregate_events(events)
    assert list(summary.by_tier) == [
        "tool", "python", "ollama", "rag", "cursor", "compress", "unknown",
    ]
    unk = summary.by_tier["unknown"]
    assert (unk.est_tokens, unk.cursor_baseline, unk.saved_vs_cursor) == (0, 0, 0)
    tool = summary.by_tier["tool"]
    assert tool.duration_ms == 70 and tool.duration_samples == 2
    assert tool.time_saved_ms == 11
    assert summary.counter_methods["unknown"] == 24
    assert len(summary.top_routes) == 10
    assert summary.top_routes[0] == ("unknown", 3)
    assert summary.top_routes[1:3] == [("zz-a", 2), ("zz-b", 2)]
    assert isinstance(summary.quality["override_hold_calibration"], list)
    assert isinstance(summary.quality["outcome_calibration"], list)


@allure.title("format_report conditional sections pin each fallback literal")
def test_format_report_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    monkeypatch.setattr(
        "greedy_token.baseline.get_baseline_settings",
        lambda: types.SimpleNamespace(overhead_tokens=1, source="s"),
    )
    monkeypatch.setattr(
        "greedy_token.baseline.get_time_baseline_settings",
        lambda: types.SimpleNamespace(
            overhead_ms=1, ms_per_1k_tokens=1, source="t"
        ),
    )
    monkeypatch.setattr("greedy_token.baseline.uncalibrated_nudge", lambda: "")
    seen: list[bool] = []
    monkeypatch.setattr(
        "greedy_token.budget_ledger.format_budget_line",
        lambda **kw: seen.append(kw.get("compact")) or "B",
    )
    # no-since header + budget call passes compact=False
    out = usage.format_report(usage.ReportSummary(events=1))
    assert out.startswith("== greedy-token usage ==\n")
    assert seen == [False]
    # quality present but no script_hits → no route-quality block, no crash
    out = usage.format_report(usage.ReportSummary(
        events=1, quality={"explicit_outcomes": 1, "successful_outcomes": 0,
                         "failed_outcomes": 1, "other_outcomes": 0,
                         "task_success_rate": 0.0},
    ))
    assert "Route quality" not in out
    assert "task_success_rate 0%" in out
    # script_hits but no by_crystal key → iteration over default []
    out = usage.format_report(usage.ReportSummary(
        events=1, quality={
            "script_hits": 4, "override_events": 1,
            "override_rate_7d": 0.25, "disable_threshold": 0.5,
            "cheap_hold_rate": 0.75,  # no override_hold_rate → fallback used
            "explicit_outcomes": 0,
        },
    ))
    assert "override_hold_rate 75%" in out
    assert "worst crystals" not in out
    # crystals: override_count=0 excluded; reuse_action falsy → no flag;
    # >3 worst rows truncated to 3
    out = usage.format_report(usage.ReportSummary(
        events=1, quality={
            "script_hits": 10, "override_events": 5,
            "override_rate_7d": 0.5, "disable_threshold": 0.3,
            # divergent values pin which key the hold-rate line reads
            "override_hold_rate": 0.5, "cheap_hold_rate": 0.9,
            "by_crystal": [
                {"crystal_id": "c0", "override_count": 0, "script_hits": 2,
                 "override_rate": 0.0, "reuse_action": None},
                *[{"crystal_id": f"c{i}", "override_count": 1,
                   "script_hits": 2, "override_rate": 0.5,
                   "reuse_action": None} for i in range(1, 5)],
            ],
        },
    ))
    worst_section = out.split("worst crystals by override:")[1]
    assert "c0" not in worst_section  # override_count=0 is filtered out
    assert "c4" not in worst_section  # only first 3 listed
    assert "override_hold_rate 50%" in out
    # falsy reuse_action → the flag column stays empty, no "XXXX" tail
    assert "    c1" in worst_section
    assert all("XXXX" not in ln for ln in worst_section.splitlines())
    assert "<- disable/re-shadow" not in out  # all reuse_action falsy
    # only explicit_outcomes (no other) → block still rendered
    out = usage.format_report(usage.ReportSummary(
        events=1, quality={"explicit_outcomes": 2, "successful_outcomes": 1,
                          "failed_outcomes": 1, "other_outcomes": 0,
                          "task_success_rate": 0.5},
    ))
    assert "Observed task outcomes" in out
    # task_success_rate None → "unknown"
    out = usage.format_report(usage.ReportSummary(
        events=1, quality={"explicit_outcomes": 0, "successful_outcomes": 0,
                          "failed_outcomes": 0, "other_outcomes": 1,
                          "task_success_rate": None},
    ))
    assert "task_success_rate unknown" in out


@allure.title("build_route_event forwards root/tier/ok verbatim into helpers")
def test_build_route_event_forwarding(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = minimal_workspace
    seen: dict = {}
    real_gate = usage.evaluate_result_gate
    def _baseline(r, t):
        seen["baseline"] = (r, t)
        return 50

    def _saved(r, t, e, tier):
        seen["saved"] = (r, t, e, tier)
        return 40

    def _scan(t, r):
        seen["scan"] = (t, r)
        return []

    def _lang(t):
        seen["lang"] = t
        return "en"

    def _tsm(b, d, tier):
        seen["tsm"] = (b, d, tier)
        return 999

    def _gate_spy(**kw):
        seen["gate"] = kw
        return real_gate(**kw)

    monkeypatch.setattr(usage, "cursor_baseline", _baseline)
    monkeypatch.setattr(usage, "cursor_saved_for", _saved)
    monkeypatch.setattr(usage, "build_tier_scan", _scan)
    monkeypatch.setattr(usage, "detect_task_language", _lang)
    monkeypatch.setattr(usage, "time_saved_ms", _tsm)
    monkeypatch.setattr(usage, "evaluate_result_gate", _gate_spy)
    dec = _rich_decision()
    e = usage.build_route_event(
        cmd="c", task="t", root=root, decision=dec, executed=True,
        result_status="produced", outcome_success=None, duration_ms=7,
    )
    assert seen["baseline"] == (root, "t")
    assert seen["saved"] == (root, "t", dec.est_tokens, "tool")
    assert seen["scan"] == ("t", root)
    assert seen["lang"] == "t"
    assert seen["tsm"] == (50, 7, "tool")
    assert seen["gate"] == {
        "started": True, "result_status": "produced",
        "tier": "tool", "ok": True,
    }
    assert e["tier_scan"] == []
    # outcome_success=True → ok=True; `is not True` would flip it to False
    seen.clear()
    usage.build_route_event(
        cmd="c", task="t", root=root, decision=dec, executed=True,
        result_status="produced", outcome_success=True,
    )
    assert seen["gate"]["ok"] is True
    # exclusion + duration → time_saved_ms must NOT be emitted
    e = usage.build_route_event(
        cmd="c", task="t", root=root, decision=dec, executed=True,
        outcome_success=False, duration_ms=7,
    )
    assert "time_saved_ms" not in e
    # 7-digit cost rounds to 6
    e = usage.build_route_event(
        cmd="c", task="t", root=root, decision=dec, executed=True,
        cost_usd=0.123456789,
    )
    assert e["cost_usd"] == 0.123457


@allure.title("build_outcome_event defaults and exact error messages")
def test_build_outcome_event_edges(minimal_workspace: Path) -> None:
    e = usage.build_outcome_event(
        task="t", root=minimal_workspace, decision=_rich_decision(),
        outcome="escalated", layer="agent",
    )
    assert e["attempts"] == 1 and e["retries"] == 0
    assert e["escalations"] == []
    with pytest.raises(ValueError) as exc:
        usage.build_outcome_event(
            task="t", root=minimal_workspace, decision=_rich_decision(),
            outcome="success", layer="executor", attempts=0,
        )
    assert str(exc.value) == "outcome attempts must be >= 1"
    with pytest.raises(ValueError) as exc:
        usage.build_outcome_event(
            task="t", root=minimal_workspace, decision=_rich_decision(),
            outcome="success", layer="executor", attempts=1, retries=1,
        )
    assert str(exc.value) == "outcome retries must be >= 0 and < attempts"


@allure.title("build_script_event forwards root and python tier into helpers")
def test_build_script_event_forwarding(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = minimal_workspace
    seen: dict = {}
    def _baseline(r, t):
        seen["baseline"] = (r, t)
        return 50

    def _tsm(b, d, tier):
        seen["tsm"] = (b, d, tier)
        return 9

    monkeypatch.setattr(usage, "cursor_baseline", _baseline)
    monkeypatch.setattr(usage, "time_saved_ms", _tsm)
    e = usage.build_script_event(
        script_id="x", root=root, duration_ms=5, executed=True,
    )
    assert seen["baseline"] == (root, "scripts --run x")
    assert seen["tsm"] == (50, 5, "python")
    assert e["time_saved_ms"] == 9
    # failed run with duration → exclusion suppresses time_saved_ms
    e = usage.build_script_event(
        script_id="x", root=root, duration_ms=5, executed=True,
        outcome_success=False,
    )
    assert "time_saved_ms" not in e
    assert e["savings_exclusion"] == "task_failed"


@allure.title("build_script_override_event default reason and missing root env")
def test_build_script_override_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_ROOT", raising=False)
    e = usage.build_script_override_event(
        task="t", selected_tier="cursor", previous_tier="python",
    )
    assert e["meta"]["reason"] == "manual"
    assert e["root"] == ""


@allure.title("build_compress_event env-root fallback and zero-saved clamp")
def test_build_compress_event_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_ROOT", raising=False)
    e = usage.build_compress_event(text="abc", short="abc", use_ollama=False)
    assert e["root"] == ""
    assert e["cursor_saved"] == 0  # max(0, before-after) not max(1, ...)
    seen: dict = {}
    def _tsm(b, d, tier):
        seen["tier"] = tier
        return 1

    monkeypatch.setattr(usage, "time_saved_ms", _tsm)
    usage.build_compress_event(text="abc", short="a", use_ollama=True,
                               duration_ms=3)
    assert seen["tier"] == "ollama"


@allure.title("find_prior_cheap_hit boundary and fallback semantics")
def test_find_prior_cheap_hit_edges(tmp_path: Path) -> None:
    log = tmp_path / "u.jsonl"
    now = datetime.now(UTC).replace(microsecond=0)

    def iso(dt: datetime) -> str:
        return dt.isoformat().replace("+00:00", "Z")

    # ts exactly == when → skipped (must be strictly before)
    log.write_text(json.dumps({
        "selected_tier": "python", "task_normalized": "t", "ts": iso(now),
    }) + "\n")
    assert usage.find_prior_cheap_hit(log, "t", now) is None
    # ts exactly at window edge → still inside (when - ts == window, not >)
    log.write_text(json.dumps({
        "selected_tier": "python", "task_normalized": "t",
        "ts": iso(now - timedelta(seconds=900)), "route_id": "edge",
    }) + "\n")
    hit = usage.find_prior_cheap_hit(log, "t", now, window_sec=900)
    assert hit and hit["route_id"] == "edge"
    # two hits with identical ts → the FIRST one wins (strict >)
    log.write_text(
        json.dumps({"selected_tier": "python", "task_normalized": "t",
                    "ts": iso(now - timedelta(seconds=5)), "route_id": "first"})
        + "\n"
        + json.dumps({"selected_tier": "python", "task_normalized": "t",
                      "ts": iso(now - timedelta(seconds=5)),
                      "route_id": "second"})
        + "\n"
    )
    hit = usage.find_prior_cheap_hit(log, "t", now)
    assert hit and hit["route_id"] == "first"
    # row without task fields → normalized "" never matches a real query
    log.write_text(json.dumps({
        "selected_tier": "python", "ts": iso(now - timedelta(seconds=5)),
        "route_id": "notask",
    }) + "\n")
    assert usage.find_prior_cheap_hit(log, "xxxx", now) is None
    # row without selected_tier → not a cheap hit
    log.write_text(json.dumps({
        "task_normalized": "t", "ts": iso(now - timedelta(seconds=5)),
    }) + "\n")
    assert usage.find_prior_cheap_hit(log, "t", now) is None
    # window_sec=0 → only strictly-past-in-0-window → nothing matches
    log.write_text(json.dumps({
        "selected_tier": "python", "task_normalized": "t",
        "ts": iso(now - timedelta(milliseconds=500)), "route_id": "half",
    }) + "\n")
    assert usage.find_prior_cheap_hit(log, "t", now, window_sec=0) is None


@allure.title("maybe_emit_auto_script_override forwards fields verbatim")
def test_maybe_emit_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC).replace(microsecond=0)

    def iso(dt: datetime) -> str:
        return dt.isoformat().replace("+00:00", "Z")

    prior_ts = iso(now - timedelta(seconds=60))
    log = tmp_path / "u.jsonl"
    log.write_text(json.dumps({
        "selected_tier": "python", "task_normalized": "probe task",
        "ts": prior_ts, "crystal_id": "cr-only", "root": "/prior",
    }) + "\n", encoding="utf-8")
    spy: list[tuple] = []
    monkeypatch.setattr(
        usage, "append_event",
        lambda ev, **kw: spy.append((ev, kw)),
    )
    # script_override event itself → early return, no recursion
    usage.maybe_emit_auto_script_override(
        {"event": "script_override", "selected_tier": "cursor",
         "task": "probe task", "ts": iso(now)},
        path=log,
    )
    assert spy == []
    # event without root → prior root; prior without route_id → crystal_id;
    # prior without selected_tier → "python" fallback; non-dict tags dropped;
    # operation_id generated; emit_auto_override=False forwarded
    usage.maybe_emit_auto_script_override(
        {"selected_tier": "cursor", "task": "probe task",
         "ts": iso(now), "tags": "notadict"},
        path=log,
    )
    assert len(spy) == 1
    ev, kw = spy[0]
    assert ev["previous_tier"] == "python"
    assert ev["crystal_id"] == "cr-only"
    assert ev["root"] == "/prior"
    assert "tags" not in ev
    assert ev["operation_id"]
    assert kw == {"path": log, "emit_auto_override": False}


@allure.title("append_event opens the log with pinned utf-8/LF settings")
def test_append_event_open_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "usage.jsonl"
    monkeypatch.delenv("GREEDY_TOKEN_LOG", raising=False)
    monkeypatch.delenv("GREEDY_TOKEN_TAG", raising=False)
    monkeypatch.delenv("GREEDY_TOKEN_SESSION", raising=False)
    monkeypatch.setenv("GREEDY_TOKEN_SESSION_FILE", str(tmp_path / "nope"))
    monkeypatch.setattr(usage, "_log_dir_ready", True)
    opened: list[dict] = []
    real_open = Path.open

    def spy(self: Path, *a, **kw):
        if self == target:
            opened.append({"args": a, "kwargs": kw})
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", spy)
    usage.append_event({"x": 1}, path=target, emit_auto_override=False)
    assert opened == [{"args": ("a",), "kwargs": {"encoding": "utf-8",
                                                  "newline": ""}}]


@allure.title("load_events continues past missing archive gaps")
def test_load_events_archive_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_FILES", "4")
    p = tmp_path / "u.jsonl"
    p.write_text(json.dumps({"id": "a"}) + "\n")
    (tmp_path / "u.jsonl.1").write_text(json.dumps({"id": "a1"}) + "\n")
    # .2 missing, .3 present → continue must reach .3
    (tmp_path / "u.jsonl.3").write_text(json.dumps({"id": "a3"}) + "\n")
    events, _ = usage.load_events(p)
    assert [e["id"] for e in events] == ["a", "a1", "a3"]
    # empty line before a valid row → later rows still loaded
    p.write_text("\n" + json.dumps({"id": "after-blank"}) + "\n")
    events, _ = usage.load_events(p, include_archives=False)
    assert [e["id"] for e in events] == ["after-blank"]
    # since boundary: ts == since is still included (not < since)
    edge = datetime(2026, 1, 1, tzinfo=UTC)
    p.write_text(json.dumps({"id": "edge", "ts": "2026-01-01T00:00:00Z"}) + "\n")
    events, _ = usage.load_events(p, since=edge, include_archives=False)
    assert [e["id"] for e in events] == ["edge"]
    # two malformed rows → skipped counts each (+= not =)
    p.write_text("{bad\n{bad2\n")
    _, skipped = usage.load_events(p, include_archives=False)
    assert skipped == 2


@allure.title("_dedupe_identical_events uses canonical sort_keys")
def test_dedupe_key_order() -> None:
    a = {"x": 1, "y": 2}
    b = {"y": 2, "x": 1}  # same content, different key order
    out = usage._dedupe_identical_events([a, b])
    assert len(out) == 1
    # unserializable values pass through instead of dropping the tail,
    # but an exact duplicate of one is still deduped via the str() key
    weird = {"v": Path("/x")}
    out = usage._dedupe_identical_events([{"a": 1}, weird, dict(weird), {"b": 2}])
    assert len(out) == 3


@allure.title("parse_since edge literals pin shorthand guards")
def test_parse_since_edges() -> None:
    now = datetime.now(UTC)
    # multi-digit day value
    got = usage.parse_since("70d")
    assert got is not None
    assert abs((now - got) - timedelta(days=70)) < timedelta(seconds=60)
    # bare digits are not a shorthand
    with pytest.raises(ValueError, match="Invalid --since"):
        usage.parse_since("30")
    # single digit minutes shorthand
    got = usage.parse_since("5m")
    assert got is not None
    assert abs((now - got) - timedelta(minutes=5)) < timedelta(seconds=60)
    # non-digit prefix with suffix → invalid message, not an int() crash
    with pytest.raises(ValueError, match="Invalid --since"):
        usage.parse_since("5xm")
    with pytest.raises(ValueError, match="Invalid --since"):
        usage.parse_since("5xd")
    with pytest.raises(ValueError, match="Invalid --since"):
        usage.parse_since("5xh")


@allure.title("session_id reads the session file with utf-8 and first-line split")
def test_session_id_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_SESSION", raising=False)
    sf = tmp_path / "sess"
    monkeypatch.setenv("GREEDY_TOKEN_SESSION_FILE", str(sf))
    # three lines → split("\n", 1)[0] is only the first line
    sf.write_text("first\nsecond\nthird\n", encoding="utf-8")
    assert usage.session_id() == "first"
    # first line containing spaces is kept whole
    sf.write_text("abc def\nrest", encoding="utf-8")
    assert usage.session_id() == "abc def"
    # utf-8 pinned explicitly — a latin-1-only byte must surface as read error
    seen: dict = {}
    real_read = Path.read_text

    def spy(self: Path, *a, **kw):
        if self == sf:
            seen.update(kw)
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", spy)
    usage.session_id()
    assert seen.get("encoding") == "utf-8"


@allure.title("_cap_matched boundary: exact budget fit and budget==1")
def test_cap_matched_edges() -> None:
    # entry of exactly MATCHED_MAX_CHARS fills the budget — kept whole
    exact = "e" * usage.MATCHED_MAX_CHARS
    out = usage._cap_matched([exact, "x"])
    assert out == [exact]
    # remaining budget of 1 still admits a 1-char pattern
    pats = ["a" * (usage.MATCHED_MAX_CHARS - 1), "z", "dropped"]
    out = usage._cap_matched(pats)
    assert out == ["a" * (usage.MATCHED_MAX_CHARS - 1), "z"]
    # pattern of exactly remaining budget length → not truncated
    pats = ["a" * 10, "b" * (usage.MATCHED_MAX_CHARS - 10)]
    out = usage._cap_matched(pats)
    assert out[1] == "b" * (usage.MATCHED_MAX_CHARS - 10)


@allure.title("count_operations treats non-str ids as unlabelled")
def test_count_operations_edges() -> None:
    # two records sharing a non-str id → each counts separately
    assert usage.count_operations(
        [{"operation_id": 5}, {"operation_id": 5}]
    ) == 2
    assert usage.count_operations([{"operation_id": ""}]) == 1


@allure.title("rotate boundary: exactly max bytes still rotates")
def test_rotate_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "u.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "10")
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_FILES", "1")
    p.write_text("x" * 10, encoding="utf-8")  # exactly the limit → rotate
    assert usage.rotate_log_if_needed(p) is True
    assert (tmp_path / "u.jsonl.1").read_text() == "x" * 10


@allure.title("build_tier_scan forwards root; executor forwards resolve args")
def test_forwarding_helpers(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = minimal_workspace
    seen: dict = {}
    def _rts(t, r):
        seen["rts"] = (t, r)
        return iter([])

    monkeypatch.setattr(usage, "route_task_all_tiers", _rts)
    usage.build_tier_scan("task", root)
    assert seen["rts"] == ("task", root)
    # executor: resolve_model receives ("", root, "cheap") verbatim
    import types as _t

    import greedy_token.model_select as ms

    def _rm(*a, **kw):
        seen["rm"] = (a, kw)
        return _t.SimpleNamespace(settings=_t.SimpleNamespace(model="m"),
                                  model_id="i")

    monkeypatch.setattr(ms, "resolve_model", _rm)
    dec = RouteDecision(target="ollama", route_id="r", confidence=0.5,
                        matched=[], command=None, note="", domains=[],
                        est_tokens=1)
    usage.executor_from_decision(dec, root)
    assert seen["rm"] == (("",), {"root": root, "tier_hint": "cheap"})
    # fallback path: get_ollama_settings receives the same root
    def _raise(*a: object, **k: object) -> object:
        raise ValueError("no models")

    monkeypatch.setattr(ms, "resolve_model", _raise)
    def _gos(r):
        seen["gos"] = r
        return _t.SimpleNamespace(model="m2")

    monkeypatch.setattr(usage, "get_ollama_settings", _gos)
    e = usage.executor_from_decision(dec, root)
    assert seen["gos"] is root
    assert e["model"] == "m2"


@allure.title("maybe_emit_auto_script_override fallback chains, round 2")
def test_maybe_emit_edges2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC).replace(microsecond=0)

    def iso(dt: datetime) -> str:
        return dt.isoformat().replace("+00:00", "Z")

    log = tmp_path / "u.jsonl"
    # prior cheap hit without "root" → event root wins, else "" env path
    log.write_text(json.dumps({
        "selected_tier": "ollama", "task_normalized": "probe task",
        "ts": iso(now - timedelta(seconds=60)), "route_id": "r9",
    }) + "\n", encoding="utf-8")
    spy: list[tuple] = []
    monkeypatch.setattr(
        usage, "append_event", lambda ev, **kw: spy.append((ev, kw))
    )
    # event WITH root → event root wins over prior root
    usage.maybe_emit_auto_script_override(
        {"selected_tier": "cursor", "task": "probe task",
         "ts": iso(now), "root": "/mine"},
        path=log,
    )
    ev, _ = spy[-1]
    assert ev["root"] == "/mine"
    assert ev["previous_tier"] == "ollama"
    spy.clear()
    # neither event nor prior has root → root_raw "" → root None → env default
    monkeypatch.delenv("GREEDY_TOKEN_ROOT", raising=False)
    usage.maybe_emit_auto_script_override(
        {"selected_tier": "cursor", "task": "probe task", "ts": iso(now)},
        path=log,
    )
    ev, _ = spy[-1]
    assert ev["root"] == ""
    spy.clear()
    # taskless event + a cheap hit normalized "xxxx": the ""-task guard must
    # return before lookup — a "XXXX" fallback would match and emit
    log.write_text(log.read_text() + json.dumps({
        "selected_tier": "python", "task_normalized": "xxxx",
        "ts": iso(now - timedelta(seconds=30)), "route_id": "rx",
    }) + "\n", encoding="utf-8")
    usage.maybe_emit_auto_script_override(
        {"selected_tier": "cursor", "ts": iso(now)},  # no "task" key
        path=log,
    )
    assert spy == []


@allure.title("find_prior_cheap_hit and load_events pin utf-8 encoding")
def test_encoding_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "u.jsonl"
    log.write_text(json.dumps({
        "selected_tier": "python", "task_normalized": "t",
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }) + "\n", encoding="utf-8")
    opened: list[dict] = []
    real_open = Path.open

    def spy(self: Path, *a, **kw):
        opened.append({"self": self, "kwargs": kw})
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", spy)
    usage.find_prior_cheap_hit(log, "t", datetime.now(UTC))
    usage.load_events(log)
    encodings = [o["kwargs"].get("encoding") for o in opened
                 if o["self"] == log]
    assert encodings and all(e == "utf-8" for e in encodings)


@allure.title("_parse_event_ts rejects lowercase-z (only uppercase Z replaced)")
def test_parse_event_ts_lowercase_z() -> None:
    assert usage._parse_event_ts({"ts": "2026-01-02T03:04:05z"}) is None
