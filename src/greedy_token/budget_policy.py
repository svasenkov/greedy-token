"""Hybrid routing policy: budget headroom + local model health."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from greedy_token.budget_ledger import cursor_budget_warn, headroom, metered_budget_exhausted
from greedy_token.resource_probe import local_health_line, run_doctor
from greedy_token.router import RouteDecision, TIER_ORDER, route_task_all_tiers


def apply_budget_policy(
    decision: RouteDecision,
    task: str,
    root: Path,
    *,
    policy: str | None = None,
) -> RouteDecision:
    """Post-process route decision based on split budget and local health."""
    if policy is None:
        try:
            from greedy_token.model_select import get_llm_registry

            reg = get_llm_registry(root)
            policy = reg.policy
        except (ImportError, ValueError):
            policy = "auto"

    snap = headroom(root=root)
    metered_exhausted = metered_budget_exhausted(root=root)
    cursor_warn = cursor_budget_warn(root=root)

    # Metered exhausted: prefer ollama/rag over cursor for medium tasks
    if metered_exhausted and decision.target == "cursor" and decision.complexity != "high":
        for tier in ("ollama", "rag", "python", "tool"):
            for _, alt in route_task_all_tiers(task, root):
                if alt.target == tier and alt.confidence > 0 and alt.matched:
                    if tier == "ollama":
                        from greedy_token.wrappers import ollama_available

                        if not ollama_available():
                            continue
                    return replace(
                        alt,
                        rationale=(
                            f"{alt.rationale} Budget: metered cap reached — prefer {tier}."
                        ).strip(),
                        note="budget_policy: metered exhausted",
                    )

    # Cursor soft cap warning: bias medium complexity to ollama
    if cursor_warn and decision.target == "cursor" and decision.complexity == "medium":
        for _, alt in route_task_all_tiers(task, root):
            if alt.target == "ollama" and alt.matched:
                from greedy_token.wrappers import ollama_available

                if ollama_available():
                    return replace(
                        alt,
                        rationale=(
                            f"{alt.rationale} Budget: cursor est. high — prefer local LLM."
                        ).strip(),
                        note="budget_policy: cursor warn",
                    )

    # hybrid policy: never suggest expensive path without headroom
    if policy in ("hybrid", "auto", "cheap_only") and metered_exhausted:
        if decision.target == "cursor" and "escalat" in task.lower():
            for _, alt in route_task_all_tiers(task, root):
                if alt.target == "ollama" and alt.matched:
                    from greedy_token.wrappers import ollama_available

                    if ollama_available():
                        return replace(
                            alt,
                            rationale=f"{alt.rationale} Budget: no metered headroom for escalation.",
                            note="budget_policy: hybrid",
                        )

    # Deprecated local model warning in rationale
    try:
        report = run_doctor(quick=True)
        if report.deprecated_installed and decision.target == "ollama":
            rec = report.recommended[0] if report.recommended else ""
            extra = f" Local: deprecated model — consider ollama pull {rec}."
            return replace(decision, rationale=(decision.rationale + extra).strip())
    except (OSError, ValueError, RuntimeError):
        pass

    if snap.mode == "exhausted" and decision.note != "budget_policy: metered exhausted":
        return replace(
            decision,
            note=(decision.note + " budget: metered exhausted").strip(),
        )

    return decision


def policy_footer_extras(*, root: Path | None = None) -> list[str]:
    """Extra footer lines: split budget + local health."""
    lines: list[str] = []
    try:
        from greedy_token.budget_ledger import format_budget_line

        lines.append(format_budget_line(root=root, compact=True))
        lines.append(local_health_line())
    except (OSError, ValueError, RuntimeError):
        pass
    return lines
