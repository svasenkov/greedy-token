"""Derived capability view — which deterministic ops exist and their readiness.

"Registered" is not "runnable": a route may be shadow/disabled, not read-only,
backed by a script that is missing, consumer-bound, untrusted, or stale in the
local manifest.  This module computes that picture *at call time* from the
existing sources — merged routes (bundled + workspace overlay), the wrapper
registry, the local trust manifest, the filesystem, and plan_run's refusal
classification — so a host (or a human) can ask "what can I invoke and why is
the rest blocked" without guessing routing phrases.

Stable operation id: the route id wins; a wrapper that no route covers gets
its own wrapper id.  Invocation goes through the exact ``run --execute`` path —
plan_run → trust/refusal → execute_plan → result contract → evaluator gate →
telemetry — and is limited to read-only ready ops, the same policy the CLI
enforces.
"""

from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from greedy_token.calibration import SOURCE_FIXED
from greedy_token.executors import (
    RunPlan,
    TaskRunResult,
    execute_plan,
    plan_run,
    task_result_gate,
)
from greedy_token.paths import (
    find_workspace_root,
    load_routes_config,
    workspace_routes_overlay,
)
from greedy_token.result_contract import RESULT_NOT_EVALUATED
from greedy_token.result_gate import evaluate_result_gate
from greedy_token.router import (
    RouteDecision,
    _confined_route_path,
    _decision_from_route,
    _route_status,
    _split_search_paths,
)
from greedy_token.scripts_lint import _is_consumer_script, extract_script_path
from greedy_token.subprocess_safe import (
    UnsafeCommandError,
    format_invocation,
)
from greedy_token.tool_paths import resolve_tool
from greedy_token.trust import (
    TrustError,
    verify_trust_manifest,
)
from greedy_token.usage import (
    append_event,
    build_outcome_event,
    build_route_event,
    new_operation_id,
)
from greedy_token.wrappers import (
    WRAPPERS,
    resolve_wrapper_invocation,
    wrapper_for_command,
)

# Readiness vocabulary — the externally meaningful classes surfaced to hosts.
# Step-2 refusal classes (not_approved/stale_*/missing_file/symlink/
# untrusted_type) pass through unchanged; the rest are derived facts.
READY = "ready"
NOT_APPROVED = "not_approved"
STALE_BYTES = "stale_bytes"
STALE_IDENTITY = "stale_identity"
MISSING_FILE = "missing_file"
SYMLINK = "symlink"
UNTRUSTED_TYPE = "untrusted_type"
# Script absent here because the route is meant for the consumer repo checkout.
CONSUMER_ONLY = "consumer_only"
# enabled: false, or a live shadow_until window (log-only match).
DISABLED_OR_SHADOW = "disabled_or_shadow"
# Declared non-read-only: visible but never invocable through this surface.
WRITE_NOT_INVOCABLE = "write_not_invocable"
# rg/jq binary not resolvable on this host.
TOOL_UNAVAILABLE = "tool_unavailable"
# Advisory route (cursor tier / no command) — no deterministic operation.
ADVISORY_ONLY = "advisory_only"
# Facts unavailable: unreadable manifest or an unclassified refusal.
UNKNOWN = "unknown"

# Trust-entry detail: the manifest row exists but grants nothing because the
# path is authorized through the wrapper registry instead.
TRUST_WRAPPER_COVERED_INERT = "wrapper_covered_inert"
TRUST_OK = "ok"

# Refusal classes that are not readiness states — caller errors.
REFUSAL_UNKNOWN_OPERATION = "unknown_operation"
REFUSAL_INVALID_PARAMS = "invalid_params"

_INVOCABLE_TIERS = frozenset({"tool", "python", "ollama"})
_TRUST_REFUSAL_CODES = frozenset(
    {
        NOT_APPROVED,
        STALE_BYTES,
        STALE_IDENTITY,
        MISSING_FILE,
        SYMLINK,
        UNTRUSTED_TYPE,
    }
)


