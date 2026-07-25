"""Naive agent-chat baseline overhead: calibrated (user config) → default-estimate.

Footer savings are **estimates** against a baseline of what a naive agent chat
would cost for the same task:

    baseline = always-on rules (measured) + task prompt (measured) + agent overhead

The agent overhead (system prompt, tool schemas, agent reply) is not directly
observable from the CLI, so its source is resolved with this priority:

1. ``baseline:`` section in ``~/.greedy-token/config.yaml`` — written by
   ``greedy-token calibrate``; labelled ``measured`` when calibrated from a
   captured agent-context dump (``--from-file``), ``calibrated`` when supplied
   explicitly (``--overhead N``);
2. ``BASE_CURSOR_OVERHEAD`` constant — labelled ``default-estimate``.

Wall-clock savings use the same provenance model:

    naive_agent_ms = overhead_ms + baseline_tokens × ms_per_1k_tokens / 1000
    time_saved_ms  = max(0, naive_agent_ms − duration_ms)   # cheap tiers only

``overhead_ms`` / ``ms_per_1k_tokens`` come from the same ``baseline:`` section
when present; otherwise built-in defaults (also labelled ``default-estimate``).

Every footer that prints a “Saved” figure marks it with the resolved source so
the number is never presented as a measurement when it is an estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from greedy_token import settings

# Default-estimate fallback for the agent-chat overhead (system prompt + tool
# schemas + agent reply) when no calibration is stored in the user config.
BASE_CURSOR_OVERHEAD = 6000

# Default-estimate wall-clock for a naive agent turn on the same task:
# fixed turn cost (TTFT + 1–2 tool RTTs + reply) + scale with context size.
BASE_AGENT_OVERHEAD_MS = 12_000
MS_PER_1K_BASELINE_TOKENS = 800

SOURCE_MEASURED = "measured"
SOURCE_CALIBRATED = "calibrated"
SOURCE_DEFAULT = "default-estimate"

METHOD_MEASURED = "measured"
METHOD_MANUAL = "manual"


@dataclass(frozen=True)
class BaselineSettings:
    overhead_tokens: int
    source: str  # measured | calibrated | default-estimate
    calibrated_at: str = ""
    method: str = ""  # measured | manual (calibrated sources only)


@dataclass(frozen=True)
class TimeBaselineSettings:
    overhead_ms: int
    ms_per_1k_tokens: int
    source: str  # measured | calibrated | default-estimate
    calibrated_at: str = ""
    method: str = ""


def get_baseline_settings() -> BaselineSettings:
    """Resolve the agent-overhead baseline: user config → default-estimate."""
    cfg = settings._read_yaml(settings.user_config_path())
    section = cfg.get("baseline")
    if not isinstance(section, dict):
        return BaselineSettings(overhead_tokens=BASE_CURSOR_OVERHEAD, source=SOURCE_DEFAULT)
    try:
        overhead = int(section.get("overhead_tokens"))
    except (TypeError, ValueError):
        return BaselineSettings(overhead_tokens=BASE_CURSOR_OVERHEAD, source=SOURCE_DEFAULT)
    if overhead <= 0:
        return BaselineSettings(overhead_tokens=BASE_CURSOR_OVERHEAD, source=SOURCE_DEFAULT)
    method = str(section.get("method") or METHOD_MANUAL).strip()
    source = SOURCE_MEASURED if method == METHOD_MEASURED else SOURCE_CALIBRATED
    return BaselineSettings(
        overhead_tokens=overhead,
        source=source,
        calibrated_at=str(section.get("calibrated_at") or ""),
        method=method,
    )


def get_time_baseline_settings() -> TimeBaselineSettings:
    """Resolve naive-agent wall-clock knobs: user config → default-estimate."""
    cfg = settings._read_yaml(settings.user_config_path())
    section = cfg.get("baseline")
    if not isinstance(section, dict):
        return TimeBaselineSettings(
            overhead_ms=BASE_AGENT_OVERHEAD_MS,
            ms_per_1k_tokens=MS_PER_1K_BASELINE_TOKENS,
            source=SOURCE_DEFAULT,
        )

    overhead_ms = BASE_AGENT_OVERHEAD_MS
    ms_per_1k = MS_PER_1K_BASELINE_TOKENS
    source = SOURCE_DEFAULT
    calibrated_at = ""
    method = ""

    raw_ms = section.get("overhead_ms")
    if raw_ms is not None:
        try:
            parsed = int(raw_ms)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            overhead_ms = parsed
            method = str(section.get("time_method") or section.get("method") or METHOD_MANUAL).strip()
            source = SOURCE_MEASURED if method == METHOD_MEASURED else SOURCE_CALIBRATED
            calibrated_at = str(
                section.get("time_calibrated_at") or section.get("calibrated_at") or ""
            )

    raw_rate = section.get("ms_per_1k_tokens")
    if raw_rate is not None:
        try:
            parsed_rate = int(raw_rate)
        except (TypeError, ValueError):
            parsed_rate = 0
        if parsed_rate > 0:
            ms_per_1k = parsed_rate
            # A rate-only override still counts as calibrated time knobs.
            if source == SOURCE_DEFAULT:
                method = str(
                    section.get("time_method") or section.get("method") or METHOD_MANUAL
                ).strip()
                source = SOURCE_MEASURED if method == METHOD_MEASURED else SOURCE_CALIBRATED
                calibrated_at = str(
                    section.get("time_calibrated_at") or section.get("calibrated_at") or ""
                )

    return TimeBaselineSettings(
        overhead_ms=overhead_ms,
        ms_per_1k_tokens=ms_per_1k,
        source=source,
        calibrated_at=calibrated_at,
        method=method,
    )


def cursor_overhead() -> int:
    """Agent-chat overhead tokens for baseline math (calibrated or default)."""
    return get_baseline_settings().overhead_tokens


def baseline_source() -> str:
    """Source label for footers: measured | calibrated | default-estimate."""
    return get_baseline_settings().source


def time_baseline_source() -> str:
    """Source label for time-saved footers."""
    return get_time_baseline_settings().source


def naive_agent_ms(baseline_tokens: int) -> int:
    """Estimated wall-clock of a naive agent chat for ``baseline_tokens``."""
    settings_t = get_time_baseline_settings()
    tokens = max(0, int(baseline_tokens))
    return settings_t.overhead_ms + (tokens * settings_t.ms_per_1k_tokens) // 1000


def time_saved_ms(
    baseline_tokens: int,
    duration_ms: int | None,
    target: str,
) -> int | None:
    """Saved wall-clock vs naive agent; ``None`` when duration is unknown."""
    if target == "cursor":
        return 0
    if duration_ms is None:
        return None
    return max(0, naive_agent_ms(baseline_tokens) - int(duration_ms))


def format_duration_short(ms: int) -> str:
    """Compact human duration for footers/reports (``42ms``, ``1.2s``, ``12s``, ``1m05s``)."""
    value = max(0, int(ms))
    if value < 1000:
        return f"{value}ms"
    if value < 10_000:
        return f"{value / 1000:.1f}s"
    if value < 60_000:
        return f"{value // 1000}s"
    minutes = value // 60_000
    seconds = (value % 60_000) // 1000
    return f"{minutes}m{seconds:02d}s"


UNCALIBRATED_NUDGE = "baseline uncalibrated — run greedy-token calibrate"


def uncalibrated_nudge() -> str | None:
    """One-line nudge when the baseline is still the default estimate.

    Returns the nudge string exactly when ``baseline_source()`` is
    ``default-estimate`` — callers print it at most once per invocation.
    """
    if get_baseline_settings().source == SOURCE_DEFAULT:
        return UNCALIBRATED_NUDGE
    return None


def write_baseline_config(
    overhead_tokens: int,
    *,
    method: str,
    overhead_ms: int | None = None,
    ms_per_1k_tokens: int | None = None,
    time_method: str | None = None,
) -> Path:
    """Merge calibrated overhead into ~/.greedy-token/config.yaml (baseline: section).

    Preserves existing time knobs unless ``overhead_ms`` / ``ms_per_1k_tokens``
    are passed explicitly.
    """
    path = settings.user_config_path()
    data = settings._read_yaml(path)
    prev = data.get("baseline") if isinstance(data.get("baseline"), dict) else {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    section: dict = {
        "overhead_tokens": int(overhead_tokens),
        "calibrated_at": now,
        "method": method,
    }
    for key in ("overhead_ms", "ms_per_1k_tokens", "time_method", "time_calibrated_at"):
        if key in prev and prev[key] is not None:
            section[key] = prev[key]
    if overhead_ms is not None:
        section["overhead_ms"] = int(overhead_ms)
        section["time_method"] = time_method or method
        section["time_calibrated_at"] = now
    if ms_per_1k_tokens is not None:
        section["ms_per_1k_tokens"] = int(ms_per_1k_tokens)
        section["time_method"] = time_method or method
        section["time_calibrated_at"] = now
    data["baseline"] = section
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return path
