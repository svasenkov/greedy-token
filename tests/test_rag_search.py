from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.rag_search import format_hits, search_rag

pytestmark = [allure.epic("RAG"), allure.feature("RAG search")]


@allure.story("Manifest search")
@allure.title("search_rag finds baseUrl chunk in e2e domain")
def test_search_rag_finds_baseurl(minimal_workspace: Path) -> None:
    hits = search_rag("baseUrl -D flag", minimal_workspace, domains=["e2e"], limit=5)
    assert len(hits) >= 1
    assert hits[0].domain == "e2e"
    assert "baseurl" in hits[0].excerpt.lower() or "baseUrl" in hits[0].excerpt


@allure.story("Input validation")
@allure.title("search_rag returns empty list for blank query")
def test_search_rag_empty_query(minimal_workspace: Path) -> None:
    assert search_rag("", minimal_workspace) == []


@allure.story("Missing manifest")
@allure.title("search_rag returns empty when manifest is absent")
def test_search_rag_no_manifest(tmp_path: Path) -> None:
    assert search_rag("anything", tmp_path) == []


@allure.story("Formatting")
@allure.title("format_hits reports no hits with manifest hint")
def test_format_hits_empty() -> None:
    out = format_hits("missing", [])
    assert "No RAG hits" in out
    assert "manifest.jsonl" in out


@allure.story("Formatting")
@allure.title("format_hits includes chunk id and excerpt")
def test_format_hits_includes_excerpt(minimal_workspace: Path) -> None:
    hits = search_rag("baseUrl", minimal_workspace, limit=1)
    out = format_hits("baseUrl", hits)
    assert "RAG hits for: baseUrl" in out
    assert "test-baseurl" in out
