from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.rag_search import format_hits, search_rag
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("RAG"),
    allure.parent_suite("RAG"),
    allure.feature("RAG search"),
    allure.suite("RAG search"),
]


@allure.story("Manifest search")
@allure.title("RAG search finds baseUrl chunk in config domain")
def test_search_rag_finds_baseurl(minimal_workspace: Path) -> None:
    with allure.step("Search RAG for baseUrl in config domain"):
        hits = search_rag("baseUrl -D flag", minimal_workspace, domains=["config"], limit=5)
        attach_json("hits", [{"domain": h.domain, "excerpt": h.excerpt} for h in hits])
    with allure.step("Verify baseUrl chunk is found"):
        assert len(hits) >= 1
        assert hits[0].domain == "config"
        assert "baseurl" in hits[0].excerpt.lower() or "baseUrl" in hits[0].excerpt


@allure.story("Input validation")
@allure.title("RAG search returns empty list for blank query")
def test_search_rag_empty_query(minimal_workspace: Path) -> None:
    with allure.step("Search RAG with blank query"):
        hits = search_rag("", minimal_workspace)
        attach_text("hit count", str(len(hits)))
    with allure.step("Verify empty result list"):
        assert hits == []


@allure.story("Missing manifest")
@allure.title("RAG search returns empty when manifest is absent")
def test_search_rag_no_manifest(tmp_path: Path) -> None:
    with allure.step("Search RAG in workspace without manifest"):
        hits = search_rag("anything", tmp_path)
        attach_text("hit count", str(len(hits)))
    with allure.step("Verify empty result list"):
        assert hits == []


@allure.story("Formatting")
@allure.title("RAG hit formatter reports no hits with manifest hint")
def test_format_hits_empty() -> None:
    with allure.step("Format empty RAG hits"):
        out = format_hits("missing", [])
        attach_text("formatted output", out)
    with allure.step("Verify no-hits message with manifest hint"):
        assert "No RAG hits" in out
        assert "manifest.jsonl" in out


@allure.story("Formatting")
@allure.title("RAG hit formatter includes chunk id and excerpt")
def test_format_hits_includes_excerpt(minimal_workspace: Path) -> None:
    with allure.step("Search and format RAG hits"):
        hits = search_rag("baseUrl", minimal_workspace, limit=1)
        out = format_hits("baseUrl", hits)
        attach_text("formatted output", out)
    with allure.step("Verify chunk id and excerpt in output"):
        assert "RAG hits for: baseUrl" in out
        assert "test-baseurl" in out
