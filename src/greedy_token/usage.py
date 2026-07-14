from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from greedy_token.estimator import cursor_baseline, cursor_saved_for
from greedy_token.router import RouteDecision, route_task_all_tiers
from greedy_token.settings import get_ollama_settings
from greedy_token.tokens import count_tokens
from greedy_token.wrappers import WRAPPERS

SCHEMA_VERSION = 2
TASK_MAX_LEN = 500
DEFAULT_LOG = Path.home() / ".greedy-token" / "usage.jsonl"
DEFAULT_MAX_LOG_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_ROTATED = 5

_log_dir_ready = False


def log_path() -> Path:
    raw = os.environ.get("GREEDY_TOKEN_LOG", "").strip()
    if raw and raw not in ("0", "false", "off", "no"):
        return Path(raw).expanduser()
    return DEFAULT_LOG


def logging_enabled(*, no_log: bool = False) -> bool:
    if no_log:
        return False
    if os.environ.get("GREEDY_TOKEN_LOG", "").strip().lower() in ("0", "false", "off", "no"):
        return False
    return True


def _ensure_log_dir(path: Path) -> None:
    global _log_dir_ready
    if _log_dir_ready:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _log_dir_ready = True


def _truncate_task(task: str) -> str:
    task = task.strip()
    if len(task) <= TASK_MAX_LEN:
        return task
    return task[: TASK_MAX_LEN - 1] + "…"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_tier_scan(task: str, root: Path) -> list[dict]:
    rows: list[dict] = []
    for tier, decision in route_task_all_tiers(task, root):
        rows.append(
            {
                "tier": tier,
                "route_id": decision.route_id,
                "matched": bool(decision.matched),
                "est_tokens": decision.est_tokens,
            }
        )
    return rows


def executor_from_decision(decision: RouteDecision, root: Path | None = None) -> dict:
    target = decision.target
    if target == "tool":
        return {"kind": decision.tool or "rg"}
    if target == "python":
        wrapper = wrapper_for_route_id(decision.route_id)
        if wrapper:
            return {"kind": "script", "script_id": wrapper.id}
        return {"kind": "script"}
    if target == "ollama":
        model = get_ollama_settings(root).model
        return {"kind": "ollama", "model": model, "eval_tokens": None}
    if target == "rag":
        return {"kind": "rag"}
    return {"kind": "cursor"}


def wrapper_for_route_id(route_id: str):
    for wrapper in WRAPPERS.values():
        if wrapper.id in route_id or route_id.endswith(wrapper.id):
            return wrapper
    return None


def build_route_event(
    *,
    cmd: str,
    task: str,
    root: Path,
    decision: RouteDecision,
    tier_scan: list[dict] | None = None,
    duration_ms: int | None = None,
    executed: bool | None = None,
    rag_hits: int | None = None,
    est_tokens_override: int | None = None,
) -> dict:
    baseline = cursor_baseline(root, task)
    est_tokens = est_tokens_override if est_tokens_override is not None else decision.est_tokens
    saved = cursor_saved_for(root, task, est_tokens, decision.target)
    counter = count_tokens(task)
    executor = executor_from_decision(decision, root)
    if rag_hits is not None:
        executor = {**executor, "rag_hits": rag_hits}
    if executed is not None:
        executor = {**executor, "executed": executed}

    event: dict = {
        "v": SCHEMA_VERSION,
        "ts": _utc_now_iso(),
        "cmd": cmd,
        "task": _truncate_task(task),
        "root": str(root),
        "selected_tier": decision.target,
        "route_id": decision.route_id,
        "confidence": round(decision.confidence, 4),
        "est_tokens": est_tokens,
        "cursor_baseline": baseline,
        "cursor_saved": saved,
        "token_counter_method": counter.method,
        "tier_scan": tier_scan if tier_scan is not None else build_tier_scan(task, root),
        "executor": executor,
    }
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    return event


def build_script_event(
    *,
    script_id: str,
    root: Path,
    duration_ms: int | None = None,
    executed: bool | None = None,
) -> dict:
    task = f"scripts --run {script_id}"
    baseline = cursor_baseline(root, task)
    event: dict = {
        "v": SCHEMA_VERSION,
        "ts": _utc_now_iso(),
        "cmd": "scripts",
        "task": task,
        "root": str(root),
        "selected_tier": "python",
        "route_id": f"script-{script_id}",
        "confidence": 1.0,
        "est_tokens": 0,
        "cursor_baseline": baseline,
        "cursor_saved": baseline,
        "token_counter_method": count_tokens(task).method,
        "tier_scan": [],
        "executor": {"kind": "script", "script_id": script_id},
    }
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    if executed is not None:
        event["executor"]["executed"] = executed
    return event


