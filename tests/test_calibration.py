"""Confidence calibration: telemetry buckets, formula fallback, monotonic clamp."""

from __future__ import annotations

import json
import os
from pathlib import Path

import allure
import pytest

from greedy_token import calibration
from greedy_token.calibration import (
    BUCKET_BOUNDS,
    CALIBRATION_MIN_EVENTS,
    SOURCE_CALIBRATED,
    SOURCE_FORMULA,
    bucket_index,
    bucket_label,
    calibration_report,
    collect_bucket_stats,
    confidence_for_score,
    formula_confidence,
    reset_calibration_cache,
)
from greedy_token.router import RouteDecision
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Router"),
    allure.parent_suite("Router"),
    allure.feature("Confidence calibration"),
    allure.suite("Confidence calibration"),
]


@pytest.fixture(autouse=True)
def _fresh_calibration_cache():
    reset_calibration_cache()
    yield
    reset_calibration_cache()


def _hit(task: str, score: float, *, tier: str = "python", route_id: str = "r1") -> dict:
    return {
        "v": 2,
        "cmd": "route",
        "task": task,
        "selected_tier": tier,
        "route_id": route_id,
        "raw_score": score,
    }


def _override(task: str) -> dict:
    return {"event": "script_override", "cmd": "override", "task": task}


def _bucket_events(tag: str, score: float, hits: int, overrides: int) -> list[dict]:
    """`hits` cheap hits at `score` (unique tasks), then `overrides` re-asks."""
    events = [_hit(f"{tag} task {i}", score) for i in range(hits)]
    events.extend(_override(f"{tag} task {i}") for i in range(overrides))
    return events


def _write_log(events: list[dict]) -> Path:
    path = Path(os.environ["GREEDY_TOKEN_LOG"])
    path.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
        encoding="utf-8",
    )
    return path


@allure.story("Formula fallback")
@allure.title("formula_confidence keeps the legacy min(0.95, 0.45 + score*0.12)")
def test_formula_confidence() -> None:
    assert formula_confidence(0.0) == pytest.approx(0.45)
    assert formula_confidence(1.0) == pytest.approx(0.57)
    assert formula_confidence(4.0) == pytest.approx(0.93)
    with allure.step("cap at 0.95"):
        assert formula_confidence(10.0) == 0.95


@allure.story("Score buckets")
@allure.title("bucket_index maps score ranges; last bucket is open-ended")
def test_bucket_index_boundaries() -> None:
    assert bucket_index(0.0) == 0
    assert bucket_index(1.99) == 0
    assert bucket_index(2.0) == 1
    assert bucket_index(3.9) == 1
    assert bucket_index(4.0) == 2
    assert bucket_index(6.0) == 3
    assert bucket_index(8.0) == len(BUCKET_BOUNDS)
    assert bucket_index(100.0) == len(BUCKET_BOUNDS)


@allure.story("Score buckets")
@allure.title("bucket_label renders [lo, hi) ranges and the open tail")
def test_bucket_label() -> None:
    assert bucket_label(0) == "[0, 2)"
    assert bucket_label(1) == "[2, 4)"
    assert bucket_label(len(BUCKET_BOUNDS)) == "[8, +)"


@allure.story("Telemetry scan")
@allure.title("collect_bucket_stats counts hits, predicted sum, and attributed overrides")
def test_collect_bucket_stats_attribution() -> None:
    events = [
        _hit("alpha", 2.5),
        _hit("beta", 2.5),
        _override("alpha"),  # attributed to bucket [2, 4)
        _override("unknown task"),  # no prior hit → ignored
        _hit("gamma", 9.0),
    ]
    stats = collect_bucket_stats(events)
    assert stats[1].hits == 2
    assert stats[1].overrides == 1
    assert stats[1].predicted_sum == pytest.approx(2 * formula_confidence(2.5))
    with allure.step("open-tail bucket got the score-9 hit"):
        assert stats[-1].hits == 1
        assert stats[-1].overrides == 0


