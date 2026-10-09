"""Token budget footer for MCP tools and CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from greedy_token.advisory import effective_hook_mode
from greedy_token.baseline import (
    SOURCE_DEFAULT,
    baseline_source,
    cursor_overhead,
    format_duration_short,
    get_baseline_settings,
    get_time_baseline_settings,
    naive_agent_ms,
    time_saved_ms,
)
from greedy_token.calibration import SOURCE_FIXED
from greedy_token.context_audit import audit_context
from greedy_token.estimator import cursor_saved_for
from greedy_token.paths import find_workspace_root
from greedy_token.rag_search import RagHit, rag_hit_tokens
from greedy_token.result_gate import (
    OUTCOME_REFUSED,
    GateDecision,
    evaluate_result_gate,
)
from greedy_token.router import RouteDecision, route_task_all_tiers
from greedy_token.settings import FooterStyle, get_cheap_llm_settings, get_footer_settings
from greedy_token.tokens import count_tokens
from greedy_token.tool_output import format_machine_output
from greedy_token.usage import (
    INVOCATION_MCP,
    SAVINGS_SCOPE_TURN,
    SAVINGS_SCOPE_TURN_SHARED,
    TurnSkipEvidence,
    append_event,
    build_outcome_event,
    build_route_event,
    new_operation_id,
    savings_scope_for,
)
from greedy_token.wrappers import ollama_available

FooterStyleArg = FooterStyle | None

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
    # Overhead source: measured | calibrated | default-estimate
    source: str = SOURCE_DEFAULT

    @property
    def total(self) -> int:
        return self.rules + self.task + self.overhead


def cursor_baseline_breakdown(root: Path, task: str) -> CursorBaselineBreakdown:
    items = audit_context(root)
    rules = sum(i.estimate.tokens for i in items if i.always_on)
    baseline_settings = get_baseline_settings()
    return CursorBaselineBreakdown(
        rules=rules,
        task=count_tokens(task).tokens,
        overhead=baseline_settings.overhead_tokens,
        source=baseline_settings.source,
    )


def rag_est_tokens(hits: list[RagHit], root: Path) -> int:
    return sum(rag_hit_tokens(hit, root) for hit in hits)


BASELINE_LABEL = "Baseline (naive agent chat):"
TOTAL_BASELINE_LABEL = "Total (naive agent chat):"
# Estimated payload tokens of the call — an estimate, never authoritative
# provider/billing usage.  Labels and counts alone are not billing evidence.
SPENT_LABEL = "Spent (est. payload tokens):"


def spent_hint(tier: str, spent: int, executor_sub: str | None = None) -> str:
    """What the executor actually is — a label, never a billing measurement.

    Payload tokens are estimated locally; provider usage and spend are
    unmetered without an authoritative source, so no label here may claim
    "0 spend" or a price class.
    """
    sub = executor_sub or tier
    if tier in ("tool", "python"):
        if sub == "rg":
            return "ripgrep on disk"
        return "script"
    if tier == "ollama":
        return "cheap LLM"
    if tier == "rag":
        if spent <= 0:
            return "docs/rag — no chunks counted"
        return "docs/rag chunks read into context"
    if tier == "cursor":
        return "expensive LLM path"
    return ""


def format_spent_line(
    spent: int,
    *,
    # equivalent: spent_hint returns "" for any unlisted tier, so a "XXXX"
    # default renders identically to "".
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
    title: str = "Saved vs naive agent chat",
    # equivalent: spent_hint returns "" for any unlisted tier, so a "XXXX"
    # default renders identically to "".
    tier: str = "",
    executor_sub: str | None = None,
    spent_note: str | None = None,
    source: str | None = None,
    # Scope qualifier — bare text (no parens): embedded inside the saved
    # line's parentheses for unknown claims, appended in parens for numeric
    # ones.  Empty only when a hook-observed turn replacement with an
    # authoritative source backs the full ``baseline − spent``.
    saved_suffix: str = "",
    # The formula spelled out after the figure; scoped potentials spell the
    # bound they assume (full replacement vs shared turn).
    saved_formula: str = "baseline − spent",
    # Earned savings are unmeasured — render ``unknown``, never a formula
    # floor or a fabricated zero.
    saved_unknown: bool = False,
) -> list[str]:
    if source is None:
        source = baseline_source()
    if saved_unknown:
        qual = f"{saved_suffix}; " if saved_suffix else ""
        saved_line = (
            f"  Saved:             unknown"
            f"  ({qual}= {saved_formula}; baseline: {source})"
        )
    else:
        if saved is None:
            saved = max(0, baseline - spent)
        suffix = f" ({saved_suffix})" if saved_suffix else ""
        saved_line = (
            f"  Saved:             ~{saved:,}{suffix}"
            f"  (= {saved_formula}; baseline: {source})"
        )
    return [
        f"{title} (baseline: {source})",
        f"  {BASELINE_LABEL}  ~{baseline:,}  ({source})",
        format_spent_line(
            spent,
            tier=tier,
            executor_sub=executor_sub,
            note=spent_note,
            # equivalent: format_spent_line defaults indent to the same "  ".
            indent="  ",
        ),
        saved_line,
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
        # equivalent: route_task_all_tiers only yields tiers that are all
        # TIER_LABELS keys, so the `tier` fallback default is unreachable.
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
                suffix = f"  · {llm.provider}/{llm.model}"
            else:
                suffix = "  · unavailable (would fall back to expensive LLM)"
        elif tier in ("tool", "python"):
            # A local executor — its spend is unmetered, not "zero LLM".
            suffix = "  · local exec"
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
    # Earned savings — None when no authoritative turn-skip source exists;
    # renderers print ``unknown``, never a formula floor or a zero.
    saved: int | None
    billing_short: str
    baseline_ms: int
    time_saved: int | None
    time_source: str
    task_success: bool | None = None
    # Non-empty only when this call did not execute the tier it describes; the
    # renderers print it next to a zero "saved" instead of branching.
    saved_note: str = ""
    # Resolved hook profile at emit time — ambient context only. It never
    # lifts the claim: scope comes from the declared invocation boundary.
    configured_hook_mode: str = ""
    # Invocation origin the caller declared (cli|mcp|hook); "" = undeclared.
    invocation: str = ""
    # turn | turn_shared | unknown — the bound the saved figure may claim.
    savings_scope: str = "unknown"
    # What the call would save if it had replaced the agent turn — a labelled
    # estimate, never claimable without an evidenced turn replacement.
    saved_potential: int = 0
    # The shared-turn bound (baseline − agent overhead − spent) — the most a
    # call inside a still-live turn could have saved.
    saved_potential_shared: int = 0
    # Authoritative source backing a measured ``saved`` figure — empty
    # while this contour has no validated host receipt/source.
    saved_source: str = ""
    # True only when ``time_saved`` came from the evidence source's own
    # measurement — not the wall-clock formula delta.
    time_measured: bool = False


def _cheap_billing_note(root: Path | None = None) -> str:
    """"metered" vs "local free" for the cheap-LLM tier (ADR-0002 honesty)."""
    from greedy_token.model_select import billing_note_for_model

    # equivalent: a "XXXX" fallback matches no registry model just like "" —
    # billing_note_for_model returns "local free" either way.
    model_id = os.environ.get("GREEDY_LLM_MODEL_ID", "").strip()
    return billing_note_for_model(model_id, root)


def _billing_short(
    tier: str,
    *,
    rag_hits: int | None = None,
    ollama_eval_tokens: int | None = None,
    root: Path | None = None,
) -> str:
    if tier in ("tool", "python"):
        # A local executor has no provider call to meter — "unmetered" is
        # the honest statement; nothing observed a price of zero.
        return "unmetered (local executor)"
    if tier == "ollama":
        llm = get_cheap_llm_settings()
        # equivalent: `if model_id` treats a None fallback identically to "" —
        # the label prefix stays empty in both cases.
        model_id = os.environ.get("GREEDY_LLM_MODEL_ID", "")
        label = f"{model_id}/" if model_id else ""
        note = f", ~{ollama_eval_tokens:,} eval" if ollama_eval_tokens else ""
        # The registry billing class is configuration, not observed spend.
        return (
            f"cheap LLM ({label}{llm.model}{note},"
            f" configured: {_cheap_billing_note(root)})"
        )
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
    # equivalent: the only caller (format_tool_footer) always forwards
    # route_id explicitly, so this default is unreachable.
    route_id: str = "",
    executor_sub: str | None = None,
    duration_ms: int | None = None,
    rag_hits: int | None = None,
    ollama_eval_tokens: int | None = None,
    task_success: bool | None = None,
    # equivalent: format_tool_footer always forwards executed explicitly,
    # so this default is unreachable.
    executed: bool = True,
    # Which boundary emitted this call — the footer only ever runs behind the
    # MCP surface today, so wrap_mcp_response declares "mcp"; an undeclared
    # origin keeps the claim at the conservative "unknown" scope.
    invocation: str | None = None,
    turn_replaced: bool | None = None,
    # Caller-reported TurnSkipEvidence does not validate a host skip —
    # the declaration stays unproven and formula savings stay potential.
    turn_evidence: str | TurnSkipEvidence | None = None,
) -> ToolFooterContext:
    breakdown = cursor_baseline_breakdown(root, task)
    baseline = breakdown.total
    scope = savings_scope_for(
        invocation, turn_replaced, turn_evidence=turn_evidence
    )
    potential_saved = cursor_saved_for(root, task, est_tokens, tier)
    shared_potential = min(
        potential_saved, max(0, baseline - cursor_overhead() - est_tokens)
    )
    saved: int | None
    saved_source = ""
    # Neither a zero formula nor reported metadata validates an earned figure;
    # source names and DTO types cannot validate measured quantities.
    # No authoritative turn-skip source: earned stays unknown — the
    # formula deltas render as labelled potentials only.
    saved = None
    baseline_ms = naive_agent_ms(baseline)
    saved_ms = time_saved_ms(baseline, duration_ms, tier)
    # Caller-reported time has no validated host source; keep only the formula.
    time_measured = False
    saved_note = ""
    if not executed:
        # A recommendation earns nothing: keep the figure as a potential.
        saved_note = f"  (not executed — potential ~{potential_saved:,} if run)"
        saved = 0
        saved_ms = None
    elif task_success is False:
        saved = 0
        saved_ms = None
    time_source = get_time_baseline_settings().source
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
            root=root,
        ),
        baseline_ms=baseline_ms,
        time_saved=saved_ms,
        time_source=time_source,
        configured_hook_mode=effective_hook_mode(),
        invocation=invocation or "",
        savings_scope=scope,
        saved_potential=potential_saved,
        saved_potential_shared=shared_potential,
        # equivalent: ctx.task_success is recorded for callers/debugging, but
        # no renderer reads it — a None/omitted mutation is unobservable.
        task_success=task_success,
        saved_note=saved_note,
        saved_source=saved_source,
        time_measured=time_measured,
    )


def _resolve_footer_style(root: Path, style: FooterStyleArg) -> FooterStyle:
    if style is not None:
        return style
    return get_footer_settings(root).style


def _saved_scope_qualifier(ctx: ToolFooterContext) -> str:
    """Bare qualifier text for unmeasured claims (``ctx.saved is None``).

    Only an evidenced turn replacement (scope ``turn``) prints the bare full
    figure; a shared turn shows its bounded potential, an undeclared or
    unproven origin shows the full-turn estimate — both labelled potential.
    The configured hook mode is context, never evidence.  Renderers wrap the
    text in parentheses.
    """
    if ctx.saved is not None:
        if ctx.savings_scope == SAVINGS_SCOPE_TURN and ctx.saved_source:
            # The figure is measured — name the source next to the claim.
            return f"measured by {ctx.saved_source}"
        return ""
    if ctx.savings_scope == SAVINGS_SCOPE_TURN_SHARED:
        return (
            f"potential ~{ctx.saved_potential_shared:,}"
            " if the call shared a live agent turn — assumed, not observed"
        )
    return (
        f"potential ~{ctx.saved_potential:,}"
        " only if the agent turn was skipped"
    )


def _saved_figure(ctx: ToolFooterContext) -> str:
    return "unknown" if ctx.saved is None else f"~{ctx.saved:,}"


def _saved_qualifier_parens(ctx: ToolFooterContext) -> str:
    qualifier = _saved_scope_qualifier(ctx)
    return f" ({qualifier})" if qualifier else ""


def _time_potential_mark(ctx: ToolFooterContext) -> str:
    # A wall-clock delta without a source-measured figure is a potential.
    return "" if ctx.time_measured else " (potential)"


def _format_tool_footer_compact(ctx: ToolFooterContext) -> str:
    duration = f" · {ctx.duration_ms}ms" if ctx.duration_ms is not None else ""
    route = f" · {ctx.route_id}" if ctx.route_id else ""
    time_saved = (
        f" · ~{format_duration_short(ctx.time_saved)}{_time_potential_mark(ctx)}"
        if ctx.time_saved is not None and ctx.time_saved > 0
        else ""
    )
    extras = _policy_footer_lines(ctx.root)
    lines = [
        "",
        "---",
        f"> **Greedy token** · `{ctx.executor_sub}`{duration} · saved **{_saved_figure(ctx)}**"
        f"{_saved_qualifier_parens(ctx)}{ctx.saved_note}{time_saved} (baseline: {ctx.breakdown.source})",
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
    spent_time = f"{ctx.duration_ms}ms" if ctx.duration_ms is not None else "—"
    time_saved = (
        (
            f"**~{format_duration_short(ctx.time_saved)}**"
            if ctx.time_measured
            else f"~{format_duration_short(ctx.time_saved)} (potential)"
        )
        if ctx.time_saved is not None
        else "~—"
    )
    return "\n".join(
        [
            "",
            "---",
            f"### Greedy token · `{ctx.executor_sub}`{duration}",
            "",
            "| | tokens | time |",
            "|:--|--:|--:|",
            f"| spent | ~{ctx.est_tokens:,} | {spent_time} |",
            f"| naive agent chat ({ctx.breakdown.source}) | ~{ctx.baseline:,} | "
            f"~{format_duration_short(ctx.baseline_ms)} ({ctx.time_source}) |",
            f"| **saved** (baseline: {ctx.breakdown.source}){ctx.saved_note} | "
            f"**{_saved_figure(ctx)}**{_saved_qualifier_parens(ctx)} | {time_saved} |",
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
            # equivalent: format_spent_line defaults indent to the same "  ".
            indent="  ",
        )
    )
    # Billing is unmetered on every tier: provider usage and spend have no
    # authoritative source here — the label names the executor class, and
    # for cheap LLM the registry billing class is marked as configuration.
    if ctx.tier in ("tool", "python"):
        lines.append("  Billing: unmetered — local executor")
    elif ctx.tier == "ollama":
        llm = get_cheap_llm_settings()
        eval_note = (
            f", ~{ctx.ollama_eval_tokens:,} eval tokens" if ctx.ollama_eval_tokens else ""
        )
        billing_note = _cheap_billing_note(ctx.root)
        lines.append(
            f"  Billing: unmetered — cheap LLM ({llm.provider}/{llm.model}"
            f"{eval_note}; configured: {billing_note})"
        )
    elif ctx.tier == "rag":
        hit_note = f", {ctx.rag_hits} chunk(s)" if ctx.rag_hits is not None else ""
        lines.append(f"  Billing: unmetered — docs/rag read{hit_note}")
    elif ctx.tier == "cursor":
        lines.append("  Billing: unmetered — expensive LLM (agent chat)")

    lines.extend(
        [
            "",
            "Agent chat (naive — same task, no MCP tool)",
            f"  Always-on rules: ~{ctx.breakdown.rules:,}  (measured)",
            f"  Task prompt:     ~{ctx.breakdown.task:,}  (measured)",
            f"  Agent overhead:  ~{ctx.breakdown.overhead:,}  ({ctx.breakdown.source})",
            f"  {TOTAL_BASELINE_LABEL}  ~{ctx.baseline:,}",
            f"  Naive wall-clock: ~{format_duration_short(ctx.baseline_ms)}  ({ctx.time_source})",
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
            # equivalent: breakdown.source already is
            # get_baseline_settings().source — the same value
            # format_savings_lines falls back to when source is None.
            source=ctx.breakdown.source,
            saved_suffix=_saved_scope_qualifier(ctx),
            saved_unknown=ctx.saved is None,
        )
    )
    if ctx.saved_note:
        lines.append(f"  Saved note:      {ctx.saved_note.strip()}")
    if ctx.time_saved is not None:
        mark = (
            ""
            if ctx.time_measured
            else "potential; "
        )
        lines.append(
            f"  Time saved:      ~{format_duration_short(ctx.time_saved)}"
            f" ({mark}= naive wall-clock − duration; time baseline: {ctx.time_source})"
        )
    for extra in _policy_footer_lines(ctx.root):
        lines.append(f"  {extra}")
    lines.extend(
        [
            "",
            "Note: MCP in Agent chat still uses agent tokens for rules + your message +",
            "agent reply. Only cheap LLM / rg / rag rows avoid the expensive LLM path.",
            "Time saved is an estimate vs a naive agent turn (not a stopwatch).",
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
    task_success: bool | None = None,
    executed: bool = True,
    style: FooterStyleArg = None,
    invocation: str | None = None,
    turn_replaced: bool | None = None,
    turn_evidence: str | TurnSkipEvidence | None = None,
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
        task_success=task_success,
        executed=executed,
        invocation=invocation,
        turn_replaced=turn_replaced,
        turn_evidence=turn_evidence,
    )
    resolved = _resolve_footer_style(root, style)
    if resolved == "machine":
        # Machine mode emits no human footer; wrap_mcp_response serializes
        # the capped JSON envelope instead.
        return ""
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
    executed: bool,
    # The boundary was hit and asked to run even when nothing executed
    # (e.g. a refused invocation) — phase "planned", not "recommended".
    execution_requested: bool = False,
    est_tokens_override: int | None = None,
    rag_hits: int | None = None,
    duration_ms: int | None = None,
    tier_scan: list[dict] | None = None,
    outcome_success: bool | None = None,
    operation_id: str | None = None,
    gate: GateDecision | None = None,
    invocation: str | None = None,
    turn_replaced: bool | None = None,
    turn_evidence: str | TurnSkipEvidence | None = None,
) -> None:
    """Log one tool call. ``executed`` is the caller's observed fact, never a default."""
    append_event(
        build_route_event(
            cmd=cmd,
            task=task,
            root=root,
            decision=decision,
            est_tokens_override=est_tokens_override,
            rag_hits=rag_hits,
            duration_ms=duration_ms,
            executed=executed,
            execution_requested=execution_requested,
            tier_scan=tier_scan,
            outcome_success=outcome_success,
            operation_id=operation_id,
            gate=gate,
            invocation=invocation,
            turn_replaced=turn_replaced,
            turn_evidence=turn_evidence,
        )
    )


