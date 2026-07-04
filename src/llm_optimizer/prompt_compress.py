from __future__ import annotations

import json
import os
import re
import urllib.request


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


def compress_ollama(text: str) -> str:
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
    return data["message"]["content"].strip()


def compress_prompt(text: str, *, use_ollama: bool = False) -> str:
    if use_ollama:
        try:
            return compress_ollama(text)
        except Exception as exc:
            return f"# Ollama failed ({exc}); heuristic fallback:\n\n{compress_heuristic(text)}"
    return compress_heuristic(text)


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
