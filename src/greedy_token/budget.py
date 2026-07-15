"""Token budget footer for MCP tools and CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import os

from greedy_token.context_audit import audit_context
from greedy_token.estimator import cursor_baseline, cursor_saved_for
from greedy_token.paths import find_workspace_root
from greedy_token.rag_search import RagHit
from greedy_token.router import BASE_CURSOR_OVERHEAD, RouteDecision, route_task_all_tiers
from greedy_token.settings import FooterStyle, get_cheap_llm_settings, get_footer_settings
from greedy_token.tokens import count_tokens
from greedy_token.usage import append_event, build_route_event
from greedy_token.wrappers import ollama_available

FooterStyleArg = Literal["compact", "markdown", "full"] | None

TIER_LABELS: dict[str, str] = {
    "tool": "rg (disk search)",
    "python": "python (script)",
    "ollama": "ollama (cheap LLM)",
    "rag": "rag (docs/rag read)",
    "cursor": "cursor (expensive LLM)",
}

EXECUTOR_SUB_LABELS: dict[str, str] = {
    "rg": "ripgrep on disk",
    "python": "python file/tree scan (rg unavailable)",
    "rag": "docs/rag chunk read",
    "ollama": "cheap LLM inference",
    "cursor": "expensive LLM agent loop",
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
        if hit.body is not None:
            total += count_tokens(hit.body).tokens
            continue
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
            return "ripgrep on disk — 0 LLM spend"
        return "script — 0 LLM spend"
    if tier == "ollama":
        return "cheap LLM — local/cheap spend"
    if tier == "rag":
        if spent <= 0:
            return "docs/rag — no chunks counted"
        return "docs/rag chunks read into context"
    if tier == "cursor":
        return "expensive LLM path — same order as baseline"
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


def _format_tier_alternatives(
    task: str,
    root: Path,
    selected: str,
    *,
    selected_spent: int | None = None,
) -> list[str]:
    """Tier scan estimates; the selected row uses actual spent when provided."""
    lines = ["Tier alternatives (estimated):"]
    for tier, decision in route_task_all_tiers(task, root):
        label = TIER_LABELS.get(tier, tier)
        if tier == selected and selected_spent is not None:
            est = selected_spent
        else:
            est = decision.est_tokens
        suffix = ""
        if tier == selected:
            suffix = "  ← this call"
        elif tier == "ollama":
            llm = get_cheap_llm_settings()
            if ollama_available():
                suffix = f"  · {llm.provider}/{llm.model}, cheap"
            else:
                suffix = "  · unavailable (would fall back to expensive LLM)"
        elif tier in ("tool", "python"):
            suffix = "  · 0 LLM"
        lines.append(f"  {label:<26} ~{est:>6,}{suffix}")
    return lines


@dataclass(frozen=True)
class ToolFooterContext:
    task: str
    root: Path
    tier: str
    est_tokens: int
    route_id: str
    executor_sub: str
    sub_label: str
    duration_ms: int | None
    rag_hits: int | None
    ollama_eval_tokens: int | None
    breakdown: CursorBaselineBreakdown
    baseline: int
    saved: int
    billing_short: str


def _billing_short(
    tier: str,
    *,
    rag_hits: int | None = None,
    ollama_eval_tokens: int | None = None,
) -> str:
    if tier in ("tool", "python"):
        return "free tier"
    if tier == "ollama":
        llm = get_cheap_llm_settings()
        model_id = os.environ.get("GREEDY_LLM_MODEL_ID", "")
        label = f"{model_id}/" if model_id else ""
        note = f", ~{ollama_eval_tokens:,} eval" if ollama_eval_tokens else ""
        return f"cheap LLM ({label}{llm.model}{note})"
    if tier == "rag":
        hit_note = f", {rag_hits} chunk(s)" if rag_hits is not None else ""
        return f"docs/rag{hit_note}"
    if tier == "cursor":
        return "expensive LLM"
    return tier


def _build_tool_footer_context(
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
) -> ToolFooterContext:
    breakdown = cursor_baseline_breakdown(root, task)
    baseline = breakdown.total
    saved = cursor_saved_for(root, task, est_tokens, tier)
    sub = executor_sub or tier
    sub_label = EXECUTOR_SUB_LABELS.get(sub, sub)
    return ToolFooterContext(
        task=task,
        root=root,
        tier=tier,
        est_tokens=est_tokens,
        route_id=route_id,
        executor_sub=sub,
        sub_label=sub_label,
        duration_ms=duration_ms,
        rag_hits=rag_hits,
        ollama_eval_tokens=ollama_eval_tokens,
        breakdown=breakdown,
        baseline=baseline,
        saved=saved,
        billing_short=_billing_short(
            tier,
            rag_hits=rag_hits,
            ollama_eval_tokens=ollama_eval_tokens,
        ),
    )


def _resolve_footer_style(root: Path, style: FooterStyleArg) -> FooterStyle:
    if style is not None:
        return style
    return get_footer_settings(root).style


def _format_tool_footer_compact(ctx: ToolFooterContext) -> str:
    duration = f" · {ctx.duration_ms}ms" if ctx.duration_ms is not None else ""
    route = f" · {ctx.route_id}" if ctx.route_id else ""
    extras = _policy_footer_lines(ctx.root)
    lines = [
        "",
        "---",
        f"> **Greedy token** · `{ctx.executor_sub}`{duration} · saved **~{ctx.saved:,}**",
        f"> spent ~{ctx.est_tokens:,} · naive ~{ctx.baseline:,} · {ctx.billing_short}{route}",
    ]
    lines.extend(extras)
    lines.append("---")
    return "\n".join(lines)


def _policy_footer_lines(root: Path) -> list[str]:
    try:
        from greedy_token.budget_policy import policy_footer_extras

        return policy_footer_extras(root=root)
    except (ImportError, OSError, ValueError, RuntimeError):
        return []


def _format_tool_footer_markdown(ctx: ToolFooterContext) -> str:
    duration = f" · {ctx.duration_ms}ms" if ctx.duration_ms is not None else ""
    route = f" · `{ctx.route_id}`" if ctx.route_id else ""
    return "\n".join(
        [
            "",
            "---",
            f"### Greedy token · `{ctx.executor_sub}`{duration}",
            "",
            "| | tokens |",
            "|:--|--:|",
            f"| spent | ~{ctx.est_tokens:,} |",
            f"| naive Cursor | ~{ctx.baseline:,} |",
            f"| **saved** | **~{ctx.saved:,}** |",
            "",
            f"{ctx.billing_short}{route}",
            "---",
        ]
    )


def _format_tool_footer_full(ctx: ToolFooterContext) -> str:
    lines = [
        "",
        "---",
        "Greedy token",
        "",
        "This call",
        f"  Executor: {ctx.executor_sub} — {ctx.sub_label}",
    ]
    if ctx.route_id:
        lines.append(f"  Route: {ctx.route_id}")
    if ctx.duration_ms is not None:
        lines.append(f"  Duration: {ctx.duration_ms} ms")
    lines.append(
        format_spent_line(
            ctx.est_tokens,
            tier=ctx.tier,
            executor_sub=ctx.executor_sub,
            indent="  ",
        )
    )
    if ctx.tier in ("tool", "python"):
        lines.append("  Billing: free tier — not expensive LLM")
    elif ctx.tier == "ollama":
        llm = get_cheap_llm_settings()
        eval_note = (
            f", ~{ctx.ollama_eval_tokens:,} eval tokens" if ctx.ollama_eval_tokens else ""
        )
        lines.append(
            f"  Billing: cheap LLM ({llm.provider}/{llm.model}{eval_note}) — not expensive path"
        )
    elif ctx.tier == "rag":
        hit_note = f", {ctx.rag_hits} chunk(s)" if ctx.rag_hits is not None else ""
        lines.append(
            f"  Billing: read docs/rag{hit_note} — small context vs expensive LLM chat"
        )
    elif ctx.tier == "cursor":
        lines.append("  Billing: expensive LLM (Cursor agent) — full context + reply")

    lines.extend(
        [
            "",
            "Cursor agent chat (naive — same task, no MCP tool)",
            f"  Always-on rules: ~{ctx.breakdown.rules:,}",
            f"  Task prompt:     ~{ctx.breakdown.task:,}",
            f"  Agent overhead:  ~{ctx.breakdown.overhead:,}",
            f"  {TOTAL_BASELINE_LABEL}  ~{ctx.baseline:,}",
            "",
        ]
    )
    lines.extend(
        _format_tier_alternatives(
            ctx.task, ctx.root, ctx.tier, selected_spent=ctx.est_tokens
        )
    )
    lines.append("")
    lines.extend(
        format_savings_lines(
            baseline=ctx.baseline,
            spent=ctx.est_tokens,
            saved=ctx.saved,
            tier=ctx.tier,
            executor_sub=ctx.executor_sub,
        )
    )
    for extra in _policy_footer_lines(ctx.root):
        lines.append(f"  {extra}")
    lines.extend(
        [
            "",
            "Note: MCP in Agent chat still uses Cursor tokens for rules + your message +",
            "agent reply. Only cheap LLM / rg / rag rows avoid the expensive LLM path.",
        ]
    )

    return "\n".join(lines)


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
    style: FooterStyleArg = None,
) -> str:
    ctx = _build_tool_footer_context(
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
    resolved = _resolve_footer_style(root, style)
    if resolved == "full":
        return _format_tool_footer_full(ctx)
    if resolved == "markdown":
        return _format_tool_footer_markdown(ctx)
    return _format_tool_footer_compact(ctx)


def log_tool_usage(
    *,
    cmd: str,
    task: str,
    root: Path,
    decision: RouteDecision,
    est_tokens_override: int | None = None,
    rag_hits: int | None = None,
    duration_ms: int | None = None,
    tier_scan: list[dict] | None = None,
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
            tier_scan=tier_scan,
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
    root = root or find_workspace_root()
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
            tier_scan=[],
        )
    return body.rstrip() + footer