def build_script_override_event(
    *,
    task: str,
    selected_tier: str,
    previous_tier: str,
    crystal_id: str | None = None,
    root: Path | None = None,
    reason: str = "manual",
    prior_usage_ts: str | None = None,
    window_sec: int | None = None,
    tags: dict[str, str] | None = None,
) -> dict:
    event: dict = {
        "v": SCHEMA_VERSION,
        "ts": _utc_now_iso(),
        "event": "script_override",
        "cmd": "override",
        "task": _truncate_task(task),
        "task_normalized": _truncate_task(task.lower()),
        "root": str(root) if root else os.environ.get("GREEDY_TOKEN_ROOT", ""),
        "selected_tier": selected_tier,
        "previous_tier": previous_tier,
        "est_tokens": 0,
        "cursor_baseline": 0,
        "cursor_saved": 0,
        "billing": {
            "spent_est": 0,
            "saved_est": 0,
            "note": "override — prior script hit rejected by user/agent",
        },
        "meta": {"reason": reason},
    }
    if crystal_id:
        event["crystal_id"] = crystal_id
        event["route_id"] = crystal_id
    if prior_usage_ts:
        event["meta"]["prior_usage_ts"] = prior_usage_ts
    if window_sec is not None:
        event["meta"]["window_sec"] = window_sec
    if tags:
        event["tags"] = dict(tags)
    return event


def build_compress_event(
    *,
    text: str,
    short: str,
    use_ollama: bool,
    duration_ms: int | None = None,
    eval_tokens: int | None = None,
) -> dict:
    before = count_tokens(text)
    after = count_tokens(short)
    compressor = "ollama" if use_ollama else "heuristic"
    executor: dict = {"kind": "compress", "compressor": compressor}
    if eval_tokens is not None:
        executor["eval_tokens"] = eval_tokens
    event: dict = {
        "v": SCHEMA_VERSION,
        "ts": _utc_now_iso(),
        "cmd": "compress",
        "task": _truncate_task(text),
        "root": os.environ.get("GREEDY_TOKEN_ROOT", ""),
        "selected_tier": "ollama" if use_ollama else "python",
        "route_id": f"compress-{compressor}",
        "confidence": 1.0,
        "est_tokens": after.tokens,
        "cursor_baseline": before.tokens,
        "cursor_saved": max(0, before.tokens - after.tokens),
        "token_counter_method": before.method,
        "tier_scan": [],
        "executor": executor,
        "tokens_before": before.tokens,
        "tokens_after": after.tokens,
        "compressor": compressor,
    }
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    return event


