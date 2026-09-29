"""Mutation kill-tests for rag_fts (SQLite FTS5 BM25 index)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import allure
import pytest

import greedy_token.rag_fts as fts
from greedy_token.rag_index import ManifestDocument


def _doc(entry_key: str, body: str, *, domain: str = "testing",
         meta: dict | None = None, rel: str | None = None) -> ManifestDocument:
    return ManifestDocument(
        meta=meta if meta is not None else {"id": entry_key},
        entry_key=entry_key,
        rel_path=rel or f"docs/rag/{domain}/{entry_key}.md",
        domain=domain,
        body=body,
        content_hash=f"hash-{entry_key}",
    )


def _isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, docs: list[ManifestDocument]
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(fts, "load_manifest_documents", lambda root: docs)


@allure.title("_cache_root: exact env-var names and fallback chain")
def test_cache_root_env_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with allure.step("GREEDY_TOKEN_CACHE_DIR wins verbatim"):
        monkeypatch.setenv("GREEDY_TOKEN_CACHE_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        assert fts._cache_root() == tmp_path / "cfg"
    with allure.step("XDG_CACHE_HOME used when configured env is absent"):
        monkeypatch.delenv("GREEDY_TOKEN_CACHE_DIR", raising=False)
        assert fts._cache_root() == tmp_path / "xdg" / "greedy-token"
    with allure.step("home fallback is ~/.cache/greedy-token exactly"):
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        assert fts._cache_root() == Path.home() / ".cache" / "greedy-token"


@allure.title("index_path: sha256 key of the resolved root and rag/ suffix")
def test_index_path_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    monkeypatch.setenv("GREEDY_TOKEN_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "ws"
    root.mkdir()
    expected_key = hashlib.sha256(
        str(root.resolve()).encode("utf-8")
    ).hexdigest()[:20]
    path = fts.index_path(root)
    assert path == tmp_path / "cache" / "rag" / f"{expected_key}.sqlite3"


@allure.title("_connect: sqlite connect gets the 5s timeout")
def test_connect_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_CACHE_DIR", str(tmp_path / "cache"))
    seen: dict = {}
    real_connect = sqlite3.connect

    def spy(path, **kwargs):
        seen.update(kwargs)
        return real_connect(path, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    conn = fts._connect(tmp_path)
    conn.close()
    assert seen["timeout"] == 5.0


@allure.title("_connect: fts5 OperationalError becomes Fts5Unavailable with the cause text")
def test_connect_fts5_unavailable_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_CACHE_DIR", str(tmp_path / "cache"))

    def boom(connection):
        raise sqlite3.OperationalError("no such module: fts5")

    monkeypatch.setattr(fts, "_ensure_schema", boom)
    # Fts5Unavailable(None)/str(None) loses the 'fts5' reason text.
    with pytest.raises(fts.Fts5Unavailable, match="no such module: fts5"):
        fts._connect(tmp_path)


@allure.title("_ensure_schema: version mismatch drops stale tables")
def test_ensure_schema_version_mismatch(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    fts._ensure_schema(conn)
    conn.execute("DELETE FROM rag_meta WHERE key = ?", ("schema_version",))
    conn.execute(
        "INSERT INTO rag_meta(key, value) VALUES (?, ?)",
        ("schema_version", "1"),
    )
    conn.execute(
        "INSERT INTO rag_documents(entry_key, path, content_hash) VALUES (?, ?, ?)",
        ("stale", "p", "h"),
    )
    conn.execute(
        "INSERT INTO rag_chunks(entry_key, chunk_id, path, domain, metadata, body)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("stale", "stale", "p", "d", "m", "b"),
    )
    conn.commit()
    fts._ensure_schema(conn)
    # current=None / wrong key / inverted == must not keep stale rows.
    assert conn.execute("SELECT COUNT(*) c FROM rag_documents").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM rag_chunks").fetchone()["c"] == 0
    assert (
        conn.execute(
            "SELECT value FROM rag_meta WHERE key = ?", ("schema_version",)
        ).fetchone()["value"]
        == fts.SCHEMA_VERSION
    )


@allure.title("_ensure_schema: matching version keeps data")
def test_ensure_schema_current_version_kept(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    fts._ensure_schema(conn)
    conn.execute(
        "INSERT INTO rag_documents(entry_key, path, content_hash) VALUES (?, ?, ?)",
        ("keep", "p", "h"),
    )
    conn.commit()
    fts._ensure_schema(conn)
    # == instead of != would drop the just-written row.
    assert conn.execute("SELECT COUNT(*) c FROM rag_documents").fetchone()["c"] == 1
    conn.close()


@allure.title("_sync_index: rowid tracks docid and chunk_id falls back to rel_path")
def test_sync_index_rowid_and_chunk_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = [
        _doc("with-meta", "alpha body", meta={"id": "custom-id"}),
        _doc("no-meta", "beta body", meta={}),
    ]
    _isolated(tmp_path, monkeypatch, docs)
    conn = fts._connect(tmp_path)
    try:
        fts._sync_index(conn, docs)
        doc_rows = {
            r["entry_key"]: r["docid"]
            for r in conn.execute("SELECT entry_key, docid FROM rag_documents")
        }
        chunk_rows = {
            r["entry_key"]: (r["rowid"], r["chunk_id"])
            for r in conn.execute("SELECT entry_key, rowid, chunk_id FROM rag_chunks")
        }
        for key, docid in doc_rows.items():
            rowid, chunk_id = chunk_rows[key]
            # docid=None inserts a NULL rowid → mismatch breaks stale deletes.
            assert rowid == docid
        # meta id wins; absent id falls back to rel_path — get(None)/XXidXX/ID
        # mutants lose the custom id.
        assert chunk_rows["with-meta"][1] == "custom-id"
        assert chunk_rows["no-meta"][1] == docs[1].rel_path
    finally:
        conn.close()


@allure.title("search_bm25: limit 0 short-circuits and the default cap is 5")
def test_search_bm25_limit_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = [_doc(f"doc{i}", "shared token body") for i in range(7)]
    _isolated(tmp_path, monkeypatch, docs)
    with allure.step("limit=0 → no rows even though docs match (kills < 0)"):
        assert fts.search_bm25("shared", tmp_path, limit=0) == []
    with allure.step("default limit caps at 5 (kills default=6 and `>` instead of `>=`)"):
        matches = fts.search_bm25("shared", tmp_path)
        assert len(matches) == 5


@allure.title("search_bm25: multi-domain placeholders stay valid SQL")
def test_search_bm25_multi_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = [
        _doc("a1", "needle body", domain="ops"),
        _doc("b1", "needle body", domain="infra"),
        _doc("c1", "needle body", domain="other"),
    ]
    _isolated(tmp_path, monkeypatch, docs)
    matches = fts.search_bm25(
        "needle", tmp_path, domains=["infra", "ops"]
    )
    # 'XX, XX'.join corrupts the IN-list SQL → OperationalError propagates.
    keys = {m.document.entry_key for m in matches}
    assert keys == {"a1", "b1"}


@allure.title("search_bm25: fts5 errors raise Fts5Unavailable carrying the cause")
def test_search_bm25_fts5_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated(tmp_path, monkeypatch, [_doc("a", "body")])
    fake = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(fts, "_connect", lambda root: fake)

    def boom(connection, documents):
        raise sqlite3.OperationalError("no such module: fts5")

    monkeypatch.setattr(fts, "_sync_index", boom)
    with pytest.raises(fts.Fts5Unavailable, match="no such module: fts5"):
        fts.search_bm25("body", tmp_path)


class _RowsConn:
    """Fake connection returning scripted rows for the result loop."""

    def __init__(self, rows):
        self._rows = rows
        self.executed = None

    def execute(self, sql, params=()):
        self.executed = (sql, list(params))
        return SimpleNamespace(fetchall=lambda: self._rows)

    def close(self):
        pass


@allure.title("search_bm25: unknown entry_key rows are skipped, not fatal")
def test_search_bm25_missing_key_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = [_doc("known", "body")]
    _isolated(tmp_path, monkeypatch, docs)
    rows = [
        {"entry_key": "ghost", "bm25_score": -1.0, "chunk_id": "g"},
        {"entry_key": "known", "bm25_score": -2.0, "chunk_id": "k"},
    ]
    conn = _RowsConn(rows)
    monkeypatch.setattr(fts, "_connect", lambda root: conn)
    monkeypatch.setattr(fts, "_sync_index", lambda c, d: None)
    matches = fts.search_bm25("body", tmp_path)
    # continue→break stops at the ghost row and drops the real hit.
    assert [m.document.entry_key for m in matches] == ["known"]
    assert matches[0].score == pytest.approx(2.0)  # kills +float


@allure.title("search_bm25: post-filter keeps scanning past foreign domains")
def test_search_bm25_domain_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = [_doc("k1", "body", domain="other"), _doc("k2", "body", domain="ops")]
    _isolated(tmp_path, monkeypatch, docs)
    rows = [
        {"entry_key": "k1", "bm25_score": -1.0, "chunk_id": "1"},
        {"entry_key": "k2", "bm25_score": -2.0, "chunk_id": "2"},
    ]
    monkeypatch.setattr(fts, "_connect", lambda root: _RowsConn(rows))
    monkeypatch.setattr(fts, "_sync_index", lambda c, d: None)
    # The SQL predicate excludes k1, so craft rows through the Python
    # belt-and-braces filter path: limit the fake to bypass SQL filtering.
    matches = fts.search_bm25("body", tmp_path, domains=["ops"])
    # continue→break would return [] after k1 instead of [k2].
    assert [m.document.entry_key for m in matches] == ["k2"]
