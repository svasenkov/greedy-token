from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from greedy_token.paths import find_workspace_root
from greedy_token.rag_fts import MAX_QUERY_CHARS, Fts5Unavailable, search_bm25
from greedy_token.rag_index import IndexedChunk, _normalize, _tokenize, get_indexed_chunks


@dataclass
class RagHit:
    chunk_id: str
    path: str
    domain: str
    score: float
    excerpt: str
    body: str | None = None
    engine: str = "overlap"


def _score_indexed(query_tokens: set[str], chunk: IndexedChunk) -> float:
    overlap = query_tokens & (chunk.body_tokens | chunk.meta_tokens)
    if not overlap:
        return 0.0
    score = len(overlap) * 1.0
    chunk_id = chunk.meta.get("id", "").lower()
    for tok in overlap:
        if tok in chunk_id:
            score += 2.0
    return score


def search_rag(
    query: str,
    root: Path | None = None,
    *,
    domains: list[str] | None = None,
    limit: int = 5,
) -> list[RagHit]:
    """Search manifest-backed chunks with FTS5 BM25 or the overlap fallback."""
    root = root or find_workspace_root()
    bounded_query = query[:MAX_QUERY_CHARS]
    query_tokens = _tokenize(bounded_query)
    if not query_tokens or limit <= 0:
        return []

    try:
        matches = search_bm25(
            bounded_query, root, domains=domains, limit=limit
        )
    except Fts5Unavailable:
        matches = None
    if matches is not None:
        return [
            RagHit(
                chunk_id=str(match.document.meta.get("id", match.document.rel_path)),
                path=match.document.rel_path,
                domain=match.document.domain,
                score=match.score,
                excerpt=_excerpt(match.document.body, query_tokens),
                body=match.document.body,
                engine="fts5-bm25",
            )
            for match in matches
        ]

    scored: list[tuple[float, IndexedChunk]] = []
    for chunk in get_indexed_chunks(root):
        if domains and chunk.domain not in domains:
            continue
        score = _score_indexed(query_tokens, chunk)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda pair: -pair[0])
    hits: list[RagHit] = []
    for score, chunk in scored[:limit]:
        rel = chunk.rel_path
        meta = chunk.meta
        body = chunk.body
        hits.append(
            RagHit(
                chunk_id=meta.get("id", rel),
                path=rel,
                domain=chunk.domain,
                score=score,
                excerpt=_excerpt(body, query_tokens),
                body=body,
                engine="overlap",
            )
        )
    return hits


def _excerpt(body: str, query_tokens: set[str], max_len: int = 320) -> str:
    lines = body.splitlines()
    for i, line in enumerate(lines):
        lower = _normalize(line)
        if any(t in lower for t in query_tokens):
            chunk = "\n".join(lines[i : i + 6]).strip()
            if len(chunk) > max_len:
                return chunk[: max_len - 1] + "…"
            return chunk
    head = body.strip()
    if len(head) > max_len:
        return head[: max_len - 1] + "…"
    return head


def format_hits(query: str, hits: list[RagHit]) -> str:
    if not hits:
        return f"No RAG hits for: {query}\nIndex: docs/rag/manifest.jsonl"
    lines = [f"RAG hits for: {query}", ""]
    for i, h in enumerate(hits, 1):
        score_label = (
            f"score={h.score:.6f} engine={h.engine} bm25={h.score:.6f}"
            if h.engine == "fts5-bm25"
            else f"score={h.score:.1f} engine={h.engine}"
        )
        lines.extend(
            [
                f"{i}. [{h.chunk_id}] {score_label}  ({h.domain})",
                f"   {h.path}",
                "",
                h.excerpt,
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines).rstrip()
