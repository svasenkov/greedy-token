"""Confidence calibration from explicit task outcomes.

An absent override is not evidence that a route was correct.  Router
confidence is therefore calibrated only from ``route_outcome`` events whose
outcome is explicitly ``success`` or ``failure``.

Calibration is segmented independently by route, tier, and language.  The
most-specific segment with enough observations wins; otherwise the legacy
score formula remains a visibly uncalibrated fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from greedy_token.calibration import (
    BUCKET_BOUNDS,
    CALIBRATION_MIN_EVENTS,
    SOURCE_FORMULA,
    bucket_index,
    bucket_label,
    formula_confidence,
)

SOURCE_OUTCOME_CALIBRATED = "outcome-calibrated"
OUTCOME_EVENT = "route_outcome"
CALIBRATABLE_OUTCOMES = frozenset({"success", "failure"})
SEGMENT_ORDER = ("route", "tier", "language", "global")


@dataclass
class OutcomeBucketStats:
    outcomes: int = 0
    successes: int = 0
    predicted_sum: float = 0.0


@dataclass(frozen=True)
class OutcomeConfidenceResult:
    confidence: float
    source: str
    n: int
    bucket: str
    segment_type: str
    segment: str


def detect_task_language(task: str) -> str:
    """Coarse benchmark/telemetry language label for RU/EN segmentation."""
    return "ru" if re.search(r"[А-Яа-яЁё]", task or "") else "en"


def _empty_stats() -> tuple[OutcomeBucketStats, ...]:
    return tuple(OutcomeBucketStats() for _ in range(len(BUCKET_BOUNDS) + 1))


def _event_raw_score(event: dict) -> float | None:
    raw = event.get("raw_score")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        return None
    return float(raw)


def _segment_value(event: dict, segment_type: str) -> str:
    if segment_type == "route":
        return str(event.get("route_id") or "")
    if segment_type == "tier":
        return str(event.get("selected_tier") or "")
    if segment_type == "language":
        explicit = str(event.get("task_language") or "").strip().lower()
        return explicit or detect_task_language(str(event.get("task") or ""))
    if segment_type == "global":
        return "all"
    raise ValueError(f"unknown calibration segment: {segment_type}")


def collect_outcome_bucket_stats(
    events: list[dict] | tuple[dict, ...],
    *,
    segment_type: str = "global",
    segment: str = "all",
) -> tuple[OutcomeBucketStats, ...]:
    """Collect explicit success/failure observations for one segment."""
    if segment_type not in SEGMENT_ORDER:
        raise ValueError(f"unknown calibration segment: {segment_type}")
    stats = _empty_stats()
    for event in events:
        if event.get("event") != OUTCOME_EVENT:
            continue
        if event.get("outcome") not in CALIBRATABLE_OUTCOMES:
            continue
        raw = _event_raw_score(event)
        if raw is None:
            continue
        if _segment_value(event, segment_type) != segment:
            continue
        bucket = stats[bucket_index(raw)]
        bucket.outcomes += 1
        bucket.successes += int(event["outcome"] == "success")
        bucket.predicted_sum += formula_confidence(raw)
    return stats


def _calibrated_values(
    stats: tuple[OutcomeBucketStats, ...],
    min_events: int,
) -> list[float | None]:
    """Outcome success rates, monotonic within a single segment."""
    threshold = max(1, min_events)
    values: list[float | None] = []
    floor: float | None = None
    for bucket in stats:
        if bucket.outcomes < threshold:
            values.append(None)
            continue
        success_rate = bucket.successes / bucket.outcomes
        if floor is not None and success_rate < floor:
            success_rate = floor
        floor = success_rate
        values.append(round(success_rate, 4))
    return values


def _candidate_segments(
    *,
    tier: str,
    language: str,
    route_id: str,
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    if route_id:
        candidates.append(("route", route_id))
    if tier:
        candidates.append(("tier", tier))
    if language:
        candidates.append(("language", language))
    candidates.append(("global", "all"))
    return candidates


# Per log path: ((mtime_ns, size) | None, explicit events).
_CACHE: dict[str, tuple[tuple[int, int] | None, tuple[dict, ...]]] = {}


def reset_outcome_calibration_cache() -> None:
    _CACHE.clear()


def _log_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _events_from_log() -> tuple[dict, ...]:
    from greedy_token.usage import load_events, log_path, logging_enabled

    if not logging_enabled():
        return ()
    path = log_path()
    key = str(path)
    signature = _log_signature(path)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    events, _skipped = load_events(path)
    explicit = tuple(event for event in events if event.get("event") == OUTCOME_EVENT)
    _CACHE[key] = (signature, explicit)
    return explicit


def confidence_for_outcome(
    score: float,
    *,
    tier: str = "",
    language: str = "",
    route_id: str = "",
    min_events: int = CALIBRATION_MIN_EVENTS,
) -> OutcomeConfidenceResult:
    """Return the most-specific sufficiently sampled outcome calibration."""
    events = _events_from_log()
    idx = bucket_index(score)
    label = bucket_label(idx)
    largest_n = 0
    for segment_type, segment in _candidate_segments(
        tier=tier,
        language=language,
        route_id=route_id,
    ):
        stats = collect_outcome_bucket_stats(
            events,
            segment_type=segment_type,
            segment=segment,
        )
        largest_n = max(largest_n, stats[idx].outcomes)
        value = _calibrated_values(stats, min_events)[idx]
        if value is not None:
            return OutcomeConfidenceResult(
                confidence=value,
                source=SOURCE_OUTCOME_CALIBRATED,
                n=stats[idx].outcomes,
                bucket=label,
                segment_type=segment_type,
                segment=segment,
            )
    return OutcomeConfidenceResult(
        confidence=formula_confidence(score),
        source=SOURCE_FORMULA,
        n=largest_n,
        bucket=label,
        segment_type="none",
        segment="",
    )


def _observed_segments(events: list[dict]) -> dict[str, set[str]]:
    observed = {name: set() for name in SEGMENT_ORDER}
    observed["global"].add("all")
    for event in events:
        if event.get("event") != OUTCOME_EVENT:
            continue
        if event.get("outcome") not in CALIBRATABLE_OUTCOMES:
            continue
        for segment_type in ("route", "tier", "language"):
            value = _segment_value(event, segment_type)
            if value:
                observed[segment_type].add(value)
    return observed


def outcome_calibration_report(
    events: list[dict],
    *,
    min_events: int = CALIBRATION_MIN_EVENTS,
) -> list[dict]:
    """Rows for global, tier, language, and route outcome calibrations."""
    rows: list[dict] = []
    observed = _observed_segments(events)
    for segment_type in SEGMENT_ORDER:
        for segment in sorted(observed[segment_type]):
            stats = collect_outcome_bucket_stats(
                events,
                segment_type=segment_type,
                segment=segment,
            )
            values = _calibrated_values(stats, min_events)
            for idx, bucket in enumerate(stats):
                if bucket.outcomes == 0:
                    continue
                observed_rate = bucket.successes / bucket.outcomes
                rows.append(
                    {
                        "segment_type": segment_type,
                        "segment": segment,
                        "bucket": bucket_label(idx),
                        "n": bucket.outcomes,
                        "successes": bucket.successes,
                        "predicted": round(
                            bucket.predicted_sum / bucket.outcomes,
                            4,
                        ),
                        "observed_success_rate": round(observed_rate, 4),
                        "calibrated": values[idx] is not None,
                        "confidence": values[idx],
                    }
                )
    return rows
