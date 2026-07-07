"""Token budget footer for MCP tools and CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from greedy_token.context_audit import audit_context
from greedy_token.estimator import BASE_CURSOR_OVERHEAD, cursor_baseline, cursor_saved_for
from greedy_token.paths import find_monorepo_root
from greedy_token.rag_search import RagHit
from greedy_token.router import RouteDecision, route_task_all_tiers
from greedy_token.tokens import count_tokens
from greedy_token.usage import append_event, build_route_event
from greedy_token.wrappers import ollama_available

TIER_LABELS: dict[str, str] = {
    "tool": "rg (local search)",
    "python": "python (script)",
    "ollama": "ollama (local LLM)",
    "rag": "rag (docs/rag read)",
    "cursor": "cursor (agent / cloud)",
}

EXECUTOR_SUB_LABELS: dict[str, str] = {
    "rg": "ripgrep on disk",
    "python": "python file/tree scan (rg unavailable)",
    "rag": "docs/rag chunk read",
    "ollama": "local Ollama inference",
    "cursor": "Cursor agent loop",
}


@dataclass
class CursorBaselineBreakdown:
    rules: int
    task: int
    overhead: int

    @property
    def total(self) -> int:
        return self.rules + self.task + self.overhead


def cursor_baseline_breakdown(root: Path, task: str) -> CursorBaselineBreakdown:
    items = audit_context(root)
    rules = sum(i.estimate.tokens for i in items if i.always_on)
    return CursorBaselineBreakdown(
        rules=rules,
        task=count_tokens(task).tokens,
        overhead=BASE_CURSOR_OVERHEAD,
    )


def rag_est_tokens(hits: list[RagHit], root: Path) -> int:
    total = 0
    for hit in hits:
        chunk_path = root / hit.path
        if chunk_path.is_file():
            total += count_tokens(
                chunk_path.read_text(encoding="utf-8", errors="replace")
            ).tokens
        else:
            total += count_tokens(hit.excerpt).tokens
    return total


BASELINE_LABEL = "Baseline (naive agent chat):"
TOTAL_BASELINE_LABEL = "Total (naive agent chat):"
SPENT_LABEL = "Spent (MCP executor, LLM tokens):"


def spent_hint(tier: str, spent: int, executor_sub: str | None = None) -> str:
    sub = executor_sub or tier
    if tier in ("tool", "python"):
        if sub == "rg":
            return "ripgrep on disk — no cloud LLM"
        return "local script — no cloud LLM"
    if tier == "ollama":
        return "local Ollama — no cloud API tokens"
    if tier == "rag":
        return "docs/rag chunks read into context"
    if tier == "cursor":
        return "full agent path — same order as baseline"
    return ""


def format_spent_line(
    spent: int,
    *,
    tier: str = "",
    executor_sub: str | None = None,
    note: str | None = None,
    indent: str = "  ",
) -> str:
    line = f"{indent}{SPENT_LABEL} ~{spent:,}"
    hint = note or spent_hint(tier, spent, executor_sub)
    if hint:
        return f"{line}  ({hint})"
    return line


def format_savings_lines(
    *,
    baseline: int,
    spent: int,
    saved: int | None = None,
    title: str = "Saved vs naive Cursor chat",
    tier: str = "",
    executor_sub: str | None = None,
    spent_note: str | None = None,
) -> list[str]:
    if saved is None:
        saved = max(0, baseline - spent)
    return [
        title,
        f"  {BASELINE_LABEL}  ~{baseline:,}",
        format_spent_line(
            spent,
            tier=tier,
            executor_sub=executor_sub,
            note=spent_note,
            indent="  ",
        ),
        f"  Saved:             ~{saved:,}  (= baseline − spent)",
    ]


def _format_tier_alternatives(task: str, root: Path, selected: str) -> list[str]:
    lines = ["Tier alternatives (estimated):"]
    for tier, decision in route_task_all_tiers(task, root):
        label = TIER_LABELS.get(tier, tier)
        est = decision.est_tokens
        suffix = ""
        if tier == selected:
            suffix = "  ← this call"
        elif tier == "ollama":
            model = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:14b")
            if ollama_available():
                suffix = f"  · {model}, 0 cloud"
            else:
                suffix = "  · unavailable (would fall back to cursor)"
        elif tier in ("tool", "python"):
            suffix = "  · 0 LLM"
        lines.append(f"  {label:<26} ~{est:>6,}{suffix}")
    return lines


def format_tool_footer(
    task: str,
    root: Path,
    *,
    tier: str,
    est_tokens: int,
    route_id: str = "",
    executor_sub: str | None = None,
    duration_ms: int | None = None,
    rag_hits: int | None = None,
    ollama_eval_tokens: int | None = None,
) -> str:
    breakdown = cursor_baseline_breakdown(root, task)
    baseline = breakdown.total
    saved = cursor_saved_for(root, task, est_tokens, tier)
    sub = executor_sub or tier
    sub_label = EXECUTOR_SUB_LABELS.get(sub, sub)

    lines = [
        "",
        "---",
        "Token economy",
        "",
        "This call",
        f"  Executor: {sub} — {sub_label}",
    ]
    if route_id:
        lines.append(f"  Route: {route_id}")
    if duration_ms is not None:
        lines.append(f"  Duration: {duration_ms} ms")
    lines.append(format_spent_line(est_tokens, tier=tier, executor_sub=sub, indent="  "))
    if tier in ("tool", "python"):
        lines.append("  Billing: local only — no Cursor cloud LLM for this step")
    elif tier == "ollama":
        model = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:14b")
        eval_note = f", ~{ollama_eval_tokens:,} eval tokens" if ollama_eval_tokens else ""
        lines.append(f"  Billing: local Ollama ({model}{eval_note}) — 0 cloud API tokens")
    elif tier == "rag":
        hit_note = f", {rag_hits} chunk(s)" if rag_hits is not None else ""
        lines.append(f"  Billing: read docs/rag{hit_note} — small context vs full agent chat")
    elif tier == "cursor":
        lines.append("  Billing: Cursor agent / cloud — full context + reply")

    lines.extend(
        [
            "",
            "Cursor agent chat (naive — same task, no MCP tool)",
            f"  Always-on rules: ~{breakdown.rules:,}",
            f"  Task prompt:     ~{breakdown.task:,}",
            f"  Agent overhead:  ~{breakdown.overhead:,}",
            f"  {TOTAL_BASELINE_LABEL}  ~{baseline:,}",
            "",
        ]
    )
    lines.extend(_format_tier_alternatives(task, root, tier))
    lines.append("")
    lines.extend(
        format_savings_lines(
            baseline=baseline,
            spent=est_tokens,
            saved=saved,
            tier=tier,
            executor_sub=sub,
        )
    )
    lines.extend(
        [
            "",
            "Note: MCP in Agent chat still uses Cursor tokens for rules + your message +",
            "agent reply. Only the executor row above is local / cheap.",
        ]
    )

    return "\n".join(lines)


def log_tool_usage(
    *,
    cmd: str,
    task: str,
    root: Path,
    decision: RouteDecision,
    est_tokens_override: int | None = None,
    rag_hits: int | None = None,
    duration_ms: int | None = None,
) -> None:
    append_event(
        build_route_event(
            cmd=cmd,
            task=task,
            root=root,
            decision=decision,
            est_tokens_override=est_tokens_override,
            rag_hits=rag_hits,
            duration_ms=duration_ms,
            executed=True,
        )
    )


def wrap_mcp_response(
    body: str,
    *,
    task: str,
    tier: str,
    est_tokens: int,
    route_id: str = "",
    root: Path | None = None,
    log: bool = True,
    duration_ms: int | None = None,
    rag_hits: int | None = None,
    executor_sub: str | None = None,
    ollama_eval_tokens: int | None = None,
) -> str:
    root = root or find_monorepo_root()
    footer = format_tool_footer(
        task,
        root,
        tier=tier,
        est_tokens=est_tokens,
        route_id=route_id,
        executor_sub=executor_sub,
        duration_ms=duration_ms,
        rag_hits=rag_hits,
        ollama_eval_tokens=ollama_eval_tokens,
    )
    if log:
        decision = RouteDecision(
            target=tier,
            route_id=route_id or f"mcp-{tier}",
            confidence=1.0,
            matched=[],
            command=None,
            note="",
            domains=[],
            est_tokens=est_tokens,
        )
        log_tool_usage(
            cmd="mcp",
            task=task,
            root=root,
            decision=decision,
            est_tokens_override=est_tokens,
            rag_hits=rag_hits,
            duration_ms=duration_ms,
        )
    return body.rstrip() + footer
