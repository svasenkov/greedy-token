"""MCP server — token-aware route / RAG / code search for agent hosts (Cursor, Claude Desktop, Continue)."""

from __future__ import annotations

import base64
import json
import time
from importlib import resources
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import Icon

from greedy_token.budget import format_savings_lines, rag_est_tokens, wrap_mcp_response
from greedy_token.code_search import search_code
from greedy_token.estimator import estimate_task
from greedy_token.paths import find_workspace_root
from greedy_token.settings import apply_ollama_env
from greedy_token.pipeline import format_pipeline_response, list_pipelines, run_pipeline
from greedy_token.rag_search import format_hits, search_rag
from greedy_token.result_contract import RESULT_EMPTY, RESULT_PRODUCED
from greedy_token.crystallize_l3 import (
    DraftResult,
    approve_crystal,
    crystal_status,
    draft_crystal,
    promote_crystal,
    reject_crystal,
)
from greedy_token.router import format_decision, route_task
from greedy_token.tokens import count_tokens
from greedy_token.usage import aggregate_events, format_report, load_events, log_path, parse_since

# Keep short: tool-map + exceptions live in alwaysApply rule (examples/cursor/rules/greedy-token.mdc).
MCP_INSTRUCTIONS = (
    "Code search (find/найди/search): greedy_token_search only — one call, no route/usage/rag in the same turn. "
    "greedy_token_usage only when the user explicitly asks for stats/billing. "
    "Multi-step chains: greedy_token_pipeline (e.g. pipeline: meta-audit configurator-boolean). "
    "Deterministic ops: greedy_token_capabilities lists readiness per operation id; "
    "greedy_token_invoke runs a ready read-only op by id (refused ops explain why)."
)


# Cursor's offerings UI times out on a 270KB PNG data URI (~370KB initialize).
# Prefer SVG; skip any payload above this cap.
MAX_ICON_BYTES = 8_192


def mcp_icons() -> list[Icon]:
    """MCP server icon (SEP-973) for Cursor / MCP Inspector."""
    static_dir = Path(__file__).resolve().parent / "static"
    pkg_static = resources.files("greedy_token.static")

    for name, mime in (("icon.svg", "image/svg+xml"), ("icon.png", "image/png")):
        icon_path = static_dir / name
        payload: bytes | None = None
        if icon_path.is_file():
            payload = (
                icon_path.read_bytes()
                if mime == "image/png"
                else icon_path.read_text(encoding="utf-8").encode("utf-8")
            )
        else:
            try:
                resource = pkg_static.joinpath(name)
                payload = (
                    resource.read_bytes()
                    if mime == "image/png"
                    else resource.read_text(encoding="utf-8").encode("utf-8")
                )
            except (FileNotFoundError, OSError):
                payload = None
        if payload is None or len(payload) > MAX_ICON_BYTES:
            continue
        encoded = base64.b64encode(payload).decode("ascii")
        # MCP 1.15 models SEP-973 ``sizes`` as a string; newer 1.x releases
        # corrected it to a list. Keep the server compatible with both schemas.
        sizes_by_schema: dict[bool, str | list[str]] = {
            False: "any",
            True: ["any"],
        }
        sizes = sizes_by_schema[
            "list" in str(Icon.model_fields["sizes"].annotation)
        ]
        return [
            Icon(
                src=f"data:{mime};base64,{encoded}",
                mimeType=mime,
                sizes=sizes,
            )
        ]

    raise FileNotFoundError("greedy_token static icon not found (icon.png or icon.svg)")


# Do not embed icons on the FastMCP server: Cursor caches a failed
# tools/list as "0 tools" and skips a later list. A 270KB PNG data URI
# in initialize previously tripped that path.
mcp = FastMCP("greedy-token", instructions=MCP_INSTRUCTIONS)


@mcp.tool()
def greedy_token_route(task: str) -> str:
    """Recommend executor tier (tool | python | ollama | rag | cursor). Not for code search — use greedy_token_search directly."""
    t0 = time.perf_counter()
    root = find_workspace_root()
    estimate = estimate_task(task, root)
    body = format_decision(estimate.decision, task, root)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    return wrap_mcp_response(
        body,
        task=task,
        tier=estimate.decision.target,
        est_tokens=estimate.est_tokens,
        route_id=estimate.decision.route_id,
        root=root,
        duration_ms=duration_ms,
        executor_sub=estimate.decision.target if estimate.decision.target != "tool" else "rg",
        # Advice only: this tool never runs the tier it recommends.
        executed=False,
        decision=estimate.decision,
    )


@mcp.tool()
def greedy_token_rag(query: str, domain: str = "") -> str:
    """Search docs/rag chunks. Optional domain filter (comma-separated manifest names)."""
    t0 = time.perf_counter()
    root = find_workspace_root()
    task = f"rag: {query}" + (f" [{domain}]" if domain else "")
    domains = [d.strip() for d in domain.split(",") if d.strip()] or None
    hits = search_rag(query, root, domains=domains, limit=5)
    body = format_hits(query, hits)
    est = rag_est_tokens(hits, root) + count_tokens(query).tokens
    duration_ms = int((time.perf_counter() - t0) * 1000)
    return wrap_mcp_response(
        body,
        task=task,
        tier="rag",
        est_tokens=est,
        route_id="mcp-rag",
        root=root,
        duration_ms=duration_ms,
        rag_hits=len(hits),
        executor_sub="rag",
        outcome="success" if hits else "failure",
        outcome_layer="retrieval",
        result_status=RESULT_PRODUCED if hits else RESULT_EMPTY,
    )


