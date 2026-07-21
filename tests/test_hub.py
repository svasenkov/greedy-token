from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from greedy_token.hub.api import handle_api
from greedy_token.hub.crystallize import list_crystals, rank_candidates, slugify
from greedy_token.hub.sessions import list_sessions
from greedy_token.usage import append_event, build_route_event
from greedy_token.router import route_task


@pytest.mark.unit
def test_slugify():
    assert slugify("Meta Sync Check!") == "meta-sync-check"


@pytest.mark.unit
def test_api_summary_empty(tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    status, payload = handle_api("/api/summary?since=7d")
    assert status == 200
    assert payload["events"] == 0


@pytest.mark.unit
def test_api_summary_with_events(tmp_path, monkeypatch, minimal_workspace):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    root = minimal_workspace
    decision = route_task("meta sync check", root)
    append_event(
        build_route_event(
            cmd="route",
            task="meta sync check",
            root=root,
            decision=decision,
            duration_ms=1,
        ),
        path=log,
    )
    status, payload = handle_api("/api/summary?since=7d")
    assert status == 200
    assert payload["events"] == 1
    assert "totals" in payload
    # Route quality surfaced next to coverage_pct
    assert "coverage_pct" in payload
    quality = payload["quality"]
    assert "override_rate_7d" in quality
    assert "cheap_hold_rate" in quality
    assert "by_crystal" in quality
    # Operational metrics: latency + cost/task next to coverage
    metrics = payload["metrics"]
    assert metrics["latency"]["samples"] == 1
    assert metrics["latency"]["p50_ms"] == 1
    assert "cost_per_task_usd" in metrics
    assert "saved_per_task_tokens" in metrics


@pytest.mark.unit
def test_rank_candidates_llm_hits(tmp_path, monkeypatch, minimal_workspace):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    root = minimal_workspace
    for _ in range(3):
        decision = route_task("find repeated crystallize pattern task", root)
        event = build_route_event(
            cmd="route",
            task="find repeated crystallize pattern task",
            root=root,
            decision=decision,
            duration_ms=1,
        )
        event["selected_tier"] = "cursor"
        append_event(event, path=log)

    report = rank_candidates(since="7d")
    assert report["total_events"] == 3
    assert report["candidates"][0]["hits"] == 3


@pytest.mark.unit
def test_list_crystals_from_lifecycle(tmp_path, monkeypatch):
    home = tmp_path / "greedy-home"
    home.mkdir()
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(home))
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(home / "usage.jsonl"))

    lifecycle = home / "crystallize-lifecycle.jsonl"
    lifecycle.write_text(
        json.dumps(
            {
                "v": 1,
                "event_id": "e1",
                "crystal_id": "script-meta-sync",
                "stage": "watch",
                "ts": "2026-07-14T12:00:00Z",
                "pattern": "meta sync",
                "hits": 5,
                "status": "pending",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    data = list_crystals(since="7d")
    assert any(c["crystal_id"] == "script-meta-sync" for c in data["crystals"])


@pytest.mark.unit
def test_sessions_fallback(tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(tmp_path))

    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    append_event(
        {
            "v": 2,
            "ts": ts,
            "cmd": "route",
            "task": "test",
            "root": "/tmp",
            "selected_tier": "tool",
            "route_id": "rg",
            "est_tokens": 0,
            "cursor_baseline": 1000,
            "cursor_saved": 900,
        },
        path=log,
    )

    sessions = list_sessions(since="7d")
    assert len(sessions) == 1
    assert sessions[0]["calls"] == 1
    assert sessions[0]["saved_vs_cursor"] == 900


@pytest.mark.unit
def test_api_health(tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    status, payload = handle_api("/api/health")
    assert status == 200
    assert payload["ok"] is True


@pytest.mark.unit
def test_api_providers_catalog(monkeypatch, minimal_workspace):
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(minimal_workspace))
    raw = minimal_workspace / "projects" / "infra-home" / "raw" / "providers"
    raw.mkdir(parents=True)
    catalog = raw / "provider-catalog.jsonl"
    catalog.write_text(
        '{"id":"demo-provider","provider":"Demo","product":"SKU","category":"llm_api",'
        '"availability":["RU"],"blocked_in":[],"requires_vpn_in":[],"pricing":{"free_tier":true,"trial":null},'
        '"compliance":["152-FZ"],"source_url":"https://example.com","verified_at":"2026-07-15"}\n',
        encoding="utf-8",
    )
    status, payload = handle_api("/api/providers/catalog")
    assert status == 200
    assert payload["count"] == 1
    assert payload["items"][0]["id"] == "demo-provider"


@pytest.mark.unit
def test_api_providers_local_models(monkeypatch, minimal_workspace):
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(minimal_workspace))
    raw = minimal_workspace / "projects" / "infra-home" / "raw" / "providers"
    raw.mkdir(parents=True)
    models = raw / "local-models-reference.jsonl"
    models.write_text(
        '{"id":"demo:7b","family":"demo","params_b":7,"quant":"q4_K_M","min_vram_gb":6,'
        '"min_ram_gb":16,"recommended":{"classify":true,"generate":false,"audit":false,'
        '"architecture":false,"prod_default":true,"local_default":false},'
        '"deprecated":false,"replacement":null,"source_url":"https://ollama.com/library/demo",'
        '"verified_at":"2026-07-15"}\n',
        encoding="utf-8",
    )
    status, payload = handle_api("/api/providers/local-models")
    assert status == 200
    assert payload["count"] == 1
    assert payload["items"][0]["family"] == "demo"


@pytest.mark.unit
def test_api_providers_missing_catalog(monkeypatch, minimal_workspace):
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(minimal_workspace))
    status, payload = handle_api("/api/providers/catalog")
    assert status == 404
    assert "provider-catalog.jsonl not found" in payload["error"]


@pytest.mark.unit
def test_api_crystal_detail(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(home))
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(home / "usage.jsonl"))
    (home / "crystallize-lifecycle.jsonl").write_text(
        json.dumps(
            {
                "v": 1,
                "crystal_id": "script-foo",
                "stage": "watch",
                "ts": "2026-07-14T12:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    status, payload = handle_api("/api/crystals/script-foo")
    assert status == 200
    assert payload["crystal_id"] == "script-foo"
    assert payload["latest_stage"] == "watch"
