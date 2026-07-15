from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.code_search import (
    enrich_search_hits,
    parse_hit_lines,
    search_code,
    unique_hit_paths,
)
from greedy_token.pipeline import parse_pipeline, run_pipeline
from greedy_token.router import has_edit_verbs, route_task
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Code search"),
    allure.parent_suite("Code search"),
    allure.feature("Context enrichment"),
    allure.suite("Context enrichment"),
]


@allure.story("Snippets")
@allure.title("search_code context=snippet includes surrounding lines")
def test_search_snippet_enrichment(minimal_workspace: Path) -> None:
    with allure.step("Search with snippet context"):
        out = search_code("baseUrl", minimal_workspace, path="sample.js", context="snippet")
        attach_text("output", out.text)
    with allure.step("Verify enriched block and counters"):
        assert "baseUrl" in out.text
        assert "enriched context" in out.text
        assert out.hit_count >= 1
        assert out.enriched_files >= 1


@allure.story("Snippets")
@allure.title("search_code context=none stays thin")
def test_search_context_none(minimal_workspace: Path) -> None:
    with allure.step("Search with context=none"):
        out = search_code("baseUrl", minimal_workspace, path="sample.js", context="none")
        attach_text("output", out.text)
    with allure.step("Verify no enrichment block"):
        assert "baseUrl" in out.text
        assert "enriched context" not in out.text
        assert out.enriched_files == 0


@allure.story("Parse hits")
@allure.title("parse_hit_lines extracts path:line:content")
def test_parse_hit_lines() -> None:
    text = "Search: 'x'\n\nprojects/a.js:12:const x = 1\ndocs/b.md:3:hello"
    hits = parse_hit_lines(text)
    assert hits == [
        ("projects/a.js", 12, "const x = 1"),
        ("docs/b.md", 3, "hello"),
    ]
    assert unique_hit_paths(hits, limit=1) == ["projects/a.js"]


@allure.story("Pipeline")
@allure.title("search-enrich-rag recipe chains read-hits")
def test_parse_search_enrich_rag() -> None:
    steps = parse_pipeline("pipeline: search-enrich-rag baseUrl path=sample.js")
    assert [s.step_id for s in steps] == ["search", "read-hits", "rag"]


@allure.story("Pipeline")
@allure.title("run_pipeline search-enrich-rag executes read-hits from prior search")
def test_run_search_enrich_rag(minimal_workspace: Path) -> None:
    result = run_pipeline(
        "pipeline: search-enrich-rag baseUrl path=sample.js",
        minimal_workspace,
        execute=True,
    )
    assert len(result.steps) == 3
    assert result.steps[0].step.step_id == "search"
    assert result.steps[0].executed
    assert result.steps[1].step.step_id == "read-hits"
    assert result.steps[1].ok
    assert "read-hits" in result.steps[1].output
    assert "sample.js" in result.steps[1].output or "enriched" in result.steps[1].output


@allure.story("Router")
@allure.title("edit verbs lower tool-route confidence")
def test_edit_verbs_confidence_penalty(minimal_workspace: Path) -> None:
    assert has_edit_verbs("refactor baseUrl lookup")
    plain = route_task("find baseUrl", minimal_workspace)
    edit = route_task("refactor find baseUrl wiring", minimal_workspace)
    if plain.target == "tool" and edit.target == "tool":
        assert edit.confidence < plain.confidence
        assert "thin context" in edit.note or "thin context" in edit.rationale
