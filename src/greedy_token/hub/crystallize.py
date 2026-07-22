from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from greedy_token.hub.paths import inbox_path, lifecycle_path, watch_state_path
from greedy_token.usage import load_events, log_path, parse_since

SCRIPT_TIERS = frozenset({"tool", "python", "script", "rag"})
LLM_TIERS = frozenset({"ollama", "cursor"})


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:48] or "task"


def rank_candidates(
    *,
    since: str | None = "7d",
    top: int = 15,
    project: str | None = None,
    step: str | None = None,
) -> dict:
    since_dt = parse_since(since) if since else None
    events, _ = load_events(log_path(), since=since_dt)
    if project or step:
        events = [e for e in events if _row_matches_tags(e, project=project, step=step)]
    if not events:
        return {
            "ok": True,
            "coverage_pct": 0.0,
            "total_events": 0,
            "script_or_tool_events": 0,
            "tier_counts": {},
            "candidates": [],
            "since": since,
        }

    tier_counts = Counter(e.get("selected_tier", "unknown") for e in events)
    script_like = sum(tier_counts.get(t, 0) for t in SCRIPT_TIERS)
    coverage_pct = round(100.0 * script_like / len(events), 1)

    llm_tasks: Counter[str] = Counter()
    for row in events:
        tier = row.get("selected_tier", "")
        if tier not in LLM_TIERS:
            continue
        task = (row.get("task") or "").strip().lower()
        if len(task) < 8:
            continue
        llm_tasks[task] += 1

    candidates = [
        {
            "pattern": task,
            "hits": hits,
            "suggested_script": f"script-{slugify(task)}",
            "crystal_id": f"script-{slugify(task)}",
            "tier_seen": "cursor/ollama",
        }
        for task, hits in llm_tasks.most_common(top)
    ]

    return {
        "ok": True,
        "coverage_pct": coverage_pct,
        "total_events": len(events),
        "script_or_tool_events": script_like,
        "tier_counts": dict(tier_counts),
        "candidates": candidates,
        "since": since,
    }


def _row_matches_tags(row: dict, *, project: str | None, step: str | None) -> bool:
    if not project and not step:
        return True
    tags = row.get("tags") or {}
    if project and tags.get("project") != project:
        return False
    if step and tags.get("step") != step:
        return False
    return True


def load_json_file(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def append_lifecycle_event(
    *,
    stage: str,
    crystal_id: str,
    pattern: str = "",
    hits: int = 0,
    status: str = "pending",
    extra: dict | None = None,
) -> dict:
    """Append a lifecycle stage event (draft/shadow/promoted/rejected/…) to the log."""
    event: dict = {
        "v": 1,
        "event_id": str(uuid.uuid4()),
        "crystal_id": crystal_id,
        "stage": stage,
        "ts": _now_iso(),
        "pattern": pattern,
        "hits": hits,
        "status": status,
    }
    if extra:
        event.update(extra)
    path = lifecycle_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def load_lifecycle_events() -> list[dict]:
    path = lifecycle_path()
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def savings_by_route(*, since: str | None = "7d") -> list[dict]:
    since_dt = parse_since(since) if since else None
    events, _ = load_events(log_path(), since=since_dt)
    by_route: dict[str, dict] = {}
    for event in events:
        route_id = event.get("route_id", "unknown")
        bucket = by_route.setdefault(
            route_id,
            {"route_id": route_id, "count": 0, "saved_vs_cursor": 0, "est_tokens": 0},
        )
        bucket["count"] += 1
        bucket["saved_vs_cursor"] += int(event.get("cursor_saved") or 0)
        bucket["est_tokens"] += int(event.get("est_tokens") or 0)
    return sorted(by_route.values(), key=lambda x: (-x["saved_vs_cursor"], x["route_id"]))


def crystal_timeline(crystal_id: str) -> dict:
    events = [e for e in load_lifecycle_events() if e.get("crystal_id") == crystal_id]
    events.sort(key=lambda e: e.get("ts", ""))
    stages = {e.get("stage"): e for e in events if e.get("stage")}
    return {
        "crystal_id": crystal_id,
        "events": events,
        "stages": stages,
        "latest_stage": events[-1].get("stage") if events else None,
    }


def list_crystals(*, since: str | None = "7d") -> dict:
    report = rank_candidates(since=since)
    inbox = load_json_file(inbox_path()) or {}
    watch = load_json_file(watch_state_path()) or {}
    lifecycle = load_lifecycle_events()

    crystals: dict[str, dict] = {}
    for item in report.get("candidates", []):
        cid = item.get("crystal_id") or item.get("suggested_script")
        crystals[cid] = {
            "crystal_id": cid,
            "pattern": item["pattern"],
            "hits": item["hits"],
            "suggested_script": item["suggested_script"],
            "source": "report",
            "latest_stage": "report",
        }

    for item in inbox.get("new_candidates", []):
        cid = f"script-{slugify(item['pattern'])}"
        entry = crystals.setdefault(
            cid,
            {
                "crystal_id": cid,
                "pattern": item["pattern"],
                "hits": item["hits"],
                "suggested_script": item.get("suggested_script", cid),
                "source": "inbox",
            },
        )
        entry["latest_stage"] = "watch"
        entry["inbox_at"] = inbox.get("updated_at")

    for event in lifecycle:
        cid = event.get("crystal_id", "")
        if not cid:
            continue
        entry = crystals.setdefault(
            cid,
            {
                "crystal_id": cid,
                "pattern": event.get("pattern", cid),
                "hits": event.get("hits", 0),
                "suggested_script": cid,
                "source": "lifecycle",
            },
        )
        stage = event.get("stage")
        if stage:
            entry["latest_stage"] = stage
        if event.get("status"):
            entry["status"] = event["status"]

    notified = watch.get("notified") or {}
    return {
        "coverage_pct": report.get("coverage_pct"),
        "total_events": report.get("total_events"),
        "crystals": sorted(crystals.values(), key=lambda x: (-x.get("hits", 0), x["crystal_id"])),
        "notified_patterns": list(notified.keys()),
        "since": since,
    }
