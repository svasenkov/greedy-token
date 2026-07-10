from __future__ import annotations

import sys
from pathlib import Path

import allure
import pytest
import yaml

from greedy_token.paths import find_monorepo_root, load_routes_config
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Configuration"),
    allure.parent_suite("Configuration"),
    allure.feature("Workspace paths"),
    allure.suite("Workspace paths"),
]


@allure.story("GREEDY_TOKEN_ROOT")
@allure.title("find_monorepo_root uses GREEDY_TOKEN_ROOT when set")
def test_find_monorepo_root_from_env(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(minimal_workspace))
    with allure.step("Resolve root from env"):
        root = find_monorepo_root()
        attach_text("root", str(root))
    assert root == minimal_workspace.resolve()


@allure.story("GREEDY_TOKEN_ROOT")
@allure.title("find_monorepo_root exits when GREEDY_TOKEN_ROOT is not a directory")
def test_find_monorepo_root_invalid_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing-dir"
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(missing))
    with allure.step("Resolve root from invalid env path"):
        with pytest.raises(SystemExit, match="not a directory"):
            find_monorepo_root()


@allure.story("Discovery")
@allure.title("find_monorepo_root walks parents for phase-manifest markers")
def test_find_monorepo_root_walks_parents(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_ROOT", raising=False)
    nested = minimal_workspace / "nested" / "deep"
    nested.mkdir(parents=True)
    with allure.step("Resolve root from nested start path"):
        root = find_monorepo_root(nested)
        attach_text("root", str(root))
    assert root == minimal_workspace.resolve()


@allure.story("Discovery")
@allure.title("find_monorepo_root exits when markers are absent")
def test_find_monorepo_root_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_ROOT", raising=False)
    isolated = Path("/tmp/greedy_token_root_isolated")
    empty = isolated / "empty"
    empty.mkdir(parents=True, exist_ok=True)
    with allure.step("Resolve root without markers"):
        with pytest.raises(SystemExit, match="Cannot find workspace root"):
            find_monorepo_root(empty)


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
