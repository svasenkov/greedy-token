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


def cursor_overhead() -> int:
    """Agent-chat overhead tokens for baseline math (calibrated or default)."""
    return get_baseline_settings().overhead_tokens


def baseline_source() -> str:
    """Source label for footers: measured | calibrated | default-estimate."""
    return get_baseline_settings().source


def write_baseline_config(overhead_tokens: int, *, method: str) -> Path:
    """Merge a calibrated overhead into ~/.greedy-token/config.yaml (baseline: section)."""
    path = settings.user_config_path()
    data = settings._read_yaml(path)
    data["baseline"] = {
        "overhead_tokens": int(overhead_tokens),
        "calibrated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": method,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return path
