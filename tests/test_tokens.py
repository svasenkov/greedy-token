from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.tokens import count_texts, count_tokens
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Token economy"),
    allure.parent_suite("Token economy"),
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
