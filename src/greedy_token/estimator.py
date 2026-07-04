from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from greedy_token.context_audit import audit_context
from greedy_token.router import RouteDecision, route_task, route_task_all_tiers
from greedy_token.tokens import count_tokens
from greedy_token.wrappers import ollama_available, ollama_status_line


TIER_ORDER = ("tool", "python", "ollama", "rag", "cursor")

COMPLEXITY_BY_TARGET = {
    "tool": "low",
    "python": "low",
    "ollama": "medium",
    "rag": "low",
    "cursor": "high",
}

BASE_CURSOR_OVERHEAD = 6000


@dataclass
class TaskEstimate:
    decision: RouteDecision
    complexity: str
    est_tokens: int
    rationale: str
    cursor_saved: int
    ollama_note: str | None = None


def _cursor_baseline_tokens(root: Path) -> int:
    items = audit_context(root)
    return sum(i.estimate.tokens for i in items if i.always_on)


def estimate_task(task: str, root: Path) -> TaskEstimate:
    decision = route_task(task, root)
    complexity = decision.complexity or COMPLEXITY_BY_TARGET.get(decision.target, "medium")
    est_tokens = decision.est_tokens
    rationale = decision.rationale
    cursor_saved = 0
    ollama_note: str | None = None

    if decision.target != "cursor":
        baseline = _cursor_baseline_tokens(root)
        task_tokens = count_tokens(task).tokens
        cursor_equiv = baseline + task_tokens + BASE_CURSOR_OVERHEAD
        cursor_saved = max(0, cursor_equiv - est_tokens)

    if decision.target == "ollama" and not ollama_available():
        ollama_note = ollama_status_line()

    return TaskEstimate(
        decision=decision,
        complexity=complexity,
        est_tokens=est_tokens,
        rationale=rationale,
        cursor_saved=cursor_saved,
        ollama_note=ollama_note,
    )


def format_estimate(estimate: TaskEstimate, task: str, root: Path) -> str:
    d = estimate.decision
    lines = [
        f"Task: {task}",
        f"Route: {d.target.upper()}  ({d.route_id}, {d.confidence:.0%})",
        f"Complexity: {estimate.complexity}",
        f"Est. Cursor tokens: {estimate.est_tokens:,}",
        f"Rationale: {estimate.rationale}",
    ]
    if d.matched:
        lines.append(f"Matched: {', '.join(d.matched)}")
    if d.command:
        cmd = d.command if d.command.startswith("cd ") else f"cd {root} && {d.command}"
        lines.append(f"Command: {cmd}")
    if estimate.cursor_saved > 0:
        lines.append(f"Cursor tokens saved vs agent chat: ~{estimate.cursor_saved:,}")
    if estimate.ollama_note:
        lines.append(f"Note: {estimate.ollama_note}")
    if d.target == "cursor":
        lines.append("→ Новый Cursor-чат; skill из docs/skills-map.md если есть.")
    lines.extend(["", "Tier scan:"])
    for tier, decision in route_task_all_tiers(task, root):
        if decision.matched:
            tag = " ← selected" if decision.route_id == d.route_id else ""
            lines.append(
                f"  {tier:<8} {decision.route_id:<22} "
                f"complexity={decision.complexity} est={decision.est_tokens:,}{tag}"
            )
        else:
            lines.append(f"  {tier:<8} —")
    return "\n".join(lines)
