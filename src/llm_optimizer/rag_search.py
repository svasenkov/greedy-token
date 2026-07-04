from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from llm_optimizer.paths import find_monorepo_root


@dataclass
class RagHit:
    chunk_id: str
    path: str
    domain: str
    score: float
    excerpt: str


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_-]{2,}", text.lower())}


def _load_manifest(root: Path) -> list[dict]:
    manifest = root / "docs" / "rag" / "manifest.jsonl"
    if not manifest.is_file():
        return []
    rows = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _score_chunk(query_tokens: set[str], body: str, meta: dict) -> float:
    body_tokens = _tokenize(body)
    meta_blob = " ".join(
        [
            meta.get("id", ""),
            meta.get("domain", ""),
            " ".join(meta.get("tags") or []),
            Path(meta.get("path", "")).stem,
        ]
    )
    meta_tokens = _tokenize(meta_blob)
    overlap = query_tokens & (body_tokens | meta_tokens)
    if not overlap:
        return 0.0
    # Weight title/id matches higher
    score = len(overlap) * 1.0
    for tok in overlap:
        if tok in meta.get("id", "").lower():
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

    hits: list[RagHit] = []
    for meta in _load_manifest(root):
        if domains and meta.get("domain") not in domains:
            continue
        rel = meta.get("path", "")
        chunk_path = root / rel
        if not chunk_path.is_file():
            continue
        body = chunk_path.read_text(encoding="utf-8", errors="replace")
        score = _score_chunk(query_tokens, body, meta)
        if score <= 0:
            continue
        excerpt = _excerpt(body, query_tokens)
        hits.append(
            RagHit(
                chunk_id=meta.get("id", rel),
                path=rel,
                domain=meta.get("domain", ""),
                score=score,
                excerpt=excerpt,
            )
        )

    hits.sort(key=lambda h: -h.score)
    return hits[:limit]


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
