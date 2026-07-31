"""Workflow-meta inventory and telemetry intersections for the hub.

Meta kinds mirror the workspace agent taxonomy (sync-agent-meta):
skill · rule · rag · adr · meta.  An event may hit several kinds at once
(true intersection); unmatched traffic lands in ``other``.
"""

from __future__ import annotations

from pathlib import Path

from greedy_token.budget_config import get_budget_settings
from greedy_token.usage import OUTCOME_EVENT

META_KINDS = ("skill", "rule", "rag", "adr", "meta")


def _usd_from_tokens(tokens: int, usd_per_1m: float) -> float:
    return round((max(0, int(tokens)) / 1_000_000) * float(usd_per_1m), 4)


def savings_block(
    *,
    events: int,
    saved_vs_cursor: int,
    time_saved_ms: int,
    est_tokens: int = 0,
    usd_per_1m: float,
) -> dict:
    return {
        "events": int(events),
        "saved_vs_cursor": int(saved_vs_cursor),
        "est_tokens": int(est_tokens),
        "time_saved_ms": int(time_saved_ms),
        "saved_usd_est": _usd_from_tokens(saved_vs_cursor, usd_per_1m),
        "usd_per_1m_tokens": float(usd_per_1m),
        "money_source": "cursor_estimate",
    }


def classify_meta_kinds(event: dict) -> list[str]:
    """Return meta kinds touched by a usage event (may be empty → other)."""
    if event.get("event") == OUTCOME_EVENT:
        return []

    route = str(event.get("route_id") or "").lower()
    task = str(event.get("task") or "").lower()
    cmd = str(event.get("cmd") or "").lower()
    tier = str(event.get("selected_tier") or "").lower()
    crystal = str(event.get("crystal_id") or "").lower()
    blob = f"{route} {task} {cmd} {crystal}"

    kinds: list[str] = []
    if tier == "rag" or cmd == "rag" or "rag" in route:
        kinds.append("rag")
    if "skill" in blob:
        kinds.append("skill")
    if "rule" in blob or "audit-context" in blob:
        kinds.append("rule")
    if "adr" in blob:
        kinds.append("adr")
    if any(
        needle in blob
        for needle in (
            "meta-sync",
            "check-meta",
            "meta-audit",
            "sync-agent-meta",
            "configurator-boolean",
            "phase-manifest",
            "skills-map",
        )
    ) or (route.startswith("pipeline-check-meta") or "meta sync" in task):
        kinds.append("meta")

    # de-dupe, preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for kind in kinds:
        if kind not in seen:
            seen.add(kind)
            ordered.append(kind)
    return ordered


def workspace_meta_inventory(root: Path | None) -> dict[str, dict]:
    """Count on-disk workflow meta artifacts (0 when root is missing)."""
    inv: dict[str, dict] = {
        kind: {"count": 0, "paths_sample": []} for kind in (*META_KINDS, "other")
    }
    if root is None or not root.is_dir():
        return inv

    def _add(kind: str, paths: list[Path], *, limit: int = 5) -> None:
        files = [p for p in paths if p.is_file()]
        inv[kind]["count"] = len(files)
        inv[kind]["paths_sample"] = [
            str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
            for p in files[:limit]
        ]

    _add(
        "skill",
        sorted(root.glob(".cursor/skills/*/SKILL.md"))
        + sorted(root.glob("docs/cursor-skills-cold/*/SKILL.md")),
    )
    _add("rule", sorted(root.glob(".cursor/rules/*.mdc")))
    adr_paths = sorted(root.glob("docs/adr/*.md")) + sorted(
        root.glob("docs/**/adr/*.md")
    )
    # de-dupe adr by resolve
    seen_adr: set[Path] = set()
    uniq_adr: list[Path] = []
    for p in adr_paths:
        key = p.resolve()
        if key in seen_adr:
            continue
        seen_adr.add(key)
        uniq_adr.append(p)
    _add("adr", uniq_adr)

    manifest = root / "docs" / "rag" / "manifest.jsonl"
    if manifest.is_file():
        lines = [
            ln for ln in manifest.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        inv["rag"]["count"] = len(lines)
        inv["rag"]["paths_sample"] = ["docs/rag/manifest.jsonl"]
    else:
        _add("rag", sorted(root.glob("docs/rag/**/*.md")))

    meta_files = [
        root / "docs" / "phase-manifest.json",
        root / "docs" / "skills-map.md",
        root / "docs" / "CONTEXT.md",
        root / "scripts" / "meta-sync-check.py",
    ]
    _add("meta", [p for p in meta_files if p.is_file()])
    return inv


def aggregate_meta_intersections(
    events: list[dict],
    *,
    root: Path | None,
    usd_per_1m: float | None = None,
) -> dict:
    """Inventory × telemetry hits per workflow-meta kind."""
    if usd_per_1m is None:
        usd_per_1m = get_budget_settings(root).cursor_usd_per_1m_tokens

    inventory = workspace_meta_inventory(root)
    buckets: dict[str, dict] = {
        kind: {
            "kind": kind,
            "inventory": inventory.get(kind, {}).get("count", 0),
            "paths_sample": inventory.get(kind, {}).get("paths_sample", []),
            "hits": 0,
            "saved_vs_cursor": 0,
            "est_tokens": 0,
            "time_saved_ms": 0,
        }
        for kind in (*META_KINDS, "other")
    }

    classified = 0
    for event in events:
        if event.get("event") == OUTCOME_EVENT:
            continue
        kinds = classify_meta_kinds(event)
        if not kinds:
            kinds = ["other"]
        else:
            classified += 1
        saved = int(event.get("cursor_saved") or 0)
        spent = int(event.get("est_tokens") or 0)
        time_ms = (
            int(event["time_saved_ms"])
            if isinstance(event.get("time_saved_ms"), (int, float))
            else 0
        )
        for kind in kinds:
            bucket = buckets[kind]
            bucket["hits"] += 1
            bucket["saved_vs_cursor"] += saved
            bucket["est_tokens"] += spent
            bucket["time_saved_ms"] += time_ms

    rows = []
    for kind in (*META_KINDS, "other"):
        bucket = buckets[kind]
        rows.append(
            {
                **bucket,
                "saved_usd_est": _usd_from_tokens(
                    bucket["saved_vs_cursor"], usd_per_1m
                ),
            }
        )

    return {
        "kinds": rows,
        "classified_events": classified,
        "usd_per_1m_tokens": float(usd_per_1m),
        "money_source": "cursor_estimate",
        "note": (
            "Intersection = telemetry events whose route/task/cmd touch "
            "skill · rule · rag · adr · meta. Multi-kind events count in each."
        ),
    }


def accumulate_totals(events: list[dict], *, usd_per_1m: float) -> dict:
    """All-time (or any event list) savings block for header / Overview."""
    saved = 0
    spent = 0
    time_ms = 0
    count = 0
    for event in events:
        if event.get("event") == OUTCOME_EVENT:
            continue
        count += 1
        saved += int(event.get("cursor_saved") or 0)
        spent += int(event.get("est_tokens") or 0)
        if isinstance(event.get("time_saved_ms"), (int, float)):
            time_ms += int(event["time_saved_ms"])
    return savings_block(
        events=count,
        saved_vs_cursor=saved,
        time_saved_ms=time_ms,
        est_tokens=spent,
        usd_per_1m=usd_per_1m,
    )
