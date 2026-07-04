from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    chars: int
    method: str


def count_tokens(text: str) -> TokenEstimate:
    chars = len(text)
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        tokens = len(enc.encode(text))
        return TokenEstimate(tokens=tokens, chars=chars, method="tiktoken/cl100k_base")
    except Exception:
        # Rough heuristic for Claude-like tokenization (~4 chars/token for English/code mix)
        tokens = max(1, (chars + 3) // 4)
        return TokenEstimate(tokens=tokens, chars=chars, method="heuristic/4")


def count_file(path) -> TokenEstimate:
    text = path.read_text(encoding="utf-8", errors="replace")
    return count_tokens(text)


def collect_paths(paths: list[str], root) -> list:
    from pathlib import Path

    out: list[Path] = []
    skip_dirs = {".git", "node_modules", "build", ".venv", "__pycache__"}

    for raw in paths:
        p = Path(raw)
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        if p.is_file():
            out.append(p)
            continue
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if not f.is_file():
                    continue
                if any(part in skip_dirs for part in f.parts):
                    continue
                if f.suffix.lower() in {
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".gif",
                    ".webp",
                    ".ico",
                    ".woff",
                    ".woff2",
                    ".jar",
                    ".class",
                    ".zip",
                }:
                    continue
                out.append(f)
    return out


def format_size_table(rows: list[tuple[str, TokenEstimate]], total: TokenEstimate) -> str:
    lines = [
        f"{'path':<60} {'tokens':>8} {'chars':>8}",
        "-" * 78,
    ]
    for path, est in rows:
        display = path if len(path) <= 60 else "…" + path[-59:]
        lines.append(f"{display:<60} {est.tokens:>8} {est.chars:>8}")
    lines.append("-" * 78)
    lines.append(
        f"{'TOTAL':<60} {total.tokens:>8} {total.chars:>8}  ({total.method})"
    )
    return "\n".join(lines)
