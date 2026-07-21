"""Public-contract tests for the hub package (api, crystallize, sessions, providers, serve)."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import allure
import pytest

import importlib

from greedy_token.hub import api as hub_api
from greedy_token.hub import crystallize, paths, providers, sessions

serve_mod = importlib.import_module("greedy_token.hub.serve")

pytestmark = [
    allure.epic("Hub"),
    allure.parent_suite("Hub"),
    allure.feature("Local ops dashboard"),
    allure.suite("Hub gaps"),
]


@pytest.fixture
def hub_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(home))
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(home / "usage.jsonl"))
    return home


# ---------------------------------------------------------------- paths


@allure.title("paths.greedy_home from env and default")
def test_paths_greedy_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(tmp_path / "gh"))
    assert paths.greedy_home() == tmp_path / "gh"
    monkeypatch.delenv("GREEDY_TOKEN_HOME", raising=False)
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(tmp_path / "logs" / "usage.jsonl"))
    assert paths.greedy_home() == tmp_path / "logs"


# ---------------------------------------------------------------- crystallize


@allure.title("rank_candidates: project/step filter and short-task skip")
def test_rank_candidates_filters(hub_home: Path) -> None:
    log = hub_home / "usage.jsonl"
    rows = [
        {"selected_tier": "cursor", "task": "x", "tags": {"project": "tms", "step": "classify"}},
        {"selected_tier": "cursor", "task": "long enough crystallize task", "tags": {"project": "tms", "step": "classify"}},
        {"selected_tier": "cursor", "task": "long enough crystallize task", "tags": {"project": "other"}},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    report = crystallize.rank_candidates(since=None, project="tms", step="classify")
    assert report["total_events"] == 2
    assert report["candidates"][0]["hits"] == 1  # short "x" task skipped


@allure.title("_row_matches_tags branches")
def test_row_matches_tags() -> None:
    assert crystallize._row_matches_tags({}, project=None, step=None) is True
    assert crystallize._row_matches_tags({"tags": {"project": "a"}}, project="b", step=None) is False
    assert crystallize._row_matches_tags({"tags": {"step": "s"}}, project=None, step="x") is False
    assert crystallize._row_matches_tags({"tags": {"project": "a", "step": "s"}}, project="a", step="s") is True


@allure.title("load_json_file: missing, valid, malformed")
def test_load_json_file(tmp_path: Path) -> None:
    assert crystallize.load_json_file(tmp_path / "nope.json") is None
    good = tmp_path / "good.json"
    good.write_text('{"a": 1}', encoding="utf-8")
    assert crystallize.load_json_file(good) == {"a": 1}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert crystallize.load_json_file(bad) is None


@allure.title("load_lifecycle_events: missing, blank + malformed skipped")
def test_load_lifecycle_events(hub_home: Path) -> None:
    assert crystallize.load_lifecycle_events() == []
    lifecycle = hub_home / "crystallize-lifecycle.jsonl"
    lifecycle.write_text(
        json.dumps({"crystal_id": "c1", "stage": "watch"}) + "\n\nnot-json\n", encoding="utf-8"
    )
    rows = crystallize.load_lifecycle_events()
    assert len(rows) == 1 and rows[0]["crystal_id"] == "c1"


@allure.title("savings_by_route aggregates and sorts")
def test_savings_by_route(hub_home: Path) -> None:
    log = hub_home / "usage.jsonl"
    rows = [
        {"route_id": "rg", "cursor_saved": 100, "est_tokens": 10},
        {"route_id": "rg", "cursor_saved": 50, "est_tokens": 5},
        {"route_id": "jq", "cursor_saved": 500, "est_tokens": 1},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out = crystallize.savings_by_route(since=None)
    assert out[0]["route_id"] == "jq"
    assert out[1]["saved_vs_cursor"] == 150


@allure.title("list_crystals merges report, inbox and lifecycle sources")
def test_list_crystals_merges(hub_home: Path) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    log = hub_home / "usage.jsonl"
    log.write_text(
        json.dumps({"ts": now, "selected_tier": "cursor", "task": "repeated crystallize candidate task"}) + "\n",
        encoding="utf-8",
    )
    inbox = hub_home / "crystallize-inbox.json"
    inbox.write_text(
        json.dumps(
            {
                "updated_at": "2026-07-15T00:00:00Z",
                "new_candidates": [{"pattern": "inbox pattern", "hits": 4}],
            }
        ),
        encoding="utf-8",
    )
    lifecycle = hub_home / "crystallize-lifecycle.jsonl"
    lifecycle.write_text(
        json.dumps({"crystal_id": "script-life", "pattern": "life", "hits": 7, "stage": "promoted", "status": "done"})
        + "\n"
        + json.dumps({"crystal_id": "script-nostage"})  # crystal_id but no stage/status
        + "\n"
        + json.dumps({"stage": "orphan"})  # no crystal_id → skipped
        + "\n",
        encoding="utf-8",
    )
    data = crystallize.list_crystals(since="7d")
    ids = {c["crystal_id"] for c in data["crystals"]}
    assert "script-inbox-pattern" in ids
    assert "script-life" in ids
    assert "script-nostage" in ids
    assert any(c["crystal_id"].startswith("script-repeated") for c in data["crystals"])
    life = next(c for c in data["crystals"] if c["crystal_id"] == "script-life")
    assert life["latest_stage"] == "promoted" and life["status"] == "done"


# ---------------------------------------------------------------- sessions


@allure.title("_parse_ts branches: empty, naive, invalid")
def test_parse_ts() -> None:
    assert sessions._parse_ts("") is None
    assert sessions._parse_ts("not-a-date") is None
    ts = sessions._parse_ts("2026-07-15T12:00:00")
    assert ts is not None and ts.tzinfo is not None


@allure.title("list_sessions reads .since files and filters")
def test_list_sessions_from_files(hub_home: Path) -> None:
    log = hub_home / "usage.jsonl"
    now = "2026-07-15T12:00:00Z"
    log.write_text(
        json.dumps({"ts": now, "cursor_saved": 300, "est_tokens": 20, "root": "/r"}) + "\n",
        encoding="utf-8",
    )
    sdir = hub_home / "statusline-sessions"
    sdir.mkdir()
    (sdir / "good.since").write_text("2026-07-14T00:00:00Z", encoding="utf-8")
    (sdir / "bad.since").write_text("not-a-ts", encoding="utf-8")  # → skipped (parse None)
    (sdir / "old.since").write_text("2020-01-01T00:00:00Z", encoding="utf-8")  # before since_dt → skipped

    result = sessions.list_sessions(since="30d")
    ids = [s["session_id"] for s in result]
    assert "good" in ids
    assert "bad" not in ids
    assert "old" not in ids


@allure.title("_aggregate honours since and root filters")
def test_aggregate_filters() -> None:
    from datetime import UTC, datetime

    events = [
        {"ts": "2026-07-15T12:00:00Z", "cursor_saved": 100, "est_tokens": 5, "root": "/r"},
        {"ts": "2000-01-01T00:00:00Z", "cursor_saved": 999, "est_tokens": 5, "root": "/r"},
        {"ts": "2026-07-15T12:00:00Z", "cursor_saved": 7, "est_tokens": 5, "root": "/other"},
    ]
    since = datetime(2026, 1, 1, tzinfo=UTC)
    out = sessions._aggregate(events, since=since, root="/r")
    assert out["calls"] == 1 and out["saved_vs_cursor"] == 100


# ---------------------------------------------------------------- providers


@allure.title("load_jsonl skips blank lines")
def test_load_jsonl_blank(tmp_path: Path) -> None:
    f = tmp_path / "x.jsonl"
    f.write_text('{"a":1}\n\n{"b":2}\n', encoding="utf-8")
    assert providers.load_jsonl(f) == [{"a": 1}, {"b": 2}]


@allure.title("provider payloads 404 on missing workspace and missing files")
def test_provider_payload_errors(monkeypatch: pytest.MonkeyPatch, minimal_workspace: Path) -> None:
    def boom():
        raise SystemExit(1)

    monkeypatch.setattr(providers, "find_workspace_root", boom)
    status, payload = providers.catalog_payload()
    assert status == 404 and "workspace root" in payload["error"]
    status2, payload2 = providers.local_models_payload()
    assert status2 == 404 and "workspace root" in payload2["error"]

    # workspace found but files absent
    status3, payload3 = providers.local_models_payload(root=minimal_workspace)
    assert status3 == 404 and "local-models-reference.jsonl" in payload3["error"]


# ---------------------------------------------------------------- api


@allure.title("handle_api routes: summary(no root), sessions, crystals, routes, tests, health, 404")
def test_handle_api_routes(hub_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    log = hub_home / "usage.jsonl"
    log.write_text(
        json.dumps({"ts": now, "selected_tier": "cursor", "task": "t", "route_id": "script-foo", "cursor_saved": 42}) + "\n",
        encoding="utf-8",
    )

    def boom():
        raise SystemExit(1)

    monkeypatch.setattr(hub_api, "find_workspace_root", boom)
    status, payload = hub_api.handle_api("/api/summary?since=7d")
    assert status == 200 and "budget" in payload

    assert hub_api.handle_api("/api/sessions")[0] == 200
    assert hub_api.handle_api("/api/crystals")[0] == 200
    assert hub_api.handle_api("/api/routes?since=7d")[0] == 200
    assert hub_api.handle_api("/api/tests")[0] == 200
    assert hub_api.handle_api("/api/health")[0] == 200

    # empty crystal id → 404
    status_c, payload_c = hub_api.handle_api("/api/crystals/")
    assert status_c == 404

    # crystal detail with matching saved events
    (hub_home / "crystallize-lifecycle.jsonl").write_text(
        json.dumps({"crystal_id": "script-foo", "stage": "watch", "ts": "2026-07-14T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    status_d, payload_d = hub_api.handle_api("/api/crystals/script-foo")
    assert status_d == 200 and payload_d["saved_vs_cursor"] == 42

    # unknown route
    assert hub_api.handle_api("/api/nope")[0] == 404


@allure.title("json_bytes encodes payload")
def test_json_bytes() -> None:
    status, body, ctype = hub_api.json_bytes(200, {"ok": True})
    assert status == 200 and b"ok" in body and "application/json" in ctype


# ---------------------------------------------------------------- serve


@allure.title("static_root returns a path; falls back on resource error")
def test_static_root(monkeypatch: pytest.MonkeyPatch) -> None:
    assert serve_mod.static_root().name == "static"
    monkeypatch.setattr(serve_mod.resources, "files", lambda *a, **k: (_ for _ in ()).throw(TypeError("x")))
    assert serve_mod.static_root() == serve_mod.STATIC_DIR


@allure.title("serve() starts server then handles KeyboardInterrupt")
def test_serve_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = {"n": 0}

    class FakeServer:
        def __init__(self, *a, **k) -> None:
            pass

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            closed["n"] += 1

    monkeypatch.setattr(serve_mod, "ThreadingHTTPServer", FakeServer)
    serve_mod.serve(host="127.0.0.1", port=0)
    assert closed["n"] == 1


@allure.title("HubHandler serves api, static, options, traversal fallback")
def test_hub_handler_http(hub_home: Path) -> None:
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), serve_mod.HubHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(f"{base}/api/health", timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read())["ok"] is True
        with urllib.request.urlopen(f"{base}/", timeout=5) as resp:
            assert resp.status == 200
            assert b"<" in resp.read()
        # traversal / missing static → index fallback
        with urllib.request.urlopen(f"{base}/../secret", timeout=5) as resp:
            assert resp.status == 200
        req = urllib.request.Request(f"{base}/api/health", method="OPTIONS")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 204

        # Local browser Origin → CORS echoes that exact origin (not "*").
        local_req = urllib.request.Request(
            f"{base}/api/health", headers={"Origin": "http://localhost:5173"}
        )
        with urllib.request.urlopen(local_req, timeout=5) as resp:
            assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"

        # Foreign Origin → no CORS header at all (cross-site read blocked).
        evil_req = urllib.request.Request(
            f"{base}/api/health", headers={"Origin": "https://evil.example.com"}
        )
        with urllib.request.urlopen(evil_req, timeout=5) as resp:
            assert resp.headers.get("Access-Control-Allow-Origin") is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@allure.title("_origin_is_local: local hosts, foreign host, malformed origin")
def test_origin_is_local() -> None:
    assert serve_mod._origin_is_local("http://localhost:8787") is True
    assert serve_mod._origin_is_local("http://127.0.0.1:5173") is True
    assert serve_mod._origin_is_local("http://[::1]:9222") is True
    assert serve_mod._origin_is_local("https://evil.example.com") is False
    assert serve_mod._origin_is_local("") is False
    assert serve_mod._origin_is_local("http://[") is False  # invalid IPv6 → ValueError
