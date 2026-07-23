"""Token audit of the agent host's always-on context (rules, skills, docs).

Host conventions (config key ``agent_host: cursor|claude|continue``):

* **cursor** — ``.cursor/rules/*.mdc`` always-on rules + ``.cursor/skills``;
* **claude** — ``CLAUDE.md`` (workspace root) + ``.claude/rules/*.md``;
* **continue** — ``.continuerules`` + ``.continue/rules/*.md``.

The sampled docs set is host-independent. Telemetry keys (``cursor_baseline``)
keep their names — they are baseline slot names, not host claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from greedy_token.paths import find_workspace_root
from greedy_token.settings import AgentHost, get_agent_host
from greedy_token.tokens import TokenEstimate, count_files, format_size_table


@dataclass
class ContextItem:
    path: str
    kind: str
    always_on: bool
    estimate: TokenEstimate


# (glob, kind, always_on) per agent host — always-on rules are charged on
# every chat in that host, so they feed the naive-chat baseline.
HOST_RULE_GLOBS: dict[AgentHost, list[tuple[str, str, bool]]] = {
    "cursor": [
        (".cursor/rules/*.mdc", "rule", True),
        (".cursor/skills/*/SKILL.md", "skill", False),
    ],
    "claude": [
        ("CLAUDE.md", "rule", True),
        (".claude/rules/*.md", "rule", True),
        (".cursor/skills/*/SKILL.md", "skill", False),
    ],
    "continue": [
        (".continuerules", "rule", True),
        (".continue/rules/*.md", "rule", True),
        (".cursor/skills/*/SKILL.md", "skill", False),
    ],
}

DOC_GLOBS: list[tuple[str, str, bool]] = [
    ("docs/CONTEXT.md", "doc", False),
    ("docs/migration-prompts.md", "doc", False),
]

HOST_LABELS: dict[AgentHost, str] = {
    "cursor": "Cursor",
    "claude": "Claude",
    "continue": "Continue",
}

HOST_RULES_HINT: dict[AgentHost, str] = {
    "cursor": ".cursor/rules/*.mdc",
    "claude": "CLAUDE.md + .claude/rules/*.md",
    "continue": ".continuerules + .continue/rules/*.md",
}


def resolve_host(root: Path | None = None, host: str | None = None) -> AgentHost:
    """Explicit host wins; otherwise the config (agent_host:, default cursor)."""
    if host in HOST_RULE_GLOBS:
        return host  # type: ignore[return-value]
    return get_agent_host(root).host


def audit_context(root: Path | None = None, host: str | None = None) -> list[ContextItem]:
    root = root or find_workspace_root()
    resolved_host = resolve_host(root, host)
    items: list[ContextItem] = []

    globs = HOST_RULE_GLOBS[resolved_host] + DOC_GLOBS

    found: list[tuple[Path, str, bool]] = []
    for pattern, kind, always_on in globs:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            found.append((path, kind, always_on))

    estimates = count_files([f[0] for f in found])
    for (path, kind, always_on), estimate in zip(found, estimates):
        items.append(
            ContextItem(
                path=str(path.relative_to(root)),
                kind=kind,
                always_on=always_on,
                estimate=estimate,
            )
        )

    return items


def render_audit(items: list[ContextItem], host: str | None = None) -> str:
    resolved_host = resolve_host(None, host)
    label = HOST_LABELS[resolved_host]
    rules_hint = HOST_RULES_HINT[resolved_host]

    always = [i for i in items if i.always_on]
    rules_total = sum(i.estimate.tokens for i in always)
    skills_total = sum(i.estimate.tokens for i in items if i.kind == "skill")
    all_total = sum(i.estimate.tokens for i in items)

    lines = [
        f"== {label} context audit ==",
        "",
        f"Always-on rules ({rules_hint}): {rules_total:,} tokens",
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
