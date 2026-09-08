from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from greedy_token.hub.api import handle_api
from greedy_token.hub.crystallize import (
    crystal_contour,
    inbox_is_fresh,
    is_fixture_task,
    is_lesson_root,
    is_lesson_task,
    list_crystals,
    parse_iso_ts,
    rank_candidates,
    slugify,
)
from greedy_token.hub.sessions import list_sessions
from greedy_token.router import route_task
from greedy_token.usage import append_event, build_route_event


@pytest.mark.unit
def test_slugify():
    assert slugify("Meta Sync Check!") == "meta-sync-check"


@pytest.mark.unit
def test_is_fixture_task():
    assert is_fixture_task("audit :: audit")
    assert is_fixture_task("classify-file gap :: classify")
    assert not is_fixture_task("audit skill configurator-boolean")
    assert not is_fixture_task("tests/foo.py::test_bar")


@pytest.mark.unit
def test_lesson_contour():
    assert is_lesson_task("llm invoke heavy")
    assert is_lesson_task("schema check lab/users.json: every json object")
    assert is_lesson_task("python check users keys")
    assert is_lesson_task("у каждого объекта есть id и email")
    assert is_lesson_task("проверка ключей id+еmail в lab/users.json")
    assert is_lesson_root("/Users/stanislav/greedy-guru-lesson")
    assert is_lesson_root("/Users/stanislav/greedy-token-workshop/.greedy-token/drafts/x.py")
    assert not is_lesson_task("grafana oss on box2: provision datasources")
    assert not is_lesson_root("/Users/stanislav/zero-design-system")
    assert crystal_contour({"pattern": "llm invoke classify"}) == "lesson"
    assert (
        crystal_contour(
            {
                "pattern": "keep this crystal",
                "draft_path": "/Users/x/greedy-guru-lesson/.greedy-token/drafts/a.py",
            }
        )
        == "lesson"
    )
    assert crystal_contour({"pattern": "keep this crystal"}) == "workspace"


@pytest.mark.unit
def test_inbox_is_fresh():
    now = datetime.now(UTC)
    since = now - timedelta(days=7)
    fresh = now.isoformat().replace("+00:00", "Z")
    assert inbox_is_fresh({"updated_at": fresh}, since_dt=since, now=now)
    assert not inbox_is_fresh({"updated_at": "2026-07-15T00:00:00Z"}, since_dt=since, now=now)
    assert not inbox_is_fresh({}, since_dt=since, now=now)
    two_days = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    assert not inbox_is_fresh(
        {"updated_at": two_days}, since_dt=now - timedelta(hours=24), now=now
    )
    eight_days = (now - timedelta(days=8)).isoformat().replace("+00:00", "Z")
    assert not inbox_is_fresh(
        {"updated_at": eight_days}, since_dt=now - timedelta(days=90), now=now
    )
    assert parse_iso_ts("") is None
    assert parse_iso_ts("not-a-date") is None
    naive = parse_iso_ts("2026-09-08T12:00:00")
    assert naive is not None and naive.tzinfo is not None


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
    assert "saved_usd_est" in metrics
    assert "accumulated" in payload
    assert payload["accumulated"]["saved_vs_cursor"] >= 0
    assert "saved_usd_est" in payload["accumulated"]
    assert "window" in payload
    assert "meta" in payload
    kinds = {row["kind"] for row in payload["meta"]["kinds"]}
    assert {"skill", "rule", "rag", "adr", "meta", "other"} <= kinds


@pytest.mark.unit
def test_api_summary_since_all(tmp_path, monkeypatch, minimal_workspace):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    root = minimal_workspace
    decision = route_task("search for baseUrl", root)
    append_event(
        build_route_event(
            cmd="route",
            task="search for baseUrl",
            root=root,
            decision=decision,
            duration_ms=2,
        ),
        path=log,
    )
    status, payload = handle_api("/api/summary?since=all")
    assert status == 200
    assert payload["since"] == "all"
    assert payload["events"] == 1
    assert payload["accumulated"]["events"] == 1
    assert payload["window"]["events"] == 1


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

    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    lifecycle = home / "crystallize-lifecycle.jsonl"
    lifecycle.write_text(
        json.dumps(
            {
                "v": 1,
                "event_id": "e1",
                "crystal_id": "script-meta-sync",
                "stage": "watch",
                "ts": now,
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
    unbounded = list_crystals(since="all")
    assert any(c["crystal_id"] == "script-meta-sync" for c in unbounded["crystals"])


@pytest.mark.unit
def test_sessions_fallback(tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(tmp_path))

    ts = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
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
