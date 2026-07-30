from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from greedy_token.paths import find_workspace_root, workspace_trusted_script_paths
from greedy_token.rag_search import format_hits, search_rag
from greedy_token.router import RouteDecision, route_task
from greedy_token.subprocess_safe import (
    UnsafeCommandError,
    trusted_script_invocation,
    trusted_tool_invocation,
)
from greedy_token.tool_paths import RG_TIMEOUT, SCRIPT_TIMEOUT, root_cd_prefix
from greedy_token.wrappers import wrapper_for_command

from greedy_token.tool_output import filter_tool_output


@dataclass
class RunPlan:
    decision: RouteDecision
    command: str | None
    dry_run_output: str
    executable: bool
    argv: tuple[str, ...] | None = None
    cwd: Path | None = None
    authorization: str = ""
    refusal_reason: str = ""


@dataclass
class TaskRunResult:
    decision: RouteDecision
    output: str
    used_rag_fallback: bool = False
    exit_code: int = 0


def plan_run(decision: RouteDecision, task: str, root: Path | None = None) -> RunPlan:
    root = root or find_workspace_root()
    target = decision.target

    if target == "tool" and decision.command:
        try:
            if decision.command_argv is None or decision.command_cwd is None:
                raise UnsafeCommandError(
                    "tool command was not produced by the internal argv builder"
                )
            invocation = trusted_tool_invocation(
                decision.command_argv,
                cwd=decision.command_cwd,
                root=root,
                tool=decision.tool,
            )
        except (UnsafeCommandError, OSError) as exc:
            return RunPlan(
                decision=decision,
                command=decision.command,
                dry_run_output=decision.command,
                executable=False,
                refusal_reason=str(exc),
            )
        return RunPlan(
            decision=decision,
            command=decision.command,
            dry_run_output=decision.command,
            executable=decision.read_only,
            argv=invocation.argv,
            cwd=invocation.cwd,
            authorization=invocation.authorization,
        )

    if target == "python" and decision.command:
        cmd = f"{root_cd_prefix(root)} {decision.command}"
        wrapper = wrapper_for_command(decision.command)
        registered = (
            (wrapper.path,)
            if wrapper is not None and wrapper.read_only
            else ()
        )
        trusted = workspace_trusted_script_paths(root) if decision.read_only else ()
        try:
            invocation = trusted_script_invocation(
                cmd,
                root=root,
                registered_script_paths=registered,
                trusted_script_paths=trusted,
            )
        except (UnsafeCommandError, OSError) as exc:
            return RunPlan(
                decision=decision,
                command=cmd,
                dry_run_output=cmd,
                executable=False,
                refusal_reason=str(exc),
            )
        return RunPlan(
            decision=decision,
            command=cmd,
            dry_run_output=cmd,
            executable=True,
            argv=invocation.argv,
            cwd=invocation.cwd,
            authorization=invocation.authorization,
        )

    if target == "ollama" and decision.command:
        cmd = f"{root_cd_prefix(root)} {decision.command}"
        wrapper = wrapper_for_command(decision.command)
        registered = (
            (wrapper.path,)
            if wrapper is not None and wrapper.read_only
            else ()
        )
        trusted = workspace_trusted_script_paths(root) if decision.read_only else ()
        try:
            invocation = trusted_script_invocation(
                cmd,
                root=root,
                registered_script_paths=registered,
                trusted_script_paths=trusted,
            )
        except (UnsafeCommandError, OSError) as exc:
            return RunPlan(
                decision=decision,
                command=cmd,
                dry_run_output=cmd + "  # pass args as needed",
                executable=False,
                refusal_reason=str(exc),
            )
        return RunPlan(
            decision=decision,
            command=cmd,
            dry_run_output=cmd + "  # pass args as needed",
            executable=True,
            argv=invocation.argv,
            cwd=invocation.cwd,
            authorization=invocation.authorization,
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
    if not plan.command and not plan.argv:
        return 0, plan.dry_run_output
    if not plan.executable:
        reason = (
            f" Trust boundary: {plan.refusal_reason}."
            if plan.refusal_reason
            else ""
        )
        return 1, (
            f"Refusing --execute: route is not authorised for execution.{reason}\n"
            f"Dry-run:\n{plan.dry_run_output}\n\n"
            "read_only is metadata, not execution authority."
        )
    if plan.argv is None or plan.cwd is None or not plan.authorization:
        return 1, (
            "Refusing --execute: structured trusted argv is missing.\n"
            f"Dry-run:\n{plan.dry_run_output}"
        )
    timeout = RG_TIMEOUT if plan.decision.target == "tool" else SCRIPT_TIMEOUT
    try:
        proc = subprocess.run(
            list(plan.argv),
            shell=False,
            capture_output=True,
            text=True,
            cwd=plan.cwd,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return 127, f"Executable not found: {exc}"
    except OSError as exc:
        return 126, f"Cannot execute command: {exc}"
    except subprocess.TimeoutExpired:
        return 124, f"Command timed out after {timeout}s: {plan.command}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out or plan.dry_run_output


def _filter_tool_output(output: str) -> str:
    return filter_tool_output(output)


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
            "quality gate",
            "allure dashboard",
            "analytics grid",
            "sparkline",
            "allure agent",
            "metrics catalog",
            "chart matrix",
            "allure shell",
            "analytics index",
        )
    ):
        domains.append("analytics")
    if any(
        token in text
        for token in (
            "page object",
            "po locator",
            "selenide",
            "test pyramid",
            "test layer",
            "ci workflow",
            "allurerc",
        )
    ):
        domains.append("testing")
    if any(
        token in text
        for token in (
            "testconfig",
            "test config",
            "baseurl",
            "base url",
            "healthcheck",
            "configurator",
            "-d flag",
            "property override",
        )
    ):
        domains.append("config")
    if any(token in text for token in ("stack", "openapi", "spring", "flows/login")):
        domains.append("stacks")
    return domains or None


