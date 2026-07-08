from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.rag_index import get_indexed_chunks, invalidate_rag_index
from greedy_token.rag_search import search_rag

pytestmark = [
    allure.epic("RAG"),
    allure.parent_suite("RAG"),
    allure.feature("RAG index"),
    allure.suite("RAG index"),
]


@allure.story("Cache")
@allure.title("RAG index caches manifest entries")
def test_get_indexed_chunks_caches(minimal_workspace: Path) -> None:
    invalidate_rag_index()
    first = get_indexed_chunks(minimal_workspace)
    second = get_indexed_chunks(minimal_workspace)
    assert first is second
    assert len(first) == 1
    assert first[0].meta["id"] == "test-baseurl"


@allure.story("Invalidation")
@allure.title("Index invalidates when chunk file is edited")
def test_index_invalidates_on_chunk_edit(minimal_workspace: Path) -> None:
    invalidate_rag_index()
    before = get_indexed_chunks(minimal_workspace)
    chunk = minimal_workspace / "docs/rag/e2e/test-chunk.md"
    chunk.write_text(
        chunk.read_text(encoding="utf-8") + "\nnewkeyword flag appears here.\n",
        encoding="utf-8",
    )
    invalidate_rag_index(minimal_workspace)
    after = get_indexed_chunks(minimal_workspace)
    assert before is not after
    assert "newkeyword" in after[0].body_tokens


@allure.story("Token economy")
@allure.title("RAG search reads only top-ranked chunk files")
def test_search_reads_only_top_hits(minimal_workspace: Path) -> None:
    rag = minimal_workspace / "docs/rag"
    manifest_lines = []
    for i in range(3):
        rel = f"docs/rag/e2e/chunk-{i}.md"
        (rag / "e2e" / f"chunk-{i}.md").write_text(
            f"sharedtoken value number {i}\n",
            encoding="utf-8",
        )
        manifest_lines.append(
            json.dumps(
                {
                    "id": f"chunk-{i}",
                    "domain": "e2e",
                    "path": rel,
                    "tags": ["sharedtoken"],
                }
            )
        )
    (rag / "manifest.jsonl").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    invalidate_rag_index(minimal_workspace)
    get_indexed_chunks(minimal_workspace)

    read_paths: list[str] = []
    original_read_text = Path.read_text

    def tracking_read_text(self, *args, **kwargs):
        read_paths.append(str(self))
        return original_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", tracking_read_text):
        hits = search_rag("sharedtoken", minimal_workspace, limit=1)

    assert len(hits) == 1
    assert len(read_paths) == 1


@allure.story("Search")
@allure.title("RAG search still finds baseUrl after index rebuild")
def test_search_rag_still_finds_baseurl(minimal_workspace: Path) -> None:
    invalidate_rag_index(minimal_workspace)
    hits = search_rag("baseUrl -D flag", minimal_workspace, domains=["e2e"], limit=5)
    assert len(hits) >= 1
    assert hits[0].body is not None
