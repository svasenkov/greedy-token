from __future__ import annotations

import allure
import pytest

from greedy_token.prompt_compress import compress_heuristic, compress_prompt_detail, format_dual

pytestmark = [allure.epic("Prompt compression"), allure.feature("Heuristic compress")]


@allure.story("Heuristic")
@allure.title("Heuristic compression drops filler lines")
def test_compress_heuristic_drops_filler_lines() -> None:
    text = (
        "Цель: поправить baseUrl.\n"
        "Контекст: configurator forms.\n"
        "Можно использовать пример из docs.\n"
        "Запрет: не трогать stacks."
    )
    short = compress_heuristic(text)
    assert "baseUrl" in short
    assert "можно" not in short.lower()
    assert "stacks" in short


@allure.story("Detail mode")
@allure.title("compress_prompt_detail uses heuristic when Ollama disabled")
def test_compress_prompt_detail_heuristic() -> None:
    short, eval_tokens = compress_prompt_detail("Сделай X.\nПочему: потому что.", use_ollama=False)
    assert "Сделай X" in short
    assert eval_tokens is None


@allure.story("Dual format")
@allure.title("format_dual wraps long and short prompt blocks")
def test_format_dual_wraps_blocks() -> None:
    out = format_dual("long prompt", "short")
    assert "**Промпт:**" in out
    assert "**Короткая версия для агента:**" in out
    assert "short" in out
