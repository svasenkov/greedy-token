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
from greedy_token.outcome_calibration import (
    SOURCE_OUTCOME_CALIBRATED,
    collect_outcome_bucket_stats,
    confidence_for_outcome,
    detect_task_language,
    outcome_calibration_report,
    reset_outcome_calibration_cache,
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
    reset_outcome_calibration_cache()
    yield
    reset_calibration_cache()
    reset_outcome_calibration_cache()


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


def _outcome(
    task: str,
    score: float,
    outcome: str,
    *,
    tier: str = "python",
    route_id: str = "python-x",
    language: str = "en",
) -> dict:
    return {
        "event": "route_outcome",
        "task": task,
        "task_language": language,
        "selected_tier": tier,
        "route_id": route_id,
        "raw_score": score,
        "outcome": outcome,
    }


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
@allure.title("Log unchanged → cached scan reused (no re-read per call)")
def test_cache_hit_when_log_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_log(_bucket_events("b1", 2.5, hits=25, overrides=5))
    assert confidence_for_score(2.5).confidence == pytest.approx(0.8)

    with allure.step("second call with the same log does not re-scan"):
        def _boom(*args, **kwargs):  # pragma: no cover - failure path
            raise AssertionError("load_events must not be called on cache hit")

        from greedy_token import usage

        monkeypatch.setattr(usage, "load_events", _boom)
        assert confidence_for_score(2.5).confidence == pytest.approx(0.8)


@allure.story("Process cache")
@allure.title("Log grows (mtime/size change) → cache invalidated without restart")
def test_cache_invalidated_on_log_growth(tmp_path: Path) -> None:
    path = _write_log(_bucket_events("b1", 2.5, hits=25, overrides=5))
    first = confidence_for_score(2.5)
    assert first.confidence == pytest.approx(0.8)

    with allure.step("log grows → fresh telemetry picked up on the next call"):
        with path.open("a", encoding="utf-8") as fh:
            for i in range(20):
                fh.write(json.dumps(_override(f"b1 task {i}")) + "\n")
        assert confidence_for_score(2.5).confidence == pytest.approx(0.0)


@allure.story("Process cache")
@allure.title("reset_calibration_cache forces a fresh scan")
def test_cache_reset(tmp_path: Path) -> None:
    _write_log(_bucket_events("b1", 2.5, hits=25, overrides=5))
    assert confidence_for_score(2.5).confidence == pytest.approx(0.8)
    reset_calibration_cache()
    assert not calibration._CACHE
    assert confidence_for_score(2.5).confidence == pytest.approx(0.8)
    assert calibration._CACHE


@allure.story("Process cache")
@allure.title("Log path unstatable → signature None, scan still works")
def test_cache_signature_missing_log(monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path(os.environ["GREEDY_TOKEN_LOG"])
    if path.exists():
        path.unlink()
    result = confidence_for_score(2.5)
    assert result.source == SOURCE_FORMULA
    assert calibration._log_signature(path) is None


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
@allure.title("calibration_report exposes override/hold, never correctness")
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
                "observed_hold_rate": pytest.approx(0.8),
            "calibrated": True,
                "hold_confidence": pytest.approx(0.8),
        },
        {
            "bucket": "[4, 6)",
            "n": 3,
            "overrides": 0,
            "predicted": pytest.approx(0.95),
                "observed_hold_rate": pytest.approx(1.0),
            "calibrated": False,
                "hold_confidence": None,
        },
    ]
    with allure.step("no events → no rows"):
        assert calibration_report([]) == []


# --- Explicit outcome calibration ------------------------------------------


@allure.story("Outcome language")
@allure.title("Outcome language segmentation recognises RU and defaults to EN")
def test_detect_task_language() -> None:
    assert detect_task_language("проверь manifest") == "ru"
    assert detect_task_language("check manifest") == "en"
    assert detect_task_language("") == "en"


