from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from greedy_token.paths import find_workspace_root, workspace_trusted_script_paths
from greedy_token.rag_search import format_hits, search_rag
from greedy_token.result_contract import RESULT_NOT_EVALUATED, evaluate_script_result
from greedy_token.router import RouteDecision, route_task
from greedy_token.subprocess_safe import (
    UnsafeCommandError,
    command_to_argv,
    format_invocation,
    trusted_script_argv,
    trusted_tool_invocation,
)
from greedy_token.tool_paths import RG_TIMEOUT, SCRIPT_TIMEOUT
from greedy_token.trust import (
    TrustError,
    VerifiedScript,
    bind_verified_argv,
    trusted_manifest_paths,
    verify_script,
)
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
    script_path: str = ""
    script_type: str = ""
    refusal_reason: str = ""
    # Refusal class (not_approved / stale_bytes / missing_file / symlink /
    # untrusted_type / …) when the refusal came from a trust decision.
    refusal_code: str = ""


@dataclass(frozen=True)
class PlanRunResult:
    """execute_plan result plus whether the executor was actually started.

    Unpacks as the historical ``(exit_code, output)`` tuple, so callers that only
    need those two keep working; ``started`` exists because a refusal, a missing
    executable, and a real failing command all share exit codes.  ``result_status``
    is the SCRIPT-CANON verdict on the output contract (produced / invalid /
    not_evaluated) — exit_code alone is never a validated result.
    """

    exit_code: int
    output: str
    started: bool = False
    result_status: str = RESULT_NOT_EVALUATED

    def __iter__(self):
        return iter((self.exit_code, self.output))