@dataclass(frozen=True)
class Capability:
    """One deterministic operation, derived (never registered twice)."""

    id: str
    source: str  # "route" | "wrapper"
    origin: str  # "bundled" | "workspace" | "registry"
    tier: str  # tool | python | ollama | rag | cursor
    read_only: bool
    status: str  # route status: active | shadow | inactive
    readiness: str
    reason: str
    invocable: bool
    command: str = ""
    argv: tuple[str, ...] = ()
    params: tuple[str, ...] = ()
    script_path: str = ""
    script_type: str = ""
    authorization: str = ""
    trust_entry: str = ""  # manifest state for script_path when one exists
    contract: str = ""  # "script-canon" when the canon {"ok": …} contract applies
    patterns: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    note: str = ""
    requires_ollama: bool = False
    # Declared rg search_paths that do not exist under root — skipped at run
    # time instead of dying on rg exit 2; listed here so the stale config is
    # visible rather than silently absorbed.
    missing_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        value: dict = {
            "id": self.id,
            "source": self.source,
            "origin": self.origin,
            "tier": self.tier,
            "read_only": self.read_only,
            "status": self.status,
            "readiness": self.readiness,
            "reason": self.reason,
            "invocable": self.invocable,
            "params": list(self.params),
        }
        if self.command:
            value["command"] = self.command
        if self.argv:
            value["argv"] = list(self.argv)
        if self.script_path:
            value["script_path"] = self.script_path
            value["script_type"] = self.script_type
        if self.authorization:
            value["authorization"] = self.authorization
        if self.trust_entry:
            value["trust_entry"] = self.trust_entry
        if self.contract:
            value["contract"] = self.contract
        if self.patterns:
            value["patterns"] = list(self.patterns)
        if self.domains:
            value["domains"] = list(self.domains)
        if self.note:
            value["note"] = self.note
        if self.requires_ollama:
            value["requires_ollama"] = True
        if self.missing_paths:
            value["missing_paths"] = list(self.missing_paths)
        return value


@dataclass(frozen=True)
class CapabilityView:
    root: str
    ops: tuple[Capability, ...]
    manifest_error: str = ""

    def get(self, op_id: str) -> Capability | None:
        return next((op for op in self.ops if op.id == op_id), None)

    def to_dict(self) -> dict:
        by_readiness: dict[str, int] = {}
        for op in self.ops:
            by_readiness[op.readiness] = by_readiness.get(op.readiness, 0) + 1
        value: dict = {
            "root": self.root,
            "derived_at": "request",  # recomputed every call — no cache/registry
            "summary": {
                "ops": len(self.ops),
                "invocable": sum(1 for op in self.ops if op.invocable),
                "by_readiness": dict(sorted(by_readiness.items())),
            },
            "ops": [op.to_dict() for op in self.ops],
        }
        if self.manifest_error:
            value["manifest_error"] = self.manifest_error
        return value


def _script_type(path: str) -> str:
    if path.endswith(".py"):
        return "python"
    if path.endswith(".sh"):
        return "shell"
    return ""


def _trust_entry_state(checks_by_path: dict[str, object], script_path: str) -> str:
    """Manifest state for the op's script path, when the manifest covers it."""
    if not script_path:
        return ""
    check = checks_by_path.get(script_path)
    if check is None:
        return ""
    if check.ok:
        return TRUST_OK
    if check.inert:
        return TRUST_WRAPPER_COVERED_INERT
    return check.code or UNKNOWN


def _probe_script_route(
    route: dict,
    root: Path,
    checks_by_path: dict[str, object],
) -> tuple[str, str, str, str, tuple[str, ...], str]:
    """plan_run as a read-only readiness probe for python/ollama routes.

    Returns (readiness, reason, authorization, script_path, argv, script_type).
    plan_run classifies trust refusals; for manifest-authorized commands we
    additionally verify freshness (read-only — hash + identity, no execution)
    so stale_bytes/stale_identity show up at listing time, not only at run time.
    """
    decision = RouteDecision(
        target=str(route["target"]),
        route_id=str(route["id"]),
        confidence=0.0,
        matched=[],
        command=route.get("command"),
        note="",
        domains=route.get("domains") or [],
        read_only=bool(route.get("read_only", False)),
        tool=route.get("tool"),
    )
    script_hint = extract_script_path(route.get("command") or "") or ""
    try:
        plan = plan_run(decision, "", root)
    except (UnsafeCommandError, TrustError, OSError) as exc:
        return UNKNOWN, str(exc), "", script_hint, (), _script_type(script_hint)

    if plan.executable:
        script_path = plan.script_path or script_hint
        if plan.authorization.startswith("manifest:"):
            check = checks_by_path.get(plan.script_path)
            if check is None:
                return (
                    UNKNOWN,
                    "authorization references a manifest entry that failed to "
                    f"load: {plan.script_path}",
                    plan.authorization,
                    script_path,
                    plan.argv or (),
                    plan.script_type,
                )
            if not check.ok:
                return (
                    check.code or UNKNOWN,
                    check.error,
                    plan.authorization,
                    script_path,
                    plan.argv or (),
                    plan.script_type,
                )
        return (
            READY,
            "authorized",
            plan.authorization,
            script_path,
            plan.argv or (),
            plan.script_type,
        )

    code = plan.refusal_code or ""
    reason = plan.refusal_reason or "not authorized for execution"
    if code == MISSING_FILE and _is_consumer_script(route):
        return (
            CONSUMER_ONLY,
            f"{reason} — script lives in the consumer repo checkout, not this workspace",
            "",
            script_hint,
            (),
            _script_type(script_hint),
        )
    if code in _TRUST_REFUSAL_CODES:
        return code, reason, "", script_hint, (), _script_type(script_hint)
    return UNKNOWN, reason, "", script_hint, (), _script_type(script_hint)