@allure.story("Outcome telemetry")
@allure.title("Only explicit success/failure events enter correctness calibration")
def test_collect_outcome_bucket_stats_filters_non_outcomes() -> None:
    events = [
        _hit("route only", 2.5),
        _override("route only"),
        _outcome("ok", 2.5, "success"),
        _outcome("bad", 2.5, "failure"),
        _outcome("unknown", 2.5, "unknown"),
        {**_outcome("bool", 2.5, "success"), "raw_score": True},
        {**_outcome("string", 2.5, "success"), "raw_score": "2.5"},
        {**_outcome("zero", 2.5, "success"), "raw_score": 0},
        _outcome("other route", 2.5, "success", route_id="other"),
    ]
    stats = collect_outcome_bucket_stats(
        events,
        segment_type="route",
        segment="python-x",
    )
    assert stats[1].outcomes == 2
    assert stats[1].successes == 1
    assert stats[1].predicted_sum == pytest.approx(
        2 * formula_confidence(2.5)
    )
    with pytest.raises(ValueError, match="unknown calibration segment"):
        collect_outcome_bucket_stats(events, segment_type="project")


@allure.story("Outcome telemetry")
@allure.title("Outcome segments support tier, language fallback, and global")
def test_collect_outcome_bucket_stats_segments() -> None:
    from greedy_token import outcome_calibration

    event = _outcome("проверь", 5.0, "success", language="")
    assert collect_outcome_bucket_stats(
        [event], segment_type="tier", segment="python"
    )[2].outcomes == 1
    assert collect_outcome_bucket_stats(
        [event], segment_type="language", segment="ru"
    )[2].outcomes == 1
    assert collect_outcome_bucket_stats(
        [event], segment_type="global", segment="all"
    )[2].outcomes == 1
    with pytest.raises(ValueError, match="unknown calibration segment"):
        outcome_calibration._segment_value(event, "project")

    sparse_dimensions = {
        **_outcome("documented task", 2.5, "success"),
        "route_id": "",
        "selected_tier": "",
    }
    rows = outcome_calibration_report([sparse_dimensions], min_events=1)
    assert {row["segment_type"] for row in rows} == {"language", "global"}


def _outcome_series(
    count: int,
    successes: int,
    *,
    score: float = 2.5,
    tier: str = "python",
    route_id: str = "python-x",
    language: str = "en",
) -> list[dict]:
    return [
        _outcome(
            f"{route_id}-{language}-{i}",
            score,
            "success" if i < successes else "failure",
            tier=tier,
            route_id=route_id,
            language=language,
        )
        for i in range(count)
    ]


@allure.story("Outcome segmentation")
@allure.title("Most-specific sufficiently sampled segment wins")
def test_confidence_for_outcome_segment_precedence() -> None:
    events = _outcome_series(20, 12, route_id="route-a")
    events += _outcome_series(20, 18, route_id="route-b")
    _write_log(events)

    route = confidence_for_outcome(
        2.5,
        tier="python",
        language="en",
        route_id="route-a",
    )
    assert route.source == SOURCE_OUTCOME_CALIBRATED
    assert route.confidence == pytest.approx(0.6)
    assert (route.segment_type, route.segment, route.n) == (
        "route",
        "route-a",
        20,
    )

    tier = confidence_for_outcome(
        2.5,
        tier="python",
        language="en",
        route_id="unseen",
    )
    assert tier.confidence == pytest.approx(0.75)
    assert (tier.segment_type, tier.segment, tier.n) == (
        "tier",
        "python",
        40,
    )


@allure.story("Outcome segmentation")
@allure.title("Language then global segments backstop sparse route and tier")
def test_confidence_for_outcome_language_and_global_fallback() -> None:
    events = _outcome_series(
        20,
        16,
        tier="python",
        route_id="py-a",
        language="ru",
    )
    events += _outcome_series(
        20,
        10,
        tier="rag",
        route_id="rag-a",
        language="en",
    )
    _write_log(events)

    language = confidence_for_outcome(
        2.5,
        tier="tool",
        language="ru",
        route_id="missing",
    )
    assert language.confidence == pytest.approx(0.8)
    assert language.segment_type == "language"

    global_result = confidence_for_outcome(
        2.5,
        tier="tool",
        language="de",
        route_id="missing",
    )
    assert global_result.confidence == pytest.approx(0.65)
    assert global_result.segment_type == "global"


@allure.story("Outcome formula fallback")
@allure.title("Sparse explicit outcomes stay formula-labelled and report n")
def test_confidence_for_outcome_sparse_fallback() -> None:
    _write_log(_outcome_series(3, 3))
    result = confidence_for_outcome(
        2.5,
        tier="python",
        language="en",
        route_id="python-x",
    )
    assert result.source == SOURCE_FORMULA
    assert result.confidence == pytest.approx(formula_confidence(2.5))
    assert result.n == 3
    assert result.segment_type == "none"
    assert result.segment == ""

    tuned = confidence_for_outcome(
        2.5,
        tier="python",
        route_id="python-x",
        min_events=0,
    )
    assert tuned.source == SOURCE_OUTCOME_CALIBRATED
    assert tuned.confidence == 1.0

    no_tier = confidence_for_outcome(
        2.5,
        tier="",
        language="en",
        route_id="missing",
    )
    assert no_tier.source == SOURCE_FORMULA


