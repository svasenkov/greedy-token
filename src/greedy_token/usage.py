from __future__ import annotations

import json
import os
import re
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
OVERRIDE_WINDOW_SEC = 900
# Every non-escalation tier we "hold": a re-ask that escalates to cursor within
# the window attributes an override against the prior cheap hit, regardless of
# its tier. This makes cheap_hold_rate honest across all cheap tiers (not just
# python/script). cursor is the escalation target; compress is a prompt helper.
CHEAP_TIERS = frozenset({"tool", "python", "ollama", "rag", "script"})
# Legacy subset: python/script were the only attributed tiers before cheap-tier
# attribution landed. Kept for callers that still reference the script subset.
SCRIPT_HIT_TIERS = frozenset({"python", "script"})
OVERRIDE_EVENT = "script_override"
# usage-override.md: override_rate >= 0.3 over 7d -> disable / re-shadow.
OVERRIDE_DISABLE_THRESHOLD = 0.3

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


def normalize_task(task: str) -> str:
    """Canonical cluster key: lowercase, trim, collapse whitespace."""
    return re.sub(r"\s+", " ", (task or "").strip().lower())


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
        try:
            from greedy_token.model_select import resolve_model

            resolved = resolve_model("", root=root, tier_hint="cheap")
            return {
                "kind": "ollama",
                "model": resolved.settings.model,
                "model_id": resolved.model_id,
                "eval_tokens": None,
            }
        except ValueError:
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
    llm_tags: dict[str, str] | None = None,
    model_id: str | None = None,
    profile: str | None = None,
    escalated_from: str | None = None,
    billing_tier: str | None = None,
    cost_usd: float | None = None,
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
    if model_id:
        executor = {**executor, "model_id": model_id}

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
    if getattr(decision, "shadow_route_id", None):
        event["shadow_route_id"] = decision.shadow_route_id
        event["shadow"] = True
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    if profile:
        event["profile"] = profile
    if escalated_from:
        event["escalated_from"] = escalated_from
    if billing_tier:
        event["billing_tier"] = billing_tier
    if cost_usd is not None:
        event["cost_usd"] = round(cost_usd, 6)
    if llm_tags:
        event["tags"] = dict(llm_tags)

    from greedy_token.budget_ledger import build_billing_event_fields

    if billing_tier:
        billing_tier_for_block = billing_tier
    elif decision.target == "cursor":
        billing_tier_for_block = "cursor"
    else:
        billing_tier_for_block = "cheap"
    billing_fields = build_billing_event_fields(
        billing_tier=billing_tier_for_block,
        cost_usd=cost_usd,
        model_id=model_id,
    )
    event.update(billing_fields)
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
        "task_normalized": normalize_task(task)[:TASK_MAX_LEN],
        "root": str(root) if root else os.environ.get("GREEDY_TOKEN_ROOT", ""),
        "selected_tier": selected_tier,
        "previous_tier": previous_tier,
        "est_tokens": 0,
        "cursor_baseline": 0,
        "cursor_saved": 0,
        "billing": {
            "spent_est": 0,
            "saved_est": 0,
            "note": "override — prior cheap hit rejected by user/agent",
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


def find_prior_cheap_hit(
    path: Path,
    task_normalized: str,
    when: datetime,
    *,
    window_sec: int = OVERRIDE_WINDOW_SEC,
) -> dict | None:
    """Nearest prior cheap-tier hit for the same normalized task within window."""
    if not task_normalized or not path.is_file():
        return None
    window = timedelta(seconds=max(0, window_sec))
    best: dict | None = None
    best_ts: datetime | None = None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") == "script_override":
                continue
            tier = row.get("selected_tier", "")
            if tier not in CHEAP_TIERS:
                continue
            row_task = normalize_task(row.get("task_normalized") or row.get("task") or "")
            if row_task != task_normalized:
                continue
            ts = _parse_event_ts(row)
            if ts is None or ts >= when:
                continue
            if when - ts > window:
                continue
            if best_ts is None or ts > best_ts:
                best = row
                best_ts = ts
    return best


# Legacy alias: attribution used to cover only python/script hits.
find_prior_script_hit = find_prior_cheap_hit


def maybe_emit_auto_script_override(event: dict, *, path: Path) -> None:
    """After a cursor route write, attribute a prior cheap-tier hit as override."""
    if event.get("event") == "script_override":
        return
    if event.get("selected_tier") != "cursor":
        return
    task = event.get("task") or ""
    normalized = normalize_task(task)
    if not normalized:
        return
    when = _parse_event_ts(event)
    if when is None:
        return
    prior = find_prior_cheap_hit(path, normalized, when, window_sec=OVERRIDE_WINDOW_SEC)
    if prior is None:
        return
    previous_tier = prior.get("selected_tier") or "python"
    if previous_tier not in CHEAP_TIERS:  # pragma: no cover - prior hits are pre-filtered to CHEAP_TIERS
        return
    crystal_id = prior.get("route_id") or prior.get("crystal_id")
    root_raw = event.get("root") or prior.get("root") or ""
    root = Path(root_raw) if root_raw else None
    tags = event.get("tags") if isinstance(event.get("tags"), dict) else None
    override = build_script_override_event(
        task=task,
        selected_tier="cursor",
        previous_tier=previous_tier,
        crystal_id=crystal_id,
        root=root,
        reason="user_reask",
        prior_usage_ts=prior.get("ts"),
        window_sec=OVERRIDE_WINDOW_SEC,
        tags=tags,
    )
    append_event(override, path=path, emit_auto_override=False)


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


def append_event(
    event: dict,
    *,
    path: Path | None = None,
    emit_auto_override: bool = True,
) -> None:
    target = path or log_path()
    try:
        _ensure_log_dir(target)
        rotate_log_if_needed(target)
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with target.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        print(f"greedy-token: usage log write failed: {exc}", file=sys.stderr)
        return
    if emit_auto_override:
        maybe_emit_auto_script_override(event, path=target)


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
    quality: dict = field(default_factory=dict)

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
            "quality": self.quality,
        }


