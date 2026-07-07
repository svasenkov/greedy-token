from __future__ import annotations

from greedy_token.prompt_compress import compress_heuristic, compress_prompt_detail, format_dual


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


def test_compress_prompt_detail_heuristic() -> None:
    short, eval_tokens = compress_prompt_detail("Сделай X.\nПочему: потому что.", use_ollama=False)
    assert "Сделай X" in short
    assert eval_tokens is None


def test_format_dual_wraps_blocks() -> None:
    out = format_dual("long prompt", "short")
    assert "**Промпт:**" in out
    assert "**Короткая версия для агента:**" in out
    assert "short" in out
