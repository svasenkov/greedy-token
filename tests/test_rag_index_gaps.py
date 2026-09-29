"""Mutation kill-tests for rag_index: boundaries, exact hashes, blob layout."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import allure
from greedy_token.rag_index import (
    _cache,
    _CacheEntry,
    _confined_chunk_path,
    _load_manifest_rows,
    _meta_blob,
    _normalize,
    _read_text_chunk,
    _strip_frontmatter,
    get_indexed_chunks,
    invalidate_rag_index,
    load_manifest_documents,
)
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("RAG"),
    allure.parent_suite("RAG"),
    allure.feature("RAG index"),
    allure.suite("RAG index mutation gaps"),
]


# --- _strip_frontmatter: delimiter offsets ---


@allure.story("Frontmatter")
@allure.title("closer search starts past the opening delimiter and picks the first close")
def test_strip_frontmatter_offsets_exact() -> None:
    # baseline
    assert _strip_frontmatter("---\nid: x\n---\nBody") == "Body"
    # opening "---\n" at pos 0 must not serve as its own closer — find() starts
    # at offset 4; a 0/None/dropped start returns "rest" here.
    assert _strip_frontmatter("---\n---\nrest") == "---\n---\nrest"
    # a closer beginning exactly at offset 4 is a match — a start of 5 misses it.
    assert _strip_frontmatter("---\n\n---\nx") == "x"
    # first close wins — rfind would jump to the last marker.
    assert _strip_frontmatter("---\na\n---\nb\n---\nc") == "b\n---\nc"
    # no closer → whole text; `end != +1/-2` mutants would slice from -1+5.
    assert _strip_frontmatter("---\nbody") == "---\nbody"
    assert _strip_frontmatter("plain") == "plain"


# --- _load_manifest_rows: byte-size boundary ---


@allure.story("Manifest rows")
@allure.title("manifest of exactly MAX_MANIFEST_BYTES still loads (>, not >=)")
def test_load_manifest_rows_size_boundary(minimal_workspace: Path) -> None:
    manifest = minimal_workspace / "docs" / "rag" / "manifest.jsonl"
    size = manifest.stat().st_size
    with patch("greedy_token.rag_index.MAX_MANIFEST_BYTES", size):
        rows = _load_manifest_rows(manifest)
    assert len(rows) == 1


# --- _meta_blob: exact layout ---


@allure.story("Meta blob")
@allure.title("blob joins id, domain, space-joined tags and path stem")
def test_meta_blob_full_meta() -> None:
    meta = {"id": "I", "domain": "d", "tags": ["a", "b"], "path": "docs/rag/f.md"}
    # kills get(None)/wrong-key mutants (real values dropped) and the
    # "XX XX" tag-join mutant.
    assert _meta_blob(meta) == "I d a b f"


@allure.story("Meta blob")
@allure.title("missing keys produce empty segments, never None/crash")
def test_meta_blob_missing_keys() -> None:
    # kills "XXXX" defaults (visible), None/dropped defaults (join/Path crash).
    assert _meta_blob({}) == "   "


# --- invalidate_rag_index: pops the resolved root key ---


@allure.story("Cache")
@allure.title("invalidate(root) evicts the resolved-root cache entry")
def test_invalidate_rag_index_resolved_key(minimal_workspace: Path) -> None:
    key = minimal_workspace.resolve()
    _cache[key] = _CacheEntry(fingerprint=(("k", "h"),), chunks=[])
    try:
        invalidate_rag_index(minimal_workspace)
        # pop(None, None) mutant leaves the entry in place.
        assert key not in _cache
    finally:
        _cache.pop(key, None)


# --- _confined_chunk_path: non-string rel ---


@allure.story("Path confinement")
@allure.title("non-string rel is rejected via or-guard, not and")
def test_confined_chunk_path_non_string(minimal_workspace: Path) -> None:
    # and-mutant falls through to Path(42) → TypeError.
    assert _confined_chunk_path(minimal_workspace, 42) is None


# --- _read_text_chunk: missing path and size boundary ---


@allure.story("Chunk reader")
@allure.title("missing path returns None without touching stat()")
def test_read_text_chunk_missing(tmp_path: Path) -> None:
    # and-mutant evaluates path.stat() on a missing file → FileNotFoundError.
    assert _read_text_chunk(tmp_path / "nope.md") is None


@allure.story("Chunk reader")
@allure.title("file of exactly MAX_CHUNK_BYTES still reads (>, not >=)")
def test_read_text_chunk_size_boundary(tmp_path: Path) -> None:
    chunk = tmp_path / "exact.md"
    chunk.write_text("abcde", encoding="utf-8")
    with patch("greedy_token.rag_index.MAX_CHUNK_BYTES", 5):
        assert _read_text_chunk(chunk) == "abcde"


# --- _build_index fields via get_indexed_chunks ---


@allure.story("Index build")
@allure.title("chunk carries document rel_path and domain verbatim")
def test_build_index_fields(minimal_workspace: Path) -> None:
    invalidate_rag_index(minimal_workspace)
    chunks = get_indexed_chunks(minimal_workspace)
    assert len(chunks) == 1
    assert chunks[0].rel_path == "docs/rag/config/test-chunk.md"
    assert chunks[0].domain == "config"


# --- get_indexed_chunks: fingerprint invalidation ---


@allure.story("Cache")
@allure.title("chunk edit invalidates the index even without explicit invalidate()")
def test_index_rebuilds_on_content_change(minimal_workspace: Path) -> None:
    invalidate_rag_index(minimal_workspace)
    first = get_indexed_chunks(minimal_workspace)
    chunk_file = minimal_workspace / "docs" / "rag" / "config" / "test-chunk.md"
    chunk_file.write_text("brandnewtoken appears here\n", encoding="utf-8")
    second = get_indexed_chunks(minimal_workspace)
    # fp=None mutant → fingerprint always "matches" → stale cache forever.
    assert second is not first
    assert "brandnewtoken" in second[0].body_tokens


# --- load_manifest_documents: canonical key + occurrence accounting ---


def _write_manifest(root: Path, rows: list[dict]) -> None:
    (root / "docs" / "rag" / "manifest.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


@allure.story("Manifest documents")
@allure.title("entry_key/content_hash are sha256 of canonical meta + NUL + body")
def test_manifest_documents_deterministic_keys(minimal_workspace: Path) -> None:
    # unsorted key order + non-ASCII values pin sort_keys/ensure_ascii down.
    meta = {"id": "док", "domain": "config", "path": "docs/rag/config/test-chunk.md", "tags": ["тэг"]}
    _write_manifest(minimal_workspace, [meta])
    docs = load_manifest_documents(minimal_workspace)
    assert len(docs) == 1
    canonical = json.dumps(meta, sort_keys=True, ensure_ascii=False).encode("utf-8")
    base = hashlib.sha256(canonical).hexdigest()
    # kills dumps(None), sort_keys=None/False/dropped, ensure_ascii flips,
    # base_key=None.
    assert docs[0].entry_key == f"{base}:0"
    normalized = _normalize(_strip_frontmatter(docs[0].body))
    expected_hash = hashlib.sha256(
        canonical + b"\0" + normalized.encode("utf-8")
    ).hexdigest()
    # kills the b"XX\0XX" separator mutant.
    assert docs[0].content_hash == expected_hash


@allure.story("Manifest documents")
@allure.title("duplicate metas get :0/:1 occurrence suffixes; missing domain is ''")
def test_manifest_documents_occurrence_and_domain(minimal_workspace: Path) -> None:
    meta = {"id": "dup", "path": "docs/rag/config/test-chunk.md"}
    _write_manifest(minimal_workspace, [meta, meta])
    docs = load_manifest_documents(minimal_workspace)
    assert len(docs) == 2
    # kills get(None) (both ":0"), default 1 (":1" first), and the
    # occurrence+/- counter mutants.
    assert docs[0].entry_key.endswith(":0")
    assert docs[1].entry_key.endswith(":1")
    attach_text("entry keys", "\n".join(d.entry_key for d in docs))
    # kills domain defaults None/dropped/"XXXX" → "None"/"None"/"XXXX".
    assert docs[0].domain == ""
    assert docs[1].domain == ""