@allure.story("Outcome monotonicity")
@allure.title("Outcome confidence is monotonic inside each segment")
def test_outcome_calibration_monotonic_clamp() -> None:
    events = _outcome_series(20, 18, score=1.0)
    events += _outcome_series(20, 8, score=3.0)
    _write_log(events)
    low = confidence_for_outcome(
        1.0, tier="python", language="en", route_id="python-x"
    )
    high = confidence_for_outcome(
        3.0, tier="python", language="en", route_id="python-x"
    )
    assert low.confidence == pytest.approx(0.9)
    assert high.confidence == pytest.approx(0.9)


@allure.story("Outcome report")
@allure.title("Outcome report calibrates each dimension only at sufficient n")
def test_outcome_calibration_report_segments() -> None:
    events = _outcome_series(20, 15)
    events.append(_outcome("ignored escalation", 2.5, "escalated"))
    rows = outcome_calibration_report(events)
    assert {row["segment_type"] for row in rows} == {
        "route",
        "tier",
        "language",
        "global",
    }
    assert all(row["calibrated"] for row in rows)
    assert all(row["observed_success_rate"] == 0.75 for row in rows)
    assert outcome_calibration_report([]) == []


@allure.story("Outcome cache")
@allure.title("Outcome cache invalidates when the telemetry log grows")
def test_outcome_calibration_cache_invalidation() -> None:
    path = _write_log(_outcome_series(20, 20))
    first = confidence_for_outcome(
        2.5, tier="python", language="en", route_id="python-x"
    )
    assert first.confidence == 1.0
    with path.open("a", encoding="utf-8") as fh:
        for event in _outcome_series(20, 0):
            fh.write(json.dumps(event) + "\n")
    second = confidence_for_outcome(
        2.5, tier="python", language="en", route_id="python-x"
    )
    assert second.confidence == pytest.approx(0.5)
    reset_outcome_calibration_cache()
    assert not __import__(
        "greedy_token.outcome_calibration",
        fromlist=["_CACHE"],
    )._CACHE


@allure.story("Outcome cache")
@allure.title("Disabled telemetry and a missing log keep outcome confidence formula-only")
def test_outcome_calibration_disabled_and_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", "0")
    disabled = confidence_for_outcome(2.5, tier="python")
    assert disabled.source == SOURCE_FORMULA
    reset_outcome_calibration_cache()
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(Path("/definitely/missing/log.jsonl")))
    missing = confidence_for_outcome(2.5, tier="python")
    assert missing.source == SOURCE_FORMULA


# --- Router integration -----------------------------------------------------


@allure.story("Router integration")
@allure.title("_decision_from_route calibrates only from explicit outcomes")
def test_decision_from_route_calibrated(minimal_workspace: Path) -> None:
    from greedy_token.router import _decision_from_route

    _write_log(
        [
            _outcome(
                f"b1 task {i}",
                2.5,
                "success" if i < 20 else "failure",
            )
            for i in range(25)
        ]
    )
    route = {"id": "python-x", "target": "python", "patterns": ["x"]}
    dec = _decision_from_route(
        route, score=3.0, matched=["x"], task="do x", root=minimal_workspace
    )
    assert dec.confidence == pytest.approx(0.8)
    assert dec.confidence_source == SOURCE_OUTCOME_CALIBRATED
    assert dec.calibration_n == 25
    assert dec.calibration_segment == "route:python-x"
    assert dec.raw_score == 3.0


@allure.story("Router integration")
@allure.title("confidence_label names explicit-outcome provenance")
def test_confidence_label() -> None:
    from greedy_token.router import confidence_label

    base = dict(
        target="python", route_id="r", confidence=0.8, matched=[], command=None,
        note="", domains=[],
    )
    calibrated = RouteDecision(
        **base,
        confidence_source=SOURCE_OUTCOME_CALIBRATED,
        calibration_n=42,
        calibration_segment="tier:python",
    )
    assert confidence_label(calibrated) == (
        "outcome-calibrated (n=42, tier:python)"
    )
    formula = RouteDecision(**base)
    assert confidence_label(formula) == (
        "formula (uncalibrated; explicit outcome n=0)"
    )


