from __future__ import annotations

import argparse
import sys

from greedy_token.context_audit import audit_context, render_audit
from greedy_token.estimator import estimate_task, format_estimate
from greedy_token.executors import execute_plan, plan_run
from greedy_token.paths import find_monorepo_root
from greedy_token.prompt_compress import compress_prompt, format_dual
from greedy_token.rag_search import format_hits, search_rag
from greedy_token.router import format_decision, route_task
from greedy_token.tokens import TokenEstimate, collect_paths, count_file, format_size_table
from greedy_token.wrappers import WRAPPERS, ollama_status_line, resolve_wrapper_command


def cmd_route(args: argparse.Namespace) -> int:
    root = find_monorepo_root()
    decision = route_task(args.task, root)
    print(format_decision(decision, args.task, root))
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    root = find_monorepo_root()
    estimate = estimate_task(args.task, root)
    print(format_estimate(estimate, args.task, root))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    root = find_monorepo_root()
    decision = route_task(args.task, root)
    plan = plan_run(decision, args.task, root)
    print(f"Route: {decision.target} ({decision.route_id})")
    print(f"Complexity: {decision.complexity}  Est. tokens: {decision.est_tokens:,}")
    print()
    if args.execute:
        code, out = execute_plan(plan)
        print(out)
        return code
    print(plan.dry_run_output)
    if plan.command:
        if plan.executable:
            print("\n(read-only — add --execute to run)")
        else:
            print("\n(not read-only — dry-run only)")
    return 0


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
    rows = []
    total_chars = 0
    total_tokens = 0
    method = "heuristic/4"
    for p in paths:
        est = count_file(p)
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
    root = find_monorepo_root()
    domains = args.domain.split(",") if args.domain else None
    hits = search_rag(args.query, root, domains=domains, limit=args.limit)
    print(format_hits(args.query, hits))
    return 0


def cmd_compress(args: argparse.Namespace) -> int:
    text = sys.stdin.read()
    if not text.strip():
        print("Read prompt from stdin.", file=sys.stderr)
        return 1
    short = compress_prompt(text, use_ollama=args.ollama)
    if args.raw:
        print(short)
    else:
        print(format_dual(text, short))
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
        try:
            cmd = resolve_wrapper_command(args.run, root, extra_args=args.args or "")
        except (KeyError, FileNotFoundError) as exc:
            print(exc, file=sys.stderr)
            return 1
        wrapper = WRAPPERS[args.run]
        if args.execute:
            if not wrapper.read_only:
                print(
                    f"Refusing --execute: {args.run} is not read-only.",
                    file=sys.stderr,
                )
                return 1
            import subprocess

            proc = subprocess.run(cmd, shell=True)
            return proc.returncode
        print(cmd)
        if wrapper.read_only:
            print("\n(read-only — add --execute to run)")
        else:
            print("\n(not read-only — dry-run only)")
        return 0
    print("Use scripts --list or scripts --run ID", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="greedy-token",
        description="Task orchestrator: tool | Python | Ollama | RAG | Cursor",
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

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    code = args.func(args)
    raise SystemExit(code)
