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
        # equivalent: SQLite keywords and identifiers are case-insensitive —
        # case-only respellings of this statement execute identically.
        "CREATE TABLE IF NOT EXISTS rag_meta "
        # equivalent: same case-insensitivity for the column definitions.
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    current = connection.execute(
        # equivalent: SQLite keywords/identifiers are case-insensitive in
        # this SELECT as well — only the bound key value is case-sensitive.
        "SELECT value FROM rag_meta WHERE key = ?", ("schema_version",)
    ).fetchone()
    # equivalent: sqlite3.Row name lookup is case-insensitive, so
    # current["VALUE"] resolves the same column.
    if current is not None and current["value"] != SCHEMA_VERSION:
        # equivalent: SQLite keywords are case-insensitive in DROP TABLE too.
        connection.execute("DROP TABLE IF EXISTS rag_chunks")
        # equivalent: same DROP TABLE case-insensitivity for rag_documents.
        connection.execute("DROP TABLE IF EXISTS rag_documents")
        # equivalent: this DELETE is redundant — INSERT OR REPLACE on the same
        # primary key rewrites the row either way, so key-spelling and keyword
        # case mutants cannot change the outcome.
        connection.execute("DELETE FROM rag_meta WHERE key = ?", ("schema_version",))
    connection.execute(
        # equivalent: SQLite identifiers/keywords ignore case — respelled
        # CREATE TABLE text executes identically.
        "CREATE TABLE IF NOT EXISTS rag_documents ("
        # equivalent: column-definition case is irrelevant to SQLite.
        "docid INTEGER PRIMARY KEY, entry_key TEXT NOT NULL UNIQUE, "
        # equivalent: same case-insensitivity for the path column.
        "path TEXT NOT NULL, "
        # equivalent: same case-insensitivity for the content_hash column.
        "content_hash TEXT NOT NULL)"
    )
    connection.execute(
        # equivalent: SQLite keywords and the FTS5 module/table names are
        # case-insensitive here.
        "CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks USING fts5("
        # equivalent: UNINDEXED and column names ignore case in FTS5 schemas.
        "entry_key UNINDEXED, chunk_id UNINDEXED, path UNINDEXED, "
        # equivalent: same case-insensitivity for the domain/metadata/body
        # column list.
        "domain UNINDEXED, metadata, body, "
        # equivalent: verified — FTS5 accepts tokenizer names and options in
        # any case (unicode61/UNICODE61 behave identically).
        "tokenize=\"unicode61 remove_diacritics 2 tokenchars '_-'\""
        ")"
    )
    connection.execute(
        # equivalent: SQLite keywords/identifiers in this INSERT OR REPLACE
        # are case-insensitive.
        "INSERT OR REPLACE INTO rag_meta(key, value) VALUES (?, ?)",
        ("schema_version", SCHEMA_VERSION),
    )
    connection.commit()


def _sync_index(
    connection: sqlite3.Connection, documents: list[ManifestDocument]
) -> None:
    existing = {
        # equivalent: sqlite3.Row key lookup is case-insensitive — ENTRY_KEY,
        # DOCID and CONTENT_HASH resolve the same columns.
        row["entry_key"]: (row["docid"], row["content_hash"])
        for row in connection.execute(
            # equivalent: this SELECT's keywords/identifiers ignore case.
            "SELECT docid, entry_key, content_hash FROM rag_documents"
        )
    }
    current_keys = {document.entry_key for document in documents}
    with connection:
        for entry_key, (docid, _) in existing.items():
            if entry_key not in current_keys:
                # equivalent: SQLite keywords ignore case in this DELETE.
                connection.execute("DELETE FROM rag_chunks WHERE rowid = ?", (docid,))
                connection.execute(
                    # equivalent: same case-insensitivity for the documents
                    # DELETE below.
                    "DELETE FROM rag_documents WHERE docid = ?", (docid,)
                )
        for document in documents:
            old = existing.get(document.entry_key)
            if old is not None and old[1] == document.content_hash:
                continue
            if old is None:
                cursor = connection.execute(
                    # equivalent: SQLite keywords/identifiers ignore case in
                    # this INSERT.
                    "INSERT INTO rag_documents("
                    # equivalent: column-list case is irrelevant.
                    "entry_key, path, content_hash"
                    # equivalent: VALUES keyword case is irrelevant.
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
                    # equivalent: UPDATE keywords/identifiers ignore case.
                    "UPDATE rag_documents SET content_hash = ? WHERE docid = ?",
                    (document.content_hash, docid),
                )
                # equivalent: this second rag_chunks DELETE also ignores case.
                connection.execute("DELETE FROM rag_chunks WHERE rowid = ?", (docid,))
            meta = document.meta
            connection.execute(
                # equivalent: SQLite keywords/identifiers ignore case in this
                # chunk INSERT.
                "INSERT INTO rag_chunks("
                # equivalent: the rowid/column list ignores case as well.
                "rowid, entry_key, chunk_id, path, domain, metadata, body"
                # equivalent: VALUES keyword case is irrelevant here too.
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
    allowed_domains = sorted(set(domains or ()))
    connection = _connect(root)
    try:
        _sync_index(connection, documents)
        # The domain predicate must run inside SQLite — a Python post-filter
        # applied after LIMIT can exhaust the result window on higher-ranked
        # rows from other domains and drop every matching document.
        sql = (
            # equivalent: SELECT keywords/column names ignore case.
            "SELECT entry_key, chunk_id, path, domain, "
            # equivalent: the bm25() function name and alias ignore case.
            "bm25(rag_chunks, 0.0, 0.0, 0.0, 0.0, 5.0, 1.0) AS bm25_score "
            # equivalent: FROM/WHERE and the MATCH operator ignore case.
            "FROM rag_chunks WHERE rag_chunks MATCH ? "
        )
        params: list[object] = [match_query]
        if allowed_domains:
            placeholders = ", ".join("?" for _ in allowed_domains)
            sql += f"AND domain IN ({placeholders}) "
            params.extend(allowed_domains)
        # equivalent: ORDER BY column names ignore case like every other
        # SQLite identifier above.
        sql += "ORDER BY bm25_score, chunk_id LIMIT ?"
        params.append(min(limit, MAX_RESULTS))
        rows = connection.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).casefold():
            raise Fts5Unavailable(str(exc)) from exc
        raise
    finally:
        connection.close()
    allowed_domains_set = set(allowed_domains)
    matches: list[Bm25Match] = []
    for row in rows:
        document = by_key.get(row["entry_key"])
        if document is None:
            continue
        if allowed_domains_set and document.domain not in allowed_domains_set:
            continue
        # SQLite FTS5 returns a lower-is-better negative rank. Public scores stay
        # higher-is-better for compatibility with the previous overlap engine.
        # equivalent: sqlite3.Row lookup is case-insensitive — BM25_SCORE
        # resolves the same aliased column.
        matches.append(Bm25Match(document=document, score=-float(row["bm25_score"])))
        if len(matches) >= min(limit, MAX_RESULTS):
            break
    return matches
