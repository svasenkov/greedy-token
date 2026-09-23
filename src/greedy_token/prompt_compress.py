from __future__ import annotations

import re
from pathlib import Path

from greedy_token.llm_invoke import invoke_profile

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
        "optional",
        "for example",
        "например",
        "пример",
        "example",
        "ref:",
        "реф:",
    )
    for ln in lines:
        low = ln.lower()
        if any(low.startswith(p) for p in drop_prefixes):
            continue
        if re.match(r"^(почему|зачем|why|because|note:|примечание)", low):
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


def compress_ollama_detail(
    text: str,
    *,
    root: Path | None = None,
    log: bool = True,
    parent_operation_id: str | None = None,
) -> tuple[str, int | None]:
    """Compress via the ``compress`` profile — guarded by the spend guard."""
    system = (
        "Сожми промпт для Cursor-агента. "
        + DUAL_VERSION_RULE
        + " Ответ — только короткий промпт, без пояснений."
    )
    result = invoke_profile(
        "compress",
        system=system,
        user=text.strip(),
        root=root,
        allow_escalate=False,
        log=log,
        parent_operation_id=parent_operation_id,
    )
    return result.text, result.eval_tokens


def compress_ollama(
    text: str,
    *,
    root: Path | None = None,
    log: bool = True,
    parent_operation_id: str | None = None,
) -> str:
    content, _ = compress_ollama_detail(
        text, root=root, log=log, parent_operation_id=parent_operation_id
    )
    return content


def compress_prompt(
    text: str,
    *,
    use_ollama: bool = False,
    root: Path | None = None,
    log: bool = True,
    parent_operation_id: str | None = None,
) -> str:
    short, _ = compress_prompt_detail(
        text,
        use_ollama=use_ollama,
        root=root,
        log=log,
        parent_operation_id=parent_operation_id,
    )
    return short


def compress_prompt_detail(
    text: str,
    *,
    use_ollama: bool = False,
    root: Path | None = None,
    log: bool = True,
    parent_operation_id: str | None = None,
) -> tuple[str, int | None]:
    if use_ollama:
        try:
            return compress_ollama_detail(
                text, root=root, log=log, parent_operation_id=parent_operation_id
            )
        except Exception as exc:
            fallback = compress_heuristic(text)
            return f"# Ollama failed ({exc}); heuristic fallback:\n\n{fallback}", None
    return compress_heuristic(text), None


def format_dual(text: str, short: str) -> str:
    return "\n".join(
        [
            "**Prompt:**",
            "```text",
            text.strip(),
            "```",
            "",
            "**Short version for agent:**",
            "```text",
            short.strip(),
            "```",
        ]
    )
