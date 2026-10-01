from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import allure
from greedy_token.budget import rag_est_tokens
from greedy_token.rag_index import IndexedChunk
from greedy_token.rag_search import _excerpt, _score_indexed, format_hits, search_rag
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


def test_overlap_score_returns_zero_without_common_tokens() -> None:
    chunk = IndexedChunk(
        meta={"id": "chunk"},
        rel_path="docs/rag/chunk.md",
        domain="config",
        body="body",
        body_tokens=frozenset({"body"}),
        meta_tokens=frozenset({"meta"}),
    )

    assert _score_indexed({"absent"}, chunk) == 0.0
    assert _score_indexed({"body"}, chunk) == 1.0


def test_overlap_fallback_skips_domains_and_zero_scores(
    minimal_workspace: Path,
) -> None:
    from greedy_token.rag_fts import Fts5Unavailable

    with patch(
        "greedy_token.rag_search.search_bm25",
        side_effect=Fts5Unavailable("missing"),
    ):
        assert search_rag(
            "baseUrl", minimal_workspace, domains=["testing"]
        ) == []
        assert search_rag("absent-token", minimal_workspace) == []


def test_excerpt_covers_matching_and_head_truncation() -> None:
    assert _excerpt("needle " + "x" * 20, {"needle"}, max_len=10).endswith("…")
    assert _excerpt("short head", {"absent"}, max_len=20) == "short head"
    assert _excerpt("x" * 20, {"absent"}, max_len=10).endswith("…")


@allure.story("Payload cap")
@allure.title("monorepo corpus: hits over rag.max_payload_tokens are dropped with a flag")
def test_search_rag_monorepo_cap(workspace_root: Path) -> None:
    with allure.step("Measure the uncapped payload on the real monorepo corpus"):
        full = search_rag("check", workspace_root, limit=5, max_payload_tokens=0)
        full_est = rag_est_tokens(full, workspace_root)
        attach_text("uncapped est_tokens", str(full_est))
    if full_est <= 4000:
        pytest.skip(f"monorepo corpus drifted below the cap scenario (est={full_est})")
    with allure.step("Re-run with max_payload_tokens=4000"):
        capped = search_rag("check", workspace_root, limit=5, max_payload_tokens=4000)
        capped_est = rag_est_tokens(capped, workspace_root)
        attach_text("capped est_tokens", str(capped_est))
    with allure.step("Verify fewer tokens and the truncated flag"):
        assert capped_est <= 4000 < full_est
        assert capped.truncated is True
        assert capped.hits_dropped == len(full) - len(capped)
