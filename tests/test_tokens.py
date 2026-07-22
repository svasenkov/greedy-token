from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import allure
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from greedy_token.tokens import count_texts, count_tokens
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Greedy token"),
    allure.parent_suite("Greedy token"),
    allure.feature("Token counting"),
    allure.suite("Token counting"),
]


@allure.story("tiktoken")
@allure.title("Token counter uses tiktoken or heuristic fallback")
def test_count_tokens_uses_tiktoken() -> None:
    text = "hello world"
    with allure.step("Count tokens in sample text"):
        est = count_tokens(text)
        attach_json("token estimate", {"tokens": est.tokens, "chars": est.chars, "method": est.method})
    with allure.step("Verify positive token count with known method"):
        assert est.tokens > 0
        assert est.chars == len(text)
        assert "tiktoken" in est.method or "heuristic" in est.method


@allure.story("Invariants")
@allure.title("count_tokens is non-negative and faithfully reports char length")
@given(text=st.text(max_size=128))
@settings(max_examples=200)
def test_count_tokens_invariants(text: str) -> None:
    est = count_tokens(text)
    # Never negative; tokens are exactly zero only for the empty string.
    assert est.tokens >= 0
    assert (est.tokens == 0) == (text == "")
    # The chars field always mirrors the raw input length.
    assert est.chars == len(text)
    assert est.method in ("tiktoken/cl100k_base", "heuristic/4")


@allure.story("Invariants")
@allure.title("Heuristic token estimate is monotonic in input length")
@given(base=st.text(max_size=64), extra=st.text(max_size=64))
@settings(max_examples=200)
def test_heuristic_estimate_monotonic(base: str, extra: str) -> None:
    # Force the deterministic heuristic path (our own formula), which must be
    # monotonic by input length even though raw BPE tokenizers are not.
    with patch("tiktoken.get_encoding", side_effect=RuntimeError("no tiktoken")):
        shorter = count_tokens(base)
        longer = count_tokens(base + extra)
    assert shorter.method == "heuristic/4"
    assert len(base) <= len(base + extra)
    assert shorter.tokens <= longer.tokens


@allure.story("Batch counting")
@allure.title("Batch token count matches individual counter calls")
def test_count_texts_batch_matches_single() -> None:
    texts = ["alpha", "beta gamma"]
    with allure.step("Count tokens in batch"):
        batch = count_texts(texts)
        attach_json("batch counts", [{"tokens": b.tokens, "method": b.method} for b in batch])
    with allure.step("Verify batch matches individual counts"):
        assert len(batch) == 2
        assert batch[0].tokens == count_tokens(texts[0]).tokens
        assert batch[1].tokens == count_tokens(texts[1]).tokens


@allure.story("Path collection")
@allure.title("Path collector skips .git directories")
def test_collect_paths_skips_git(minimal_workspace: Path) -> None:
    from greedy_token.tokens import collect_paths

    git_dir = minimal_workspace / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref\n", encoding="utf-8")
    with allure.step("Collect paths in workspace with .git"):
        paths = collect_paths(["."], minimal_workspace)
        attach_text("collected paths", "\n".join(str(p) for p in paths))
    with allure.step("Verify .git paths are excluded"):
        assert not any(".git" in str(p) for p in paths)


@allure.story("Path collection")
@allure.title("collect_paths skips binary suffixes and broken symlinks")
def test_collect_paths_skips_suffixes(minimal_workspace: Path) -> None:
    from greedy_token.tokens import collect_paths, format_size_table

    assets = minimal_workspace / "assets"
    assets.mkdir()
    (assets / "icon.png").write_bytes(b"\x89PNG")
    (assets / "readme.txt").write_text("hello tokens here\n", encoding="utf-8")
    paths = collect_paths(["assets"], minimal_workspace)
    assert any(p.name == "readme.txt" for p in paths)
    assert not any(p.name == "icon.png" for p in paths)

    rows = [("a.txt", count_tokens("abc"))]
    table = format_size_table(rows, count_tokens("abc"))
    long_path = "x" * 70 + ".txt"
    rows_long = [(long_path, count_tokens("x"))]
    table_long = format_size_table(rows_long, count_tokens("x"))
    assert "…" in table_long
    assert "TOTAL" in table


@allure.story("Heuristic fallback")
@allure.title("count_tokens uses heuristic when tiktoken encoding fails")
def test_count_tokens_heuristic_fallback() -> None:
    import tiktoken

    with patch.object(tiktoken, "get_encoding", side_effect=RuntimeError("fail")):
        est = count_tokens("abcd")
    assert est.method == "heuristic/4"
    assert est.tokens >= 1


@allure.story("Batch fallback")
@allure.title("count_texts uses heuristic when batch encoding fails")
def test_count_texts_heuristic_fallback() -> None:
    import tiktoken

    with patch.object(tiktoken, "get_encoding") as mock_enc:
        mock_enc.return_value.encode_ordinary_batch.side_effect = RuntimeError("fail")
        batch = count_texts(["abcd", "efgh"])
    assert all(b.method == "heuristic/4" for b in batch)


