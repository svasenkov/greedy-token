from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from greedy_token.budget_ledger import aggregate_budget
from greedy_token.hub.crystallize import (
    crystal_timeline,
    list_crystals,
    rank_candidates,
    savings_by_route,
)
from greedy_token.hub.providers import catalog_payload, local_models_payload
from greedy_token.hub.sessions import list_sessions
from greedy_token.paths import find_workspace_root
from greedy_token.usage import aggregate_events, load_events, log_path, parse_since


def _query_since(path: str, default: str = "7d") -> str:
    qs = parse_qs(urlparse(path).query)
    return (qs.get("since") or [default])[0]


def handle_api(path: str) -> tuple[int, dict]:
    parsed = urlparse(path)
    route = parsed.path

    if route.startswith("/api/summary"):
        since = _query_since(path)
        since_dt = parse_since(since)
        events, skipped = load_events(log_path(), since=since_dt)
        summary = aggregate_events(events, since_label=since)
        summary.skipped_lines = skipped
        report = rank_candidates(since=since)
        try:
            root = find_workspace_root()
        except SystemExit:
            root = None
        budget = aggregate_budget(root=root)
        payload = summary.to_dict()
        payload["coverage_pct"] = report.get("coverage_pct")
        payload["budget"] = {
            "metered_spent_usd": budget.metered_spent_usd,
            "cursor_est_spent_usd": budget.cursor_est_spent_usd,
            "mode": budget.mode,
        }
        return 200, payload

    if route.startswith("/api/sessions"):
        since = _query_since(path)
        return 200, {"sessions": list_sessions(since=since), "since": since}

    if route.startswith("/api/crystals/"):
        crystal_id = unquote(route.removeprefix("/api/crystals/").strip("/"))
        if crystal_id:
            data = crystal_timeline(crystal_id)
            since = _query_since(path)
            events, _ = load_events(log_path(), since=parse_since(since))
            saved = sum(
                int(e.get("cursor_saved") or 0)
                for e in events
                if crystal_id in (e.get("route_id") or "")
            )
            data["saved_vs_cursor"] = saved
            return 200, data
        return 404, {"error": "crystal_id required"}

    if route == "/api/crystals" or route.startswith("/api/crystals?"):
        since = _query_since(path)
        return 200, list_crystals(since=since)

    if route.startswith("/api/routes"):
        since = _query_since(path)
        return 200, {"routes": savings_by_route(since=since), "since": since}

    if route.startswith("/api/tests"):
        return 200, _tests_summary()

    if route.startswith("/api/providers/catalog"):
        return catalog_payload()

    if route.startswith("/api/providers/local-models"):
        return local_models_payload()

    if route.startswith("/api/health"):
        return 200, {"ok": True, "log_path": str(log_path())}

    return 404, {"error": "not found"}


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