def _rag_fallback_output(task: str, root: Path) -> str | None:
    domains = _infer_rag_domains(task)
    hits = search_rag(task, root, domains=domains, limit=5)
    if not hits:
        # equivalent: domains defaults to None — dropping the kwarg is the same call.
        hits = search_rag(task, root, domains=None, limit=5)  # pragma: no mutate
    if not hits:
        return None
    return format_hits(task, hits)


def execute_task(task: str, root: Path | None = None) -> TaskRunResult:
    root = root or find_workspace_root()
    decision = route_task(task, root)
    plan = plan_run(decision, task, root)

    if decision.target == "cursor":
        return TaskRunResult(
            decision=decision,
            output=(
                "Refusing --execute: cursor tier requires expensive LLM (Agent chat).\n"
                f"{plan.dry_run_output}"
            ),
            exit_code=1,
        )

    if plan.executable and plan.command:
        code, out = execute_plan(plan)
        if decision.target == "tool":
            filtered = _filter_tool_output(out)
            if _tool_output_weak(out, code):
                rag_out = _rag_fallback_output(task, root)
                if rag_out:
                    note = (
                        f"rg: no useful matches for «{_extract_query_note(task)}» "
                        f"→ fallback RAG\n\n"
                    )
                    return TaskRunResult(
                        decision=decision,
                        output=note + rag_out,
                        used_rag_fallback=True,
                        # exit_code stays at the dataclass default 0.
                    )
                return TaskRunResult(
                    decision=decision,
                    output=out.strip() or plan.dry_run_output,
                    exit_code=code,
                )
            if filtered != out.strip():
                note = f"rg (without .cursor/hooks):\n{filtered}\n"
                rag_out = _rag_fallback_output(task, root)
                if rag_out and len(filtered.splitlines()) < 3:
                    note += f"\n---\nAdditional RAG:\n\n{rag_out}"
                    return TaskRunResult(
                        decision=decision,
                        output=note,
                        used_rag_fallback=True,
                        # exit_code stays at the dataclass default 0.
                    )
                return TaskRunResult(decision=decision, output=note, exit_code=code)
            return TaskRunResult(decision=decision, output=filtered, exit_code=code)

        return TaskRunResult(decision=decision, output=out, exit_code=code)

    code, out = execute_plan(plan)
    return TaskRunResult(decision=decision, output=out, exit_code=code)


def _extract_query_note(task: str) -> str:
    from greedy_token.router import _extract_search_query

    return _extract_search_query(task)