def _refusal_error_code(machine_error: dict | None) -> str:
    """Classify ``machine_error.code``: boundary refusal vs infrastructure.

    Only the declared refusal/readiness vocabulary (the boundary declined
    the work — trust gate, readiness, caller error) counts as a refusal;
    any other code is an infrastructure failure and stays ``failure``.
    """
    if not machine_error:
        return ""
    code = str(machine_error.get("code") or "")
    if not code:
        return ""
    try:
        from greedy_token import capabilities, capabilities_invoke

        refusal_codes = frozenset(
            {
                capabilities.NOT_APPROVED,
                capabilities.STALE_BYTES,
                capabilities.STALE_IDENTITY,
                capabilities.MISSING_FILE,
                capabilities.SYMLINK,
                capabilities.UNTRUSTED_TYPE,
                capabilities.CONSUMER_ONLY,
                capabilities.DISABLED_OR_SHADOW,
                capabilities.WRITE_NOT_INVOCABLE,
                capabilities.TOOL_UNAVAILABLE,
                capabilities.ADVISORY_ONLY,
                capabilities.UNKNOWN,
                capabilities_invoke.REFUSAL_UNKNOWN_OPERATION,
                capabilities_invoke.REFUSAL_INVALID_PARAMS,
            }
        )
    except ImportError:
        refusal_codes = frozenset()
    return code if code in refusal_codes else ""


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
    outcome: str | None = None,
    outcome_layer: str = "executor",
    attempts: int = 1,
    retries: int = 0,
    escalations: list[str] | None = None,
    executed: bool = True,
    decision: RouteDecision | None = None,
    result_status: str | None = None,
    style: FooterStyleArg = None,
    machine_payload: dict | None = None,
    machine_error: dict | None = None,
) -> str:
    """Append the footer and log the call.

    ``executed`` must be False for tools that only recommend an executor — the
    tool answering successfully says nothing about the recommended work. Pass the
    real ``decision`` when one exists so its score, matched patterns, and
    calibration survive into telemetry instead of a fixed-confidence stand-in.
    ``result_status`` carries the Step 2 contract verdict (produced / empty /
    invalid / not_evaluated) so telemetry records the evaluator-gate ruling
    instead of crediting bare exit codes.

    Opt-in ``style="machine"`` (or ``footer.style: machine``) replaces the
    human body+footer with a single JSON envelope capped across output,
    metadata and accounting fields; ``machine_payload`` supplies structured
    result fields and ``machine_error`` marks a technical refusal (distinct
    from a negative terminal verdict). Logging/telemetry are unchanged.
    """
    root = root or find_workspace_root()
    task_success = None if outcome is None else outcome == "success"
    # A machine_error in the refusal vocabulary is a boundary-declared
    # refusal: the work was declined before it started.  Other error codes
    # are infrastructure failures and keep the ``failure`` outcome.  The
    # boundary verdict is classified *before* the logging branch — ``log``
    # only controls persistence, never the response or gate semantics, and
    # the refusal code is sufficient without a caller-declared outcome.
    refused = bool(_refusal_error_code(machine_error))
    resolved_outcome = OUTCOME_REFUSED if refused else outcome
    resolved = _resolve_footer_style(root, style)
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
        task_success=task_success,
        executed=executed,
        style=resolved,
        # This surface is only reached through the MCP boundary: the call
        # provably shares a live agent turn.
        invocation=INVOCATION_MCP,
    )
    operation_id = ""
    if log:
        logged_decision = decision or RouteDecision(
            # Direct tool invocation: the tier was given, not scored.
            target=tier,
            route_id=route_id or f"mcp-{tier}",
            confidence=1.0,
            confidence_source=SOURCE_FIXED,
            matched=[],
            command=None,
            note="",
            domains=[],
            est_tokens=est_tokens,
        )
        operation_id = new_operation_id()
        gate = (
            evaluate_result_gate(
                started=executed,
                # A refusal rules even without a contract verdict — the
                # empty status normalizes to not_evaluated inside the gate.
                result_status=result_status or "",
                tier=logged_decision.target,
                # For the gate ``ok`` is the caller's outcome verdict; a tool
                # reporting no outcome means the call itself did not fail.
                ok=task_success is not False,
                refused=refused,
            )
            if result_status is not None or refused
            else None
        )
        log_tool_usage(
            cmd="mcp",
            task=task,
            root=root,
            decision=logged_decision,
            executed=executed,
            # The boundary was hit and declined (or ran): execution was
            # requested even when nothing executed.
            execution_requested=refused or executed,
            est_tokens_override=est_tokens,
            rag_hits=rag_hits,
            duration_ms=duration_ms,
            tier_scan=[],
            outcome_success=task_success,
            operation_id=operation_id,
            gate=gate,
            invocation=INVOCATION_MCP,
        )
        # The outcome record carries the boundary's verdict — a caller
        # label must not launder a refusal into "failure", and a declared
        # refusal is an observed outcome even without a caller outcome.
        if resolved_outcome is not None:
            append_event(
                build_outcome_event(
                    task=task,
                    root=root,
                    decision=logged_decision,
                    outcome=resolved_outcome,
                    layer=outcome_layer,
                    duration_ms=duration_ms,
                    attempts=attempts,
                    retries=retries,
                    escalations=escalations,
                    exit_code=0 if resolved_outcome == "success" else 1,
                    operation_id=operation_id,
                    gate=gate,
                    invocation=INVOCATION_MCP,
                )
            )
    if resolved == "machine":
        payload = machine_payload
        if (
            refused
            and payload is not None
            and payload.get("outcome") != OUTCOME_REFUSED
        ):
            # A payload's outcome field is a caller label too — the
            # boundary verdict wins over it the same way.
            payload = {**payload, "outcome": OUTCOME_REFUSED}
        return format_machine_output(
            body,
            payload=payload,
            ok=(
                task_success
                if task_success is not None
                else machine_error is None
            ),
            outcome=resolved_outcome,
            result_status=result_status,
            executed=executed,
            operation_id=operation_id or None,
            error=machine_error,
        )
    return body.rstrip() + footer
