from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from greedy_token.rag_fts import (
    Fts5Unavailable,
    _cache_root,
    _connect,
    _ensure_schema,
    index_path,
    search_bm25,
)
from greedy_token.rag_index import (
    MAX_CHUNK_BYTES,
    load_manifest_documents,
)
from greedy_token.rag_search import format_hits, search_rag


def _add_chunk(
    root: Path,
    *,
    chunk_id: str,
    body: str,
    domain: str = "testing",
    filename: str | None = None,
) -> Path:
    filename = filename or f"{chunk_id}.md"
    path = root / "docs" / "rag" / domain / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    manifest = root / "docs" / "rag" / "manifest.jsonl"
    row = {
        "id": chunk_id,
        "path": path.relative_to(root).as_posix(),
        "domain": domain,
        "tags": [chunk_id],
    }
    with manifest.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


@pytest.fixture(autouse=True)
def isolated_rag_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path.parent / f"{tmp_path.name}-rag-cache"
    monkeypatch.setenv("GREEDY_TOKEN_CACHE_DIR", str(cache))


def test_unicode61_bm25_handles_ru_en_and_code_identifiers(
    minimal_workspace: Path,
) -> None:
    _add_chunk(
        minimal_workspace,
        chunk_id="unicode-code",
        body="Настройка профиля использует resolveBaseUrl и snake_case_identifier.",
    )

    russian = search_rag("настройка профиля", minimal_workspace)
    code = search_rag("snake_case_identifier", minimal_workspace)
    camel = search_rag("RESOLVEBASEURL", minimal_workspace)

    assert russian[0].chunk_id == "unicode-code"
    assert code[0].chunk_id == "unicode-code"
    assert camel[0].chunk_id == "unicode-code"
    assert all(hit.engine == "fts5-bm25" for hit in (russian[0], code[0], camel[0]))


def test_query_is_normalized_and_fts_syntax_is_not_executed(
    minimal_workspace: Path,
) -> None:
    _add_chunk(
        minimal_workspace,
        chunk_id="normalized",
        body="Ｆｕｌｌｗｉｄｔｈ BaseUrl and café configuration.",
    )

    hits = search_rag('ＦＵＬＬＷＩＤＴＨ" OR * NOT', minimal_workspace)

    assert hits[0].chunk_id == "normalized"


def test_index_contains_only_safe_manifest_documents(
    minimal_workspace: Path, tmp_path: Path
) -> None:
    rag = minimal_workspace / "docs" / "rag"
    (rag / "unlisted.md").write_text("unique-unlisted-secret", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("escaped-secret", encoding="utf-8")
    binary = rag / "binary.md"
    binary.write_bytes(b"\x00binary-secret")
    missing = rag / "missing.md"
    manifest = rag / "manifest.jsonl"
    unsafe_rows = [
        {"id": "escape", "path": "../outside.md", "domain": "testing"},
        {"id": "absolute", "path": str(outside), "domain": "testing"},
        {
            "id": "binary",
            "path": binary.relative_to(minimal_workspace).as_posix(),
            "domain": "testing",
        },
        {
            "id": "missing",
            "path": missing.relative_to(minimal_workspace).as_posix(),
            "domain": "testing",
        },
    ]
    with manifest.open("a", encoding="utf-8") as stream:
        for row in unsafe_rows:
            stream.write(json.dumps(row) + "\n")

    documents = load_manifest_documents(minimal_workspace)

    assert [document.meta["id"] for document in documents] == ["test-baseurl"]
    assert search_rag("unique-unlisted-secret", minimal_workspace) == []
    assert search_rag("escaped-secret", minimal_workspace) == []
    assert search_rag("binary-secret", minimal_workspace) == []


def test_oversized_manifest_chunk_is_skipped(minimal_workspace: Path) -> None:
    huge = minimal_workspace / "docs" / "rag" / "huge.md"
    huge.write_bytes(b"x" * (MAX_CHUNK_BYTES + 1))
    manifest = minimal_workspace / "docs" / "rag" / "manifest.jsonl"
    row = {
        "id": "huge",
        "path": huge.relative_to(minimal_workspace).as_posix(),
        "domain": "testing",
    }
    with manifest.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\n")

    assert all(doc.meta["id"] != "huge" for doc in load_manifest_documents(minimal_workspace))


def test_manifest_symlink_outside_rag_root_is_rejected(
    minimal_workspace: Path, tmp_path: Path
) -> None:
    manifest = minimal_workspace / "docs" / "rag" / "manifest.jsonl"
    external = tmp_path / "external-manifest.jsonl"
    external.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    manifest.unlink()
    manifest.symlink_to(external)

    assert load_manifest_documents(minimal_workspace) == []
    assert search_rag("baseUrl", minimal_workspace) == []


def test_content_hash_invalidates_even_when_mtime_and_size_match(
    minimal_workspace: Path,
) -> None:
    chunk = _add_chunk(
        minimal_workspace,
        chunk_id="hash-change",
        body="alpha marker",
    )
    before = chunk.stat()
    assert search_rag("alpha", minimal_workspace)[0].chunk_id == "hash-change"

    chunk.write_text("omega marker", encoding="utf-8")
    os.utime(chunk, ns=(before.st_atime_ns, before.st_mtime_ns))

    assert search_rag("alpha", minimal_workspace) == []
    assert search_rag("omega", minimal_workspace)[0].chunk_id == "hash-change"


def test_incremental_index_removes_deleted_manifest_entries(
    minimal_workspace: Path,
) -> None:
    _add_chunk(
        minimal_workspace,
        chunk_id="temporary",
        body="temporary_unique_marker",
    )
    manifest = minimal_workspace / "docs" / "rag" / "manifest.jsonl"
    original_row = manifest.read_text(encoding="utf-8").splitlines()[0]
    assert search_rag("temporary_unique_marker", minimal_workspace)

    manifest.write_text(original_row + "\n", encoding="utf-8")

    assert search_rag("temporary_unique_marker", minimal_workspace) == []


def test_cache_is_outside_workspace(minimal_workspace: Path) -> None:
    search_rag("baseUrl", minimal_workspace)

    cache = index_path(minimal_workspace)
    assert cache.is_file()
    assert not cache.is_relative_to(minimal_workspace)


def test_cache_honors_xdg_when_explicit_directory_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_CACHE_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    assert _cache_root() == tmp_path / "greedy-token"


@pytest.mark.parametrize(
    ("message", "expected_exception"),
    [
        ("no such module: fts5", Fts5Unavailable),
        ("disk I/O error", sqlite3.OperationalError),
    ],
)
def test_connect_translates_only_fts5_errors(
    minimal_workspace: Path,
    message: str,
    expected_exception: type[Exception],
) -> None:
    connection = sqlite3.connect(":memory:")
    with (
        patch("greedy_token.rag_fts.sqlite3.connect", return_value=connection),
        patch(
            "greedy_token.rag_fts._ensure_schema",
            side_effect=sqlite3.OperationalError(message),
        ),
        pytest.raises(expected_exception),
    ):
        _connect(minimal_workspace)


def test_schema_version_mismatch_rebuilds_fts_tables() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE rag_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO rag_meta(key, value) VALUES (?, ?)", ("schema_version", "old")
    )

    _ensure_schema(connection)

    version = connection.execute(
        "SELECT value FROM rag_meta WHERE key = ?", ("schema_version",)
    ).fetchone()["value"]
    assert version == "2"
    connection.close()


