from __future__ import annotations

import argparse
import json
import sys
import time

from greedy_token.context_audit import audit_context, render_audit
from greedy_token.estimator import estimate_task, format_estimate
from greedy_token.executors import execute_task, plan_run
from greedy_token.paths import find_workspace_root
from greedy_token.pipeline import format_pipeline_response, list_pipelines, run_pipeline
from greedy_token.prompt_compress import compress_prompt_detail, format_dual
from greedy_token.rag_search import format_hits, search_rag
from greedy_token.router import format_decision, route_task
from greedy_token.settings import (
    apply_ollama_env,
    format_config,
    format_shell_export,
    init_user_config,
    init_user_config_from_preset,
    list_preset_names,
)
from greedy_token.tokens import TokenEstimate, collect_paths, count_files, count_tokens, format_size_table
from greedy_token.usage import (
    aggregate_events,
    build_compress_event,
    build_route_event,
    build_script_event,
    build_script_override_event,
    build_tier_scan,
    format_report,
    load_events,
    log_path,
    maybe_append_event,
    parse_since,
)
from greedy_token.wrappers import WRAPPERS, ollama_status_line, resolve_wrapper_command


from greedy_token.budget import rag_est_tokens
from greedy_token.advisory import watch_events

COMPRESS_MAX_BYTES = 256 * 1024


