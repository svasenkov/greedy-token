"""MCP server — token-aware route / RAG / code search for Cursor agents."""

from __future__ import annotations

import base64
import time
from importlib import resources
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import Icon

from greedy_token.budget import format_savings_lines, rag_est_tokens, wrap_mcp_response
from greedy_token.code_search import search_code
from greedy_token.estimator import estimate_task
from greedy_token.paths import find_monorepo_root
from greedy_token.settings import apply_ollama_env
from greedy_token.pipeline import format_pipeline_response, list_pipelines, run_pipeline
from greedy_token.rag_search import format_hits, search_rag
from greedy_token.router import format_decision, route_task
from greedy_token.tokens import count_tokens
from greedy_token.usage import aggregate_events, format_report, load_events, log_path, parse_since

# Keep short: tool-map + exceptions live in alwaysApply rule (examples/cursor/rules/token-economy.mdc).
MCP_INSTRUCTIONS = (
    "Relay the full «Token economy» footer from every tool result. "
    "Multi-step chains: greedy_token_pipeline (e.g. pipeline: meta-audit configurator-boolean)."
)


def mcp_icons() -> list[Icon]:
    """MCP server icon (SEP-973) for Cursor / MCP Inspector."""
    static_dir = Path(__file__).resolve().parent / "static"
    pkg_static = resources.files("greedy_token.static")

    for name, mime in (("icon.png", "image/png"), ("icon.svg", "image/svg+xml")):
        icon_path = static_dir / name
        if icon_path.is_file():
            payload = icon_path.read_bytes() if mime == "image/png" else icon_path.read_text(encoding="utf-8").encode("utf-8")
        else:
            try:
                resource = pkg_static.joinpath(name)
                payload = (
                    resource.read_bytes()
                    if mime == "image/png"
                    else resource.read_text(encoding="utf-8").encode("utf-8")
                )
            except (FileNotFoundError, OSError):
                continue
        encoded = base64.b64encode(payload).decode("ascii")
        return [
            Icon(
                src=f"data:{mime};base64,{encoded}",
                mimeType=mime,
                sizes=["any"],
            )
        ]

    raise FileNotFoundError("greedy_token static icon not found (icon.png or icon.svg)")


mcp = FastMCP("greedy-token", instructions=MCP_INSTRUCTIONS, icons=mcp_icons())


@mcp.tool()
def greedy_token_route(task: str) -> str:
    """Recommend executor tier: tool | python | ollama | rag | cursor. Includes token budget."""
    t0 = time.perf_counter()
    root = find_monorepo_root()
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
    )


@mcp.tool()
def greedy_token_rag(query: str, domain: str = "") -> str:
    """Search docs/rag chunks. Optional domain filter (comma-separated manifest names)."""
    t0 = time.perf_counter()
    root = find_monorepo_root()
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
    )


@mcp.tool()
def greedy_token_search(query: str, path: str = "") -> str:
    """Ripgrep codebase. query=term (e.g. baseUrl); path=optional file (e.g. configurator-option-presets.html)."""
    t0 = time.perf_counter()
    root = find_monorepo_root()
    task = f"search: {query}" + (f" in {path}" if path else "")
    result = search_code(query, root, path=path or None)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    return wrap_mcp_response(
        result.text,
        task=task,
        tier="tool",
        est_tokens=result.spent_tokens,
        route_id="mcp-search",
        root=root,
        duration_ms=duration_ms,
        executor_sub=result.engine,
    )


@mcp.tool()
def greedy_token_usage(since: str = "7d") -> str:
    """Aggregate token savings from ~/.greedy-token/usage.jsonl (default last 7 days)."""
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
            title="Saved vs naive Cursor (all events)",
            spent_note="sum across logged greedy-token calls",
        )
    )
    footer_lines.extend(["", f"Log: {path}"])
    if skipped:
        footer_lines.append(f"({skipped} malformed log lines skipped)")
    return body + "\n".join(footer_lines)


@mcp.tool()
def greedy_token_pipeline(task: str, execute: bool = False) -> str:
    """Run multi-step pipeline with unified token stats.

    Named: pipeline: meta-audit configurator-boolean
    Custom: pipeline: check-meta-sync then audit-skill configurator-boolean
    List recipes: pipeline: list

    Set execute=true to run allowlisted steps (default: dry-run).
    """
    if task.strip().lower() in ("list", "help", "pipeline: list"):
        return list_pipelines()
    root = find_monorepo_root()
    result = run_pipeline(task, root, execute=execute, stop_on_error=True)
    return format_pipeline_response(result, root)


def main() -> None:
    root = find_monorepo_root()
    apply_ollama_env(root)
    mcp.run()


if __name__ == "__main__":
    main()