def test_search_bm25_handles_empty_query_and_empty_manifest(tmp_path: Path) -> None:
    assert search_bm25("", tmp_path) == []
    assert search_bm25("term", tmp_path) == []


@pytest.mark.parametrize(
    ("message", "expected_exception"),
    [
        ("no such module: fts5", Fts5Unavailable),
        ("database locked", sqlite3.OperationalError),
    ],
)
def test_search_translates_only_fts5_query_errors(
    minimal_workspace: Path,
    message: str,
    expected_exception: type[Exception],
) -> None:
    class BrokenConnection:
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError(message)

        def close(self) -> None:
            pass

    with (
        patch("greedy_token.rag_fts._connect", return_value=BrokenConnection()),
        patch("greedy_token.rag_fts._sync_index"),
        pytest.raises(expected_exception),
    ):
        search_bm25("baseUrl", minimal_workspace)


def test_search_ignores_stale_rows_and_filtered_domains(
    minimal_workspace: Path,
) -> None:
    document = load_manifest_documents(minimal_workspace)[0]

    class Rows:
        def __init__(self, rows: list[dict]) -> None:
            self.rows = rows

        def execute(self, *args, **kwargs):
            return self

        def fetchall(self) -> list[dict]:
            return self.rows

        def close(self) -> None:
            pass

    stale = {
        "entry_key": "removed",
        "chunk_id": "removed",
        "path": "removed",
        "domain": "config",
        "bm25_score": -1.0,
    }
    filtered = {
        "entry_key": document.entry_key,
        "chunk_id": "test-baseurl",
        "path": document.rel_path,
        "domain": document.domain,
        "bm25_score": -1.0,
    }
    with (
        patch("greedy_token.rag_fts._sync_index"),
        patch("greedy_token.rag_fts._connect", return_value=Rows([stale])),
    ):
        assert search_bm25("baseUrl", minimal_workspace) == []
    with (
        patch("greedy_token.rag_fts._sync_index"),
        patch("greedy_token.rag_fts._connect", return_value=Rows([filtered])),
    ):
        assert search_bm25(
            "baseUrl", minimal_workspace, domains=["testing"]
        ) == []


def test_overlap_fallback_preserves_public_api(minimal_workspace: Path) -> None:
    with patch(
        "greedy_token.rag_search.search_bm25",
        side_effect=Fts5Unavailable("no such module: fts5"),
    ):
        hits = search_rag("baseUrl", minimal_workspace)

    assert hits[0].chunk_id == "test-baseurl"
    assert hits[0].engine == "overlap"
    assert hits[0].score > 0


def test_explain_output_names_bm25_engine_and_score(minimal_workspace: Path) -> None:
    hits = search_rag("baseUrl", minimal_workspace)

    output = format_hits("baseUrl", hits)

    assert "engine=fts5-bm25" in output
    assert "bm25=" in output


def test_domain_filter_and_limit_are_applied_after_bm25(
    minimal_workspace: Path,
) -> None:
    _add_chunk(
        minimal_workspace,
        chunk_id="testing-baseurl",
        body="baseUrl baseUrl test configuration",
        domain="testing",
    )

    hits = search_rag(
        "baseUrl", minimal_workspace, domains=["config"], limit=1
    )

    assert [hit.chunk_id for hit in hits] == ["test-baseurl"]
    assert search_rag("baseUrl", minimal_workspace, limit=0) == []
