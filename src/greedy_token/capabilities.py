"""Derived capability view — which deterministic ops exist and their readiness.

"Registered" is not "runnable": a route may be shadow/disabled, not read-only,
backed by a script that is missing, consumer-bound, untrusted, or stale in the
local manifest.  This module computes that picture *at call time* from the
existing sources — merged routes (bundled + workspace overlay), the wrapper
registry, the local trust manifest, the filesystem, and plan_run's refusal
classification — so a host (or a human) can ask "what can I invoke and why is
the rest blocked" without guessing routing phrases.

Stable operation id: the route id wins; a wrapper that no route covers gets
its own wrapper id.  Invocation lives in ``capabilities_invoke`` and goes
through the exact ``run --execute`` path — plan_run → trust/refusal →
execute_plan → result contract → evaluator gate → telemetry — limited to
read-only ready ops, the same policy the CLI enforces; presentation lives in
``capabilities_format``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from greedy_token.executors import plan_run
from greedy_token.hub.crystallize import STATE_UNKNOWN, crystal_states
from greedy_token.paths import (
    find_workspace_root,
    load_routes_config,
    workspace_routes_overlay,
)
from greedy_token.router import (
    RouteDecision,
    _confined_route_path,
    _route_status,
    _split_search_paths,
)
from greedy_token.scripts_lint import _is_consumer_script, extract_script_path
from greedy_token.subprocess_safe import UnsafeCommandError
from greedy_token.tool_paths import resolve_tool
from greedy_token.trust import (
    TrustError,
    verify_trust_manifest,
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

# Crystallize lifecycle verbs are CLI commands, not argv ops — listed here so
# the derived inventory is complete; they are never invocable through the
# read-only invoke surface (mutations stay explicit CLI commands).
LIFECYCLE_OPS: tuple[tuple[str, bool, str], ...] = (
    (
        "crystallize-candidates",
        True,
        "list candidates + derived lifecycle state — CLI: greedy-token crystallize candidates",
    ),
    (
        "crystallize-status",
        True,
        "per-crystal state + audit timeline — CLI: greedy-token crystallize status <id>",
    ),
    (
        "crystallize-draft",
        False,
        "propose: draft script + shadow route — CLI: greedy-token crystallize draft <id>",
    ),
    (
        "crystallize-approve",
        False,
        "human approval — who/why + sha256 pin — CLI: greedy-token crystallize approve <id>",
    ),
    (
        "crystallize-promote",
        False,
        "apply: trust pinned draft + activate route — CLI: greedy-token crystallize promote",
    ),
    (
        "crystallize-reject",
        False,
        "remove draft + route + trust entry — CLI: greedy-token crystallize reject <id>",
    ),
)

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
    # Derived crystallize lifecycle state when the op id is a crystal
    # (candidate/proposed/approved/applied/rejected) — audit-trail fact.
    lifecycle_state: str = ""
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
        if self.lifecycle_state:
            value["lifecycle_state"] = self.lifecycle_state
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
    lifecycle_states: dict[str, dict] | None = None,
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
        "lifecycle_state": (
            str((lifecycle_states or {}).get(rid, {}).get("state") or "")
        ),
    }
    if base["lifecycle_state"] == STATE_UNKNOWN:
        base["lifecycle_state"] = ""

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

    lifecycle_states = crystal_states()
    ops: list[Capability] = [
        _capability_for_route(
            route,
            root,
            overlay_ids=overlay_ids,
            checks_by_path=checks_by_path,
            lifecycle_states=lifecycle_states,
        )
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
    ops.extend(
        Capability(
            id=op_id,
            source="lifecycle",
            origin="builtin",
            tier="lifecycle",
            read_only=read_only,
            status="active",
            readiness=(ADVISORY_ONLY if read_only else WRITE_NOT_INVOCABLE),
            reason=note,
            invocable=False,
        )
        for op_id, read_only, note in LIFECYCLE_OPS
    )
    return CapabilityView(root=str(root), ops=tuple(ops), manifest_error=manifest_error)


def capability_by_id(root: Path, op_id: str) -> Capability | None:
    return collect_capabilities(root).get(op_id)
