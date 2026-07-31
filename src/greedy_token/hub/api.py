from __future__ import annotations

import json
from datetime import datetime
from statistics import median
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from greedy_token.budget_config import get_budget_settings
from greedy_token.budget_ledger import aggregate_budget
from greedy_token.hub.crystallize import (
    crystal_timeline,
    list_crystals,
    rank_candidates,
    savings_by_route,
)
from greedy_token.hub.meta_stats import (
    accumulate_totals,
    aggregate_meta_intersections,
    savings_block,
)
from greedy_token.hub.providers import catalog_payload, local_models_payload
from greedy_token.hub.sessions import list_sessions
from greedy_token.paths import find_workspace_root
from greedy_token.usage import aggregate_events, load_events, log_path, parse_since

_ALL_SINCE = frozenset({"all", "lifetime", "total"})


def _query_since(path: str, default: str = "7d") -> str:
    qs = parse_qs(urlparse(path).query)
    return (qs.get("since") or [default])[0]


def _resolve_since(path: str, default: str = "7d") -> tuple[str, datetime | None]:
    """Return (label, since_dt). ``all`` / ``lifetime`` / ``total`` → no filter."""
    since = _query_since(path, default)
    if since.lower() in _ALL_SINCE:
        return "all", None
    return since, parse_since(since)


def _workspace_root() -> Path | None:
    try:
        return find_workspace_root()
    except SystemExit:
        return None


def handle_api(path: str) -> tuple[int, dict]:
    parsed = urlparse(path)
    route = parsed.path

    if route.startswith("/api/summary"):
        since, since_dt = _resolve_since(path)
        all_events, _ = load_events(log_path(), since=None)
        events, skipped = load_events(log_path(), since=since_dt)
        summary = aggregate_events(events, since_label=since)
        summary.skipped_lines = skipped
        report = rank_candidates(since=None if since == "all" else since)
        root = _workspace_root()
        budget = aggregate_budget(root=root)
        usd_per_1m = get_budget_settings(root).cursor_usd_per_1m_tokens
        payload = summary.to_dict()
        payload["coverage_pct"] = report.get("coverage_pct")
        payload["budget"] = {
            "metered_spent_usd": budget.metered_spent_usd,
            "cursor_est_spent_usd": budget.cursor_est_spent_usd,
            "mode": budget.mode,
        }
        payload["metrics"] = _operational_metrics(
            events, summary, budget, usd_per_1m=usd_per_1m
        )
        payload["accumulated"] = accumulate_totals(all_events, usd_per_1m=usd_per_1m)
        payload["window"] = savings_block(
            events=summary.events,
            saved_vs_cursor=payload["totals"]["saved_vs_cursor"],
            time_saved_ms=payload["totals"]["time_saved_ms"],
            est_tokens=payload["totals"]["est_tokens"],
            usd_per_1m=usd_per_1m,
        )
        payload["meta"] = aggregate_meta_intersections(
            events, root=root, usd_per_1m=usd_per_1m
        )
        return 200, payload

    if route.startswith("/api/sessions"):
        since, _ = _resolve_since(path)
        return 200, {
            "sessions": list_sessions(since=None if since == "all" else since),
            "since": since,
        }

    if route.startswith("/api/crystals/"):
        crystal_id = unquote(route.removeprefix("/api/crystals/").strip("/"))
        if crystal_id:
            data = crystal_timeline(crystal_id)
            since, since_dt = _resolve_since(path)
            events, _ = load_events(log_path(), since=since_dt)
            saved = sum(
                int(e.get("cursor_saved") or 0)
                for e in events
                if crystal_id in (e.get("route_id") or "")
            )
            data["saved_vs_cursor"] = saved
            data["since"] = since
            return 200, data
        return 404, {"error": "crystal_id required"}

    if route == "/api/crystals" or route.startswith("/api/crystals?"):
        since, _ = _resolve_since(path)
        return 200, list_crystals(since=None if since == "all" else since)

    if route.startswith("/api/routes"):
        since, _ = _resolve_since(path)
        return 200, {
            "routes": savings_by_route(since=None if since == "all" else since),
            "since": since,
        }

    if route.startswith("/api/tests"):
        return 200, _tests_summary()

    if route.startswith("/api/providers/catalog"):
        return catalog_payload()

    if route.startswith("/api/providers/local-models"):
        return local_models_payload()

    if route.startswith("/api/health"):
        return 200, {"ok": True, "log_path": str(log_path())}

    return 404, {"error": "not found"}


def _operational_metrics(
    events: list[dict], summary, budget, *, usd_per_1m: float
) -> dict:
    """Hub-only ops metrics: execution latency + cost/task next to coverage.

    Latency from ``duration_ms`` samples (route/script/compress events that
    recorded one). cost/task is the Cursor-estimate spend spread over calls —
    what the window's traffic is charging against the soft budget.
    """
    durations = [
        int(e["duration_ms"])
        for e in events
        if isinstance(e.get("duration_ms"), (int, float)) and e.get("event") != "script_override"
    ]
    calls = max(1, summary.events)
    totals = summary.to_dict()["totals"]
    saved_tokens = int(totals["saved_vs_cursor"])
    latency = {
        "samples": len(durations),
        "p50_ms": int(median(durations)) if durations else None,
        "p95_ms": (
            int(sorted(durations)[min(len(durations) - 1, int(round(0.95 * (len(durations) - 1))))])
            if durations
            else None
        ),
    }
    return {
        "latency": latency,
        "cost_per_task_usd": round(budget.cursor_est_spent_usd / calls, 4),
        "metered_cost_per_task_usd": round(budget.metered_spent_usd / calls, 4),
        "saved_per_task_tokens": int(saved_tokens / calls),
        "saved_usd_est": round((saved_tokens / 1_000_000) * usd_per_1m, 4),
        "usd_per_1m_tokens": float(usd_per_1m),
        "money_source": "cursor_estimate",
        "time_saved_ms": int(totals["time_saved_ms"]),
        "time_saved_per_task_ms": int(totals["time_saved_ms"] / max(1, totals["duration_samples"])),
        "duration_samples": int(totals["duration_samples"]),
    }


def _tests_summary() -> dict:
    here = Path(__file__).resolve()
    tests_dir = here.parents[3] / "tests"
    test_files = list(tests_dir.glob("test_*.py")) if tests_dir.is_dir() else []
    return {
        "test_files": len(test_files),
        "dashboard_url": "https://svasenkov.github.io/greedy-token/reports/latest/dashboard/",
        "testops_project_id": "5276",
        "source": "greedy-token pytest suite",
    }


def json_bytes(status: int, payload: dict) -> tuple[int, bytes, str]:
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return status, body.encode("utf-8"), "application/json; charset=utf-8"