@allure.story("Telemetry scan")
@allure.title("collect_bucket_stats prefers task_normalized and skips empty task keys")
def test_collect_bucket_stats_task_keys() -> None:
    events = [
        {**_hit("Display Name", 2.5), "task_normalized": "canon key"},
        {"event": "script_override", "task": "other", "task_normalized": "canon key"},
        _hit("", 2.5),  # counted as a hit but never remembered for attribution
        _override(""),  # empty key → never attributed
    ]
    stats = collect_bucket_stats(events)
    assert stats[1].hits == 2
    assert stats[1].overrides == 1


@allure.story("Telemetry scan")
@allure.title("collect_bucket_stats ignores non-cheap tiers and bad raw_score values")
def test_collect_bucket_stats_skips() -> None:
    events = [
        _hit("cursor task", 2.5, tier="cursor"),  # not a cheap tier
        {**_hit("no score", 2.5), "raw_score": None},
        {**_hit("str score", 2.5), "raw_score": "big"},
        {**_hit("bool score", 2.5), "raw_score": True},
        {**_hit("zero score", 2.5), "raw_score": 0},
        {**_hit("negative", 2.5), "raw_score": -3.0},
        {k: v for k, v in _hit("legacy", 2.5).items() if k != "raw_score"},
    ]
    stats = collect_bucket_stats(events)
    assert all(b.hits == 0 and b.overrides == 0 for b in stats)
    with allure.step("int raw_score is accepted"):
        stats2 = collect_bucket_stats([{**_hit("int", 2.5), "raw_score": 3}])
        assert stats2[1].hits == 1


@allure.story("Calibrated confidence")
@allure.title("Bucket with >= min events: confidence = 1 - override_rate (calibrated)")
def test_confidence_calibrated_bucket() -> None:
    _write_log(_bucket_events("b1", 2.5, hits=25, overrides=5))
    result = confidence_for_score(3.0)
    attach_json("result", result.__dict__)
    assert result.source == SOURCE_CALIBRATED
    assert result.confidence == pytest.approx(0.8)
    assert result.n == 25
    assert result.bucket == "[2, 4)"


@allure.story("Formula fallback")
@allure.title("Bucket below the threshold falls back to the formula (uncalibrated)")
def test_confidence_fallback_insufficient_data() -> None:
    _write_log(_bucket_events("b1", 2.5, hits=CALIBRATION_MIN_EVENTS - 1, overrides=0))
    result = confidence_for_score(2.5)
    assert result.source == SOURCE_FORMULA
    assert result.confidence == pytest.approx(formula_confidence(2.5))
    assert result.n == CALIBRATION_MIN_EVENTS - 1
    with allure.step("empty log → formula too"):
        reset_calibration_cache()
        _write_log([])
        empty = confidence_for_score(2.5)
        assert empty.source == SOURCE_FORMULA
        assert empty.n == 0


@allure.story("Calibrated confidence")
@allure.title("min_events is a parameter (threshold floors at 1)")
def test_confidence_min_events_param() -> None:
    _write_log(_bucket_events("b1", 2.5, hits=2, overrides=1))
    assert confidence_for_score(2.5).source == SOURCE_FORMULA
    tuned = confidence_for_score(2.5, min_events=0)
    assert tuned.source == SOURCE_CALIBRATED
    assert tuned.confidence == pytest.approx(0.5)


@allure.story("Calibrated confidence")
@allure.title("Override count above hits clamps accuracy at 0.0")
def test_confidence_accuracy_floor_zero() -> None:
    events = _bucket_events("b1", 2.5, hits=20, overrides=0)
    # 25 overrides against 20 hits (re-asking the same task repeatedly).
    events.extend(_override("b1 task 0") for _ in range(25))
    _write_log(events)
    result = confidence_for_score(2.5)
    assert result.source == SOURCE_CALIBRATED
    assert result.confidence == 0.0


@allure.story("Monotonic sanity")
@allure.title("Higher score never yields a lower calibrated confidence (clamp)")
def test_monotonic_clamp() -> None:
    events = (
        _bucket_events("b0", 1.0, hits=20, overrides=2)  # accuracy 0.9
        + _bucket_events("b1", 3.0, hits=20, overrides=10)  # raw 0.5 → clamped 0.9
        + _bucket_events("b2", 5.0, hits=20, overrides=0)  # 1.0, no clamp needed
    )
    _write_log(events)
    with allure.step("violating bucket is clamped to the lower-bucket value"):
        assert confidence_for_score(3.0).confidence == pytest.approx(0.9)
    with allure.step("sweep: calibrated confidence is non-decreasing in score"):
        sweep = [confidence_for_score(s) for s in (0.5, 1.5, 2.5, 3.5, 4.5, 5.9)]
        attach_text("sweep", "\n".join(f"{r.bucket} {r.confidence}" for r in sweep))
        calibrated = [r.confidence for r in sweep if r.source == SOURCE_CALIBRATED]
        assert calibrated == sorted(calibrated)
        assert calibrated[-1] == pytest.approx(1.0)