@mcp.tool()
def greedy_token_search(query: str, path: str = "", context: str = "") -> str:
    """Ripgrep codebase — sole tool for find/search/найди. query=term; path=optional file or dir; context=snippet|none|file (default from .greedy-token.yaml). Do not pair with route or usage."""
    t0 = time.perf_counter()
    root = find_workspace_root()
    task = f"search: {query}" + (f" in {path}" if path else "")
    ctx = context.strip().lower() or None
    if ctx is not None and ctx not in ("none", "snippet", "file"):
        raise ValueError(
            f"search: invalid context {context!r} (expected none, snippet, or file)"
        )
    result = search_code(query, root, path=path or None, context=ctx)  # type: ignore[arg-type]
    body = result.text
    if result.hit_count or result.enriched_files:
        body = (
            f"{body}\n\n"
            f"hits: {result.hit_count} · enriched: {result.enriched_files} file(s) "
            f"· ~{result.context_tokens} ctx tokens"
        )
    duration_ms = int((time.perf_counter() - t0) * 1000)
    return wrap_mcp_response(
        body,
        task=task,
        tier="tool",
        est_tokens=result.context_tokens,
        route_id="mcp-search",
        root=root,
        duration_ms=duration_ms,
        executor_sub=result.engine,
        outcome="success" if result.hit_count else "failure",
        outcome_layer="executor",
        result_status=RESULT_PRODUCED if result.hit_count else RESULT_EMPTY,
    )


@mcp.tool()
def greedy_token_usage(since: str = "7d") -> str:
    """Usage/billing stats only — when user explicitly asks. Never call for code search or alongside greedy_token_search."""
    path = log_path()
    since_dt = parse_since(since)
    events, skipped = load_events(path, since=since_dt)
    summary = aggregate_events(events, since_label=since)
    body = format_report(summary)
    totals_baseline = sum(s.cursor_baseline for s in summary.by_tier.values())
    totals_saved = sum(s.saved_vs_cursor for s in summary.by_tier.values())
    totals_spent = sum(s.est_tokens for s in summary.by_tier.values())
    footer_lines = [
        "",
        "---",
        "Session totals (this window)",
    ]
    footer_lines.extend(
        format_savings_lines(
            baseline=totals_baseline,
            spent=totals_spent,
            saved=totals_saved,
            title="Saved vs naive agent chat (all events)",
            spent_note="sum across logged greedy-token calls",
        )
    )
    footer_lines.extend(["", f"Log: {path}"])
    if skipped:
        footer_lines.append(f"({skipped} malformed log lines skipped)")
    return body + "\n".join(footer_lines)


@mcp.tool()
def greedy_token_pipeline(task: str, execute: bool = False, profile: str = "") -> str:
    """Run multi-step pipeline with unified token stats.

    Named: pipeline: meta-audit configurator-boolean
    TMS: pipeline: tms-classify path=case.json (profile from recipe or profile arg)
    Custom: pipeline: check-meta-sync then audit-skill configurator-boolean
    List recipes: pipeline: list

    Set execute=true to run allowlisted steps (default: dry-run).
    Optional profile= for LLM model selection (tms-classify, tms-generate, …).
    """
    if task.strip().lower() in ("list", "help", "pipeline: list"):
        return list_pipelines()
    root = find_workspace_root()
    result = run_pipeline(
        task,
        root,
        execute=execute,
        stop_on_error=True,
        profile=profile.strip(),
    )
    return format_pipeline_response(result, root)


@mcp.tool()
def greedy_token_capabilities() -> str:
    """Derived capability view: every deterministic op (routes + wrappers) with readiness — ready / not_approved / stale_* / missing_file / consumer_only / disabled_or_shadow / write_not_invocable / tool_unavailable / advisory_only. Read-only probe, no execution."""
    from greedy_token.capabilities import collect_capabilities

    root = find_workspace_root()
    view = collect_capabilities(root)
    return json.dumps(view.to_dict(), ensure_ascii=False)


