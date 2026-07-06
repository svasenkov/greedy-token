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


def count_texts(texts: list[str]) -> list[TokenEstimate]:
    """Batch variant of count_tokens: one parallel Rust call instead of a Python loop."""
    chars = [len(t) for t in texts]
    try:
        import os

        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        # encode_ordinary skips special-token scanning (irrelevant for size counting)
        # and releases the GIL, encoding on num_threads in parallel.
        encoded = enc.encode_ordinary_batch(texts, num_threads=os.cpu_count() or 4)
        return [
            TokenEstimate(tokens=len(e), chars=c, method="tiktoken/cl100k_base")
            for e, c in zip(encoded, chars)
        ]
    except Exception:
        return [
            TokenEstimate(tokens=max(1, (c + 3) // 4), chars=c, method="heuristic/4")
            for c in chars
        ]


def count_files(paths: list) -> list[TokenEstimate]:
    texts = [p.read_text(encoding="utf-8", errors="replace") for p in paths]
    return count_texts(texts)


def collect_paths(paths: list[str], root) -> list:
    import os
    from pathlib import Path

    out: list[Path] = []
    skip_dirs = {".git", "node_modules", "build", ".venv", "__pycache__"}
    skip_suffixes = {
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
    }

    for raw in paths:
        p = Path(raw)
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        if p.is_file():
            out.append(p)
            continue
        if p.is_dir():
            found: list[str] = []
            # os.walk with in-place pruning: skip_dirs are never descended into
            # (rglob walked them fully and filtered afterwards).
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = [d for d in dirnames if d not in skip_dirs]
                for name in filenames:
                    dot = name.rfind(".")
                    if dot != -1 and name[dot:].lower() in skip_suffixes:
                        continue
                    full = os.path.join(dirpath, name)
                    # parity with rglob + is_file(): drop broken symlinks etc.
                    if not os.path.isfile(full):
                        continue
                    found.append(full)
            found.sort()
            out.extend(Path(f) for f in found)
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
