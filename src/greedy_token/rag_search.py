from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from greedy_token.paths import find_monorepo_root
from greedy_token.rag_index import IndexedChunk, get_indexed_chunks


@dataclass
class RagHit:
    chunk_id: str
    path: str
    domain: str
    score: float
    excerpt: str
    body: str | None = None


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_-]{2,}", text.lower())}


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
    root = root or find_monorepo_root()
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

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
        chunk_path = root / rel
        body = chunk_path.read_text(encoding="utf-8", errors="replace")
        hits.append(
            RagHit(
                chunk_id=meta.get("id", rel),
                path=rel,
                domain=chunk.domain,
                score=score,
                excerpt=_excerpt(body, query_tokens),
                body=body,
            )
        )
    return hits


def _excerpt(body: str, query_tokens: set[str], max_len: int = 320) -> str:
    lines = body.splitlines()
    for i, line in enumerate(lines):
        lower = line.lower()
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
        lines.extend(
            [
                f"{i}. [{h.chunk_id}] score={h.score:.1f}  ({h.domain})",
                f"   {h.path}",
                "",
                h.excerpt,
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines).rstrip()