@allure.story("Router integration")
@allure.title("explain_route exposes confidence, source, and calibration_n")
def test_explain_route_confidence_fields(minimal_workspace: Path) -> None:
    from greedy_token.router import explain_route

    decision = RouteDecision(
        target="python", route_id="r", confidence=0.812345, matched=["x"], command=None,
        note="", domains=[], confidence_source=SOURCE_OUTCOME_CALIBRATED,
        calibration_n=25, calibration_segment="tier:python",
    )
    exp = explain_route(decision, "do x", minimal_workspace)
    attach_json("explain", exp)
    # exact 4-digit rounding (kills the round(…, 4) → round(…, 5) mutant)
    assert exp["confidence"] == 0.8123
    assert exp["confidence_source"] == SOURCE_OUTCOME_CALIBRATED
    assert exp["calibration_n"] == 25
    assert exp["calibration_segment"] == "tier:python"


@allure.story("Router integration")
@allure.title("format_decision and format_estimate print the confidence provenance")
def test_format_outputs_show_confidence_source(minimal_workspace: Path) -> None:
    from greedy_token.estimator import estimate_task, format_estimate
    from greedy_token.router import _decision_from_route, format_decision

    with allure.step("explicit outcomes → outcome-calibrated route output"):
        _write_log(
            [
                _outcome(
                    f"b1 task {i}",
                    2.5,
                    "success" if i < 20 else "failure",
                )
                for i in range(25)
            ]
        )
        route = {"id": "python-x", "target": "python", "patterns": ["x"]}
        dec = _decision_from_route(
            route, score=3.0, matched=["x"], task="do x", root=minimal_workspace
        )
        out = format_decision(dec, "do x", minimal_workspace)
        attach_text("route output", out)
        assert (
            "Confidence: 80% — outcome-calibrated "
            "(n=25, route:python-x)"
        ) in out

    with allure.step("no telemetry → estimate marks the formula as uncalibrated"):
        reset_calibration_cache()
        _write_log([])
        estimate = estimate_task("find baseUrl in configurator", minimal_workspace)
        est_out = format_estimate(estimate, "find baseUrl in configurator", minimal_workspace)
        attach_text("estimate output", est_out)
        assert "— formula (uncalibrated; explicit outcome n=0)" in est_out


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
        **base,
        raw_score=2.5,
        confidence_source=SOURCE_OUTCOME_CALIBRATED,
        calibration_n=25,
        calibration_segment="route:r",
    )
    event = build_route_event(
        cmd="route", task="do x", root=minimal_workspace, decision=scored, tier_scan=[]
    )
    assert event["raw_score"] == 2.5
    assert event["confidence_source"] == SOURCE_OUTCOME_CALIBRATED
    assert event["calibration_segment"] == "route:r"

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
@allure.title("report renders explicit outcome calibration separately")
def test_report_calibration_block() -> None:
    from greedy_token.usage import aggregate_events, format_report

    events = [
        _outcome(
            f"big {i}",
            2.5,
            "success" if i < 20 else "failure",
        )
        for i in range(25)
    ]
    events.extend(_outcome(f"small {i}", 5.0, "success") for i in range(3))
    summary = aggregate_events(events, since_label="7d")
    text = format_report(summary)
    attach_text("report", text)
    assert (
        "Outcome confidence calibration (explicit success/failure; "
        f"min n={CALIBRATION_MIN_EVENTS}):"
    ) in text
    assert "[2, 4)" in text and "calibrated" in text
    assert f"uncalibrated (n<{CALIBRATION_MIN_EVENTS})" in text

    with allure.step("JSON report carries segmented outcome rows"):
        payload = summary.to_dict()
        rows = payload["quality"]["outcome_calibration"]
        attach_json("quality.outcome_calibration", rows)
        assert {r["segment_type"] for r in rows} == {
            "global",
            "tier",
            "language",
            "route",
        }
        assert {r["bucket"] for r in rows} == {"[2, 4)", "[4, 6)"}

    with allure.step("no scored events → block absent"):
        empty = aggregate_events([_hit("legacy", 0)], since_label="7d")
        assert "Outcome confidence calibration" not in format_report(empty)