def max_log_bytes() -> int:
    raw = os.environ.get("GREEDY_TOKEN_LOG_MAX_BYTES", "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return DEFAULT_MAX_LOG_BYTES


def max_rotated_files() -> int:
    raw = os.environ.get("GREEDY_TOKEN_LOG_MAX_FILES", "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return DEFAULT_MAX_ROTATED


def log_archive_paths(path: Path, *, max_files: int | None = None) -> list[Path]:
    """Active log first, then .1, .2, … (newest archive to oldest)."""
    limit = max_files if max_files is not None else max_rotated_files()
    archives = [path.with_name(f"{path.name}.{i}") for i in range(1, limit + 1)]
    return [path, *archives]


def rotate_log_if_needed(path: Path) -> bool:
    """Rotate usage.jsonl when it exceeds GREEDY_TOKEN_LOG_MAX_BYTES. Returns True if rotated."""
    if not path.is_file():
        return False
    if path.stat().st_size < max_log_bytes():
        return False
    limit = max_rotated_files()
    oldest = path.with_name(f"{path.name}.{limit}")
    if oldest.is_file():
        oldest.unlink()
    for i in range(limit, 0, -1):
        src = path if i == 1 else path.with_name(f"{path.name}.{i - 1}")
        dst = path.with_name(f"{path.name}.{i}")
        if src.is_file():
            src.rename(dst)
    return True


def append_event(event: dict, *, path: Path | None = None) -> None:
    target = path or log_path()
    try:
        _ensure_log_dir(target)
        rotate_log_if_needed(target)
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with target.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        print(f"greedy-token: usage log write failed: {exc}", file=sys.stderr)


def maybe_append_event(args, event: dict) -> None:
    if not logging_enabled(no_log=getattr(args, "no_log", False)):
        return
    append_event(event)


def parse_since(value: str) -> datetime:
    value = value.strip().lower()
    now = datetime.now(timezone.utc)
    if value.endswith("d") and value[:-1].isdigit():
        return now - timedelta(days=int(value[:-1]))
    if value.endswith("h") and value[:-1].isdigit():
        return now - timedelta(hours=int(value[:-1]))
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError as exc:
        raise ValueError(
            f"Invalid --since {value!r}; use 7d, 24h, or ISO date"
        ) from exc


def _parse_event_ts(event: dict) -> datetime | None:
    ts_raw = event.get("ts", "")
    if not ts_raw:
        return None
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except ValueError:
        return None


def load_events(path: Path, *, since: datetime | None = None) -> tuple[list[dict], int]:
    events: list[dict] = []
    skipped = 0
    for log_file in log_archive_paths(path):
        if not log_file.is_file():
            continue
        with log_file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                if since is not None:
                    ts = _parse_event_ts(event)
                    if ts is None:
                        skipped += 1
                        continue
                    if ts < since:
                        continue
                events.append(event)
    return events, skipped


@dataclass
class TierStats:
    count: int = 0
    est_tokens: int = 0
    cursor_baseline: int = 0
    saved_vs_cursor: int = 0


@dataclass
class ReportSummary:
    events: int = 0
    skipped_lines: int = 0
    since: str | None = None
    by_tier: dict[str, TierStats] = field(default_factory=dict)
    top_routes: list[tuple[str, int]] = field(default_factory=list)
    counter_methods: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "events": self.events,
            "skipped_lines": self.skipped_lines,
            "since": self.since,
            "by_tier": {
                tier: {
                    "count": stats.count,
                    "est_tokens": stats.est_tokens,
                    "saved_vs_cursor": stats.saved_vs_cursor,
                }
                for tier, stats in self.by_tier.items()
            },
            "totals": {
                "cursor_baseline": sum(s.cursor_baseline for s in self.by_tier.values()),
                "est_tokens": sum(s.est_tokens for s in self.by_tier.values()),
                "saved_vs_cursor": sum(s.saved_vs_cursor for s in self.by_tier.values()),
            },
            "top_routes": [{"route_id": rid, "count": n} for rid, n in self.top_routes],
            "counter_methods": self.counter_methods,
        }


def aggregate_events(events: list[dict], *, since_label: str | None = None) -> ReportSummary:
    summary = ReportSummary(events=len(events), since=since_label)
    route_counts: dict[str, int] = {}
    tier_order = ("tool", "python", "ollama", "rag", "cursor", "compress")

    for event in events:
        tier = event.get("selected_tier", "unknown")
        stats = summary.by_tier.setdefault(tier, TierStats())
        stats.count += 1
        stats.est_tokens += int(event.get("est_tokens") or 0)
        stats.cursor_baseline += int(event.get("cursor_baseline") or 0)
        stats.saved_vs_cursor += int(event.get("cursor_saved") or 0)

        route_id = event.get("route_id", "unknown")
        route_counts[route_id] = route_counts.get(route_id, 0) + 1

        method = event.get("token_counter_method", "unknown")
        summary.counter_methods[method] = summary.counter_methods.get(method, 0) + 1

    summary.top_routes = sorted(route_counts.items(), key=lambda x: (-x[1], x[0]))[:10]

    ordered: dict[str, TierStats] = {}
    for tier in tier_order:
        if tier in summary.by_tier:
            ordered[tier] = summary.by_tier[tier]
    for tier, stats in summary.by_tier.items():
        if tier not in ordered:
            ordered[tier] = stats
    summary.by_tier = ordered
    return summary


def format_report(summary: ReportSummary) -> str:
    if summary.events == 0:
        msg = "No events yet."
        if summary.since:
            msg = f"No events since {summary.since}."
        if summary.skipped_lines:
            msg += f" ({summary.skipped_lines} malformed lines skipped)"
        return msg

    window = f" (since {summary.since})" if summary.since else ""
    lines = [
        f"== greedy-token usage{window} ==",
        f"Events: {summary.events}",
        "",
        "By tier:",
        f"  {'tier':<10} {'count':>6} {'est_tokens':>12} {'saved_vs_cursor':>16}",
    ]
    for tier, stats in summary.by_tier.items():
        note = ""
        if tier == "ollama":
            note = "  (cheap LLM)"
        lines.append(
            f"  {tier:<10} {stats.count:>6} {stats.est_tokens:>12,} "
            f"{stats.saved_vs_cursor:>16,}{note}"
        )

    if summary.top_routes:
        lines.extend(["", "Top routes:"])
        for route_id, count in summary.top_routes:
            lines.append(f"  {route_id:<28} {count:>4}")

    if summary.counter_methods:
        total = summary.events
        parts = [f"{m} ({n}/{total})" for m, n in sorted(summary.counter_methods.items())]
        lines.extend(["", f"Token counter: {', '.join(parts)}"])

    if summary.skipped_lines:
        lines.append(f"\n({summary.skipped_lines} malformed lines skipped)")
    return "\n".join(lines)
