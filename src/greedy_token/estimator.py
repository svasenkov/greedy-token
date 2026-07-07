from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from greedy_token.context_audit import audit_context
from greedy_token.router import (
    BASE_CURSOR_OVERHEAD,
    COMPLEXITY_BY_TARGET,
    RouteDecision,
    route_task,
    route_task_all_tiers,
)
from greedy_token.tool_paths import root_cd_prefix
from greedy_token.tokens import count_tokens
from greedy_token.wrappers import ollama_available, ollama_status_line


@dataclass
class TaskEstimate:
    decision: RouteDecision
    complexity: str
    est_tokens: int
    rationale: str
    cursor_saved: int
    ollama_note: str | None = None


def cursor_baseline(root: Path, task: str) -> int:
    items = audit_context(root)
    rules = sum(i.estimate.tokens for i in items if i.always_on)
    task_tokens = count_tokens(task).tokens
    return rules + task_tokens + BASE_CURSOR_OVERHEAD


def cursor_saved_for(root: Path, task: str, est_tokens: int, target: str) -> int:
    if target == "cursor":
        return 0
    return max(0, cursor_baseline(root, task) - est_tokens)


def estimate_task(task: str, root: Path) -> TaskEstimate:
    decision = route_task(task, root)
    complexity = decision.complexity or COMPLEXITY_BY_TARGET.get(decision.target, "medium")
    est_tokens = decision.est_tokens
    rationale = decision.rationale
    cursor_saved = cursor_saved_for(root, task, est_tokens, decision.target)
    ollama_note: str | None = None

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
        cmd = d.command if d.command.startswith("cd ") else f"{root_cd_prefix(root)} {d.command}"
        lines.append(f"Command: {cmd}")
    baseline = cursor_baseline(root, task)
    target = d.target
    spent = estimate.est_tokens
    spent_line = f"Spent (MCP executor, LLM tokens): ~{spent:,}"
    if target in ("tool", "python"):
        spent_line += "  (local — no cloud LLM)"
    elif target == "ollama":
        spent_line += "  (local Ollama — no cloud API tokens)"
    elif target == "rag":
        spent_line += "  (docs/rag chunks read into context)"
    elif target == "cursor":
        spent_line += "  (full agent path — same order as baseline)"
    lines.extend(
        [
            "",
            f"Baseline (naive agent chat):  ~{baseline:,}",
            spent_line,
            f"Saved:             ~{estimate.cursor_saved:,}  (= baseline − spent)",
        ]
    )
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