def cmd_route(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    root = find_workspace_root()
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
    root = find_workspace_root()
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
    root = find_workspace_root()
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
    root = find_workspace_root()
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
    root = find_workspace_root()
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
    root = find_workspace_root()
    action = str(getattr(args, "args", "") or "").strip()
    if not args.list and not args.run and action == "lint":
        from greedy_token.scripts_lint import format_lint_report, lint_routes

        result = lint_routes(root=root)
        print(format_lint_report(result))
        return 0 if result.get("ok") else 1
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
    print("Use scripts --list, scripts --run ID, or scripts lint", file=sys.stderr)
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
    if args.list_presets:
        for name in list_preset_names():
            print(name)
        return 0

    if args.init:
        try:
            if args.preset:
                if args.url or args.model or args.provider:
                    print(
                        "config --init --preset ignores --url, --model, and --provider",
                        file=sys.stderr,
                    )
                path = init_user_config_from_preset(preset=args.preset, force=args.force)
            else:
                path = init_user_config(
                    url=args.url,
                    model=args.model,
                    provider=args.provider,
                    force=args.force,
                )
        except FileExistsError as exc:
            print(exc, file=sys.stderr)
            return 1
        except (FileNotFoundError, ValueError) as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Created {path}")
        print()

    try:
        root = find_workspace_root()
    except SystemExit:
        if args.init:
            root = None
        else:
            raise

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
    root = find_workspace_root()
    result = run_pipeline(
        args.task,
        root,
        execute=args.execute,
        stop_on_error=not args.continue_on_error,
        profile=getattr(args, "profile", "") or "",
        escalate=getattr(args, "escalate", False),
    )
    print(format_pipeline_response(result, root))
    return 0 if result.all_ok else 1


def _parse_tags(raw: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        tags[key.strip()] = value.strip()
    return tags


def cmd_llm_invoke(args: argparse.Namespace) -> int:
    from pathlib import Path

    from greedy_token.llm_invoke import invoke_profile, invoke_result_to_dict

    root = find_workspace_root()
    system = args.system or ""
    user = args.user or ""
    try:
        if args.system_file:
            system = Path(args.system_file).read_text(encoding="utf-8")
        if args.user_file:
            user = Path(args.user_file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"llm invoke: cannot read prompt file: {exc}", file=sys.stderr)
        return 2
    if not user and not sys.stdin.isatty():
        user = sys.stdin.read()
    if not user.strip():
        print("llm invoke needs --user, --user-file, or stdin", file=sys.stderr)
        return 2

    try:
        result = invoke_profile(
            args.profile,
            system=system,
            user=user,
            root=root,
            tags=_parse_tags(args.tags or ""),
            allow_escalate=not args.no_escalate,
            allow_expensive=args.allow_expensive,
            log=not getattr(args, "no_log", False),
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(invoke_result_to_dict(result), indent=2, ensure_ascii=False))
    else:
        print(result.text)
        if result.escalated_from:
            print(f"\n(escalated from {result.escalated_from} → {result.model_id})", file=sys.stderr)
    return 0


def cmd_llm_list(args: argparse.Namespace) -> int:
    from greedy_token.model_select import get_llm_registry, list_models

    root = find_workspace_root()
    reg = get_llm_registry(root)
    lines = [
        f"policy: {reg.policy}",
        f"cheap default: {reg.cheap_default_id} ({reg.cheap_selection})",
        f"expensive opt_in: {reg.expensive_opt_in}  daily_cap_usd: {reg.daily_cap_usd}",
        "",
        "models:",
    ]
    for spec in list_models(root):
        state = "on" if spec.enabled else "off"
        lines.append(
            f"  {spec.id:<16} [{spec.tier}] {state}  {spec.provider}/{spec.model}  "
            f"profiles={','.join(spec.profiles)}"
        )
    print("\n".join(lines))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from greedy_token.resource_probe import apply_doctor_config, format_doctor_report, run_doctor

    root = None
    try:
        root = find_workspace_root()
    except SystemExit:
        pass

    if args.apply:
        try:
            path = apply_doctor_config(force=args.force)
            print(f"Updated {path}")
            return 0
        except (ValueError, FileExistsError) as exc:
            print(exc, file=sys.stderr)
            return 1

    report = run_doctor(
        root=root,
        quick=not args.benchmark,
        include_paid=args.paid,
        benchmark=args.benchmark,
    )
    if args.json:
        import dataclasses

        payload = {
            "hardware": dataclasses.asdict(report.hardware),
            "ollama_available": report.ollama_available,
            "configured_model": report.configured_model,
            "recommended": report.recommended,
            "warnings": report.warnings,
            "deprecated_installed": report.deprecated_installed,
        }
        if report.benchmark:
            payload["benchmark"] = dataclasses.asdict(report.benchmark)
        if args.paid:
            payload["paid_recommendations"] = report.paid_recommendations
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_doctor_report(report, include_paid=args.paid))
    return 0


INIT_PROFILE_POLICY = {"solo": "safe", "team": "hybrid", "ci": "cheap_only"}


def detect_environment() -> dict:
    """Detect executor prerequisites for tiers (Phase 4 bootstrap)."""
    import platform
    import shutil

    from greedy_token.settings import user_config_path
    from greedy_token.wrappers import ollama_available

    cfg_path = user_config_path()
    return {
        "ripgrep": shutil.which("rg") is not None,
        "python_version": platform.python_version(),
        "python_ok": sys.version_info >= (3, 12),
        "ollama": ollama_available(),
        "config_path": str(cfg_path),
        "config_exists": cfg_path.is_file(),
    }


def cmd_init(args: argparse.Namespace) -> int:
    profile = (getattr(args, "profile", None) or "solo").lower()
    if profile not in INIT_PROFILE_POLICY:
        print("--profile must be one of: solo | team | ci", file=sys.stderr)
        return 2

    env = detect_environment()
    policy = INIT_PROFILE_POLICY[profile]
    env["profile"] = profile
    env["recommended_policy"] = policy

    if args.json and not args.apply:
        print(json.dumps(env, indent=2, ensure_ascii=False))
        return 0

    def mark(ok: bool) -> str:
        return "OK" if ok else "missing"

    lines = [
        "greedy-token init",
        f"  profile:  {profile}  (policy: {policy})",
        f"  ripgrep:  {mark(env['ripgrep'])}  — tool tier (find/search)",
        f"  python:   {env['python_version']}"
        f"{'' if env['python_ok'] else '  (need >= 3.12)'}  — python/script tier",
        f"  ollama:   {mark(env['ollama'])}  — cheap LLM tier",
    ]
    if not env["ripgrep"]:
        lines.append("  ! install ripgrep for the tool tier (brew install ripgrep)")
    if not env["ollama"]:
        lines.append("  · ollama offline — cheap LLM tier skipped; tool/python/rag still work")

    if not args.apply:
        lines.extend(
            [
                "",
                "Next:",
                f"  greedy-token init --profile {profile} --apply   # write config with policy={policy}",
                "  greedy-token doctor --apply                     # pick optimal local model",
                "  greedy-token config                             # show effective config",
            ]
        )
        print("\n".join(lines))
        return 0

    from greedy_token.settings import init_user_config, user_config_path

    if env["config_exists"] and not args.force:
        lines.append("")
        lines.append(f"Config exists: {env['config_path']} (use --force to overwrite)")
        print("\n".join(lines))
        return 0

    try:
        import yaml

        path = init_user_config(force=args.force)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data.setdefault("llm", {})["policy"] = policy
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except (FileExistsError, FileNotFoundError, ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1

    lines.extend(["", f"Wrote {path} (policy: {policy})", "Next: greedy-token doctor --apply"])
    print("\n".join(lines))
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    from greedy_token.budget_ledger import aggregate_budget, format_budget_line

    root = None
    try:
        root = find_workspace_root()
    except SystemExit:
        pass

    snap = aggregate_budget(root=root)
    if args.json:
        print(
            json.dumps(
                {
                    "metered_spent_usd": snap.metered_spent_usd,
                    "metered_cap_usd": snap.metered_cap_usd,
                    "metered_remaining_usd": snap.metered_remaining_usd,
                    "metered_pct": snap.metered_pct,
                    "cursor_est_spent_usd": snap.cursor_est_spent_usd,
                    "cursor_est_cap_usd": snap.cursor_est_cap_usd,
                    "cursor_est_remaining_usd": snap.cursor_est_remaining_usd,
                    "cursor_est_pct": snap.cursor_est_pct,
                    "mode": snap.mode,
                    "period_label": snap.period_label,
                },
                indent=2,
            )
        )
    else:
        print(format_budget_line(root=root, compact=not args.verbose))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    return watch_events(
        follow=not args.once,
        from_start=args.from_start,
        json_out=args.json,
    )


def cmd_override(args: argparse.Namespace) -> int:
    root = find_workspace_root()
    event = build_script_override_event(
        task=args.task,
        selected_tier=args.selected_tier,
        previous_tier=args.previous_tier,
        crystal_id=args.crystal_id,
        root=root,
        reason=args.reason,
        prior_usage_ts=args.prior_usage_ts,
        window_sec=args.window_sec,
        tags=_parse_tags(args.tags),
    )
    maybe_append_event(args, event)
    if args.json:
        print(json.dumps(event, ensure_ascii=False))
    else:
        crystal = args.crystal_id or "unknown"
        print(f"script_override logged: {crystal} -> {args.selected_tier}")
    return 0


def cmd_hub_serve(args: argparse.Namespace) -> int:
    from greedy_token.hub import serve

    serve(host=args.host, port=args.port)
    return 0


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
    c.add_argument("--ollama", action="store_true", help="Use cheap LLM (Ollama / openai_compat)")
    c.add_argument("--raw", action="store_true", help="Print short text only")
    c.set_defaults(func=cmd_compress)

    scr = sub.add_parser("scripts", help="Wrappers for workspace scripts")
    scr.add_argument("--list", action="store_true", help="List script wrappers")
    scr.add_argument("--run", metavar="ID", help="Wrapper id (e.g. check-meta-sync)")
    scr.add_argument(
        "args",
        nargs="?",
        default="",
        help="Extra args for --run, or 'lint' for crystallize pattern/script checks",
    )
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

    over = sub.add_parser("override", help="Log a script_override telemetry event")
    over.add_argument("crystal_id", help="Route / crystal id being overridden")
    over.add_argument("task", help="Retry task text")
    over.add_argument("--selected-tier", default="cursor", help="Retry tier (default: cursor)")
    over.add_argument("--previous-tier", default="python", help="Prior tier (default: python)")
    over.add_argument(
        "--reason",
        default="manual",
        choices=("user_reask", "agent_fallback", "smoke_fail", "manual"),
        help="Override reason",
    )
    over.add_argument("--prior-usage-ts", default=None, help="Timestamp of prior script hit")
    over.add_argument("--window-sec", type=int, default=900, help="Attribution window seconds")
    over.add_argument("--tags", default="", help="Telemetry tags key=value,key=value")
    over.add_argument("--json", action="store_true", help="JSON output")
    over.set_defaults(func=cmd_override)

    cfg = sub.add_parser("config", help="Show or init cheap LLM settings (Ollama / OpenAI-compatible)")
    cfg.add_argument("--init", action="store_true", help="Create ~/.greedy-token/config.yaml")
    cfg.add_argument(
        "--preset",
        help="Preset name for --init (see greedy-token config --list-presets)",
    )
    cfg.add_argument(
        "--list-presets",
        action="store_true",
        help="List available config presets",
    )
    cfg.add_argument("--url", help="Cheap LLM base URL for --init")
    cfg.add_argument("--model", help="Cheap LLM model for --init")
    cfg.add_argument(
        "--provider",
        choices=("ollama", "openai_compat"),
        help="Cheap LLM provider for --init (default: ollama)",
    )
    cfg.add_argument("--force", action="store_true", help="Overwrite existing user config")
    cfg.add_argument(
        "--export",
        action="store_true",
        help="Print shell exports (CHEAP_LLM_* and OLLAMA_* aliases)",
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
    pipe.add_argument(
        "--profile",
        default="",
        help="LLM profile for ollama steps (e.g. tms-classify, tms-generate)",
    )
    pipe.add_argument(
        "--escalate",
        action="store_true",
        help="Allow model escalation on weak ollama output (requires expensive opt-in for paid)",
    )
    pipe.set_defaults(func=cmd_pipeline)

    llm = sub.add_parser("llm", help="Multi-model LLM invoke (headless / TMS automator)")
    llm_sub = llm.add_subparsers(dest="llm_command", required=True)

    invoke = llm_sub.add_parser("invoke", help="Invoke LLM by profile")
    invoke.add_argument("--profile", required=True, help="Model profile (tms-classify, tms-generate, …)")
    invoke.add_argument("--system", default="", help="System prompt text")
    invoke.add_argument("--user", default="", help="User prompt text")
    invoke.add_argument("--system-file", help="System prompt file")
    invoke.add_argument("--user-file", help="User prompt file")
    invoke.add_argument(
        "--tags",
        default="",
        help="Telemetry tags key=value,key=value (e.g. project=tms-automator,step=classify)",
    )
    invoke.add_argument("--json", action="store_true", help="JSON output")
    invoke.add_argument("--allow-expensive", action="store_true", help="Opt in to paid expensive LLM")
    invoke.add_argument("--no-escalate", action="store_true", help="Disable escalation chain")
    invoke.set_defaults(func=cmd_llm_invoke)

    llm_list = llm_sub.add_parser("list", help="List configured models")
    llm_list.set_defaults(func=cmd_llm_list)

    doc = sub.add_parser("doctor", help="Probe hardware + Ollama models; recommend optimal local model")
    doc.add_argument("--apply", action="store_true", help="Update ~/.greedy-token/config.yaml with recommendation")
    doc.add_argument("--force", action="store_true", help="Overwrite on --apply")
    doc.add_argument("--benchmark", action="store_true", help="Run micro-benchmark on recommended model")
    doc.add_argument("--paid", action="store_true", help="Include paid model economy recommendations")
    doc.add_argument("--json", action="store_true", help="JSON output")
    doc.set_defaults(func=cmd_doctor)

    ini = sub.add_parser(
        "init",
        help="Bootstrap: detect rg/python/ollama + profile (solo|team|ci) over config/doctor",
    )
    ini.add_argument(
        "--profile",
        default="solo",
        choices=("solo", "team", "ci"),
        help="Setup profile: solo=safe, team=hybrid, ci=cheap_only (default: solo)",
    )
    ini.add_argument("--apply", action="store_true", help="Write ~/.greedy-token/config.yaml with the profile policy")
    ini.add_argument("--force", action="store_true", help="Overwrite existing config on --apply")
    ini.add_argument("--json", action="store_true", help="JSON detection output (detect-only)")
    ini.set_defaults(func=cmd_init)

    bud = sub.add_parser("budget", help="Split budget view: metered API + Cursor estimate")
    bud.add_argument("--json", action="store_true", help="JSON output")
    bud.add_argument("--verbose", action="store_true", help="Multi-line breakdown")
    bud.set_defaults(func=cmd_budget)

    w = sub.add_parser(
        "watch",
        help="Tail hook advisory log (~/.greedy-token/advisory.jsonl)",
    )
    w.add_argument(
        "--once",
        action="store_true",
        help="Print new events since start, then exit (no follow)",
    )
    w.add_argument(
        "--from-start",
        action="store_true",
        help="Include entire log from file start",
    )
    w.add_argument("--json", action="store_true", help="Raw JSONL output")
    w.set_defaults(func=cmd_watch)

    hub = sub.add_parser("hub", help="Local ops dashboard (telemetry + crystallize)")
    hub_sub = hub.add_subparsers(dest="hub_command", required=True)
    hub_serve = hub_sub.add_parser("serve", help="Serve hub on localhost")
    hub_serve.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    hub_serve.add_argument("--port", type=int, default=8787, help="Bind port (default 8787)")
    hub_serve.set_defaults(func=cmd_hub_serve)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "config":
        try:
            apply_ollama_env(find_workspace_root())
        except SystemExit:
            pass
    code = args.func(args)
    raise SystemExit(code)
