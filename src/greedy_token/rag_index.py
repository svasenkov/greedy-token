"""In-memory RAG index — tokenize chunks once, read full bodies only for top hits."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


def _tokenize(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9_-]{2,}", text.lower()))


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5 :]
    return text


def _load_manifest_rows(manifest: Path) -> list[dict]:
    if not manifest.is_file():
        return []
    rows: list[dict] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
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
    body_tokens: frozenset[str]
    meta_tokens: frozenset[str]


@dataclass
class _CacheEntry:
    fingerprint: tuple[float, float]
    chunks: list[IndexedChunk]
    chunk_paths: tuple[str, ...]


_cache: dict[Path, _CacheEntry] = {}


def invalidate_rag_index(root: Path | None = None) -> None:
    """Clear cached index (tests or after manifest edits)."""
    if root is None:
        _cache.clear()
        return
    _cache.pop(root.resolve(), None)


def _fingerprint(root: Path, chunk_paths: tuple[str, ...] | None = None) -> tuple[float, float]:
    manifest = root / "docs" / "rag" / "manifest.jsonl"
    if not manifest.is_file():
        return (0.0, 0.0)
    manifest_mtime = manifest.stat().st_mtime
    paths = chunk_paths
    if paths is None:
        paths = tuple(meta.get("path", "") for meta in _load_manifest_rows(manifest))
    max_chunk_mtime = 0.0
    for rel in paths:
        if not rel:
            continue
        chunk_path = root / rel
        if chunk_path.is_file():
            max_chunk_mtime = max(max_chunk_mtime, chunk_path.stat().st_mtime)
    return (manifest_mtime, max_chunk_mtime)


def _build_index(root: Path) -> list[IndexedChunk]:
    manifest = root / "docs" / "rag" / "manifest.jsonl"
    entries: list[IndexedChunk] = []
    for meta in _load_manifest_rows(manifest):
        rel = meta.get("path", "")
        if not rel:
            continue
        chunk_path = root / rel
        if not chunk_path.is_file():
            continue
        body = _strip_frontmatter(
            chunk_path.read_text(encoding="utf-8", errors="replace")
        )
        entries.append(
            IndexedChunk(
                meta=meta,
                rel_path=rel,
                domain=meta.get("domain", ""),
                body_tokens=_tokenize(body),
                meta_tokens=_tokenize(_meta_blob(meta)),
            )
        )
    return entries


def get_indexed_chunks(root: Path) -> list[IndexedChunk]:
    """Return cached indexed chunks; rebuild when manifest or chunks change."""
    key = root.resolve()
    cached = _cache.get(key)
    paths = cached.chunk_paths if cached else None
    fp = _fingerprint(root, paths)
    if cached is not None and cached.fingerprint == fp:
        return cached.chunks
    chunks = _build_index(root)
    chunk_paths = tuple(c.rel_path for c in chunks)
    fp = _fingerprint(root, chunk_paths)
    _cache[key] = _CacheEntry(
        fingerprint=fp, chunks=chunks, chunk_paths=chunk_paths
    )
    return chunks