@mcp.tool()
def greedy_token_invoke(op_id: str, args: str = "", query: str = "") -> str:
    """Invoke a ready read-only operation by stable id (see greedy_token_capabilities).

    Fixed argv for route ops; query= is the rg tool parameter; args= only for
    wrapper ops. Refusals report the readiness class and execute nothing.
    """
    from greedy_token.capabilities import invoke_capability

    t0 = time.perf_counter()
    root = find_workspace_root()
    result = invoke_capability(root, op_id, args=args, query=query)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    if not result.invocable:
        body = (
            f"Refused: {result.op_id} [{result.refusal_code}] — {result.refusal_reason}\n"
            "Nothing executed (gate: bypassed/not_started, no savings)."
        )
    else:
        lines = [result.output.rstrip()] if result.output else []
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
        body = "\n".join(lines)
    return wrap_mcp_response(
        body,
        task=f"invoke {op_id}",
        tier=result.tier,
        est_tokens=0,
        route_id=op_id,
        root=root,
        # invoke_capability already emitted the cmd="invoke" request/outcome
        # pair; wrapping must not log a second, mcp-attributed operation.
        log=False,
        duration_ms=duration_ms,
        executor_sub=result.tier,
        outcome=result.outcome or None,
        executed=result.executed,
    )


def _format_draft_result(result: DraftResult) -> str:
    lines = [
        f"Draft crystal: {result.crystal_id}  (source: {result.source})",
        f"  Pattern: {result.pattern}  (hits: {result.hits})",
        f"  Script:  {result.draft_path}",
        f"  Route:   shadow until {result.shadow_until} (log-only, does not affect route_task)",
        f"  Config:  {result.config_path}",
    ]
    if result.lint_ok:
        lines.append("  Lint:    scripts lint OK")
    else:
        lines.append("  Lint:    FAILED")
        lines.extend(f"    {v['id']}: {v['detail']}" for v in result.lint_violations)
    lines.append(
        f"Review the script, then: greedy-token crystallize approve {result.crystal_id}"
    )
    return "\n".join(lines)


def _format_promote_result(result: dict, crystal_id: str) -> str:
    pattern = (result["route"].get("patterns") or [""])[0]
    return (
        f"Promoted {crystal_id}: approved → applied in {result['config']}\n"
        f"  Trust: {result['trusted']} (sha256 {str(result['sha256'])[:12]}…)\n"
        f'Verify: greedy-token route "{pattern}"'
    )


def _format_reject_result(result: dict, crystal_id: str) -> str:
    return (
        f"Rejected {crystal_id}: "
        f"route removed={result['removed_route']}, draft removed={result['removed_draft']}, "
        f"trust revoked={result['revoked_trust']}"
    )


@mcp.tool()
def greedy_token_crystallize(
    action: str,
    crystal_id: str = "",
    since: str = "30d",
    reason: str = "",
    by: str = "",
) -> str:
    """Auditable crystallization lifecycle: candidates | status | draft/propose | approve | promote | reject.

    No auto-apply — promote requires a prior ``approve`` and passes the draft
    through the local trust manifest. ``by``/``reason`` land in the lifecycle
    audit log (default actor: mcp).
    """
    root = find_workspace_root()
    act = action.strip().lower()
    actor = by.strip() or "mcp"
    if act == "candidates":
        from greedy_token.hub.crystallize import list_crystals

        return json.dumps(list_crystals(since=since), indent=2, ensure_ascii=False)
    if act == "status":
        if not crystal_id.strip():
            raise ValueError("crystallize status requires crystal_id")
        return json.dumps(
            crystal_status(crystal_id, root=root), indent=2, ensure_ascii=False
        )
    if not crystal_id.strip():
        raise ValueError(f"crystallize {act or '?'} requires crystal_id")
    if act in ("draft", "propose"):
        try:
            result = draft_crystal(
                crystal_id, root=root, since=since, actor=actor, reason=reason
            )
        except ValueError as exc:
            raise ValueError(f"crystallize draft: {exc}") from exc
        return _format_draft_result(result)
    if act == "approve":
        try:
            result = approve_crystal(
                crystal_id, root=root, actor=actor, reason=reason
            )
        except ValueError as exc:
            raise ValueError(f"crystallize approve: {exc}") from exc
        return (
            f"Approved {result['crystal_id']} by {result['actor']}"
            + (f" — {result['reason']}" if result.get("reason") else "")
            + f"\n  pinned draft sha256: {result['approved_sha256'][:12]}…"
            f"\n  Next: greedy-token crystallize promote {result['crystal_id']}"
        )
    if act == "promote":
        try:
            result = promote_crystal(
                crystal_id, root=root, actor=actor, reason=reason
            )
        except ValueError as exc:
            raise ValueError(f"crystallize promote: {exc}") from exc
        return _format_promote_result(result, crystal_id)
    if act == "reject":
        result = reject_crystal(crystal_id, root=root, actor=actor, reason=reason)
        return _format_reject_result(result, crystal_id)
    raise ValueError(
        f"crystallize: unknown action {action!r} "
        "(expected candidates, status, draft/propose, approve, promote, or reject)"
    )


def main() -> None:
    import logging
    import warnings

    # Cursor treats MCP stderr as errors and may skip tools/list after noise.
    warnings.filterwarnings("ignore")
    logging.getLogger("mcp").setLevel(logging.CRITICAL)
    logging.getLogger("mcp.server").setLevel(logging.CRITICAL)
    logging.getLogger("mcp.server.lowlevel").setLevel(logging.CRITICAL)

    root = find_workspace_root()
    apply_ollama_env(root)
    mcp.run()


if __name__ == "__main__":
    main()