def quality_metrics(events: list[dict], *, since_label: str | None = None) -> dict:
    """Route quality telemetry (NOT ML accuracy).

    Two headline numbers over real traffic, per usage-override.md:
      * override_rate = script_override events / cheap-tier hits
      * cheap_hold_rate = 1 - override_rate (cheap hits kept, not re-asked)

    Failures = script_override (weight 1.0). Cursor escalation on wiring is not a
    failure. Auto-override attribution covers every cheap tier (tool/python/
    ollama/rag/script), so the hold denominator spans all of them — no tier is
    excluded and there is no fake 100% from unmeasured tiers. ``script_hits``
    keeps its contract name but now counts all cheap hits; ``cheap_hits`` is the
    canonical alias and ``cheap_hits_by_tier`` gives the per-tier breakdown.
    """
    cheap_hits_by_crystal: dict[str, int] = {}
    override_by_crystal: dict[str, int] = {}
    cheap_hits_by_tier: dict[str, int] = {}
    cheap_hits_total = 0
    override_total = 0

    for event in events:
        if event.get("event") == OVERRIDE_EVENT:
            crystal = event.get("crystal_id") or event.get("route_id") or "unknown"
            override_by_crystal[crystal] = override_by_crystal.get(crystal, 0) + 1
            override_total += 1
            continue
        tier = event.get("selected_tier", "")
        if tier in CHEAP_TIERS:
            crystal = event.get("route_id") or "unknown"
            cheap_hits_by_crystal[crystal] = cheap_hits_by_crystal.get(crystal, 0) + 1
            cheap_hits_by_tier[tier] = cheap_hits_by_tier.get(tier, 0) + 1
            cheap_hits_total += 1

    override_rate = round(override_total / max(1, cheap_hits_total), 4)
    cheap_hold_rate = round(max(0.0, 1.0 - override_rate), 4)

    by_crystal: list[dict] = []
    for crystal in set(cheap_hits_by_crystal) | set(override_by_crystal):
        hits = cheap_hits_by_crystal.get(crystal, 0)
        overrides = override_by_crystal.get(crystal, 0)
        rate = round(overrides / max(1, hits), 4)
        by_crystal.append(
            {
                "crystal_id": crystal,
                "script_hits": hits,
                "override_count": overrides,
                "override_rate": rate,
                "reuse_action": (
                    "disable/re-shadow" if rate >= OVERRIDE_DISABLE_THRESHOLD else None
                ),
            }
        )
    by_crystal.sort(
        key=lambda row: (-row["override_rate"], -row["override_count"], row["crystal_id"])
    )

    return {
        "since": since_label,
        "override_rate_7d": override_rate,
        "cheap_hold_rate": cheap_hold_rate,
        "script_hits": cheap_hits_total,
        "cheap_hits": cheap_hits_total,
        "cheap_hits_by_tier": dict(sorted(cheap_hits_by_tier.items())),
        "override_events": override_total,
        "disable_threshold": OVERRIDE_DISABLE_THRESHOLD,
        "by_crystal": by_crystal,
        "signal_scope": {
            "with_override_signal": sorted(CHEAP_TIERS),
            "no_signal_yet": {},
        },
    }


def aggregate_events(events: list[dict], *, since_label: str | None = None) -> ReportSummary:
    summary = ReportSummary(events=len(events), since=since_label)
    summary.quality = quality_metrics(events, since_label=since_label)
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

    quality = summary.quality
    if quality and quality.get("script_hits"):
        lines.extend(
            [
                "",
                "Route quality (not ML accuracy):",
                f"  override_rate   {quality['override_rate_7d']:.0%}"
                f"  (threshold {quality['disable_threshold']:.0%})",
                f"  cheap_hold_rate {quality['cheap_hold_rate']:.0%}"
                f"  ({quality['override_events']} overrides / {quality['script_hits']} cheap hits)",
            ]
        )
        by_tier = quality.get("cheap_hits_by_tier") or {}
        if by_tier:
            parts = ", ".join(f"{t} {n}" for t, n in sorted(by_tier.items()))
            lines.append(f"  cheap hits by tier: {parts}")
        worst = [c for c in quality.get("by_crystal", []) if c["override_count"] > 0][:3]
        if worst:
            lines.append("  worst crystals by override:")
            for crystal in worst:
                flag = "  <- disable/re-shadow" if crystal["reuse_action"] else ""
                lines.append(
                    f"    {crystal['crystal_id']:<28} "
                    f"{crystal['override_rate']:>5.0%} "
                    f"({crystal['override_count']}/{crystal['script_hits']}){flag}"
                )

    if summary.counter_methods:
        total = summary.events
        parts = [f"{m} ({n}/{total})" for m, n in sorted(summary.counter_methods.items())]
        lines.extend(["", f"Token counter: {', '.join(parts)}"])

    try:
        from greedy_token.budget_ledger import format_budget_line

        lines.extend(["", format_budget_line(compact=False)])
    except (ImportError, OSError, ValueError):
        pass

    if summary.skipped_lines:
        lines.append(f"\n({summary.skipped_lines} malformed lines skipped)")
    return "\n".join(lines)
