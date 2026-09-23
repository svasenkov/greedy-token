"""Invoke-by-id — guarded execution for the derived capability surface.

Same policy as ``run --execute``: only read-only ready ops run; refused
invocations carry the readiness/refusal class and emit Step-1 telemetry
(planned, not executed, no savings).  ``invoke_capability`` is the single
entry point shared by the CLI (``capabilities invoke``) and the MCP
``greedy_token_invoke`` tool.
"""

from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from greedy_token.calibration import SOURCE_FIXED
from greedy_token.capabilities import (
    MISSING_FILE,
    UNKNOWN,
    Capability,
    capability_by_id,
)
from greedy_token.executors import (
    RunPlan,
    TaskRunResult,
    execute_plan,
    plan_run,
    task_result_gate,
)
from greedy_token.paths import load_routes_config
from greedy_token.result_contract import RESULT_NOT_EVALUATED
from greedy_token.result_gate import evaluate_result_gate
from greedy_token.router import RouteDecision, _decision_from_route
from greedy_token.subprocess_safe import UnsafeCommandError, format_invocation
from greedy_token.usage import (
    append_event,
    build_outcome_event,
    build_route_event,
    new_operation_id,
)
from greedy_token.wrappers import resolve_wrapper_invocation

# Refusal classes that are not readiness states — caller errors.
REFUSAL_UNKNOWN_OPERATION = "unknown_operation"
REFUSAL_INVALID_PARAMS = "invalid_params"


@dataclass(frozen=True)
class InvocationResult:
    """Structured outcome of invoke-by-id (executed or refused)."""

    op_id: str
    tier: str
    invocable: bool
    executed: bool
    exit_code: int
    output: str
    readiness: str = ""
    refusal_code: str = ""
    refusal_reason: str = ""
    gate_action: str = ""
    gate_reason: str = ""
    result_status: str = ""
    outcome: str = ""
    operation_id: str = ""
    missing_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        value: dict = {
            "op_id": self.op_id,
            "tier": self.tier,
            "invocable": self.invocable,
            "executed": self.executed,
            "exit_code": self.exit_code,
            "readiness": self.readiness,
            "refusal_code": self.refusal_code,
            "refusal_reason": self.refusal_reason,
            "gate_action": self.gate_action,
            "gate_reason": self.gate_reason,
            "result_status": self.result_status,
            "outcome": self.outcome,
            "operation_id": self.operation_id,
            "output": self.output,
        }
        if self.missing_paths:
            value["missing_paths"] = list(self.missing_paths)
        return value


def _log_invocation(
    *,
    root: Path,
    op_id: str,
    decision: RouteDecision,
    duration_ms: int,
    executed: bool,
    authorized: bool,
    exit_code: int,
    gate,
) -> str:
    """Step-1 telemetry for an invoke: request + outcome share one operation_id."""
    operation_id = new_operation_id()
    task = f"invoke {op_id}"
    append_event(
        build_route_event(
            cmd="invoke",
            task=task,
            root=root,
            decision=decision,
            duration_ms=duration_ms,
            executed=executed,
            execution_requested=True,
            authorized=authorized,
            outcome_success=gate.succeeded if executed else None,
            operation_id=operation_id,
            gate=gate,
            tier_scan=[],
        )
    )
    append_event(
        build_outcome_event(
            task=task,
            root=root,
            decision=decision,
            outcome=gate.outcome,
            layer="executor",
            duration_ms=duration_ms,
            exit_code=exit_code,
            operation_id=operation_id,
            gate=gate,
        )
    )
    return operation_id


