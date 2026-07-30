"""Trust-cut regressions: false-cheap edit routing + Cyrillic RAG tokenize."""

from __future__ import annotations

import json
import re
from pathlib import Path

import allure
import pytest

from greedy_token.rag_index import _tokenize, get_indexed_chunks, invalidate_rag_index
from greedy_token.rag_search import search_rag
from greedy_token.router import has_edit_verbs, route_task
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Routing"),
    allure.parent_suite("Routing"),
    allure.feature("Trust cut"),
    allure.suite("Trust cut"),
]

# Reviewer false-cheap corpus: search+edit must not hold on tool/rg.
FALSE_CHEAP_EDIT_TASKS = [
    "fix findings from review",
    "найди race condition и исправь",
    "search for race condition and fix it",
]


@allure.story("False-cheap edit")
@allure.title("Reviewer edit prompts escalate to cursor, not tool/rg")
@pytest.mark.parametrize("task", FALSE_CHEAP_EDIT_TASKS)
def test_false_cheap_edit_corpus_escalates(minimal_workspace: Path, task: str) -> None:
    with allure.step(f"Route edit+search task: {task}"):
        assert has_edit_verbs(task)
        decision = route_task(task, minimal_workspace)
        attach_json(
            "decision",
            {
                "task": task,
                "target": decision.target,
                "route_id": decision.route_id,
                "confidence": decision.confidence,
                "note": decision.note,
                "matched": decision.matched,
            },
        )
    with allure.step("Must not remain a successful cheap tool/rg hold"):
        assert decision.target == "cursor"
        assert decision.route_id == "cursor-edit-escalate"
        assert decision.target != "tool"
        assert "edit verbs" in decision.note


@allure.story("False-cheap edit")
@allure.title("Pure search without edit verbs still stays on tool")
def test_pure_search_stays_tool(minimal_workspace: Path) -> None:
    decision = route_task("find baseUrl in sample.js", minimal_workspace)
    assert not has_edit_verbs("find baseUrl in sample.js")
    assert decision.target == "tool"


@allure.story("Unicode tokenize")
@allure.title("Cyrillic query yields a non-empty token set")
def test_tokenize_cyrillic_not_empty() -> None:
    tokens = _tokenize("проверка конфигурации сервиса")
    attach_text("tokens", ", ".join(sorted(tokens)))
    assert tokens
    assert "проверка" in tokens
    assert "конфигурации" in tokens


@allure.story("Unicode tokenize")
@allure.title("ASCII identifiers with underscores still tokenize")
def test_tokenize_keeps_code_identifiers() -> None:
    tokens = _tokenize("look up base_url and baseUrl flags")
    assert "base_url" in tokens
    assert "baseurl" in tokens  # casefold of baseUrl


@allure.story("Unicode tokenize")
@allure.title("RU query finds RU RAG chunk (not empty-token miss)")
def test_rag_search_russian_query(minimal_workspace: Path) -> None:
    rag = minimal_workspace / "docs/rag"
    chunk = rag / "config" / "ru-chunk.md"
    chunk.write_text(
        "Проверка конфигурации сервиса через флаг baseUrl.\n",
        encoding="utf-8",
    )
    (rag / "manifest.jsonl").write_text(
        json.dumps(
            {
                "id": "ru-config",
                "domain": "config",
                "path": "docs/rag/config/ru-chunk.md",
                "tags": ["конфигурация"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    invalidate_rag_index(minimal_workspace)
    get_indexed_chunks(minimal_workspace)
    with allure.step("Search with Cyrillic query"):
        hits = search_rag("проверка конфигурации", minimal_workspace, limit=3)
        attach_json("hits", [{"id": h.chunk_id, "score": h.score} for h in hits])
    with allure.step("Must hit the Russian chunk"):
        assert hits
        assert any(h.chunk_id == "ru-config" for h in hits)


@allure.story("MCP pin")
@allure.title("optional mcp dep pins v1 line (<2) for fastmcp")
def test_mcp_optional_dep_pins_v1_line() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    # Find the optional mcp extra assignment (not a prose mention).
    match = re.search(r'^mcp\s*=\s*\[([^\]]+)\]', text, flags=re.MULTILINE)
    assert match, "mcp optional-extra missing from pyproject.toml"
    spec = match.group(1)
    attach_text("mcp extra", spec)
    assert "mcp>=" in spec
    assert "<2" in spec


@allure.story("MCP pin")
@allure.title("mcp.server.fastmcp imports under installed mcp extra")
def test_mcp_fastmcp_importable() -> None:
    pytest.importorskip("mcp")
    from mcp.server.fastmcp import FastMCP

    assert FastMCP is not None
