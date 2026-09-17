"""Crystal naming canon: python-{stem} / script-{stem} / scripts/{stem}.py.

``python-`` is the script-tier executor prefix, not a language.
Keep stem rules in sync with ``scripts/routes-sync-check.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROUTE_PREFIX = "python-"
RAG_PREFIX = "script-"
MIN_STEM_TOKENS = 2
MAX_STEM_TOKENS = 4
STEM_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){1,3}$")
ROUTE_ID_RE = re.compile(r"^python-([a-z0-9]+(?:-[a-z0-9]+){1,3})$")
_NON_TOKEN = re.compile(r"[^a-z0-9]+")
_EXECUTOR_STEM_TOKENS = frozenset({"python", "script"})
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "for",
        "of",
        "on",
        "in",
        "at",
        "by",
        "and",
        "or",
        "with",
        "from",
        "via",
        "vs",
        "into",
        "over",
    }
)


def slugify(text: str) -> str:
    text = (text or "").lower().strip()
    text = _NON_TOKEN.sub("-", text)
    return text.strip("-")[:48] or "task"


def tokens_of(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def is_valid_stem(stem: str) -> bool:
    return bool(STEM_RE.fullmatch(stem or ""))


def _drop_executor_tokens(tokens: list[str]) -> list[str]:
    """Strip leading python/script so python-{stem} cannot become python-python-*."""
    dropped = list(tokens)
    while dropped and dropped[0] in _EXECUTOR_STEM_TOKENS:
        dropped = dropped[1:]
    return dropped


def choose_stem(pattern: str) -> str:
    tokens = _drop_executor_tokens(
        [t for t in tokens_of(pattern) if t not in STOPWORDS]
    )
    if len(tokens) < MIN_STEM_TOKENS:
        tokens = _drop_executor_tokens(tokens_of(pattern))
    if len(tokens) < MIN_STEM_TOKENS:
        fallback = _drop_executor_tokens(
            [t for t in slugify(pattern).split("-") if t]
        )
        tokens = fallback or ["task"]
        if len(tokens) < MIN_STEM_TOKENS:
            tokens = (tokens + ["task"])[:MIN_STEM_TOKENS]
    return "-".join(tokens[:MAX_STEM_TOKENS])


def crystal_id_for_pattern(pattern: str) -> str:
    cid = (pattern or "").strip()
    if is_valid_route_id(cid):
        return cid
    return f"{ROUTE_PREFIX}{choose_stem(cid)}"


def crystal_id_for_stem(stem: str) -> str:
    return f"{ROUTE_PREFIX}{stem}"


def rag_id_for_stem(stem: str) -> str:
    return f"{RAG_PREFIX}{stem}"


def stem_of(crystal_id: str) -> str:
    """Display stem: strip python- or script- prefix. Does not validate."""
    cid = (crystal_id or "").strip()
    if cid.startswith(ROUTE_PREFIX):
        return cid[len(ROUTE_PREFIX) :]
    if cid.startswith(RAG_PREFIX):
        return cid[len(RAG_PREFIX) :]
    return cid


def is_valid_route_id(crystal_id: str) -> bool:
    return bool(ROUTE_ID_RE.fullmatch(crystal_id or ""))


def is_slug_prompt_id(crystal_id: str, pattern: str) -> bool:
    """True when id is the full slug of a long prompt (forbidden promote id)."""
    tokens = tokens_of(pattern)
    if len(tokens) <= MAX_STEM_TOKENS:
        return False
    full = slugify(pattern)
    stem = stem_of(crystal_id)
    return stem == full or stem == "-".join(tokens)


def validate_route_id(crystal_id: str, *, pattern: str | None = None) -> str | None:
    """Return error message or None if ok."""
    cid = (crystal_id or "").strip()
    if cid.startswith(RAG_PREFIX):
        return (
            f"crystal_id {cid!r} uses script- prefix; promote id must be "
            "python-{stem} (not slugify(prompt))"
        )
    if cid.startswith("python-python-") or cid.startswith("script-python-"):
        return f"crystal_id {cid!r} doubles executor/language prefix"
    if not is_valid_route_id(cid):
        return (
            f"crystal_id {cid!r} must be python-{{stem}} with kebab stem of "
            f"{MIN_STEM_TOKENS}–{MAX_STEM_TOKENS} tokens, no underscore"
        )
    if pattern and is_slug_prompt_id(cid, pattern):
        return (
            f"crystal_id {cid!r} is slugify(prompt); choose a "
            f"{MIN_STEM_TOKENS}–{MAX_STEM_TOKENS}-token stem first"
        )
    return None


def stem_from_script_path(script: str) -> str | None:
    tokens = (script or "").strip().split()
    if not tokens:
        return None
    raw = tokens[0].lstrip("./")
    if not raw:
        return None
    path = Path(raw)
    name = path.name
    file_stem = name.rsplit(".", 1)[0].replace("_", "-")
    parts = path.parts
    if len(parts) == 2 and parts[0] == "scripts":
        return file_stem
    if len(parts) >= 3 and parts[0] == "scripts":
        pkg = parts[1].replace("_", "-")
        if file_stem == pkg:
            return pkg
        return file_stem
    return file_stem or None


def route_id_from_run_arg(script_id: str) -> str:
    s = (script_id or "").strip()
    if s.startswith(ROUTE_PREFIX):
        return s
    if s.startswith(RAG_PREFIX):
        s = s[len(RAG_PREFIX) :]
    return f"{ROUTE_PREFIX}{s}"
