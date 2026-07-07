from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from greedy_token.paths import find_monorepo_root
from greedy_token.rag_search import format_hits, search_rag
from greedy_token.router import RouteDecision, route_task
from greedy_token.wrappers import wrapper_for_command

JUNK_TOOL_PATH_FRAGMENTS = (
    ".cursor/hooks/",
    "greedy-token-route.sh",
    "greedy-token-home/dev/README",
)


@dataclass
class RunPlan:
    decision: RouteDecision
    command: str | None
    dry_run_output: str
    executable: bool


@dataclass
class TaskRunResult:
    decision: RouteDecision
    output: str
    used_rag_fallback: bool = False
    exit_code: int = 0


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
                "Before paste: greedy-token audit-context && greedy-token rag \"<topic>\""
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


def _filter_tool_output(output: str) -> str:
    lines: list[str] = []
    for line in output.splitlines():
        if any(fragment in line for fragment in JUNK_TOOL_PATH_FRAGMENTS):
            continue
        if line.strip():
            lines.append(line)
    return "\n".join(lines).strip()


def _tool_output_weak(output: str, exit_code: int) -> bool:
    filtered = _filter_tool_output(output)
    if not filtered:
        return True
    if exit_code not in (0, 1):
        return True
    return False


def _infer_rag_domains(task: str) -> list[str] | None:
    text = task.lower()
    domains: list[str] = []
    if any(
        token in text
        for token in (
            "e2e",
            "selenide",
            "allure",
            "testconfig",
            "gradle -d",
            "baseurl",
            "base url",
            "healthcheck",
            "configurator",
        )
    ):
        domains.append("e2e")
    if any(token in text for token in ("stack", "openapi", "spring", "flows/login")):
        domains.append("stacks")
    return domains or None


def _rag_fallback_output(task: str, root: Path) -> str | None:
    domains = _infer_rag_domains(task)
    hits = search_rag(task, root, domains=domains, limit=5)
    if not hits:
        hits = search_rag(task, root, domains=None, limit=5)
    if not hits:
        return None
    return format_hits(task, hits)


def execute_task(task: str, root: Path | None = None) -> TaskRunResult:
    root = root or find_monorepo_root()
    decision = route_task(task, root)
    plan = plan_run(decision, task, root)

    if decision.target == "cursor":
        return TaskRunResult(decision=decision, output="", exit_code=0)

    if plan.executable and plan.command:
        code, out = execute_plan(plan)
        if decision.target == "tool":
            filtered = _filter_tool_output(out)
            if _tool_output_weak(out, code):
                rag_out = _rag_fallback_output(task, root)
                if rag_out:
                    note = (
                        f"rg: нет полезных совпадений для «{_extract_query_note(task)}» "
                        f"→ fallback RAG\n\n"
                    )
                    return TaskRunResult(
                        decision=decision,
                        output=note + rag_out,
                        used_rag_fallback=True,
                        exit_code=0,
                    )
                return TaskRunResult(
                    decision=decision,
                    output=out.strip() or plan.dry_run_output,
                    exit_code=code,
                )
            if filtered != out.strip():
                note = f"rg (без .cursor/hooks):\n{filtered}\n"
                rag_out = _rag_fallback_output(task, root)
                if rag_out and len(filtered.splitlines()) < 3:
                    note += f"\n---\nДополнительно RAG:\n\n{rag_out}"
                    return TaskRunResult(
                        decision=decision,
                        output=note,
                        used_rag_fallback=True,
                        exit_code=0,
                    )
                return TaskRunResult(decision=decision, output=note, exit_code=code)
            return TaskRunResult(decision=decision, output=filtered, exit_code=code)

        return TaskRunResult(decision=decision, output=out, exit_code=code)

    code, out = execute_plan(plan)
    return TaskRunResult(decision=decision, output=out, exit_code=code)


def _extract_query_note(task: str) -> str:
    from greedy_token.router import _extract_search_query

    return _extract_search_query(task)
