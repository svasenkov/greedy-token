"""Local SQLite FTS5 index for lexical BM25 retrieval."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from greedy_token.rag_index import (
    ManifestDocument,
    _meta_blob,
    _normalize,
    _strip_frontmatter,
    _tokenize,
    load_manifest_documents,
)

SCHEMA_VERSION = "2"
MAX_QUERY_CHARS = 4096
MAX_RESULTS = 100


class Fts5Unavailable(RuntimeError):
    """Raised when the local SQLite build cannot provide FTS5."""


@dataclass(frozen=True)
class Bm25Match:
    document: ManifestDocument
    score: float


def _cache_root() -> Path:
    configured = os.environ.get("GREEDY_TOKEN_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "greedy-token"
    return Path.home() / ".cache" / "greedy-token"


def index_path(root: Path) -> Path:
    """Return a root-specific cache path outside the indexed workspace."""
    key = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:20]
    return _cache_root() / "rag" / f"{key}.sqlite3"


def _connect(root: Path) -> sqlite3.Connection:
    path = index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        _ensure_schema(connection)
    except sqlite3.OperationalError as exc:
        connection.close()
        if "fts5" in str(exc).casefold():
            raise Fts5Unavailable(str(exc)) from exc
        raise
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS rag_meta "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    current = connection.execute(
        "SELECT value FROM rag_meta WHERE key = ?", ("schema_version",)
    ).fetchone()
    if current is not None and current["value"] != SCHEMA_VERSION:
        connection.execute("DROP TABLE IF EXISTS rag_chunks")
        connection.execute("DROP TABLE IF EXISTS rag_documents")
        connection.execute("DELETE FROM rag_meta WHERE key = ?", ("schema_version",))
    connection.execute(
        "CREATE TABLE IF NOT EXISTS rag_documents ("
        "docid INTEGER PRIMARY KEY, entry_key TEXT NOT NULL UNIQUE, "
        "path TEXT NOT NULL, "
        "content_hash TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks USING fts5("
        "entry_key UNINDEXED, chunk_id UNINDEXED, path UNINDEXED, "
        "domain UNINDEXED, metadata, body, "
        "tokenize=\"unicode61 remove_diacritics 2 tokenchars '_-'\""
        ")"
    )
    connection.execute(
        "INSERT OR REPLACE INTO rag_meta(key, value) VALUES (?, ?)",
        ("schema_version", SCHEMA_VERSION),
    )
    connection.commit()


def _sync_index(
    connection: sqlite3.Connection, documents: list[ManifestDocument]
) -> None:
    existing = {
        row["entry_key"]: (row["docid"], row["content_hash"])
        for row in connection.execute(
            "SELECT docid, entry_key, content_hash FROM rag_documents"
        )
    }
    current_keys = {document.entry_key for document in documents}
    with connection:
        for entry_key, (docid, _) in existing.items():
            if entry_key not in current_keys:
                connection.execute("DELETE FROM rag_chunks WHERE rowid = ?", (docid,))
                connection.execute(
                    "DELETE FROM rag_documents WHERE docid = ?", (docid,)
                )
        for document in documents:
            old = existing.get(document.entry_key)
            if old is not None and old[1] == document.content_hash:
                continue
            if old is None:
                cursor = connection.execute(
                    "INSERT INTO rag_documents("
                    "entry_key, path, content_hash"
                    ") VALUES (?, ?, ?)",
                    (
                        document.entry_key,
                        document.rel_path,
                        document.content_hash,
                    ),
                )
                docid = int(cursor.lastrowid)
            else:
                docid = int(old[0])
                connection.execute(
                    "UPDATE rag_documents SET content_hash = ? WHERE docid = ?",
                    (document.content_hash, docid),
                )
                connection.execute("DELETE FROM rag_chunks WHERE rowid = ?", (docid,))
            meta = document.meta
            connection.execute(
                "INSERT INTO rag_chunks("
                "rowid, entry_key, chunk_id, path, domain, metadata, body"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    docid,
                    document.entry_key,
                    str(meta.get("id", document.rel_path)),
                    document.rel_path,
                    document.domain,
                    _normalize(_meta_blob(meta)),
                    _normalize(_strip_frontmatter(document.body)),
                ),
            )


def _match_query(query: str) -> str:
    # Tokens, not user syntax, are quoted so FTS operators cannot be injected.
    return " OR ".join(f'"{token}"' for token in sorted(_tokenize(query)))


def search_bm25(
    query: str,
    root: Path,
    *,
    domains: list[str] | None = None,
    limit: int = 5,
) -> list[Bm25Match]:
    """Search the manifest corpus with local unicode61 BM25."""
    match_query = _match_query(query[:MAX_QUERY_CHARS])
    if not match_query or limit <= 0:
        return []
    documents = load_manifest_documents(root)
    by_key = {document.entry_key: document for document in documents}
    if not by_key:
        return []
    connection = _connect(root)
    try:
        _sync_index(connection, documents)
        rows = connection.execute(
            "SELECT entry_key, chunk_id, path, domain, "
            "bm25(rag_chunks, 0.0, 0.0, 0.0, 0.0, 5.0, 1.0) AS bm25_score "
            "FROM rag_chunks WHERE rag_chunks MATCH ? "
            "ORDER BY bm25_score, chunk_id LIMIT ?",
            (match_query, MAX_RESULTS),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).casefold():
            raise Fts5Unavailable(str(exc)) from exc
        raise
    finally:
        connection.close()
    allowed_domains = set(domains or ())
    matches: list[Bm25Match] = []
    for row in rows:
        document = by_key.get(row["entry_key"])
        if document is None:
            continue
        if allowed_domains and document.domain not in allowed_domains:
            continue
        # SQLite FTS5 returns a lower-is-better negative rank. Public scores stay
        # higher-is-better for compatibility with the previous overlap engine.
        matches.append(Bm25Match(document=document, score=-float(row["bm25_score"])))
        if len(matches) >= min(limit, MAX_RESULTS):
            break
    return matches
