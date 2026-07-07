from __future__ import annotations

import argparse
import json
import sys
import time

from greedy_token.context_audit import audit_context, render_audit
from greedy_token.estimator import estimate_task, format_estimate
from greedy_token.executors import execute_task, plan_run
from greedy_token.paths import find_monorepo_root
from greedy_token.pipeline import format_pipeline_response, list_pipelines, run_pipeline
from greedy_token.prompt_compress import compress_prompt_detail, format_dual
from greedy_token.rag_search import format_hits, search_rag
from greedy_token.router import format_decision, route_task
from greedy_token.settings import (
    apply_ollama_env,
    format_config,
    format_shell_export,
    init_user_config,
)
from greedy_token.tokens import TokenEstimate, collect_paths, count_files, count_tokens, format_size_table
from greedy_token.usage import (
    aggregate_events,
    build_compress_event,
    build_route_event,
    build_script_event,
    build_tier_scan,
    format_report,
    load_events,
    log_path,
    maybe_append_event,
    parse_since,
)
from greedy_token.wrappers import WRAPPERS, ollama_status_line, resolve_wrapper_command


from greedy_token.budget import rag_est_tokens

COMPRESS_MAX_BYTES = 256 * 1024


def cmd_route(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    root = find_monorepo_root()
    decision = route_task(args.task, root)
    tier_scan = build_tier_scan(args.task, root)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    print(format_decision(decision, args.task, root))
    maybe_append_event(
        args,
        build_route_event(
            cmd="route",
            task=args.task,
            root=root,
            decision=decision,
            tier_scan=tier_scan,
            duration_ms=duration_ms,
        ),
    )
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    root = find_monorepo_root()
    estimate = estimate_task(args.task, root)
    tier_scan = build_tier_scan(args.task, root)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    print(format_estimate(estimate, args.task, root))
    maybe_append_event(
        args,
        build_route_event(
            cmd="estimate",
            task=args.task,
            root=root,
            decision=estimate.decision,
            tier_scan=tier_scan,
            duration_ms=duration_ms,
        ),
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    root = find_monorepo_root()
    decision = route_task(args.task, root)
    plan = plan_run(decision, args.task, root)
    print(f"Route: {decision.target} ({decision.route_id})")
    print(f"Complexity: {decision.complexity}  Est. tokens: {decision.est_tokens:,}")
    print()
    code = 0
    executed = False
    if args.execute:
        result = execute_task(args.task, root)
        if result.output:
            print(result.output)
        if result.used_rag_fallback:
            print("\n(fallback: rg → RAG)")
        code = result.exit_code
        executed = True
    else:
        print(plan.dry_run_output)
        if plan.command:
            if plan.executable:
                print("\n(read-only — add --execute to run)")
            else:
                print("\n(not read-only — dry-run only)")
    duration_ms = int((time.perf_counter() - t0) * 1000)
    maybe_append_event(
        args,
        build_route_event(
            cmd="run",
            task=args.task,
            root=root,
            decision=decision,
            duration_ms=duration_ms,
            executed=executed,
        ),
    )
    return code


def cmd_audit_context(_: argparse.Namespace) -> int:
    items = audit_context()
    print(render_audit(items))
    return 0


def cmd_tokens(args: argparse.Namespace) -> int:
    root = find_monorepo_root()
    paths = collect_paths(args.paths, root)
    if not paths:
        print("No files found.", file=sys.stderr)
        return 1
    estimates = count_files(paths)
    rows = []
    total_chars = 0
    total_tokens = 0
    method = "heuristic/4"
    for p, est in zip(paths, estimates):
        rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
        rows.append((rel, est))
        total_chars += est.chars
        total_tokens += est.tokens
        method = est.method
    rows.sort(key=lambda r: -r[1].tokens)
    total = TokenEstimate(tokens=total_tokens, chars=total_chars, method=method)
    print(format_size_table(rows, total))
    return 0


def cmd_rag(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    root = find_monorepo_root()
    domains = args.domain.split(",") if args.domain else None
    hits = search_rag(args.query, root, domains=domains, limit=args.limit)
    est_tokens = rag_est_tokens(hits, root) if hits else 0
    duration_ms = int((time.perf_counter() - t0) * 1000)
    print(format_hits(args.query, hits))
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="rag",
        route_id="rag-cli",
        confidence=1.0 if hits else 0.0,
        matched=["rag"] if hits else [],
        command=None,
        note="",
        domains=domains or [],
        complexity="low",
        est_tokens=est_tokens,
        rationale="RAG lookup via greedy-token rag",
    )
    maybe_append_event(
        args,
        build_route_event(
            cmd="rag",
            task=args.query,
            root=root,
            decision=decision,
            tier_scan=[],
            duration_ms=duration_ms,
            rag_hits=len(hits),
            est_tokens_override=est_tokens,
        ),
    )
    return 0


def cmd_compress(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    text = sys.stdin.read(COMPRESS_MAX_BYTES + 1)
    if len(text) > COMPRESS_MAX_BYTES:
        print(
            f"Prompt too large (>{COMPRESS_MAX_BYTES // 1024} KiB). "
            "Split or trim before compress.",
            file=sys.stderr,
        )
        return 1
    if not text.strip():
        print("Read prompt from stdin.", file=sys.stderr)
        return 1
    short, eval_tokens = compress_prompt_detail(text, use_ollama=args.ollama)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    if args.raw:
        print(short)
    else:
        print(format_dual(text, short))
    event = build_compress_event(
        text=text,
        short=short,
        use_ollama=args.ollama,
        duration_ms=duration_ms,
        eval_tokens=eval_tokens,
    )
    maybe_append_event(args, event)
    return 0


def cmd_scripts(args: argparse.Namespace) -> int:
    root = find_monorepo_root()
    if args.list:
        lines = ["Script wrappers (scripts/ollama | migrate | check-meta-sync):", ""]
        for wrapper in WRAPPERS.values():
            ro = "read-only" if wrapper.read_only else "writes"
            oll = " +ollama" if wrapper.requires_ollama else ""
            lines.append(f"  {wrapper.id:<20} [{wrapper.category}] {ro}{oll}")
            lines.append(f"    {wrapper.path}")
            if wrapper.note:
                lines.append(f"    {wrapper.note}")
        lines.append("")
        lines.append(ollama_status_line())
        print("\n".join(lines))
        return 0
    if args.run:
        t0 = time.perf_counter()
        try:
            cmd = resolve_wrapper_command(args.run, root, extra_args=args.args or "")
        except (KeyError, FileNotFoundError) as exc:
            print(exc, file=sys.stderr)
            return 1
        wrapper = WRAPPERS[args.run]
        code = 0
        executed = False
        if args.execute:
            if not wrapper.read_only:
                print(
                    f"Refusing --execute: {args.run} is not read-only.",
                    file=sys.stderr,
                )
                return 1
            import subprocess

            from greedy_token.tool_paths import SCRIPT_TIMEOUT

            try:
                proc = subprocess.run(cmd, shell=True, timeout=SCRIPT_TIMEOUT)
            except subprocess.TimeoutExpired:
                print(
                    f"Script timed out after {SCRIPT_TIMEOUT}s.",
                    file=sys.stderr,
                )
                return 124
            code = proc.returncode
            executed = True
        else:
            print(cmd)
            if wrapper.read_only:
                print("\n(read-only — add --execute to run)")
            else:
                print("\n(not read-only — dry-run only)")
        duration_ms = int((time.perf_counter() - t0) * 1000)
        maybe_append_event(
            args,
            build_script_event(
                script_id=args.run,
                root=root,
                duration_ms=duration_ms,
                executed=executed,
            ),
        )
        return code
    print("Use scripts --list or scripts --run ID", file=sys.stderr)
    return 1


def cmd_report(args: argparse.Namespace) -> int:
    path = log_path()
    since_dt = parse_since(args.since) if args.since else None
    events, skipped = load_events(path, since=since_dt)
    since_label = args.since if args.since else None
    summary = aggregate_events(events, since_label=since_label)
    summary.skipped_lines = skipped
    if args.json:
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_report(summary))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    root = find_monorepo_root()
    if args.init:
        try:
            path = init_user_config(url=args.url, model=args.model, force=args.force)
        except FileExistsError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Created {path}")
        print()
    settings = apply_ollama_env(root)
    if args.export:
        print(format_shell_export(settings, root=root))
        return 0
    print(format_config(settings, root=root))
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    if args.list or not (args.task or "").strip():
        print(list_pipelines())
        return 0
    root = find_monorepo_root()
    result = run_pipeline(
        args.task,
        root,
        execute=args.execute,
        stop_on_error=not args.continue_on_error,
    )
    print(format_pipeline_response(result, root))
    return 0 if result.all_ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="greedy-token",
        description="Task orchestrator: tool | Python | Ollama | RAG | Cursor",
    )
    p.add_argument(
        "--no-log",
        action="store_true",
        help="Disable usage telemetry (see also GREEDY_TOKEN_LOG=0)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("route", help="Recommend executor for a task")
    r.add_argument("task", help="Natural language task description")
    r.set_defaults(func=cmd_route)

    est = sub.add_parser("estimate", help="Token-aware route estimate with tier scan")
    est.add_argument("task", help="Task description")
    est.set_defaults(func=cmd_estimate)

    run = sub.add_parser("run", help="Route and show/run command")
    run.add_argument("task", help="Task description")
    run.add_argument(
        "--execute",
        action="store_true",
        help="Execute read-only tool/python commands only",
    )
    run.set_defaults(func=cmd_run)

    sub.add_parser("audit-context", help="Token audit of rules/skills").set_defaults(
        func=cmd_audit_context
    )

    t = sub.add_parser("tokens", help="Count tokens in paths")
    t.add_argument("paths", nargs="+", help="Files or directories")
    t.set_defaults(func=cmd_tokens)

    rag = sub.add_parser("rag", help="Search docs/rag chunks")
    rag.add_argument("query", help="Search query")
    rag.add_argument("--domain", help="Comma-separated domains filter")
    rag.add_argument("--limit", type=int, default=5)
    rag.set_defaults(func=cmd_rag)

    c = sub.add_parser("compress", help="Short agent prompt from stdin")
    c.add_argument("--ollama", action="store_true", help="Use local Ollama")
    c.add_argument("--raw", action="store_true", help="Print short text only")
    c.set_defaults(func=cmd_compress)

    scr = sub.add_parser("scripts", help="Wrappers for monorepo scripts")
    scr.add_argument("--list", action="store_true", help="List script wrappers")
    scr.add_argument("--run", metavar="ID", help="Wrapper id (e.g. check-meta-sync)")
    scr.add_argument("args", nargs="?", default="", help="Extra args for script")
    scr.add_argument(
        "--execute",
        action="store_true",
        help="Run read-only wrapper only",
    )
    scr.set_defaults(func=cmd_scripts)

    rep = sub.add_parser("report", help="Aggregate usage telemetry")
    rep.add_argument(
        "--since",
        default="7d",
        help="Time window: 7d, 24h, or ISO date (default: 7d)",
    )
    rep.add_argument("--json", action="store_true", help="JSON output")
    rep.set_defaults(func=cmd_report)

    cfg = sub.add_parser("config", help="Show or init Ollama URL/model settings")
    cfg.add_argument("--init", action="store_true", help="Create ~/.greedy-token/config.yaml")
    cfg.add_argument("--url", help="Ollama URL for --init")
    cfg.add_argument("--model", help="Ollama model for --init")
    cfg.add_argument("--force", action="store_true", help="Overwrite existing user config")
    cfg.add_argument(
        "--export",
        action="store_true",
        help="Print shell exports (export OLLAMA_URL=...)",
    )
    cfg.set_defaults(func=cmd_config)

    pipe = sub.add_parser("pipeline", help="Multi-step python/ollama/rag pipeline")
    pipe.add_argument("task", nargs="?", default="", help="Pipeline task or recipe")
    pipe.add_argument("--list", action="store_true", help="List named pipelines")
    pipe.add_argument(
        "--execute",
        action="store_true",
        help="Run steps (default: dry-run)",
    )
    pipe.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Do not stop on step failure",
    )
    pipe.set_defaults(func=cmd_pipeline)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "config":
        try:
            apply_ollama_env(find_monorepo_root())
        except SystemExit:
            pass
    code = args.func(args)
    raise SystemExit(code)
