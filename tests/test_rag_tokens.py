from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from greedy_token.budget import rag_est_tokens
from greedy_token.rag_search import RagHit


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
