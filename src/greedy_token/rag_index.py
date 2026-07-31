"""Safe manifest-backed documents for lexical RAG retrieval."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_CHUNK_BYTES = 1024 * 1024
MAX_MANIFEST_ROWS = 10_000


def _tokenize(text: str) -> frozenset[str]:
    # Unicode letters/digits (incl. Cyrillic) plus ASCII _- for code ids.
    # ASCII-only [a-z0-9_] left RU queries as an empty token set → no RAG hits.
    normalized = _normalize(text)
    return frozenset(re.findall(r"[\w-]{2,}", normalized, flags=re.UNICODE))


def _normalize(text: str) -> str:
    """Normalize canonically equivalent Unicode before case-insensitive search."""
    return unicodedata.normalize("NFKC", text).casefold()


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5 :]
    return text


def _load_manifest_rows(manifest: Path) -> list[dict]:
    if not manifest.is_file():
        return []
    if manifest.stat().st_size > MAX_MANIFEST_BYTES:
        return []
    rows: list[dict] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
            if len(rows) >= MAX_MANIFEST_ROWS:
                break
    return rows


def _meta_blob(meta: dict) -> str:
    return " ".join(
        [
            meta.get("id", ""),
            meta.get("domain", ""),
            " ".join(meta.get("tags") or []),
            Path(meta.get("path", "")).stem,
        ]
    )


@dataclass(frozen=True)
class IndexedChunk:
    meta: dict
    rel_path: str
    domain: str
    body: str
    body_tokens: frozenset[str]
    meta_tokens: frozenset[str]


@dataclass
class _CacheEntry:
    fingerprint: tuple[tuple[str, str], ...]
    chunks: list[IndexedChunk]


_cache: dict[Path, _CacheEntry] = {}


def invalidate_rag_index(root: Path | None = None) -> None:
    """Clear cached index (tests or after manifest edits)."""
    if root is None:
        _cache.clear()
        return
    _cache.pop(root.resolve(), None)


@dataclass(frozen=True)
class ManifestDocument:
    meta: dict
    entry_key: str
    rel_path: str
    domain: str
    body: str
    content_hash: str


def _confined_chunk_path(root: Path, rel: object) -> tuple[str, Path] | None:
    if not isinstance(rel, str) or not rel:
        return None
    candidate_rel = Path(rel)
    if candidate_rel.is_absolute():
        return None
    rag_root = (root / "docs" / "rag").resolve()
    candidate = (root / candidate_rel).resolve()
    try:
        candidate.relative_to(rag_root)
    except ValueError:
        return None
    return candidate_rel.as_posix(), candidate


def _read_text_chunk(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_CHUNK_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > MAX_CHUNK_BYTES or b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def load_manifest_documents(root: Path) -> list[ManifestDocument]:
    """Load only safe UTF-8 documents explicitly listed by the RAG manifest."""
    root = root.resolve()
    manifest = root / "docs" / "rag" / "manifest.jsonl"
    rag_root = (root / "docs" / "rag").resolve()
    try:
        if manifest.resolve().parent != rag_root:
            return []
    except OSError:
        return []
    documents: list[ManifestDocument] = []
    key_counts: dict[str, int] = {}
    for meta in _load_manifest_rows(manifest):
        confined = _confined_chunk_path(root, meta.get("path"))
        if confined is None:
            continue
        rel, chunk_path = confined
        body = _read_text_chunk(chunk_path)
        if body is None:
            continue
        normalized_body = _normalize(_strip_frontmatter(body))
        canonical_meta = json.dumps(
            meta, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        base_key = hashlib.sha256(canonical_meta).hexdigest()
        occurrence = key_counts.get(base_key, 0)
        key_counts[base_key] = occurrence + 1
        entry_key = f"{base_key}:{occurrence}"
        content_hash = hashlib.sha256(
            canonical_meta + b"\0" + normalized_body.encode("utf-8")
        ).hexdigest()
        documents.append(
            ManifestDocument(
                meta=meta,
                entry_key=entry_key,
                rel_path=rel,
                domain=str(meta.get("domain", "")),
                body=body,
                content_hash=content_hash,
            )
        )
    return documents


def _fingerprint(documents: list[ManifestDocument]) -> tuple[tuple[str, str], ...]:
    return tuple((doc.entry_key, doc.content_hash) for doc in documents)


def _build_index(documents: list[ManifestDocument]) -> list[IndexedChunk]:
    entries: list[IndexedChunk] = []
    for document in documents:
        meta = document.meta
        entries.append(
            IndexedChunk(
                meta=meta,
                rel_path=document.rel_path,
                domain=document.domain,
                body=document.body,
                body_tokens=_tokenize(_strip_frontmatter(document.body)),
                meta_tokens=_tokenize(_meta_blob(meta)),
            )
        )
    return entries


def get_indexed_chunks(root: Path) -> list[IndexedChunk]:
    """Return overlap-fallback chunks invalidated by deterministic content hashes."""
    key = root.resolve()
    cached = _cache.get(key)
    documents = load_manifest_documents(key)
    fp = _fingerprint(documents)
    if cached is not None and cached.fingerprint == fp:
        return cached.chunks
    chunks = _build_index(documents)
    _cache[key] = _CacheEntry(fingerprint=fp, chunks=chunks)
    return chunks
