"""Unit tests for hub workflow-meta inventory and intersections."""

from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.hub.meta_stats import (
    accumulate_totals,
    aggregate_meta_intersections,
    classify_meta_kinds,
    workspace_meta_inventory,
)


pytestmark = [
    allure.epic("Hub"),
    allure.feature("Workflow meta intersections"),
]


@allure.title("classify_meta_kinds maps route/task/tier to skill·rule·rag·adr·meta")
def test_classify_meta_kinds() -> None:
    assert "rag" in classify_meta_kinds(
        {"selected_tier": "rag", "route_id": "mcp-rag", "task": "x", "cmd": "mcp"}
    )
    assert "skill" in classify_meta_kinds(
        {"selected_tier": "ollama", "route_id": "ollama-audit-skill", "task": "audit skill", "cmd": "run"}
    )
    assert "meta" in classify_meta_kinds(
        {
            "selected_tier": "python",
            "route_id": "pipeline-check-meta-sync",
            "task": "meta sync check",
            "cmd": "pipeline",
        }
    )
    assert "adr" in classify_meta_kinds(
        {"selected_tier": "tool", "route_id": "tool-rg-search", "task": "find adr 0002", "cmd": "route"}
    )
    assert classify_meta_kinds({"event": "route_outcome", "selected_tier": "tool"}) == []
    assert classify_meta_kinds(
        {"selected_tier": "tool", "route_id": "mcp-search", "task": "find foo", "cmd": "mcp"}
    ) == []


@allure.title("workspace_meta_inventory counts skills/rules/adr/rag/meta on disk")
def test_workspace_meta_inventory(tmp_path: Path) -> None:
    # Isolate from autouse minimal_workspace (same tmp_path).
    root = tmp_path / "meta-island"
    (root / ".cursor" / "skills" / "demo").mkdir(parents=True)
    (root / ".cursor" / "skills" / "demo" / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    (root / ".cursor" / "rules").mkdir(parents=True)
    (root / ".cursor" / "rules" / "x.mdc").write_text("rule\n", encoding="utf-8")
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "docs" / "adr" / "001-x.md").write_text("# adr\n", encoding="utf-8")
    (root / "docs" / "rag").mkdir(parents=True)
    (root / "docs" / "rag" / "manifest.jsonl").write_text(
        '{"id":"a"}\n{"id":"b"}\n', encoding="utf-8"
    )
    (root / "docs" / "phase-manifest.json").write_text("{}", encoding="utf-8")
    (root / "docs" / "skills-map.md").write_text("# map\n", encoding="utf-8")
    (root / "docs" / "CONTEXT.md").write_text("# ctx\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "meta-sync-check.py").write_text("print(1)\n", encoding="utf-8")

    inv = workspace_meta_inventory(root)
    assert inv["skill"]["count"] == 1
    assert inv["rule"]["count"] == 1
    assert inv["adr"]["count"] == 1
    assert inv["rag"]["count"] == 2
    assert inv["meta"]["count"] == 4


@allure.title("aggregate_meta_intersections tallies hits and savings per kind")
def test_aggregate_meta_intersections(tmp_path: Path) -> None:
    events = [
        {
            "selected_tier": "rag",
            "route_id": "mcp-rag",
            "task": "explain config",
            "cmd": "mcp",
            "cursor_saved": 1000,
            "est_tokens": 10,
            "time_saved_ms": 5000,
        },
        {
            "selected_tier": "python",
            "route_id": "pipeline-check-meta-sync",
            "task": "meta sync check",
            "cmd": "pipeline",
            "cursor_saved": 2000,
            "est_tokens": 0,
            "time_saved_ms": 1000,
        },
        {
            "selected_tier": "tool",
            "route_id": "mcp-search",
            "task": "find foo",
            "cmd": "mcp",
            "cursor_saved": 500,
            "est_tokens": 0,
            "time_saved_ms": 100,
        },
    ]
    report = aggregate_meta_intersections(events, root=tmp_path, usd_per_1m=15.0)
    by_kind = {row["kind"]: row for row in report["kinds"]}
    assert by_kind["rag"]["hits"] == 1
    assert by_kind["rag"]["saved_vs_cursor"] == 1000
    assert by_kind["rag"]["saved_usd_est"] == pytest.approx(0.015)
    assert by_kind["meta"]["hits"] == 1
    assert by_kind["other"]["hits"] == 1
    assert report["classified_events"] == 2


@allure.title("accumulate_totals sums all-time savings including USD estimate")
def test_accumulate_totals() -> None:
    events = [
        {"cursor_saved": 1_000_000, "est_tokens": 0, "time_saved_ms": 2000},
        {"event": "route_outcome", "cursor_saved": 999},
        {"cursor_saved": 500_000, "est_tokens": 1, "time_saved_ms": 1000},
    ]
    block = accumulate_totals(events, usd_per_1m=15.0)
    assert block["events"] == 2
    assert block["saved_vs_cursor"] == 1_500_000
    assert block["saved_usd_est"] == pytest.approx(22.5)
    assert block["time_saved_ms"] == 3000
