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
        "Цель: поправить baseUrl.\n"
        "Контекст: configurator forms.\n"
        "Можно использовать пример из docs.\n"
        "Запрет: не трогать stacks."
    )
    with allure.step("Compress prompt with heuristic"):
        attach_text("original prompt", text)
        short = compress_heuristic(text)
        attach_text("compressed prompt", short)
    with allure.step("Verify filler lines are dropped"):
        assert "baseUrl" in short
        assert "можно" not in short.lower()
        assert "stacks" in short


@allure.story("Detail mode")
@allure.title("Prompt detail compression uses heuristic when Ollama disabled")
def test_compress_prompt_detail_heuristic() -> None:
    with allure.step("Compress prompt detail without Ollama"):
        short, eval_tokens = compress_prompt_detail("Сделай X.\nПочему: потому что.", use_ollama=False)
        attach_text("compressed prompt", short)
        attach_text("eval_tokens", str(eval_tokens))
    with allure.step("Verify heuristic compression result"):
        assert "Сделай X" in short
        assert eval_tokens is None


@allure.story("Dual format")
@allure.title("Dual-format output wraps long and short prompt blocks")
def test_format_dual_wraps_blocks() -> None:
    with allure.step("Format dual prompt blocks"):
        out = format_dual("long prompt", "short")
        attach_text("dual format output", out)
    with allure.step("Verify both prompt blocks are present"):
        assert "**Промпт:**" in out
        assert "**Короткая версия для агента:**" in out
        assert "short" in out
