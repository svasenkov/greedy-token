"""Naming canon: python-{stem} is an executor prefix, not a language."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import allure
import greedy_token.crystal_ids as crystal_ids
from greedy_token.crystal_ids import (
    choose_stem,
    crystal_id_for_pattern,
    crystal_id_for_stem,
    is_slug_prompt_id,
    is_valid_stem,
    rag_id_for_stem,
    route_id_from_run_arg,
    stem_from_script_path,
    stem_of,
    validate_route_id,
)

pytestmark = [
    allure.epic("Crystallize"),
    allure.parent_suite("Crystallize"),
    allure.feature("Crystal ids"),
    allure.suite("Crystal ids"),
]


@allure.title("is_valid_stem accepts 2–4 kebab tokens")
def test_is_valid_stem() -> None:
    assert is_valid_stem("meta-sync") is True
    assert is_valid_stem("x") is False
    assert is_valid_stem("") is False


@allure.title("choose_stem pads short/stopword-only patterns")
def test_choose_stem_short_and_stopwords() -> None:
    assert choose_stem("to") == "to-task"
    assert choose_stem("") == "task-task"
    assert choose_stem("x") == "x-task"


@allure.title("choose_stem uses slugify fallback when it already has 2+ tokens")
def test_choose_stem_slugify_fallback_long_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    import greedy_token.crystal_ids as crystal_ids

    monkeypatch.setattr(crystal_ids, "slugify", lambda pattern: "alpha-beta-gamma")
    assert crystal_ids.choose_stem("z") == "alpha-beta-gamma"


@allure.title("crystal_id_for_stem and rag_id_for_stem prefix the stem")
def test_id_helpers() -> None:
    assert crystal_id_for_stem("meta-sync-check") == "python-meta-sync-check"
    assert rag_id_for_stem("meta-sync-check") == "script-meta-sync-check"


@allure.title("stem_of strips python-/script- or returns the id")
def test_stem_of_without_prefix() -> None:
    assert stem_of("plain-id") == "plain-id"
    assert stem_of("script-foo-bar") == "foo-bar"


@allure.title("is_slug_prompt_id is false when the pattern is already a short stem")
def test_is_slug_prompt_id_short_pattern() -> None:
    assert is_slug_prompt_id("python-foo-bar", "foo bar") is False


@allure.title("validate_route_id rejects doubled python-python- prefix")
def test_validate_doubled_executor_prefix() -> None:
    err = validate_route_id("python-python-foo-bar")
    assert err is not None
    assert "doubles" in err


@allure.title("validate_route_id rejects non-python-{stem} ids")
def test_validate_invalid_route_id() -> None:
    err = validate_route_id("not_a_crystal")
    assert err is not None
    assert "python-{stem}" in err or "python-" in err


@allure.title("validate_route_id rejects a 4-token id that is the truncated slug of a longer prompt")
def test_validate_truncated_slug_prompt() -> None:
    dds = "d" * 39
    cid = f"python-aa-bb-cc-{dds}"
    pattern = f"aa bb cc {dds} extra"
    err = validate_route_id(cid, pattern=pattern)
    assert err is not None
    assert "slugify" in err


@allure.title("stem_from_script_path covers scripts/, package, and empty")
def test_stem_from_script_path() -> None:
    assert stem_from_script_path("") is None
    assert stem_from_script_path("   ") is None
    assert stem_from_script_path("./") is None
    assert stem_from_script_path("scripts/meta_sync_check.py") == "meta-sync-check"
    assert stem_from_script_path("scripts/foo_bar/foo_bar.py") == "foo-bar"
    assert stem_from_script_path("scripts/foo_bar/other.py") == "other"
    assert stem_from_script_path("bare.py") == "bare"


@allure.title("route_id_from_run_arg normalizes python-/script-/bare ids")
def test_route_id_from_run_arg() -> None:
    assert route_id_from_run_arg("python-foo-bar") == "python-foo-bar"
    assert route_id_from_run_arg("script-foo-bar") == "python-foo-bar"
    assert route_id_from_run_arg("foo-bar") == "python-foo-bar"


@allure.title("crystal_id_for_pattern keeps a valid python-{stem} unchanged")
def test_crystal_id_for_pattern_identity() -> None:
    assert crystal_id_for_pattern("python-meta-sync-check") == "python-meta-sync-check"


@allure.title("scripts/_crystallize_lib derives the same canonical crystal id")
def test_scripts_lib_crystal_id_parity(workspace_root: Path, tmp_path: Path) -> None:
    """The workspace scripts ranker is a thin delegate over the package SSOT:
    same fixture log must yield identical candidates and canonical ids."""
    lib_path = workspace_root / "scripts" / "_crystallize_lib.py"
    spec = importlib.util.spec_from_file_location("_crystallize_lib", lib_path)
    assert spec is not None and spec.loader is not None
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)

    task = "summarize the weekly spend report table"
    log = tmp_path / "usage.jsonl"
    row = {
        "ts": "2026-09-22T04:23:42Z",
        "selected_tier": "cursor",
        "task": task,
        "task_normalized": task,
    }
    log.write_text(json.dumps(row) + "\n", encoding="utf-8")

    from greedy_token.hub.crystallize import rank_candidates as hub_rank_candidates

    scr = lib.rank_candidates(log, None, 15)
    pkg = hub_rank_candidates(since=None, top=15, usage_path=log)

    cid = crystal_id_for_pattern(task)
    assert scr["candidates"] == pkg["candidates"]
    assert scr["candidates"][0]["suggested_script"] == scr["candidates"][0]["crystal_id"] == cid
    assert lib.crystal_id_for_pattern is crystal_ids.crystal_id_for_pattern
