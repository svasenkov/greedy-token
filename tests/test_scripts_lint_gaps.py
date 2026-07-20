"""Unit tests for scripts_lint edge branches (fail_under=100)."""

from __future__ import annotations

from pathlib import Path

import allure

import greedy_token.scripts_lint as sl

pytestmark = [
    allure.epic("Scripts"),
    allure.parent_suite("Scripts"),
    allure.feature("Route lint"),
    allure.suite("Scripts lint gaps"),
]


@allure.title("extract_script_path handles None, embedded, bare, and non-script commands")
def test_extract_script_path() -> None:
    assert sl.extract_script_path(None) is None
    assert sl.extract_script_path("git status") is None
    assert sl.extract_script_path("scripts/foo.sh --flag") == "scripts/foo.sh"
    assert sl.extract_script_path("make && scripts/bar.sh") == "scripts/bar.sh"
    # bare dir prefix with nothing/space after the slash: _COMMAND_SCRIPT can't
    # match (needs a non-space path segment), so the startswith fallback fires
    assert sl.extract_script_path("scripts/") == "scripts/"
    assert sl.extract_script_path("projects/ tail") == "projects/"


@allure.title("_is_consumer_script recognises external/consumer/note markers")
def test_is_consumer_script() -> None:
    assert sl._is_consumer_script({"external": True}) is True
    assert sl._is_consumer_script({"consumer": True}) is True
    assert sl._is_consumer_script({"note": "lives in consumer repo"}) is True
    assert sl._is_consumer_script({}) is False


@allure.title("pattern_violations flags empty, lone verbs, and creative stems")
def test_pattern_violations() -> None:
    assert sl.pattern_violations("") == ["empty pattern"]
    assert sl.pattern_violations("fix")[0].startswith("lone generic verb")
    assert "wire" in sl.pattern_violations("wire up the header")[0]
    assert "refactor" in sl.pattern_violations("refactor module")[0]


@allure.title("lint_routes reports missing scripts and skips package-only roots")
def test_lint_routes_missing_and_package_only(tmp_path: Path) -> None:
    routes = [
        {"target": "cursor"},  # skipped (not python)
        {"target": "python", "id": "no-cmd", "command": None},  # script_missing
        {"target": "python", "id": "bad", "command": "scripts/../../outside.sh"},  # escapes root
    ]
    result = sl.lint_routes(root=tmp_path, routes=routes)
    assert result["ok"] is False
    kinds = {v["kind"] for v in result["violations"]}
    assert "script_missing" in kinds
    assert result["checked_routes"] == 2

    # package-only root: src/greedy_token present, no monorepo phase-manifest → missing script tolerated
    pkg = tmp_path / "pkg"
    (pkg / "src" / "greedy_token").mkdir(parents=True)
    res2 = sl.lint_routes(
        root=pkg,
        routes=[{"target": "python", "id": "p", "command": "scripts/ship.sh"}],
    )
    assert res2["ok"] is True


@allure.title("format_lint_report renders both ok and failure states")
def test_format_lint_report() -> None:
    ok = sl.format_lint_report({"ok": True, "checked_routes": 3, "checked_patterns": 5})
    assert "scripts lint OK" in ok
    bad = sl.format_lint_report({"ok": False, "violations": [{"id": "r1", "detail": "boom"}]})
    assert "FAILED" in bad and "r1: boom" in bad