@dataclass
class TaskRunResult:
    decision: RouteDecision
    output: str
    used_rag_fallback: bool = False
    exit_code: int = 0
    # Observed fact: the executor process really started. False also covers
    # "never observed" — it is never inferred from a request to execute.
    started: bool = False
    # SCRIPT-CANON result-contract verdict for script-tier output.
    result_status: str = RESULT_NOT_EVALUATED


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
                refusal_code=getattr(exc, "code", ""),
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

    if target in ("python", "ollama") and decision.command:
        wrapper = wrapper_for_command(decision.command)
        registered = (
            (wrapper.path,)
            if wrapper is not None and wrapper.read_only
            else ()
        )
        try:
            approved = trusted_manifest_paths(root) if decision.read_only else ()
            deprecated = (
                workspace_trusted_script_paths(root) if decision.read_only else ()
            )
            argv = decision.command_argv
            cwd = decision.command_cwd
            if argv is None or cwd is None:
                # Legacy route compatibility only; execution below remains argv/cwd.
                parsed_cwd, parsed_argv = command_to_argv(
                    decision.command,
                    default_cwd=root,
                    workspace_root=root,
                )
                argv = tuple(parsed_argv)
                cwd = parsed_cwd or root
            invocation = trusted_script_argv(
                argv,
                cwd=cwd,
                root=root,
                registered_script_paths=registered,
                manifest_script_paths=approved,
                trusted_script_paths=deprecated,
            )
        except (UnsafeCommandError, TrustError, OSError) as exc:
            dry_run = (
                format_invocation(decision.command_argv, decision.command_cwd)
                if decision.command_argv is not None and decision.command_cwd is not None
                else decision.command
            )
            return RunPlan(
                decision=decision,
                command=decision.command,
                dry_run_output=dry_run,
                executable=False,
                refusal_reason=str(exc),
                refusal_code=getattr(exc, "code", ""),
            )
        dry_run = format_invocation(invocation.argv, invocation.cwd)
        if target == "ollama":
            dry_run += "  # pass args as needed"
        return RunPlan(
            decision=decision,
            command=decision.command,
            dry_run_output=dry_run,
            executable=True,
            argv=invocation.argv,
            cwd=invocation.cwd,
            authorization=invocation.authorization,
            script_path=invocation.script_path,
            script_type=invocation.script_type,
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


def execute_plan(plan: RunPlan) -> PlanRunResult:
    if not plan.command and not plan.argv:
        return PlanRunResult(0, plan.dry_run_output)
    if not plan.executable:
        reason = (
            f" Trust boundary: {plan.refusal_reason}."
            if plan.refusal_reason
            else ""
        )
        code = f" [{plan.refusal_code}]" if plan.refusal_code else ""
        return PlanRunResult(
            1,
            (
                f"Refusing --execute: route is not authorised for execution{code}.{reason}\n"
                f"Dry-run:\n{plan.dry_run_output}\n\n"
                "read_only is metadata, not execution authority."
            ),
        )
    if plan.argv is None or plan.cwd is None or not plan.authorization:
        return PlanRunResult(
            1,
            (
                "Refusing --execute: structured trusted argv is missing.\n"
                f"Dry-run:\n{plan.dry_run_output}"
            ),
        )
    timeout = RG_TIMEOUT if plan.decision.target == "tool" else SCRIPT_TIMEOUT
    verified: VerifiedScript | None = None
    try:
        argv = list(plan.argv)
        # equivalent: None and an empty tuple are both falsy here and are replaced before any manifest descriptor is forwarded.
        pass_fds: tuple[int, ...] = ()  # pragma: no mutate
        if plan.authorization.startswith("manifest:"):
            if not plan.script_path or not plan.script_type:
                raise TrustError("manifest-authorised plan is missing script metadata")
            revalidated = trusted_script_argv(
                plan.argv,
                cwd=plan.cwd,
                root=plan.cwd,
                manifest_script_paths=(plan.script_path,),
            )
            if (
                revalidated.authorization != plan.authorization
                or revalidated.script_path != plan.script_path
                or revalidated.script_type != plan.script_type
            ):
                raise TrustError("manifest-authorised argv changed after planning")
            verified = verify_script(plan.cwd, plan.script_path)
            argv, pass_fds = bind_verified_argv(verified, plan.argv)
        run_kwargs = {
            "shell": False,
            "capture_output": True,
            "text": True,
            "cwd": plan.cwd,
            "timeout": timeout,
        }
        if pass_fds:
            run_kwargs["pass_fds"] = pass_fds
        proc = subprocess.run(
            argv,
            **run_kwargs,
        )
    except TrustError as exc:
        code = getattr(exc, "code", "")
        tag = f" [{code}]" if code else ""
        return PlanRunResult(
            1, f"Refusing --execute: trust verification failed{tag}: {exc}"
        )
    except FileNotFoundError as exc:
        return PlanRunResult(127, f"Executable not found: {exc}")
    except OSError as exc:
        return PlanRunResult(126, f"Cannot execute command: {exc}")
    except subprocess.TimeoutExpired:
        # It did start — it was killed for running too long.
        return PlanRunResult(
            124, f"Command timed out after {timeout}s: {plan.command}", started=True
        )
    finally:
        if verified is not None:
            verified.close()
    out = (proc.stdout or "") + (proc.stderr or "")
    # The canon contract applies to script stdout; the exit code stays the
    # observed fact, result_status is the contract verdict.
    result_status = (
        evaluate_script_result(proc.stdout or "", proc.returncode)
        if plan.script_type == "python"
        else RESULT_NOT_EVALUATED
    )
    return PlanRunResult(
        proc.returncode,
        out or plan.dry_run_output,
        started=True,
        result_status=result_status,
    )


def _filter_tool_output(output: str) -> str:
    return filter_tool_output(output)


def _plan_started(run: PlanRunResult | tuple[int, str]) -> bool:
    """Whether the executor started; a plain tuple never observed it."""
    return getattr(run, "started", False)


def _plan_result_status(run: PlanRunResult | tuple[int, str]) -> str:
    """The contract verdict; a plain tuple was never evaluated."""
    return getattr(run, "result_status", RESULT_NOT_EVALUATED)


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
        run = execute_plan(plan)
        code, out = run
        started = _plan_started(run)
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
                        started=started,
                        # exit_code stays at the dataclass default 0.
                    )
                return TaskRunResult(
                    decision=decision,
                    output=out.strip() or plan.dry_run_output,
                    exit_code=code,
                    started=started,
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
                        started=started,
                        # exit_code stays at the dataclass default 0.
                    )
                return TaskRunResult(
                    decision=decision, output=note, exit_code=code, started=started
                )
            return TaskRunResult(
                decision=decision, output=filtered, exit_code=code, started=started
            )

        return TaskRunResult(
            decision=decision,
            output=out,
            exit_code=code,
            started=started,
            result_status=_plan_result_status(run),
        )

    run = execute_plan(plan)
    code, out = run
    return TaskRunResult(
        decision=decision,
        output=out,
        exit_code=code,
        started=_plan_started(run),
        result_status=_plan_result_status(run),
    )


def _extract_query_note(task: str) -> str:
    from greedy_token.router import _extract_search_query

    return _extract_search_query(task)
