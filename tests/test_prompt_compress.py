from __future__ import annotations

import allure
import pytest

from greedy_token.prompt_compress import compress_heuristic, compress_prompt_detail, format_dual
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Prompt compression"),
    allure.parent_suite("Prompt compression"),
    allure.feature("Heuristic compress"),
    allure.suite("Heuristic compress"),
]


@allure.story("Heuristic")
@allure.title("Heuristic compression drops filler lines")
def test_compress_heuristic_drops_filler_lines() -> None:
    text = (
        "Goal: fix baseUrl.\n"
        "Context: configurator forms.\n"
        "Optional: use example from docs.\n"
        "Constraint: do not touch stacks."
    )
    with allure.step("Compress prompt with heuristic"):
        attach_text("original prompt", text)
        short = compress_heuristic(text)
        attach_text("compressed prompt", short)
    with allure.step("Verify filler lines are dropped"):
        assert "baseUrl" in short
        assert "optional" not in short.lower()
        assert "stacks" in short


@allure.story("Detail mode")
@allure.title("Prompt detail compression uses heuristic when Ollama disabled")
def test_compress_prompt_detail_heuristic() -> None:
    with allure.step("Compress prompt detail without Ollama"):
        short, eval_tokens = compress_prompt_detail("Do X.\nWhy: because.", use_ollama=False)
        attach_text("compressed prompt", short)
        attach_text("eval_tokens", str(eval_tokens))
    with allure.step("Verify heuristic compression result"):
        assert "Do X" in short
        assert eval_tokens is None


@allure.story("Dual format")
@allure.title("Dual-format output wraps long and short prompt blocks")
def test_format_dual_wraps_blocks() -> None:
    with allure.step("Format dual prompt blocks"):
        out = format_dual("long prompt", "short")
        attach_text("dual format output", out)
    with allure.step("Verify both prompt blocks are present"):
        assert "**Prompt:**" in out
        assert "**Short version for agent:**" in out
        assert "short" in out


@allure.story("Heuristic")
@allure.title("Heuristic compression keeps short prompts unchanged")
def test_compress_heuristic_short_unchanged() -> None:
    text = "One line only."
    assert compress_heuristic(text) == text


@allure.story("Heuristic")
@allure.title("Heuristic compression splits long sentences")
def test_compress_heuristic_splits_long() -> None:
    long = "Goal A. " + "detail " * 30 + ". Constraint B."
    short = compress_heuristic(long)
    assert "Goal A" in short
    assert short.endswith(".")


@allure.story("Ollama")
@allure.title("compress_ollama_detail calls stub server")
def test_compress_ollama_detail(ollama_stub: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.prompt_compress import compress_ollama, compress_ollama_detail

    monkeypatch.setenv("OLLAMA_URL", ollama_stub)
    monkeypatch.setenv("OLLAMA_MODEL", "stub-model")
    text = "Fix baseUrl in configurator forms."
    short, eval_tokens = compress_ollama_detail(text)
    assert short
    assert compress_ollama(text) == short


@allure.story("Ollama")
@allure.title("compress_prompt_detail falls back on Ollama failure")
def test_compress_prompt_ollama_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.prompt_compress import compress_prompt, compress_prompt_detail

    def boom(*args, **kwargs):
        raise OSError("down")

    monkeypatch.setattr("greedy_token.prompt_compress.compress_ollama_detail", boom)
    short, tokens = compress_prompt_detail("Do X.\nWhy: because.", use_ollama=True)
    assert "Ollama failed" in short
    assert tokens is None
    assert compress_prompt("Do X.", use_ollama=True).startswith("# Ollama failed")

