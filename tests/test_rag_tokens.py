from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.budget import rag_est_tokens
from greedy_token.rag_search import RagHit

pytestmark = [
    allure.epic("Token economy"),
    allure.parent_suite("Token economy"),
    allure.feature("RAG token estimate"),
    allure.suite("RAG token estimate"),
]


@allure.story("Token estimate")
@allure.title("rag_est_tokens reuses hit body without re-reading files")
def test_rag_est_tokens_reuses_body_without_reread(minimal_workspace: Path) -> None:
    hits = [
        RagHit(
            chunk_id="x",
            path="docs/rag/e2e/test-chunk.md",
            domain="e2e",
            score=1.0,
            excerpt="short",
            body="baseUrl is configured via -DbaseUrl flag in Gradle.\n",
        )
    ]
    with patch("pathlib.Path.read_text") as mock_read:
        total = rag_est_tokens(hits, minimal_workspace)
    mock_read.assert_not_called()
    assert total > 0
