from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from llm_optimizer.paths import find_monorepo_root
from llm_optimizer.tokens import TokenEstimate, count_file, format_size_table


@dataclass
class ContextItem:
    path: str
    kind: str
    always_on: bool
    estimate: TokenEstimate


def audit_context(root: Path | None = None) -> list[ContextItem]:
    root = root or find_monorepo_root()
    items: list[ContextItem] = []

    globs = [
        (".cursor/rules/*.mdc", "rule", True),
        (".cursor/skills/*/SKILL.md", "skill", False),
        ("docs/CONTEXT.md", "doc", False),
        ("docs/migration-prompts.md", "doc", False),
    ]

    for pattern, kind, always_on in globs:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root))
            items.append(
                ContextItem(
                    path=rel,
                    kind=kind,
                    always_on=always_on,
                    estimate=count_file(path),
                )
            )

    return items


def render_audit(items: list[ContextItem]) -> str:
    always = [i for i in items if i.always_on]
    rules_total = sum(i.estimate.tokens for i in always)
    skills_total = sum(i.estimate.tokens for i in items if i.kind == "skill")
    all_total = sum(i.estimate.tokens for i in items)

    lines = [
        "== Cursor context audit ==",
        "",
        f"Always-on rules (.cursor/rules/*.mdc): {rules_total:,} tokens",
        f"Skills on disk (.cursor/skills/*/SKILL.md): {skills_total:,} tokens",
        f"Sampled docs: {all_total - rules_total - skills_total:,} tokens",
        f"Grand total (sampled set): {all_total:,} tokens",
        "",
        "Always-on rules (charged every chat):",
    ]

    rule_rows = [
        (i.path, i.estimate) for i in sorted(always, key=lambda x: -x.estimate.tokens)
    ]
    if rule_rows:
        total = TokenEstimate(
            tokens=rules_total,
            chars=sum(i.estimate.chars for i in always),
            method=always[0].estimate.method if always else "n/a",
        )
        lines.append(format_size_table(rule_rows, total))
    else:
        lines.append("  (none)")

    lines.extend(["", "Top skills by size:"])
    top_skills = sorted(
        [i for i in items if i.kind == "skill"], key=lambda x: -x.estimate.tokens
    )[:10]
    for s in top_skills:
        lines.append(f"  {s.estimate.tokens:>6}  {s.path}")

    cache_hint = 1024
    if rules_total >= cache_hint:
        lines.extend(
            [
                "",
                f"Note: always-on rules ({rules_total:,} tok) ≥ {cache_hint} —",
                "stable prefix is cache-friendly for Claude API prompt caching.",
            ]
        )
    elif rules_total > 0:
        lines.extend(
            [
                "",
                f"Note: always-on rules ({rules_total:,} tok) < {cache_hint} —",
                "below typical prompt-cache minimum; keep rules slim.",
            ]
        )

    return "\n".join(lines)
