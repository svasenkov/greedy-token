from __future__ import annotations

import json
import os
import re


DUAL_VERSION_RULE = """
Сохрани: цель, scope/зона, skill/rule, пути/файлы, ограничения, критерий готовности, запреты.
Убери: вводные, повторы, «можно/желательно», пояснения «почему», примеры если суть в ограничениях.
Запрещено: новые требования, новый scope, размытые формулировки.
"""


def compress_heuristic(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) <= 3:
        return text.strip()

    keep: list[str] = []
    drop_prefixes = (
        "можно",
        "желательно",
        "например",
        "пример",
        "ref:",
        "реф:",
    )
    for ln in lines:
        low = ln.lower()
        if any(low.startswith(p) for p in drop_prefixes):
            continue
        if re.match(r"^(почему|зачем|note:|примечание)", low):
            continue
        keep.append(ln)

    # Merge short related lines with semicolons
    parts: list[str] = []
    for ln in keep:
        ln = re.sub(r"\s+", " ", ln)
        if len(ln) > 120 and ". " in ln:
            parts.extend(p.strip() for p in ln.split(". ") if p.strip())
        else:
            parts.append(ln)

    short = ". ".join(parts)
    short = re.sub(r"\.\s*\.", ".", short)
    if not short.endswith("."):
        short += "."
    return short


def compress_ollama_detail(text: str) -> tuple[str, int | None]:
    import urllib.request

    url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:14b")
    system = (
        "Сожми промпт для Cursor-агента. "
        + DUAL_VERSION_RULE
        + " Ответ — только короткий промпт, без пояснений."
    )
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text.strip()},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        f"{url}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    content = data["message"]["content"].strip()
    eval_tokens = data.get("eval_count")
    return content, eval_tokens


def compress_ollama(text: str) -> str:
    content, _ = compress_ollama_detail(text)
    return content


def compress_prompt(text: str, *, use_ollama: bool = False) -> str:
    short, _ = compress_prompt_detail(text, use_ollama=use_ollama)
    return short


def compress_prompt_detail(text: str, *, use_ollama: bool = False) -> tuple[str, int | None]:
    if use_ollama:
        try:
            return compress_ollama_detail(text)
        except Exception as exc:
            fallback = compress_heuristic(text)
            return f"# Ollama failed ({exc}); heuristic fallback:\n\n{fallback}", None
    return compress_heuristic(text), None


def format_dual(text: str, short: str) -> str:
    return "\n".join(
        [
            "**Промпт:**",
            "```text",
            text.strip(),
            "```",
            "",
            "**Короткая версия для агента:**",
            "```text",
            short.strip(),
            "```",
        ]
    )
