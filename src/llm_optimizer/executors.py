from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from llm_optimizer.paths import find_monorepo_root
from llm_optimizer.rag_search import format_hits, search_rag
from llm_optimizer.router import RouteDecision
from llm_optimizer.wrappers import wrapper_for_command


@dataclass
class RunPlan:
    decision: RouteDecision
    command: str | None
    dry_run_output: str
    executable: bool


def plan_run(decision: RouteDecision, task: str, root: Path | None = None) -> RunPlan:
    root = root or find_monorepo_root()
    target = decision.target

    if target == "tool" and decision.command:
        return RunPlan(
            decision=decision,
            command=decision.command,
            dry_run_output=decision.command,
            executable=decision.read_only,
        )

    if target == "python" and decision.command:
        cmd = f"cd {root} && {decision.command}"
        wrapper = wrapper_for_command(decision.command)
        read_only = decision.read_only or (wrapper.read_only if wrapper else False)
        return RunPlan(
            decision=decision,
            command=cmd,
            dry_run_output=cmd,
            executable=read_only,
        )

    if target == "ollama" and decision.command:
        cmd = f"cd {root} && {decision.command}"
        return RunPlan(
            decision=decision,
            command=cmd,
            dry_run_output=cmd + "  # pass args as needed",
            executable=False,
        )

    if target == "rag":
        hits = search_rag(task, root, domains=decision.domains or None)
        return RunPlan(
            decision=decision,
            command=None,
            dry_run_output=format_hits(task, hits),
            executable=False,
        )

    if target == "cursor":
        return RunPlan(
            decision=decision,
            command=None,
            dry_run_output=(
                "Open new Cursor chat.\n"
                f"Task: {task}\n"
                "Before paste: llm-opt audit-context && llm-opt rag \"<topic>\""
            ),
            executable=False,
        )

    return RunPlan(
        decision=decision,
        command=None,
        dry_run_output="No executor.",
        executable=False,
    )


def execute_plan(plan: RunPlan) -> tuple[int, str]:
    if not plan.command:
        return 0, plan.dry_run_output
    if not plan.executable:
        return 1, (
            "Refusing --execute: route is not read-only.\n"
            f"Dry-run:\n{plan.dry_run_output}\n\n"
            "Run the script manually if side effects are intended."
        )
    proc = subprocess.run(
        plan.command,
        shell=True,
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out or plan.dry_run_output