def _capability_for_route(
    route: dict,
    root: Path,
    *,
    overlay_ids: set[str],
    checks_by_path: dict[str, object],
) -> Capability:
    rid = str(route["id"])
    target = str(route.get("target") or "")
    status = _route_status(route)
    read_only = bool(route.get("read_only", False)) or target == "tool"
    command = str(route.get("command") or "")
    note = str(route.get("note") or "").strip()

    base: dict = {
        "id": rid,
        "source": "route",
        "origin": "workspace" if rid in overlay_ids else "bundled",
        "tier": target,
        "read_only": read_only,
        "status": status,
        "command": command,
        "patterns": tuple(str(p) for p in route.get("patterns") or []),
        "domains": tuple(str(d) for d in route.get("domains") or []),
        "note": note,
        "requires_ollama": bool(
            (w := wrapper_for_command(command)) and w.requires_ollama
        ),
    }

    def cap(readiness: str, reason: str, **kw: object) -> Capability:
        invocable = readiness == READY and read_only and target in _INVOCABLE_TIERS
        script_path = str(kw.get("script_path") or "")
        return Capability(
            readiness=readiness,
            reason=reason,
            invocable=invocable,
            contract=(
                "script-canon"
                if _script_type(script_path) == "python"
                else ""
            ),
            trust_entry=_trust_entry_state(checks_by_path, script_path),
            **base,
            **kw,
        )

    if status == "shadow":
        until = str(route.get("shadow_until") or "")
        return cap(
            DISABLED_OR_SHADOW,
            f"shadow route — log-only match until {until}; no execution authority",
        )
    if status == "inactive":
        return cap(DISABLED_OR_SHADOW, "route disabled (enabled: false)")

    if target == "tool":
        tool = str(route.get("tool") or "rg")
        binary = resolve_tool(tool)
        if binary is None:
            return cap(
                TOOL_UNAVAILABLE,
                f"{tool} binary not found on this host (override: GREEDY_TOKEN_{tool.upper()})",
            )
        missing_paths: tuple[str, ...] = ()
        if tool == "rg":
            try:
                existing, missing = _split_search_paths(route, root)
            except (OSError, ValueError) as exc:
                return cap(UNKNOWN, f"invalid search_paths config: {exc}")
            missing_paths = tuple(missing)
            path_state = ""
            if missing_paths:
                dropped = ", ".join(missing_paths)
                path_state = (
                    "; all configured search_paths missing — rg falls back to '.'"
                    if not existing
                    else f"; search_paths missing on disk (skipped): {dropped}"
                )
        elif tool == "jq":
            try:
                json_path = _confined_route_path(
                    route.get("json_path") or "docs/phase-manifest.json",
                    root,
                    field="json_path",
                )
            except (OSError, ValueError) as exc:
                return cap(UNKNOWN, f"invalid json_path config: {exc}")
            if not (root / json_path).is_file():
                return cap(MISSING_FILE, f"json_path not on disk: {json_path}")
            path_state = ""
        else:
            path_state = ""
        return cap(
            READY,
            (
                f"internal {tool} argv builder ({binary}); "
                + (
                    "query is the parameterizable part"
                    if tool == "rg"
                    else "fixed argv — no parameters"
                )
                + path_state
            ),
            authorization=f"internal-tool:{tool}",
            params=("query",) if tool == "rg" else (),
            missing_paths=missing_paths,
        )

    if target == "rag":
        return cap(
            READY,
            "pinned-domain retrieval — query via greedy_token_rag / "
            "'greedy-token rag --domain' (not an executable op)",
        )

    if target not in ("python", "ollama") or not command:
        return cap(
            ADVISORY_ONLY,
            "advisory route — recommends the agent path; no deterministic command",
        )

    if not read_only:
        script_hint = extract_script_path(command) or ""
        detail = "not read_only — write ops are listed but never invoked through this surface"
        if script_hint and not (root / script_hint).is_file():
            detail += f"; script file also missing: {script_hint}"
        return cap(
            WRITE_NOT_INVOCABLE,
            detail,
            script_path=script_hint,
            script_type=_script_type(script_hint),
        )

    readiness, reason, authorization, script_path, argv, script_type = (
        _probe_script_route(route, root, checks_by_path)
    )
    return cap(
        readiness,
        reason,
        authorization=authorization,
        script_path=script_path,
        script_type=script_type,
        argv=argv,
    )


