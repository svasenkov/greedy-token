from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.tokens import count_texts, count_tokens

pytestmark = [
    allure.epic("Token economy"),
    allure.parent_suite("Token economy"),
    allure.feature("Token counting"),
    allure.suite("Token counting"),
]


@allure.story("tiktoken")
@allure.title("count_tokens uses tiktoken or heuristic fallback")
def test_count_tokens_uses_tiktoken() -> None:
    est = count_tokens("hello world")
    assert est.tokens > 0
    assert est.chars == len("hello world")
    assert "tiktoken" in est.method or "heuristic" in est.method


@allure.story("Batch counting")
@allure.title("count_texts batch matches individual count_tokens")
def test_count_texts_batch_matches_single() -> None:
    texts = ["alpha", "beta gamma"]
    batch = count_texts(texts)
    assert len(batch) == 2
    assert batch[0].tokens == count_tokens(texts[0]).tokens
    assert batch[1].tokens == count_tokens(texts[1]).tokens


@allure.story("Path collection")
@allure.title("collect_paths skips .git directories")
def test_collect_paths_skips_git(minimal_workspace: Path) -> None:
    from greedy_token.tokens import collect_paths

    git_dir = minimal_workspace / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref\n", encoding="utf-8")
    paths = collect_paths(["."], minimal_workspace)
    assert not any(".git" in str(p) for p in paths)
