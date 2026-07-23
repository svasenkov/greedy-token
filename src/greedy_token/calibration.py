"""Confidence calibration from usage telemetry (route quality).

The router's raw pattern score used to map to confidence through a fixed
pseudo-probability formula only. This module grounds that number in reality:

* **Bucket** = raw-score range (``BUCKET_BOUNDS``).
* **Actual accuracy** of a bucket = ``1 - override_rate`` — overrides are
  attributed to the most recent cheap-tier hit for the same normalized task,
  the same rule the usage quality metrics use.
* A bucket with at least ``CALIBRATION_MIN_EVENTS`` cheap-tier hits is
  **calibrated**: confidence comes from telemetry. Otherwise the formula is
  the fallback, marked ``uncalibrated`` in explain/footer output.
* **Monotonic sanity**: calibrated values are clamped to be non-decreasing
  across buckets, so a higher score never yields a lower calibrated
  confidence.
* The telemetry scan is **cached per log path** and invalidated when the
  ``usage.jsonl`` mtime/size changes — routing does not re-read the log on
  every call, yet a long-lived process (MCP server) picks up fresh telemetry
  without a restart.

Only route events that carry a positive ``raw_score`` participate (the field
is logged since this module landed); events without it are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass

CONFIDENCE_BASE = 0.45
CONFIDENCE_SLOPE = 0.12
CONFIDENCE_CAP = 0.95
# Minimum cheap-tier hits in a score bucket before telemetry wins over formula.
CALIBRATION_MIN_EVENTS = 20
# Raw-score bucket upper bounds; the last bucket is open-ended.
BUCKET_BOUNDS = (2.0, 4.0, 6.0, 8.0)

SOURCE_CALIBRATED = "calibrated"
SOURCE_FORMULA = "formula"


def formula_confidence(score: float) -> float:
    """The legacy pseudo-probability formula (fallback when uncalibrated)."""
    return min(CONFIDENCE_CAP, CONFIDENCE_BASE + score * CONFIDENCE_SLOPE)


def bucket_index(score: float) -> int:
    for i, bound in enumerate(BUCKET_BOUNDS):
        if score < bound:
            return i
    return len(BUCKET_BOUNDS)


def bucket_label(index: int) -> str:
    lo = 0.0 if index == 0 else BUCKET_BOUNDS[index - 1]
    if index >= len(BUCKET_BOUNDS):
        return f"[{lo:g}, +)"
    return f"[{lo:g}, {BUCKET_BOUNDS[index]:g})"


@dataclass
class BucketStats:
    hits: int = 0
    overrides: int = 0
    predicted_sum: float = 0.0


@dataclass(frozen=True)
class ConfidenceResult:
    confidence: float
    source: str  # SOURCE_CALIBRATED | SOURCE_FORMULA
    n: int  # cheap-tier telemetry hits in this score bucket
    bucket: str


def _empty_stats() -> tuple[BucketStats, ...]:
    return tuple(BucketStats() for _ in range(len(BUCKET_BOUNDS) + 1))


def _event_raw_score(event: dict) -> float | None:
    raw = event.get("raw_score")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if raw <= 0:
        return None
    return float(raw)


def collect_bucket_stats(events: list[dict]) -> tuple[BucketStats, ...]:
    """Per-bucket hits / overrides / formula-predicted sum from raw events.

    Events must be in chronological (file) order: an override event is
    attributed to the bucket of the most recent cheap-tier hit for the same
    normalized task seen so far.
    """
    from greedy_token.usage import CHEAP_TIERS, OVERRIDE_EVENT, normalize_task

    stats = _empty_stats()
    last_bucket_by_task: dict[str, int] = {}
    for event in events:
        task_key = normalize_task(event.get("task_normalized") or event.get("task") or "")
        if event.get("event") == OVERRIDE_EVENT:
            idx = last_bucket_by_task.get(task_key)
            if idx is not None:
                stats[idx].overrides += 1
            continue
        if event.get("selected_tier") not in CHEAP_TIERS:
            continue
        raw = _event_raw_score(event)
        if raw is None:
            continue
        idx = bucket_index(raw)
        stats[idx].hits += 1
        stats[idx].predicted_sum += formula_confidence(raw)
        if task_key:
            last_bucket_by_task[task_key] = idx
    return stats


# Per log path: (log signature at scan time, scanned stats). The signature is
# the (mtime_ns, size) of usage.jsonl — when the log grows (new telemetry) the
# cache entry is stale and the log is re-scanned, so a long-lived MCP server
# picks up fresh calibration without a restart.
_CACHE: dict[str, tuple[tuple[int, int] | None, tuple[BucketStats, ...]]] = {}


def reset_calibration_cache() -> None:
    _CACHE.clear()


def _log_signature(path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _stats_from_log() -> tuple[BucketStats, ...]:
    from greedy_token.usage import load_events, log_path, logging_enabled

    if not logging_enabled():
        return _empty_stats()
    path = log_path()
    key = str(path)
    signature = _log_signature(path)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    events, _skipped = load_events(path)
    stats = collect_bucket_stats(events)
    _CACHE[key] = (signature, stats)
    return stats


def _calibrated_values(
    stats: tuple[BucketStats, ...],
    min_events: int,
) -> list[float | None]:
    """Per-bucket calibrated confidence (None = uncalibrated), monotonic-clamped."""
    threshold = max(1, min_events)
    values: list[float | None] = []
    floor: float | None = None
    for bucket in stats:
        if bucket.hits < threshold:
            values.append(None)
            continue
        accuracy = max(0.0, 1.0 - bucket.overrides / bucket.hits)
        if floor is not None and accuracy < floor:
            accuracy = floor
        floor = accuracy
        values.append(round(accuracy, 4))
    return values


def confidence_for_score(
    score: float,
    *,
    min_events: int = CALIBRATION_MIN_EVENTS,
) -> ConfidenceResult:
    """Telemetry-calibrated confidence for a raw route score (formula fallback)."""
    stats = _stats_from_log()
    idx = bucket_index(score)
    label = bucket_label(idx)
    value = _calibrated_values(stats, min_events)[idx]
    if value is not None:
        return ConfidenceResult(
            confidence=value,
            source=SOURCE_CALIBRATED,
            n=stats[idx].hits,
            bucket=label,
        )
    return ConfidenceResult(
        confidence=formula_confidence(score),
        source=SOURCE_FORMULA,
        n=stats[idx].hits,
        bucket=label,
    )


def calibration_report(
    events: list[dict],
    *,
    min_events: int = CALIBRATION_MIN_EVENTS,
) -> list[dict]:
    """Report rows: bucket → predicted (formula) vs actual (1 − override_rate) vs n."""
    stats = collect_bucket_stats(events)
    values = _calibrated_values(stats, min_events)
    rows: list[dict] = []
    for idx, bucket in enumerate(stats):
        if bucket.hits == 0:
            continue
        rows.append(
            {
                "bucket": bucket_label(idx),
                "n": bucket.hits,
                "overrides": bucket.overrides,
                "predicted": round(bucket.predicted_sum / bucket.hits, 4),
                "actual": round(max(0.0, 1.0 - bucket.overrides / bucket.hits), 4),
                "calibrated": values[idx] is not None,
                "confidence": values[idx],
            }
        )
    return rows