def _capability_for_wrapper(
    wrapper,
    root: Path,
    checks_by_path: dict[str, object],
) -> Capability:
    tier = "ollama" if wrapper.requires_ollama else "python"
    base: dict = {
        "id": wrapper.id,
        "source": "wrapper",
        "origin": "registry",
        "tier": tier,
        "read_only": wrapper.read_only,
        "status": "active",
        "script_path": wrapper.path,
        "script_type": _script_type(wrapper.path),
        "note": wrapper.note,
        "requires_ollama": wrapper.requires_ollama,
        "params": ("args",),  # same extra-args contract as `scripts --run ID`
    }

    def cap(readiness: str, reason: str, **kw: object) -> Capability:
        invocable = readiness == READY and wrapper.read_only
        return Capability(
            readiness=readiness,
            reason=reason,
            invocable=invocable,
            contract="script-canon" if wrapper.path.endswith(".py") else "",
            trust_entry=_trust_entry_state(checks_by_path, wrapper.path),
            **base,
            **kw,
        )

    if not wrapper.read_only:
        return cap(
            WRITE_NOT_INVOCABLE,
            "wrapper is not read_only — listed but never invoked through this surface",
        )
    try:
        invocation = resolve_wrapper_invocation(wrapper.id, root)
    except FileNotFoundError as exc:
        return cap(MISSING_FILE, str(exc))
    except UnsafeCommandError as exc:
        return cap(exc.code or UNKNOWN, str(exc))
    return cap(
        READY,
        "registered wrapper (read-only)",
        authorization=invocation.authorization,
        argv=invocation.argv,
    )


def collect_capabilities(root: Path | None = None) -> CapabilityView:
    """Derive the capability view: routes + wrapper registry + trust + fs."""
    root = root or find_workspace_root()
    cfg = load_routes_config(root)
    routes = [r for r in cfg.get("routes", []) if isinstance(r, dict) and r.get("id")]
    overlay_ids = {
        str(r["id"]) for r in workspace_routes_overlay(root).get("routes", [])
    }
    manifest_error = ""
    checks_by_path: dict[str, object] = {}
    try:
        checks = verify_trust_manifest(
            root, wrapper_paths={w.path for w in WRAPPERS.values()}
        )
        checks_by_path = {check.entry.path: check for check in checks}
    except TrustError as exc:
        manifest_error = str(exc)

    ops: list[Capability] = [
        _capability_for_route(route, root, overlay_ids=overlay_ids, checks_by_path=checks_by_path)
        for route in routes
    ]

    covered: set[str] = set()
    for route in routes:
        wrapper = wrapper_for_command(route.get("command"))
        if wrapper is not None:
            covered.add(wrapper.id)
    ops.extend(
        _capability_for_wrapper(wrapper, root, checks_by_path)
        for wrapper_id, wrapper in sorted(WRAPPERS.items())
        if wrapper_id not in covered
    )
    return CapabilityView(root=str(root), ops=tuple(ops), manifest_error=manifest_error)