def _decision_for_op(cap: Capability, *, task: str, root: Path) -> RouteDecision:
    """Decision built like routing does, minus pattern matching.

    The op id is already chosen by the caller, so confidence is a fixed
    direct-invoke marker rather than a match score.
    """
    if cap.source == "wrapper":
        return RouteDecision(
            target=cap.tier,
            route_id=cap.id,
            confidence=1.0,
            confidence_source=SOURCE_FIXED,
            matched=[],
            command=None,
            note="",
            domains=[],
            read_only=cap.read_only,
        )
    route = next(
        r for r in load_routes_config(root).get("routes", []) if r.get("id") == cap.id
    )
    decision = _decision_from_route(
        route, score=0.0, matched=[], task=task, root=root
    )
    decision.confidence = 1.0
    decision.confidence_source = SOURCE_FIXED
    return decision


def _query_task(query: str) -> str:
    """Wrap a tool query so _extract_search_query returns it verbatim."""
    return f'find "{query}"'


def invoke_capability(
    root: Path,
    op_id: str,
    *,
    args: str = "",
    query: str = "",
    log: bool = True,
) -> InvocationResult:
    """Invoke one capability by stable id through the guarded execution path.

    Same policy as ``run --execute``: only read-only ready ops run; refused
    invocations carry the readiness/refusal class and emit Step-1 telemetry
    (planned, not executed, no savings).
    """
    t0 = time.perf_counter()
    cap = capability_by_id(root, op_id)

    def refused(
        code: str, reason: str, *, tier: str, read_only: bool, exit_code: int
    ) -> InvocationResult:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        gate = evaluate_result_gate(
            started=False, result_status=RESULT_NOT_EVALUATED, tier=tier, ok=False
        )
        operation_id = ""
        if log:
            decision = RouteDecision(
                target=tier,
                route_id=op_id,
                confidence=1.0,
                confidence_source=SOURCE_FIXED,
                matched=[],
                command=cap.command if cap else "",
                note="",
                domains=[],
                read_only=read_only,
            )
            operation_id = _log_invocation(
                root=root,
                op_id=op_id,
                decision=decision,
                duration_ms=duration_ms,
                executed=False,
                authorized=False,
                exit_code=exit_code,
                gate=gate,
            )
        return InvocationResult(
            op_id=op_id,
            tier=tier,
            invocable=False,
            executed=False,
            exit_code=exit_code,
            output="",
            readiness=cap.readiness if cap else "",
            refusal_code=code,
            refusal_reason=reason,
            gate_action=gate.action,
            gate_reason=gate.reason,
            result_status=gate.result_status,
            outcome=gate.outcome,
            operation_id=operation_id,
            missing_paths=cap.missing_paths if cap else (),
        )

    if cap is None:
        return refused(
            REFUSAL_UNKNOWN_OPERATION,
            f"no capability with id {op_id!r} — see 'greedy-token capabilities'",
            tier="cursor",
            read_only=False,
            exit_code=2,
        )

    if not cap.invocable:
        return refused(
            cap.readiness,
            cap.reason or "not invocable through this surface",
            tier=cap.tier,
            read_only=cap.read_only,
            exit_code=1,
        )

    # Parameter contract: `query` for rg tool ops; `args` for wrapper ops and
    # routes declaring ``params: [args]`` (validated workspace-relative inside
    # trusted_script_argv, same as `scripts --run`); other route ops stay
    # fixed-argv.
    if cap.params == ("query",):
        if not query.strip():
            return refused(
                REFUSAL_INVALID_PARAMS,
                f"{op_id} requires --query (the rg search term is the parameterized part)",
                tier=cap.tier,
                read_only=cap.read_only,
                exit_code=2,
            )
        if '"' in query or "'" in query:
            return refused(
                REFUSAL_INVALID_PARAMS,
                "query must not contain quote characters",
                tier=cap.tier,
                read_only=cap.read_only,
                exit_code=2,
            )
    elif query.strip():
        return refused(
            REFUSAL_INVALID_PARAMS,
            f"{op_id} declares no query parameter",
            tier=cap.tier,
            read_only=cap.read_only,
            exit_code=2,
        )
    extra_args: tuple[str, ...] = ()
    if args.strip():
        if "args" not in cap.params:
            return refused(
                REFUSAL_INVALID_PARAMS,
                f"{op_id} has a fixed argv contract — no extra args accepted",
                tier=cap.tier,
                read_only=cap.read_only,
                exit_code=2,
            )
        try:
            extra_args = tuple(shlex.split(args))
        except ValueError as exc:
            return refused(
                REFUSAL_INVALID_PARAMS,
                f"cannot parse --args: {exc}",
                tier=cap.tier,
                read_only=cap.read_only,
                exit_code=2,
            )

    task = _query_task(query.strip()) if cap.params == ("query",) else f"invoke {op_id}"
    decision = _decision_for_op(cap, task=task, root=root)

    if cap.source == "wrapper":
        try:
            invocation = resolve_wrapper_invocation(
                cap.id, root, extra_args=extra_args
            )
        except FileNotFoundError as exc:
            return refused(
                getattr(exc, "code", "") or MISSING_FILE,
                str(exc),
                tier=cap.tier,
                read_only=cap.read_only,
                exit_code=1,
            )
        except UnsafeCommandError as exc:
            # A codeless argv violation from caller args is a params error;
            # without args it is an unclassified refusal — never "ready".
            code = exc.code or (
                REFUSAL_INVALID_PARAMS if extra_args else UNKNOWN
            )
            return refused(
                code,
                str(exc),
                tier=cap.tier,
                read_only=cap.read_only,
                exit_code=2 if code == REFUSAL_INVALID_PARAMS else 1,
            )
        except OSError as exc:
            return refused(
                UNKNOWN,
                str(exc),
                tier=cap.tier,
                read_only=cap.read_only,
                exit_code=1,
            )
        plan = RunPlan(
            decision=decision,
            command=cap.command or None,
            dry_run_output=format_invocation(invocation.argv, invocation.cwd),
            executable=True,
            argv=invocation.argv,
            cwd=invocation.cwd,
            authorization=invocation.authorization,
            script_path=invocation.script_path,
            script_type=invocation.script_type,
        )
    else:
        if extra_args:
            if decision.command_argv is None:  # pragma: no cover - probe refuses unsafe argv routes before they become invocable
                return refused(
                    REFUSAL_INVALID_PARAMS,
                    f"{op_id} accepts args but its command argv did not resolve",
                    tier=cap.tier,
                    read_only=cap.read_only,
                    exit_code=2,
                )
            # Fixed command args stay ahead of caller args — same contract as
            # wrapper invocations; trusted_script_argv confines every token.
            decision.command_argv = (*decision.command_argv, *extra_args)
        plan = plan_run(decision, task, root)

    if not plan.executable:
        code = plan.refusal_code or (
            REFUSAL_INVALID_PARAMS if extra_args else UNKNOWN
        )
        return refused(
            code,
            plan.refusal_reason or "not authorized for execution",
            tier=cap.tier,
            read_only=cap.read_only,
            exit_code=2 if code == REFUSAL_INVALID_PARAMS else 1,
        )

    run = execute_plan(plan)
    result = TaskRunResult(
        decision=decision,
        output=run.output,
        exit_code=run.exit_code,
        started=run.started,
        result_status=run.result_status,
    )
    gate = task_result_gate(result, decision)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    operation_id = ""
    if log:
        operation_id = _log_invocation(
            root=root,
            op_id=op_id,
            decision=decision,
            duration_ms=duration_ms,
            executed=run.started,
            authorized=True,
            exit_code=run.exit_code,
            gate=gate,
        )
    return InvocationResult(
        op_id=op_id,
        tier=cap.tier,
        invocable=True,
        executed=run.started,
        exit_code=run.exit_code,
        output=run.output,
        readiness=cap.readiness,
        gate_action=gate.action,
        gate_reason=gate.reason,
        result_status=gate.result_status,
        outcome=gate.outcome,
        operation_id=operation_id,
        missing_paths=cap.missing_paths,
    )