@allure.story("Process cache")
@allure.title("Telemetry scan is cached per process; reset re-reads the log")
def test_cache_and_reset(tmp_path: Path) -> None:
    path = _write_log(_bucket_events("b1", 2.5, hits=25, overrides=5))
    first = confidence_for_score(2.5)
    assert first.confidence == pytest.approx(0.8)

    with allure.step("log grows → cached result unchanged"):
        with path.open("a", encoding="utf-8") as fh:
            for event in _bucket_events("b2", 2.5, hits=0, overrides=20):
                fh.write(json.dumps(event) + "\n")
            for i in range(20):
                fh.write(json.dumps(_override(f"b1 task {i}")) + "\n")
        assert confidence_for_score(2.5).confidence == pytest.approx(0.8)

    with allure.step("reset_calibration_cache → fresh scan sees the overrides"):
        reset_calibration_cache()
        assert confidence_for_score(2.5).confidence == pytest.approx(0.0)


@allure.story("Process cache")
@allure.title("Telemetry disabled (GREEDY_TOKEN_LOG=0) → formula, nothing cached")
def test_logging_disabled_uses_formula(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_log(_bucket_events("b1", 2.5, hits=25, overrides=5))
    monkeypatch.setenv("GREEDY_TOKEN_LOG", "0")
    result = confidence_for_score(2.5)
    assert result.source == SOURCE_FORMULA
    assert result.n == 0
    assert not calibration._CACHE


@allure.story("Report block")
@allure.title("calibration_report: bucket → predicted vs actual vs n")
def test_calibration_report_rows() -> None:
    events = _bucket_events("big", 2.5, hits=25, overrides=5) + _bucket_events(
        "small", 5.0, hits=3, overrides=0
    )
    rows = calibration_report(events)
    attach_json("rows", rows)
    assert rows == [
        {
            "bucket": "[2, 4)",
            "n": 25,
            "overrides": 5,
            "predicted": pytest.approx(formula_confidence(2.5)),
            "actual": pytest.approx(0.8),
            "calibrated": True,
            "confidence": pytest.approx(0.8),
        },
        {
            "bucket": "[4, 6)",
            "n": 3,
            "overrides": 0,
            "predicted": pytest.approx(0.95),
            "actual": pytest.approx(1.0),
            "calibrated": False,
            "confidence": None,
        },
    ]
    with allure.step("no events → no rows"):
        assert calibration_report([]) == []


# --- Router integration -----------------------------------------------------


@allure.story("Router integration")
@allure.title("_decision_from_route uses calibrated confidence and records provenance")
def test_decision_from_route_calibrated(minimal_workspace: Path) -> None:
    from greedy_token.router import _decision_from_route

    _write_log(_bucket_events("b1", 2.5, hits=25, overrides=5))
    route = {"id": "python-x", "target": "python", "patterns": ["x"]}
    dec = _decision_from_route(
        route, score=3.0, matched=["x"], task="do x", root=minimal_workspace
    )
    assert dec.confidence == pytest.approx(0.8)
    assert dec.confidence_source == SOURCE_CALIBRATED
    assert dec.calibration_n == 25
    assert dec.raw_score == 3.0


@allure.story("Router integration")
@allure.title("confidence_label: calibrated (n=…) vs formula (uncalibrated)")
def test_confidence_label() -> None:
    from greedy_token.router import confidence_label

    base = dict(
        target="python", route_id="r", confidence=0.8, matched=[], command=None,
        note="", domains=[],
    )
    calibrated = RouteDecision(**base, confidence_source=SOURCE_CALIBRATED, calibration_n=42)
    assert confidence_label(calibrated) == "calibrated (n=42)"
    formula = RouteDecision(**base)
    assert confidence_label(formula) == "formula (uncalibrated)"


@allure.story("Router integration")
@allure.title("explain_route exposes confidence, source, and calibration_n")
def test_explain_route_confidence_fields(minimal_workspace: Path) -> None:
    from greedy_token.router import explain_route

    decision = RouteDecision(
        target="python", route_id="r", confidence=0.812345, matched=["x"], command=None,
        note="", domains=[], confidence_source=SOURCE_CALIBRATED, calibration_n=25,
    )
    exp = explain_route(decision, "do x", minimal_workspace)
    attach_json("explain", exp)
    # exact 4-digit rounding (kills the round(…, 4) → round(…, 5) mutant)
    assert exp["confidence"] == 0.8123
    assert exp["confidence_source"] == SOURCE_CALIBRATED
    assert exp["calibration_n"] == 25


@allure.story("Router integration")
@allure.title("format_decision and format_estimate print the confidence provenance")
def test_format_outputs_show_confidence_source(minimal_workspace: Path) -> None:
    from greedy_token.estimator import estimate_task, format_estimate
    from greedy_token.router import _decision_from_route, format_decision

    with allure.step("calibrated decision → calibrated (n=…) in route output"):
        _write_log(_bucket_events("b1", 2.5, hits=25, overrides=5))
        route = {"id": "python-x", "target": "python", "patterns": ["x"]}
        dec = _decision_from_route(
            route, score=3.0, matched=["x"], task="do x", root=minimal_workspace
        )
        out = format_decision(dec, "do x", minimal_workspace)
        attach_text("route output", out)
        assert "Confidence: 80% — calibrated (n=25)" in out

    with allure.step("no telemetry → estimate marks the formula as uncalibrated"):
        reset_calibration_cache()
        _write_log([])
        estimate = estimate_task("find baseUrl in configurator", minimal_workspace)
        est_out = format_estimate(estimate, "find baseUrl in configurator", minimal_workspace)
        attach_text("estimate output", est_out)
        assert "— formula (uncalibrated)" in est_out


# --- Usage / report integration ---------------------------------------------


@allure.story("Telemetry logging")
@allure.title("build_route_event logs raw_score + confidence_source for scored routes")
def test_build_route_event_raw_score(minimal_workspace: Path) -> None:
    from greedy_token.usage import build_route_event

    base = dict(
        target="python", route_id="r", confidence=0.8, matched=["x"], command=None,
        note="", domains=[],
    )
    scored = RouteDecision(
        **base, raw_score=2.5, confidence_source=SOURCE_CALIBRATED, calibration_n=25
    )
    event = build_route_event(
        cmd="route", task="do x", root=minimal_workspace, decision=scored, tier_scan=[]
    )
    assert event["raw_score"] == 2.5
    assert event["confidence_source"] == SOURCE_CALIBRATED

    with allure.step("no raw score (fallback decision) → fields absent"):
        plain = build_route_event(
            cmd="route",
            task="do x",
            root=minimal_workspace,
            decision=RouteDecision(**base),
            tier_scan=[],
        )
        assert "raw_score" not in plain
        assert "confidence_source" not in plain


@allure.story("Report block")
@allure.title("report renders the calibration block (text + JSON)")
def test_report_calibration_block() -> None:
    from greedy_token.usage import aggregate_events, format_report

    events = _bucket_events("big", 2.5, hits=25, overrides=5) + _bucket_events(
        "small", 5.0, hits=3, overrides=0
    )
    summary = aggregate_events(events, since_label="7d")
    text = format_report(summary)
    attach_text("report", text)
    assert f"Confidence calibration (score buckets, min n={CALIBRATION_MIN_EVENTS}):" in text
    assert "[2, 4)" in text and "calibrated" in text
    assert f"uncalibrated (n<{CALIBRATION_MIN_EVENTS})" in text

    with allure.step("JSON report carries quality.calibration rows"):
        payload = summary.to_dict()
        attach_json("quality.calibration", payload["quality"]["calibration"])
        assert [r["bucket"] for r in payload["quality"]["calibration"]] == ["[2, 4)", "[4, 6)"]

    with allure.step("no scored events → block absent"):
        empty = aggregate_events([_hit("legacy", 0)], since_label="7d")
        assert "Confidence calibration" not in format_report(empty)