def capability_by_id(root: Path, op_id: str) -> Capability | None:
    return collect_capabilities(root).get(op_id)


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

    # Parameter contract: fixed argv for route ops; `query` for rg tool ops;
    # `args` only for wrapper ops (validated workspace-relative inside
    # trusted_script_argv, same as `scripts --run`).
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
    if args.strip() and "args" not in cap.params:
        return refused(
            REFUSAL_INVALID_PARAMS,
            f"{op_id} has a fixed argv contract — no extra args accepted",
            tier=cap.tier,
            read_only=cap.read_only,
            exit_code=2,
        )

    task = _query_task(query.strip()) if cap.params == ("query",) else f"invoke {op_id}"
    decision = _decision_for_op(cap, task=task, root=root)

    if cap.source == "wrapper":
        try:
            invocation = resolve_wrapper_invocation(
                cap.id, root, extra_args=tuple(shlex.split(args)) if args.strip() else ()
            )
        except (FileNotFoundError, UnsafeCommandError, OSError) as exc:
            return refused(
                getattr(exc, "code", "") or cap.readiness or UNKNOWN,
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
        plan = plan_run(decision, task, root)

    if not plan.executable:
        return refused(
            plan.refusal_code or cap.readiness or UNKNOWN,
            plan.refusal_reason or "not authorized for execution",
            tier=cap.tier,
            read_only=cap.read_only,
            exit_code=1,
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


def format_capabilities(view: CapabilityView) -> str:
    lines = [
        "Deterministic operations — derived from routes + wrappers + trust + fs",
        f"root: {view.root}",
        (
            f"ops: {len(view.ops)} · invocable: "
            f"{sum(1 for op in view.ops if op.invocable)}"
        ),
    ]
    if view.manifest_error:
        lines.append(f"trust manifest: UNREADABLE — {view.manifest_error}")
    lines.append("")
    width = max((len(op.id) for op in view.ops), default=10)
    for op in view.ops:
        flag = "invocable" if op.invocable else "—"
        params = f" params={','.join(op.params)}" if op.params else ""
        tail = op.reason if not op.invocable else (op.note or op.reason)
        lines.append(
            f"  {op.id:<{width}}  [{op.source}:{op.tier}]  "
            f"{op.readiness:<20} {flag:<9}  {tail}{params}"
        )
    lines.append("")
    lines.append("Inspect: greedy-token capabilities show <id> [--json]")
    lines.append("Invoke:  greedy-token capabilities invoke <id> [--query Q|--args A]")
    return "\n".join(lines)


def format_capability_detail(cap: Capability) -> str:
    lines = [
        f"{cap.id}  [{cap.source}/{cap.origin}:{cap.tier}]  {cap.readiness}",
        (
            f"  invocable: {'yes' if cap.invocable else 'no'}"
            f"  ·  read_only: {cap.read_only}  ·  status: {cap.status}"
        ),
        f"  reason:    {cap.reason}",
    ]
    if cap.command:
        lines.append(f"  command:   {cap.command}")
    if cap.argv:
        lines.append(f"  argv:      {shlex.join(list(cap.argv))}")
    if cap.params:
        lines.append(f"  params:    {', '.join(cap.params)}")
    else:
        lines.append("  params:    none — fixed argv")
    if cap.missing_paths:
        lines.append(
            f"  missing_paths: {', '.join(cap.missing_paths)} — declared but not on disk"
        )
    if cap.script_path:
        lines.append(f"  script:    {cap.script_path} ({cap.script_type or 'unknown'})")
    if cap.authorization:
        lines.append(f"  auth:      {cap.authorization}")
    if cap.trust_entry:
        lines.append(f"  trust:     {cap.trust_entry}")
    if cap.contract:
        lines.append(f"  contract:  {cap.contract}")
    if cap.domains:
        lines.append(f"  domains:   {', '.join(cap.domains)}")
    if cap.patterns:
        lines.append(f"  patterns:  {', '.join(cap.patterns[:8])}")
    if cap.note:
        lines.append(f"  note:      {cap.note}")
    return "\n".join(lines)


def format_invocation_result(result: InvocationResult) -> str:
    if not result.invocable:
        head = (
            f"Refused: {result.op_id} [{result.refusal_code}] — {result.refusal_reason}"
        )
        return head
    lines = [result.output.rstrip()] if result.output else []
    if result.missing_paths:
        lines.append(
            "note: search_paths skipped (not on disk): "
            + ", ".join(result.missing_paths)
        )
    lines.extend(
        [
            "---",
            (
                f"invoke {result.op_id}: exit={result.exit_code} "
                f"executed={result.executed} result={result.result_status or 'n/a'} "
                f"gate={result.gate_action}/{result.gate_reason} "
                f"outcome={result.outcome} op={result.operation_id or '-'}"
            ),
        ]
    )
    return "\n".join(lines)
