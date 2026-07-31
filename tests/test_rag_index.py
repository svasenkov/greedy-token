from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.rag_index import get_indexed_chunks, invalidate_rag_index
from greedy_token.rag_search import search_rag
from tests.allure_reporting import attach_json, attach_text

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
    with allure.step("Load indexed chunks twice"):
        first = get_indexed_chunks(minimal_workspace)
        second = get_indexed_chunks(minimal_workspace)
        attach_json("first chunk", {"id": first[0].meta["id"], "count": len(first)})
        attach_text("cache hit", str(first is second))
    with allure.step("Verify cache returns same object"):
        assert first is second
        assert len(first) == 1
        assert first[0].meta["id"] == "test-baseurl"


@allure.story("Invalidation")
@allure.title("Index invalidates when chunk file is edited")
def test_index_invalidates_on_chunk_edit(minimal_workspace: Path) -> None:
    invalidate_rag_index()
    before = get_indexed_chunks(minimal_workspace)
    chunk = minimal_workspace / "docs/rag/config/test-chunk.md"
    with allure.step("Edit chunk file and reload index"):
        chunk.write_text(
            chunk.read_text(encoding="utf-8") + "\nnewkeyword flag appears here.\n",
            encoding="utf-8",
        )
        invalidate_rag_index(minimal_workspace)
        after = get_indexed_chunks(minimal_workspace)
        attach_text("cache invalidated", str(before is not after))
        attach_text("newkeyword in body", str("newkeyword" in after[0].body_tokens))
    with allure.step("Verify index was rebuilt with new content"):
        assert before is not after
        assert "newkeyword" in after[0].body_tokens


@allure.story("Greedy token")
@allure.title("RAG search reads only top-ranked chunk files")
def test_search_reads_only_top_hits(minimal_workspace: Path) -> None:
    rag = minimal_workspace / "docs/rag"
    manifest_lines = []
    for i in range(3):
        rel = f"docs/rag/config/chunk-{i}.md"
        (rag / "config" / f"chunk-{i}.md").write_text(
            f"sharedtoken value number {i}\n",
            encoding="utf-8",
        )
        manifest_lines.append(
            json.dumps(
                {
                    "id": f"chunk-{i}",
                    "domain": "config",
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

    with allure.step("Search RAG with file read tracking"):
        with patch.object(Path, "read_text", tracking_read_text):
            hits = search_rag("sharedtoken", minimal_workspace, limit=1)
        attach_text("read paths", "\n".join(read_paths))
        attach_text("hit count", str(len(hits)))
    with allure.step("Verify only top hit file was read"):
        assert len(hits) == 1
        assert len(read_paths) == 1


@allure.story("Search")
@allure.title("RAG search still finds baseUrl after index rebuild")
def test_search_rag_still_finds_baseurl(minimal_workspace: Path) -> None:
    with allure.step("Rebuild index and search for baseUrl"):
        invalidate_rag_index(minimal_workspace)
        hits = search_rag("baseUrl -D flag", minimal_workspace, domains=["config"], limit=5)
        attach_json("hits", [{"chunk_id": h.chunk_id, "has_body": h.body is not None} for h in hits])
    with allure.step("Verify baseUrl chunk is found with body"):
        assert len(hits) >= 1
        assert hits[0].body is not None


@allure.story("Search")
@allure.title("search_rag returns empty for token-less query")
def test_search_rag_empty_query(minimal_workspace: Path) -> None:
    assert search_rag("!!!", minimal_workspace) == []


@allure.story("Format")
@allure.title("format_hits reports no hits message")
def test_format_hits_empty() -> None:
    from greedy_token.rag_search import format_hits

    out = format_hits("missing topic", [])
    assert "No RAG hits" in out


@allure.story("Index")
@allure.title("RAG index handles missing manifest")
def test_rag_index_missing_manifest() -> None:
    from greedy_token.rag_index import _load_manifest_rows, invalidate_rag_index

    isolated = Path("/tmp/greedy_token_rag_isolated")
    isolated.mkdir(parents=True, exist_ok=True)
    invalidate_rag_index(isolated)
    assert _load_manifest_rows(isolated / "missing.jsonl") == []
    chunks = get_indexed_chunks(isolated)
    assert chunks == []


def test_manifest_loader_enforces_size_row_and_object_limits(
    minimal_workspace: Path,
) -> None:
    from greedy_token.rag_index import _load_manifest_rows

    manifest = minimal_workspace / "docs" / "rag" / "manifest.jsonl"
    with patch("greedy_token.rag_index.MAX_MANIFEST_BYTES", 1):
        assert _load_manifest_rows(manifest) == []
    manifest.write_text(
        "[]\n"
        '{"id":"one","path":"docs/rag/config/test-chunk.md"}\n'
        '{"id":"two","path":"docs/rag/config/test-chunk.md"}\n',
        encoding="utf-8",
    )
    with patch("greedy_token.rag_index.MAX_MANIFEST_ROWS", 1):
        rows = _load_manifest_rows(manifest)
    assert [row["id"] for row in rows] == ["one"]


def test_chunk_reader_skips_io_errors_and_invalid_utf8(
    minimal_workspace: Path,
) -> None:
    from greedy_token.rag_index import _read_text_chunk

    chunk = minimal_workspace / "docs" / "rag" / "config" / "test-chunk.md"
    with patch.object(Path, "read_bytes", side_effect=OSError("denied")):
        assert _read_text_chunk(chunk) is None
    chunk.write_bytes(b"\xff\xfe")
    assert _read_text_chunk(chunk) is None


def test_manifest_resolution_error_is_confined(
    minimal_workspace: Path,
) -> None:
    from greedy_token.rag_index import load_manifest_documents

    manifest = minimal_workspace / "docs" / "rag" / "manifest.jsonl"
    original_resolve = Path.resolve

    def resolve(path: Path, *args, **kwargs):
        if path == manifest:
            raise OSError("cannot resolve")
        return original_resolve(path, *args, **kwargs)

    with patch.object(Path, "resolve", resolve):
        assert load_manifest_documents(minimal_workspace) == []


def test_frontmatter_strip_and_invalid_relative_path(
    minimal_workspace: Path,
) -> None:
    from greedy_token.rag_index import _confined_chunk_path, _strip_frontmatter

    assert _strip_frontmatter("---\nid: x\n---\nBody") == "Body"
    assert _confined_chunk_path(minimal_workspace, None) is None

