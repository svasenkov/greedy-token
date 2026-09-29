"""Mutation kill-tests for rag_search: exact scores, fields, ordering, layout."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
from greedy_token.rag_fts import Bm25Match, Fts5Unavailable
from greedy_token.rag_index import IndexedChunk, ManifestDocument
from greedy_token.rag_search import RagHit, _excerpt, _score_indexed, format_hits, search_rag

pytestmark = [
    allure.epic("RAG"),
    allure.parent_suite("RAG"),
    allure.feature("RAG search"),
    allure.suite("RAG search mutation gaps"),
]


def _chunk(
    meta: dict,
    rel_path: str = "docs/rag/x.md",
    domain: str = "config",
    body_tokens: frozenset[str] = frozenset({"tok"}),
    meta_tokens: frozenset[str] = frozenset(),
    body: str = "body tok",
) -> IndexedChunk:
    return IndexedChunk(
        meta=meta,
        rel_path=rel_path,
        domain=domain,
        body=body,
        body_tokens=body_tokens,
        meta_tokens=meta_tokens,
    )


def _fts5_unavailable() -> patch:
    return patch(
        "greedy_token.rag_search.search_bm25",
        side_effect=Fts5Unavailable("forced overlap path"),
    )


# --- _score_indexed: exact arithmetic ---


@allure.story("Overlap scoring")
@allure.title("id-token bonus is exactly +2.0 per matching token")
def test_score_indexed_id_bonus_exact() -> None:
    chunk = _chunk(meta={"id": "api-guide"}, body_tokens=frozenset({"api", "x"}))
    # {"api"}: 1 overlap + 2.0 ("api" in "api-guide"); mutants to .upper(),
    # get(None)/"XXidXX"/"ID" keys lose the bonus → 1.0.
    assert _score_indexed({"api"}, chunk) == 3.0
    # {"api","x"}: 2 overlap + 2.0; `score = 2.0` → 2.0, `+= 3.0` → 5.0.
    assert _score_indexed({"api", "x"}, chunk) == 4.0


@allure.story("Overlap scoring")
@allure.title("missing meta id never crashes and earns no bonus")
def test_score_indexed_missing_id() -> None:
    chunk = _chunk(meta={}, body_tokens=frozenset({"tok"}))
    # get("id", None)/get("id") mutants → None.lower() AttributeError.
    assert _score_indexed({"tok"}, chunk) == 1.0
    # get("id", "XXXX") mutant: "xx" in "xxxx" → phantom +2.0 bonus.
    phantom = _chunk(meta={}, body_tokens=frozenset({"xx"}))
    assert _score_indexed({"xx"}, phantom) == 1.0


# --- search_rag: overlap fallback path ---


@allure.story("Overlap fallback")
@allure.title("default limit is exactly 5 on the overlap path")
def test_search_rag_default_limit_five(minimal_workspace: Path) -> None:
    chunks = [
        _chunk(meta={"id": f"c{i}"}, body_tokens=frozenset({"tok"})) for i in range(6)
    ]
    with _fts5_unavailable(), patch(
        "greedy_token.rag_search.get_indexed_chunks", return_value=chunks
    ):
        # bare call — no limit arg; limit=6 mutant would return 6.
        hits = search_rag("tok", minimal_workspace)
    assert len(hits) == 5


@allure.story("Overlap fallback")
@allure.title("provided root short-circuits find_workspace_root")
def test_search_rag_root_or_semantics(minimal_workspace: Path) -> None:
    def _boom() -> Path:
        raise AssertionError("find_workspace_root must not run when root is given")

    with _fts5_unavailable(), patch(
        "greedy_token.rag_search.find_workspace_root", side_effect=_boom
    ), patch(
        "greedy_token.rag_search.get_indexed_chunks", return_value=[]
    ):
        assert search_rag("tok", minimal_workspace) == []


@allure.story("Overlap fallback")
@allure.title("non-matching domain is skipped via continue, not break")
def test_search_rag_domain_continue_not_break() -> None:
    chunks = [
        _chunk(meta={"id": "other"}, domain="testing"),
        _chunk(meta={"id": "wanted"}, domain="config", rel_path="docs/rag/w.md"),
    ]
    with _fts5_unavailable(), patch(
        "greedy_token.rag_search.get_indexed_chunks", return_value=chunks
    ):
        hits = search_rag("tok", Path("/tmp"), domains=["config"])
    # break-mutant stops at the first non-matching domain → loses `wanted`.
    assert [h.chunk_id for h in hits] == ["wanted"]


@allure.story("Overlap fallback")
@allure.title("score 1.0 chunk is kept (threshold is > 0, not > 1)")
def test_search_rag_score_one_kept() -> None:
    chunk = _chunk(meta={"id": "plain-chunk"}, body_tokens=frozenset({"tok"}))
    with _fts5_unavailable(), patch(
        "greedy_token.rag_search.get_indexed_chunks", return_value=[chunk]
    ):
        hits = search_rag("tok", Path("/tmp"))
    assert len(hits) == 1
    assert hits[0].score == 1.0


@allure.story("Overlap fallback")
@allure.title("scored hits sort by descending score")
def test_search_rag_descending_sort() -> None:
    low = _chunk(meta={"id": "low"}, body_tokens=frozenset({"tok"}))
    high = _chunk(
        meta={"id": "tok-id"}, rel_path="docs/rag/h.md", body_tokens=frozenset({"tok"})
    )  # "tok" in "tok-id" → score 3.0 vs 1.0
    with _fts5_unavailable(), patch(
        "greedy_token.rag_search.get_indexed_chunks", return_value=[low, high]
    ):
        hits = search_rag("tok", Path("/tmp"))
    # key=None sorts ascending by tuple / key=lambda: None keeps input order /
    # +score sorts ascending — all put `low` first.
    assert [h.chunk_id for h in hits] == ["tok-id", "low"]
    assert hits[0].score == 3.0


@allure.story("Overlap fallback")
@allure.title("hit carries rel path, domain, excerpt, body and id-fallback")
def test_search_rag_hit_fields_exact() -> None:
    chunk = _chunk(
        meta={},  # no "id" → chunk_id falls back to rel path
        rel_path="docs/rag/plain.md",
        domain="config",
        body="needle line\nsecond line",
        body_tokens=frozenset({"tok"}),
    )
    with _fts5_unavailable(), patch(
        "greedy_token.rag_search.get_indexed_chunks", return_value=[chunk]
    ):
        hits = search_rag("tok", Path("/tmp"))
    assert len(hits) == 1
    hit = hits[0]
    # kills path/domain/excerpt/body=None mutants and get("id", None)/get("id")
    # chunk_id fallbacks ("None" string instead of the rel path).
    assert hit.chunk_id == "docs/rag/plain.md"
    assert hit.path == "docs/rag/plain.md"
    assert hit.domain == "config"
    assert hit.excerpt == "needle line\nsecond line"
    assert hit.body == "needle line\nsecond line"
    assert hit.engine == "overlap"


# --- search_rag: early-exit guards reach the fts5 path when mutated ---


def _bm25_doc(meta: dict) -> Bm25Match:
    return Bm25Match(
        document=ManifestDocument(
            meta=meta,
            entry_key="docs/rag/b.md",
            rel_path="docs/rag/b.md",
            domain="config",
            body="bm body",
            content_hash="h",
        ),
        score=1.5,
    )


@allure.story("Early exit")
@allure.title("empty token set exits before bm25 (or-guard, not and)")
def test_search_rag_empty_tokens_short_circuit(minimal_workspace: Path) -> None:
    with patch(
        "greedy_token.rag_search.search_bm25", return_value=[_bm25_doc({"id": "b"})]
    ) as bm25:
        # `and`-mutant would proceed and return the mocked bm25 hit.
        assert search_rag("", minimal_workspace) == []
        assert bm25.call_count == 0


@allure.story("Early exit")
@allure.title("limit=0 exits before bm25 (<= 0 guard, not < 0)")
def test_search_rag_limit_zero_short_circuit(minimal_workspace: Path) -> None:
    with patch(
        "greedy_token.rag_search.search_bm25", return_value=[_bm25_doc({"id": "b"})]
    ) as bm25:
        assert search_rag("tok", minimal_workspace, limit=0) == []
        assert bm25.call_count == 0


@allure.story("BM25 path")
@allure.title("bm25 hit fields: rel path and meta-id fallback are exact")
def test_search_rag_bm25_hit_fields(minimal_workspace: Path) -> None:
    with patch(
        "greedy_token.rag_search.search_bm25", return_value=[_bm25_doc({})]
    ):
        hits = search_rag("tok", minimal_workspace)
    assert len(hits) == 1
    hit = hits[0]
    # kills path=None and meta.get("id", None)/get("id") → "None" chunk_id.
    assert hit.path == "docs/rag/b.md"
    assert hit.chunk_id == "docs/rag/b.md"
    assert hit.domain == "config"
    assert hit.engine == "fts5-bm25"


# --- _excerpt: exact truncation/window semantics ---


@allure.story("Excerpt")
@allure.title("default max_len is 320 on the head path")
def test_excerpt_default_max_len_head() -> None:
    body = "x" * 321
    result = _excerpt(body, {"absent"})
    # default 321 mutant would return the whole 321-char head.
    assert result == "x" * 319 + "…"
    assert len(result) == 320


@allure.story("Excerpt")
@allure.title("first line containing a token wins (in, not not-in)")
def test_excerpt_matching_line_wins() -> None:
    body = "plain line\nneedle line\nthird"
    # not-in mutant triggers on "plain line" instead.
    assert _excerpt(body, {"needle"}, max_len=320) == "needle line\nthird"


@allure.story("Excerpt")
@allure.title("excerpt window is exactly 6 lines joined with newline")
def test_excerpt_six_line_window() -> None:
    lines = [f"l{i}" for i in range(8)]
    body = "\n".join(lines)
    result = _excerpt(body, {"l0"}, max_len=320)
    # "XX\nXX" join and i+7 window mutants both diverge from the exact string.
    assert result == "\n".join(lines[:6])


@allure.story("Excerpt")
@allure.title("chunk of exactly max_len is returned whole (>, not >=)")
def test_excerpt_chunk_at_max_len_not_truncated() -> None:
    # craft a chunk whose joined length is exactly 320 with the token on line 0
    filler_len = 320 - len("tok") - 1
    body = "tok\n" + "y" * filler_len
    result = _excerpt(body, {"tok"}, max_len=320)
    assert result == body
    assert not result.endswith("…")


@allure.story("Excerpt")
@allure.title("long chunk truncates to max_len chars ending with ellipsis")
def test_excerpt_chunk_truncation_exact() -> None:
    lines = ["tok", "y" * 400]
    body = "\n".join(lines)
    result = _excerpt(body, {"tok"}, max_len=320)
    # max_len+1/max_len-2 mutants give 321/319-char results.
    assert len(result) == 320
    assert result.endswith("…")
    assert result == ("tok\n" + "y" * 400)[:319] + "…"


@allure.story("Excerpt")
@allure.title("head of exactly max_len is returned whole (>, not >=)")
def test_excerpt_head_at_max_len_not_truncated() -> None:
    body = "z" * 320
    # >= mutant would truncate to 319 + ellipsis.
    assert _excerpt(body, {"absent"}, max_len=320) == body


@allure.story("Excerpt")
@allure.title("long head truncates to max_len chars ending with ellipsis")
def test_excerpt_head_truncation_exact() -> None:
    body = "z" * 400
    result = _excerpt(body, {"absent"}, max_len=320)
    assert len(result) == 320
    assert result == "z" * 319 + "…"


# --- format_hits: exact layout ---


@allure.story("Formatting")
@allure.title("format_hits layout is byte-exact for overlap hits")
def test_format_hits_exact_layout_overlap() -> None:
    hit = RagHit(
        chunk_id="c1",
        path="docs/a.md",
        domain="config",
        score=3.0,
        excerpt="EX",
        body="b",
        engine="overlap",
    )
    out = format_hits("q", [hit])
    # kills "" → "XXXX" fillers, enumerate start mutants, "XX---XX", join and
    # the `or True` engine-label mutant (overlap hit would print bm25 format).
    assert out == (
        "RAG hits for: q\n"
        "\n"
        "1. [c1] score=3.0 engine=overlap  (config)\n"
        "   docs/a.md\n"
        "\n"
        "EX\n"
        "\n"
        "---"
    )


@allure.story("Formatting")
@allure.title("format_hits layout is byte-exact for bm25 hits")
def test_format_hits_exact_layout_bm25() -> None:
    hit = RagHit(
        chunk_id="b1",
        path="docs/b.md",
        domain="testing",
        score=1.5,
        excerpt="EB",
        body="b",
        engine="fts5-bm25",
    )
    out = format_hits("q", [hit])
    assert out == (
        "RAG hits for: q\n"
        "\n"
        "1. [b1] score=1.500000 engine=fts5-bm25 bm25=1.500000  (testing)\n"
        "   docs/b.md\n"
        "\n"
        "EB\n"
        "\n"
        "---"
    )
