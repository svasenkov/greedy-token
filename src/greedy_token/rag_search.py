from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from greedy_token.paths import find_workspace_root
from greedy_token.rag_fts import MAX_QUERY_CHARS, Fts5Unavailable, search_bm25
from greedy_token.rag_index import IndexedChunk, _normalize, _tokenize, get_indexed_chunks
from greedy_token.settings import get_rag_settings
from greedy_token.tokens import count_tokens


@dataclass
class RagHit:
    chunk_id: str
    path: str
    domain: str
    score: float
    excerpt: str
    body: str | None = None
    engine: str = "overlap"


class RagHits(list[RagHit]):
    """Ranked hits plus the ``rag.max_payload_tokens`` truncation verdict."""

    def __init__(
        self,
        hits: Iterable[RagHit] = (),
        *,
        truncated: bool = False,
        hits_dropped: int = 0,
    ) -> None:
        super().__init__(hits)
        self.truncated = truncated
        self.hits_dropped = hits_dropped


def rag_hit_tokens(hit: RagHit, root: Path) -> int:
    """est_tokens of a single hit — the accounting budget.rag_est_tokens sums."""
    if hit.body is not None:
        return count_tokens(hit.body).tokens
    chunk_path = root / hit.path
    if chunk_path.is_file():
        return count_tokens(chunk_path.read_text(encoding="utf-8", errors="replace")).tokens
    return count_tokens(hit.excerpt).tokens


def _cap_payload(hits: list[RagHit], root: Path, cap: int) -> RagHits:
    """Keep rank-ordered hits whose cumulative est fits the payload cap.

    A hit that would overflow the cap is skipped (counted in
    ``hits_dropped``); smaller later hits may still fit.
    """
    if cap <= 0:
        return RagHits(hits)
    kept: list[RagHit] = []
    dropped = 0
    total = 0
    for hit in hits:
        cost = rag_hit_tokens(hit, root)
        if total + cost > cap:
            dropped += 1
            continue
        total += cost
        kept.append(hit)
    return RagHits(kept, truncated=dropped > 0, hits_dropped=dropped)


def _score_indexed(query_tokens: set[str], chunk: IndexedChunk) -> float:
    overlap = query_tokens & (chunk.body_tokens | chunk.meta_tokens)
    if not overlap:
        return 0.0
    # equivalent: n*1.0 and n/1.0 are the same float for every int n ≥ 1
    # reachable here.
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
    max_payload_tokens: int | None = None,
) -> RagHits:
    """Search manifest-backed chunks with FTS5 BM25 or the overlap fallback.

    ``max_payload_tokens`` caps the cumulative est_tokens of returned hits —
    ``rag.max_payload_tokens`` config when None; <= 0 disables the cap.
    """
    root = root or find_workspace_root()
    bounded_query = query[:MAX_QUERY_CHARS]
    query_tokens = _tokenize(bounded_query)
    if not query_tokens or limit <= 0:
        return RagHits()
    if max_payload_tokens is None:
        cap = get_rag_settings(root).max_payload_tokens
    else:
        cap = max_payload_tokens

    try:
        matches = search_bm25(
            bounded_query, root, domains=domains, limit=limit
        )
    except Fts5Unavailable:
        matches = None
    if matches is not None:
        return _cap_payload(
            [
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
            ],
            root,
            cap,
        )

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
                # equivalent: RagHit.engine already defaults to "overlap" — the
                # explicit arg restates the dataclass default.
                engine="overlap",
            )
        )
    return _cap_payload(hits, root, cap)


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
    truncated = getattr(hits, "truncated", False)
    dropped = getattr(hits, "hits_dropped", 0)
    if not hits:
        out = f"No RAG hits for: {query}\nIndex: docs/rag/manifest.jsonl"
        if truncated:
            out += f"\ntruncated: true, hits_dropped: {dropped}"
        return out
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
    if truncated:
        lines.append(f"truncated: true, hits_dropped: {dropped}")
    return "\n".join(lines).rstrip()
