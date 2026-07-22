from __future__ import annotations

import sys
from pathlib import Path

import allure
import pytest
import yaml

from greedy_token.paths import find_workspace_root, load_routes_config
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Configuration"),
    allure.parent_suite("Configuration"),
    allure.feature("Workspace paths"),
    allure.suite("Workspace paths"),
]


@allure.story("GREEDY_TOKEN_ROOT")
@allure.title("find_workspace_root uses GREEDY_TOKEN_ROOT when set")
def test_find_workspace_root_from_env(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(minimal_workspace))
    with allure.step("Resolve root from env"):
        root = find_workspace_root()
        attach_text("root", str(root))
    assert root == minimal_workspace.resolve()


@allure.story("GREEDY_TOKEN_ROOT")
@allure.title("find_workspace_root exits when GREEDY_TOKEN_ROOT is not a directory")
def test_find_workspace_root_invalid_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing-dir"
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(missing))
    with allure.step("Resolve root from invalid env path"):
        with pytest.raises(SystemExit, match="not a directory"):
            find_workspace_root()


@allure.story("Discovery")
@allure.title("find_workspace_root walks parents for phase-manifest markers")
def test_find_workspace_root_walks_parents(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_ROOT", raising=False)
    nested = minimal_workspace / "nested" / "deep"
    nested.mkdir(parents=True)
    with allure.step("Resolve root from nested start path"):
        root = find_workspace_root(nested)
        attach_text("root", str(root))
    assert root == minimal_workspace.resolve()


@allure.story("Discovery")
@allure.title("find_workspace_root exits when markers are absent")
def test_find_workspace_root_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_ROOT", raising=False)
    isolated = Path("/tmp/greedy_token_root_isolated")
    empty = isolated / "empty"
    empty.mkdir(parents=True, exist_ok=True)
    with allure.step("Resolve root without markers"):
        with pytest.raises(SystemExit, match="Cannot find workspace root"):
            find_workspace_root(empty)


@allure.story("Routes config")
@allure.title("load_routes_config reads bundled routes.yaml")
def test_load_routes_config() -> None:
    with allure.step("Load routes config"):
        cfg = load_routes_config()
        attach_text("route count", str(len(cfg.get("routes", []))))
    assert isinstance(cfg, dict)
    assert "routes" in cfg


@allure.story("Routes config")
@allure.title("Bundled routes have no phantom null-command executors")
def test_routes_have_no_null_commands() -> None:
    """Regression: ollama-rag-draft had command:null → plan_run 'No executor.'"""
    cfg = load_routes_config()
    null_cmds = [
        r.get("id")
        for r in cfg.get("routes", [])
        if "command" in r and r.get("command") is None
    ]
    attach_text("null-command routes", ", ".join(null_cmds) or "(none)")
    assert null_cmds == []


@allure.story("Generic defaults")
@allure.title("Bundled routes.yaml is generic: rg over '.', rag, cursor — no workspace ids")
def test_bundled_routes_are_generic(tmp_path: Path) -> None:
    from greedy_token.paths import bundled_routes_config

    no_overlay = tmp_path / "no-overlay"
    no_overlay.mkdir()
    with allure.step("Load bundled defaults (no workspace overlay)"):
        cfg = load_routes_config(root=no_overlay)
        ids = [r["id"] for r in cfg["routes"]]
        attach_text("route ids", ", ".join(ids))
    with allure.step("Verify only generic routes ship with the package"):
        assert ids == ["tool-rg-search", "rag-lookup", "cursor-wiring"]
        tool = cfg["routes"][0]
        assert tool["search_paths"] == ["."]
        assert cfg["cursor_fallback"]["message"]
    with allure.step("No overlay file → merged config equals the bundled one"):
        assert cfg == bundled_routes_config()


@allure.story("Generic defaults")
@allure.title("load_routes_config falls back to bundled defaults outside a workspace")
def test_load_routes_config_outside_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    import greedy_token.paths as paths_mod

    def no_root() -> Path:
        raise SystemExit("Cannot find workspace root")

    monkeypatch.setattr(paths_mod, "find_workspace_root", no_root)
    cfg = load_routes_config()
    assert [r["id"] for r in cfg["routes"]] == ["tool-rg-search", "rag-lookup", "cursor-wiring"]


@allure.story("Workspace overlay")
@allure.title("Auto-resolved root picks up the workspace routes overlay")
def test_load_routes_config_auto_root_merges_overlay(minimal_workspace: Path) -> None:
    # autouse fixture sets GREEDY_TOKEN_ROOT=minimal_workspace (routes_file overlay).
    cfg = load_routes_config()
    ids = {r["id"] for r in cfg["routes"]}
    with allure.step("Workspace routes merged over bundled generics"):
        assert "tool-jq-manifest" in ids
        assert "python-meta-sync-check" in ids
        assert "rag-lookup" in ids  # bundled generic survives the merge
    with allure.step("Same-id override: workspace tool-rg-search search_paths win"):
        tool = next(r for r in cfg["routes"] if r["id"] == "tool-rg-search")
        assert tool["search_paths"] == ["projects", "docs", "stacks", "scripts", "generators"]
    with allure.step("Workspace cursor_fallback replaces the bundled message"):
        assert "Нет точного match" in cfg["cursor_fallback"]["message"]


@allure.story("Workspace overlay")
@allure.title("Inline routes win over routes_file entries; inline cursor_fallback wins")
def test_workspace_overlay_inline_beats_routes_file(tmp_path: Path) -> None:
    from greedy_token.paths import workspace_routes_overlay

    (tmp_path / "rf.yaml").write_text(
        "routes:\n"
        "  - id: a\n    target: rag\n    patterns: [alpha]\n"
        "  - id: b\n    target: rag\n    patterns: [from-file]\n"
        "cursor_fallback:\n  message: from-file\n",
        encoding="utf-8",
    )
    (tmp_path / ".greedy-token.yaml").write_text(
        "routes_file: rf.yaml\n"
        "routes:\n"
        "  - id: b\n    target: rag\n    patterns: [inline]\n"
        "  - id: c\n    target: rag\n    patterns: [gamma]\n"
        "cursor_fallback:\n  message: inline\n",
        encoding="utf-8",
    )
    overlay = workspace_routes_overlay(tmp_path)
    ids = [r["id"] for r in overlay["routes"]]
    assert ids == ["a", "b", "c"]
    b = next(r for r in overlay["routes"] if r["id"] == "b")
    assert b["patterns"] == ["inline"]
    assert overlay["cursor_fallback"]["message"] == "inline"


@allure.story("Workspace overlay")
@allure.title("routes_file supports absolute paths; missing file is ignored")
def test_workspace_overlay_routes_file_paths(tmp_path: Path) -> None:
    from greedy_token.paths import workspace_routes_overlay

    ws = tmp_path / "ws"
    ws.mkdir()
    external = tmp_path / "external-routes.yaml"
    external.write_text(
        "routes:\n  - id: ext\n    target: rag\n    patterns: [ext]\n",
        encoding="utf-8",
    )
    (ws / ".greedy-token.yaml").write_text(
        f"routes_file: {external}\n", encoding="utf-8"
    )
    with allure.step("Absolute routes_file is read as-is"):
        overlay = workspace_routes_overlay(ws)
        assert [r["id"] for r in overlay["routes"]] == ["ext"]

    with allure.step("Missing routes_file → empty overlay, no crash"):
        (ws / ".greedy-token.yaml").write_text(
            "routes_file: does-not-exist.yaml\n", encoding="utf-8"
        )
        assert workspace_routes_overlay(ws) == {}


@allure.story("Workspace overlay")
@allure.title("Overlay tolerates junk: non-dict yaml, non-list routes, id-less entries")
def test_workspace_overlay_tolerates_junk(tmp_path: Path) -> None:
    from greedy_token.paths import workspace_routes_overlay

    with allure.step("Workspace config that is a YAML list → no overlay"):
        (tmp_path / ".greedy-token.yaml").write_text("- not\n- a-dict\n", encoding="utf-8")
        assert workspace_routes_overlay(tmp_path) == {}

    with allure.step("routes: scalar and id-less / non-dict entries are dropped"):
        (tmp_path / ".greedy-token.yaml").write_text(
            "routes: nope\n", encoding="utf-8"
        )
        assert workspace_routes_overlay(tmp_path) == {}
        (tmp_path / ".greedy-token.yaml").write_text(
            "routes:\n"
            "  - just-a-string\n"
            "  - target: rag\n    patterns: [no-id]\n"
            "  - id: ok\n    target: rag\n    patterns: [ok]\n",
            encoding="utf-8",
        )
        overlay = workspace_routes_overlay(tmp_path)
        assert [r["id"] for r in overlay["routes"]] == ["ok"]

    with allure.step("cursor_fallback-only config yields fallback-only overlay"):
        (tmp_path / ".greedy-token.yaml").write_text(
            "cursor_fallback:\n  message: only-fallback\n", encoding="utf-8"
        )
        overlay = workspace_routes_overlay(tmp_path)
        assert overlay == {"cursor_fallback": {"message": "only-fallback"}}


@allure.story("Merge priority")
@allure.title("merge_routes_config: overlay replaces same id, prepends new, keeps base")
def test_merge_routes_config_priority() -> None:
    from greedy_token.paths import merge_routes_config

    base = {
        "routes": [
            {"id": "keep", "target": "rag", "patterns": ["k"]},
            {"id": "override-me", "target": "tool", "patterns": ["base"]},
        ],
        "cursor_fallback": {"message": "base"},
    }
    overlay = {
        "routes": [
            {"id": "new-first", "target": "tool", "patterns": ["n"]},
            {"id": "override-me", "target": "tool", "patterns": ["overlay"]},
        ],
        "cursor_fallback": {"message": "overlay"},
    }
    merged = merge_routes_config(base, overlay)
    assert [r["id"] for r in merged["routes"]] == ["new-first", "override-me", "keep"]
    assert merged["routes"][1]["patterns"] == ["overlay"]
    assert merged["cursor_fallback"]["message"] == "overlay"

    with allure.step("Empty overlay returns base untouched"):
        assert merge_routes_config(base, {}) is base

    with allure.step("Routes-only overlay keeps the base cursor_fallback"):
        no_fb = merge_routes_config(base, {"routes": [{"id": "x", "target": "rag"}]})
        assert no_fb["cursor_fallback"]["message"] == "base"


@allure.story("Merge priority")
@allure.title("route_task: workspace route wins a tier tie-break against bundled route")
def test_route_task_workspace_route_wins_tiebreak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.router import route_task

    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / ".greedy-token.yaml").write_text(
        "routes:\n"
        "  - id: ws-rg-search\n"
        "    target: tool\n"
        "    tool: rg\n"
        "    read_only: true\n"
        "    patterns: [find]\n"
        "    search_paths: [src]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(ws))
    with allure.step("'find' ties with bundled tool-rg-search → workspace route first"):
        decision = route_task("find needle", ws)
        attach_text("route_id", decision.route_id)
        assert decision.route_id == "ws-rg-search"
        assert decision.command is not None
        assert " src" in decision.command


@allure.story("Init routes helpers")
@allure.title("detect_search_paths lists top-level folders, skipping hidden and vendor dirs")
def test_detect_search_paths(tmp_path: Path) -> None:
    from greedy_token.paths import detect_search_paths

    project = tmp_path / "plain-project"
    for name in ("src", "docs", "node_modules", ".git", "build"):
        (project / name).mkdir(parents=True)
    (project / "loose-file.txt").write_text("x", encoding="utf-8")
    assert detect_search_paths(project) == ["docs", "src"]

    with allure.step("No usable folders → fall back to '.'"):
        assert detect_search_paths(project / "node_modules") == ["."]


@allure.story("Init routes helpers")
@allure.title("scaffold_routes_overlay reuses bundled tool-rg-search with detected paths")
def test_scaffold_routes_overlay(tmp_path: Path) -> None:
    from greedy_token.paths import scaffold_routes_overlay

    project = tmp_path / "scaffold-project"
    (project / "app").mkdir(parents=True)
    overlay = scaffold_routes_overlay(project)
    route = overlay["routes"][0]
    assert route["id"] == "tool-rg-search"
    assert route["search_paths"] == ["app"]
    assert "find" in route["patterns"]


@allure.story("Init routes helpers")
@allure.title("upsert_workspace_routes replaces same id in place and appends new routes")
def test_upsert_workspace_routes(tmp_path: Path) -> None:
    import yaml

    from greedy_token.paths import upsert_workspace_routes

    ws = tmp_path / "upsert-ws"
    ws.mkdir()
    with allure.step("Fresh workspace → config file created with routes"):
        path = upsert_workspace_routes(
            ws, {"routes": [{"id": "a", "target": "rag", "patterns": ["a1"]}]}
        )
        assert path == ws / ".greedy-token.yaml"
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert [r["id"] for r in cfg["routes"]] == ["a"]

    with allure.step("Same id replaced in place, new id appended, other keys kept"):
        path.write_text(
            path.read_text(encoding="utf-8") + "footer:\n  style: compact\n",
            encoding="utf-8",
        )
        upsert_workspace_routes(
            ws,
            {
                "routes": [
                    {"id": "b", "target": "rag", "patterns": ["b1"]},
                    {"id": "a", "target": "rag", "patterns": ["a2"]},
                ],
                "cursor_fallback": {"message": "upserted"},
            },
        )
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert [r["id"] for r in cfg["routes"]] == ["a", "b"]
        assert cfg["routes"][0]["patterns"] == ["a2"]
        assert cfg["cursor_fallback"]["message"] == "upserted"
        assert cfg["footer"]["style"] == "compact"
