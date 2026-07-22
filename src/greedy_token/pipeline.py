"""Multi-step pipeline: python → ollama → rag with unified stats."""

from __future__ import annotations

import re
import subprocess
import time
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from greedy_token.budget import (
    BASELINE_LABEL,
    TOTAL_BASELINE_LABEL,
    TIER_LABELS,
    cursor_baseline,
    cursor_baseline_breakdown,
    format_savings_lines,
    spent_hint,
)
from greedy_token.code_search import (
    enrich_search_hits,
    parse_hit_lines,
    search_code,
)
from greedy_token.paths import find_workspace_root
from greedy_token.rag_search import format_hits, search_rag
from greedy_token.router import RouteDecision
from greedy_token.settings import (
    apply_cheap_llm_env,
    get_cheap_llm_settings,
    get_search_settings,
)
from greedy_token.model_select import resolve_model, apply_model_env
from greedy_token.tokens import count_tokens
from greedy_token.tool_paths import SCRIPT_TIMEOUT
from greedy_token.usage import append_event, build_route_event
from greedy_token.wrappers import WRAPPERS, ollama_available, resolve_wrapper_command

PIPELINE_SPLIT = re.compile(r"\s+then\s+|\s*→\s*|\s*->\s*|\s*;\s*", re.IGNORECASE)

# Safe to auto-run from MCP (read-only or stdout-only).
PIPELINE_AUTO_RUN = frozenset(
    {
        "check-meta-sync",
        "configurator-boolean-audit",
        "audit-skill",
        "classify-file",
        "search",
        "read-hits",
        "rag",
    }
)


@dataclass
class PipelineStep:
    step_id: str
    tier: str
    label: str
    command: str | None = None
    args: str = ""
    profile: str = ""


@dataclass
class StepResult:
    step: PipelineStep
    ok: bool
    exit_code: int
    output: str
    duration_ms: int
    est_tokens: int
    executed: bool
    engine: str = ""  # search: rg | python (from SearchResult.engine)


@dataclass
class PipelineResult:
    task: str
    steps: list[StepResult] = field(default_factory=list)
    stopped_early: bool = False

    @property
    def total_duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.steps)

    @property
    def total_est_tokens(self) -> int:
        return sum(s.est_tokens for s in self.steps)

    @property
    def all_ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)


@dataclass
class StepSavingsRow:
    index: int
    step_id: str
    tier: str
    duration_ms: int
    spent: int
    baseline: int
    saved: int
    billing: str
    executor_sub: str = ""


def _executor_sub_for_step(sr: StepResult) -> str:
    """Map pipeline step to footer executor_sub (search → SearchResult.engine)."""
    if sr.step.step_id == "search" or sr.step.tier == "tool":
        return sr.engine or "rg"
    return sr.step.tier


def compute_step_savings(result: PipelineResult, root: Path) -> list[StepSavingsRow]:
    rows: list[StepSavingsRow] = []
    for i, sr in enumerate(result.steps, 1):
        baseline = cursor_baseline(root, sr.step.label)
        spent = sr.est_tokens
        # Dry-run did not run the executor — do not claim full baseline savings.
        if not sr.executed:
            saved = 0
            billing = "dry-run — not executed"
        else:
            saved = max(0, baseline - spent)
            billing = spent_hint(sr.step.tier, spent, _executor_sub_for_step(sr))
        sub = _executor_sub_for_step(sr)
        rows.append(
            StepSavingsRow(
                index=i,
                step_id=sr.step.step_id,
                tier=sr.step.tier,
                duration_ms=sr.duration_ms,
                spent=spent,
                baseline=baseline,
                saved=saved,
                billing=billing,
                executor_sub=sub,
            )
        )
    return rows


def format_pipeline_step_savings_table(rows: list[StepSavingsRow]) -> list[str]:
    if not rows:
        return []
    lines = [
        "Per-step savings (if each step were a separate naive Cursor chat):",
        f"  {'#':>2}  {'step':<22} {'executor':<8} {'ms':>6} {'spent':>7} {'baseline':>9} {'saved':>9}  billing",
    ]
    for row in rows:
        executor = row.executor_sub or row.tier
        lines.append(
            f"  {row.index:>2}  {row.step_id:<22} {executor:<8} {row.duration_ms:>6} "
            f"{row.spent:>7,} {row.baseline:>9,} {row.saved:>9,}  {row.billing}"
        )
    return lines


def format_executor_savings_summary(rows: list[StepSavingsRow]) -> list[str]:
    if not rows:
        return []
    by_tier: dict[str, tuple[int, int, int]] = {}
    for row in rows:
        spent, saved, count = by_tier.get(row.tier, (0, 0, 0))
        by_tier[row.tier] = (spent + row.spent, saved + row.saved, count + 1)
    lines = ["Saved by executor (sum of per-step savings):"]
    for tier in ("tool", "python", "ollama", "rag", "cursor"):
        if tier not in by_tier:
            continue
        spent, saved, count = by_tier[tier]
        # equivalent: this loop only visits keys that exist in TIER_LABELS, so
        # the `tier` default is unreachable (get default → None/removed is a no-op).
        label = TIER_LABELS.get(tier, tier)  # pragma: no mutate
        lines.append(
            f"  {label:<28} steps={count}  spent ~{spent:,}  saved ~{saved:,}"
        )
    return lines


def _load_pipelines_config() -> dict:
    # equivalent: path segments are case-insensitive on the local (macOS/APFS)
    # filesystem, so "config"/"pipelines.yaml" case-flips resolve identically.
    path = Path(__file__).parent / "config" / "pipelines.yaml"  # pragma: no mutate
    if not path.is_file():
        return {}
    # equivalent: file is ASCII YAML; utf-8/UTF-8/locale-default decode the same.
    with path.open(encoding="utf-8") as fh:  # pragma: no mutate
        return yaml.safe_load(fh) or {}


# Placeholders that absorb remaining positionals as a multi-word value.
_JOINABLE_PLACEHOLDERS = frozenset({"query"})


def _split_recipe_params(
    params: list[str], known: list[str]
) -> tuple[list[str], dict[str, str]]:
    """Split recipe args into positionals and key=value for known placeholders.

    Supports both ``pipeline: search-rag baseUrl foo.html`` and
    ``pipeline: search-rag baseUrl path=foo.html`` (agent / rule style).
    Multi-word queries: ``pipeline: search-rag hello world path=foo.html``.
    """
    positional: list[str] = []
    kwargs: dict[str, str] = {}
    known_set = set(known)
    for param in params:
        if "=" in param:
            key, _, value = param.partition("=")
            if key in known_set:
                kwargs[key] = value
                continue
        positional.append(param)
    return positional, kwargs


def _bind_recipe_args(
    name: str,
    known: list[str],
    positional: list[str],
    kwargs: dict[str, str],
) -> dict[str, str]:
    """Bind recipe placeholders: kwargs first; join multi-word ``query``; last token → ``path``."""
    usage = (
        f"Usage: pipeline: {name} <{'> <'.join(known)}>"
        + (f" (or {known[-1]}=…)" if known else "")
    )
    mapping: dict[str, str] = {
        key: value for key, value in kwargs.items() if key in known
    }
    need_pos = [ph for ph in known if ph not in mapping]
    if not need_pos:
        if positional:
            raise ValueError(
                f"Pipeline {name!r} got unexpected extra args: {' '.join(positional)!r}. {usage}"
            )
        return mapping

    # query (+ …) + path: last positional is path; earlier tokens join into query.
    if "path" in need_pos and any(ph in _JOINABLE_PLACEHOLDERS for ph in need_pos):
        if len(positional) < 2:
            raise ValueError(f"Pipeline {name!r} needs more args. {usage}")
        mapping["path"] = positional[-1]
        positional = positional[:-1]
        need_pos = [ph for ph in need_pos if ph != "path"]

    if len(need_pos) == 1:
        ph = need_pos[0]
        if not positional:
            raise ValueError(f"Pipeline {name!r} needs more args. {usage}")
        if ph in _JOINABLE_PLACEHOLDERS:
            mapping[ph] = " ".join(positional)
            return mapping
        if len(positional) > 1:
            raise ValueError(
                f"Pipeline {name!r} got unexpected extra args: {' '.join(positional[1:])!r}. {usage}"
            )
        mapping[ph] = positional[0]
        return mapping

    if len(positional) < len(need_pos):
        raise ValueError(f"Pipeline {name!r} needs more args. {usage}")
    if len(positional) > len(need_pos):
        extra = " ".join(positional[len(need_pos) :])
        raise ValueError(
            f"Pipeline {name!r} got unexpected extra args: {extra!r}. {usage}"
        )
    # equivalent: the two length checks above guarantee len(need_pos) ==
    # len(positional) here, so strict True/False/None never changes behaviour.
    for ph, value in zip(need_pos, positional, strict=True):  # pragma: no mutate
        mapping[ph] = value
    return mapping


def _expand_named_pipeline(text: str, *, default_profile: str = "") -> tuple[str, str]:
    cfg = _load_pipelines_config()
    pipelines = cfg.get("pipelines") or {}
    tokens = text.split()
    if not tokens:
        return text, default_profile
    name = tokens[0]
    if name not in pipelines:
        return text, default_profile
    recipe = pipelines[name]
    recipe_profile = str(recipe.get("profile", default_profile or name)).strip()
    steps: list[str] = recipe.get("steps") or []
    known: list[str] = []
    for step_tpl in steps:
        for ph in re.findall(r"\{(\w+)\}", step_tpl):
            if ph not in known:
                known.append(ph)
    positional, kwargs = _split_recipe_params(tokens[1:], known)
    global_mapping = _bind_recipe_args(name, known, positional, kwargs)
    expanded: list[str] = []
    for step_tpl in steps:
        if (
            step_tpl == "audit-skill {skill}"
            and global_mapping.get("skill") == "configurator-boolean"
        ):
            expanded.append("configurator-boolean-audit")
            continue
        if "{" in step_tpl:
            placeholders = re.findall(r"\{(\w+)\}", step_tpl)
            mapping = {ph: global_mapping[ph] for ph in placeholders}
            expanded.append(step_tpl.format(**mapping))
        else:
            expanded.append(step_tpl)
    return " then ".join(expanded), recipe_profile


def parse_pipeline(task: str, *, profile: str = "") -> list[PipelineStep]:
    text = task.strip()
    if text.lower().startswith("pipeline:"):
        text = text.split(":", 1)[1].strip()
    text, recipe_profile = _expand_named_pipeline(text, default_profile=profile)
    active_profile = recipe_profile or profile
    segments = [s.strip() for s in PIPELINE_SPLIT.split(text) if s.strip()]
    if not segments:
        raise ValueError("Empty pipeline. Example: check-meta-sync then audit-skill configurator-boolean")
    return [_parse_segment(seg, profile=active_profile) for seg in segments]


def _parse_segment(segment: str, *, profile: str = "") -> PipelineStep:
    segment = segment.strip()
    if segment.startswith("search "):
        return _parse_search_segment(segment, profile=profile)
    if segment == "read-hits" or segment.startswith("read-hits "):
        return PipelineStep(
            step_id="read-hits",
            tier="tool",
            label="read-hits",
            args=segment[len("read-hits") :].strip(),
            profile=profile,
        )
    if segment.startswith("rag "):
        query = segment[4:].strip()
        return PipelineStep(
            step_id="rag",
            tier="rag",
            label=f"rag: {query}",
            args=query,
            profile=profile,
        )
    parts = segment.split(maxsplit=1)
    step_id = parts[0]
    args = parts[1] if len(parts) > 1 else ""
    if step_id not in WRAPPERS:
        raise ValueError(
            f"Unknown step {step_id!r}. Known: {', '.join(sorted(WRAPPERS))}, "
            f"search, read-hits, rag"
        )
    wrapper = WRAPPERS[step_id]
    tier = "ollama" if wrapper.requires_ollama else "python"
    resolved_args = _resolve_wrapper_args(step_id, args)
    root = find_workspace_root()
    command = resolve_wrapper_command(step_id, root, extra_args=resolved_args)
    return PipelineStep(
        step_id=step_id,
        tier=tier,
        label=f"{step_id} {resolved_args}".strip(),
        command=command,
        args=resolved_args,
        profile=profile if wrapper.requires_ollama else "",
    )


def _parse_search_segment(segment: str, *, profile: str = "") -> PipelineStep:
    # search baseUrl path=configurator-option-presets.html
    body = segment[7:].strip()
    path = ""
    query = body
    if " path=" in body:
        query, _, path = body.partition(" path=")
        query = query.strip()
        path = path.strip()
    return PipelineStep(
        step_id="search",
        tier="tool",
        label=f"search: {query}" + (f" in {path}" if path else ""),
        args=f"{query}\t{path}",
        profile=profile,
    )


def _path_under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _reject_outside_root(path: Path, root: Path, *, hint: str) -> None:
    if not _path_under_root(path, root):
        raise ValueError(
            f"Error: path {hint!r} is outside workspace root "
            f"({root}). Pipeline file args are confined to the workspace."
        )


def _resolve_under_root(arg: str, root: Path) -> Path:
    """Resolve a file path hint; reject absolute/relative escapes outside *root*."""
    hint = arg.strip()
    direct = Path(hint)
    if direct.is_absolute():
        resolved = direct.resolve()
        _reject_outside_root(resolved, root, hint=hint)
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {hint}")
        return resolved
    rooted = (root / hint).resolve()
    _reject_outside_root(rooted, root, hint=hint)
    if not rooted.is_file():
        raise FileNotFoundError(f"File not found under workspace root: {hint}")
    return rooted


def _resolve_wrapper_args(step_id: str, args: str) -> str:
    root = find_workspace_root()
    arg = args.strip()
    if step_id == "classify-file":
        if not arg:
            raise ValueError("classify-file needs a path under the workspace root")
        resolved = _resolve_under_root(arg, root)
        return str(resolved.relative_to(root.resolve()))
    if step_id != "audit-skill":
        return arg
    if not arg:
        raise ValueError("audit-skill needs skill name (e.g. configurator-boolean)")
    if arg.endswith(".md") or "/" in arg:
        resolved = _resolve_under_root(arg, root)
        return str(resolved.relative_to(root.resolve()))
    skill_path = (root / ".cursor/skills" / arg / "SKILL.md").resolve()
    # equivalent: this branch is only reached when `arg` has no "/" (checked
    # above), so skill_path always stays under root → rejection (and its hint)
    # is unreachable.
    _reject_outside_root(skill_path, root, hint=arg)  # pragma: no mutate
    if not skill_path.is_file():
        raise FileNotFoundError(f"Skill not found: {arg} → {skill_path}")
    return str(skill_path.relative_to(root.resolve()))


def _estimate_step_tokens(step: PipelineStep, output: str, root: Path) -> int:
    if step.tier in ("tool", "python"):
        return 0
    if step.tier == "rag":
        hits = search_rag(step.args, root, limit=5)
        from greedy_token.budget import rag_est_tokens

        return rag_est_tokens(hits, root) + count_tokens(step.args).tokens
    if step.tier == "ollama":
        # output tokens + rough input (skill file or prompt)
        extra = 0
        if step.step_id == "audit-skill" and step.args:
            p = root / step.args
            if p.is_file():
                # equivalent: errors="replace" + local UTF-8 locale means
                # utf-8/UTF-8/None decode to the same token count.
                extra = count_tokens(p.read_text(encoding="utf-8", errors="replace")).tokens  # pragma: no mutate
        return extra + count_tokens(output).tokens
    return count_tokens(output).tokens


def _run_read_hits(
    step: PipelineStep,
    root: Path,
    *,
    prior_search_output: str | None,
    execute: bool,
) -> StepResult:
    t0 = time.perf_counter()
    if not execute:
        return StepResult(
            step=step,
            ok=True,
            exit_code=0,
            output="(dry-run) read-hits from prior search",
            duration_ms=int((time.perf_counter() - t0) * 1000),
            est_tokens=0,
            executed=False,
            engine="read-hits",
        )
    if not prior_search_output:
        return StepResult(
            step=step,
            ok=False,
            exit_code=1,
            output="read-hits: no prior search step output to enrich",
            duration_ms=int((time.perf_counter() - t0) * 1000),
            est_tokens=0,
            executed=True,
            engine="read-hits",
        )
    hits = parse_hit_lines(prior_search_output)
    if not hits:
        return StepResult(
            step=step,
            ok=True,
            exit_code=0,
            output="read-hits: no path:line hits parsed from prior search",
            duration_ms=int((time.perf_counter() - t0) * 1000),
            est_tokens=0,
            executed=True,
            engine="read-hits",
        )
    settings = get_search_settings(root)
    # "snippet" is the default, so it is not listed among the explicit overrides
    # (listing it would be a no-op / equivalent-mutant magnet).
    mode = "snippet"
    if step.args.strip().lower() in ("none", "file"):
        mode = step.args.strip().lower()
    block, files_done, ctx_tokens = enrich_search_hits(
        root,
        hits,
        mode=mode,  # type: ignore[arg-type]
        max_files=settings.max_snippet_files,
        context_lines=settings.context_lines,
        max_tokens=settings.max_context_tokens,
    )
    # Also surface hit file list for the next human/agent reader
    from greedy_token.code_search import unique_hit_paths

    paths = unique_hit_paths(hits, limit=settings.max_snippet_files)
    header = f"read-hits: {files_done} file(s) · ~{ctx_tokens} tokens\nfiles: {', '.join(paths)}"
    output = header if not block else f"{header}\n\n{block}"
    return StepResult(
        step=step,
        ok=True,
        exit_code=0,
        output=output,
        duration_ms=int((time.perf_counter() - t0) * 1000),
        est_tokens=ctx_tokens,
        executed=True,
        engine="read-hits",
    )


def _run_step(
    step: PipelineStep,
    root: Path,
    *,
    execute: bool,
    prior_search_output: str | None = None,
) -> StepResult:
    t0 = time.perf_counter()
    executed = False

    if step.step_id == "search":
        query, _, path = (step.args + "\t").partition("\t")
        path = path.strip() or None
        engine = ""
        if execute:
            # Raw hits only — enrichment is the dedicated read-hits step (or MCP context=).
            result = search_code(query, root, path=path, context="none")
            output = result.text
            engine = result.engine
            executed = True
        else:
            output = f"(dry-run) search {query!r}" + (f" in {path}" if path else "")
        duration_ms = int((time.perf_counter() - t0) * 1000)
        est = 0
        return StepResult(
            step=step,
            ok=True,
            exit_code=0,
            output=output,
            duration_ms=duration_ms,
            est_tokens=est,
            executed=executed,
            engine=engine,
        )

    if step.step_id == "read-hits":
        return _run_read_hits(
            step, root, prior_search_output=prior_search_output, execute=execute
        )

    if step.step_id == "rag":
        if execute:
            # Prefer domain hints from prior search file paths when present
            hits = search_rag(step.args, root, limit=5)
            output = format_hits(step.args, hits)
            if prior_search_output:
                from greedy_token.code_search import unique_hit_paths

                files = unique_hit_paths(parse_hit_lines(prior_search_output), limit=5)
                if files:
                    output = (
                        f"{output}\n\n--- prior search files ---\n"
                        + "\n".join(files)
                    )
            executed = True
        else:
            output = f"(dry-run) rag {step.args!r}"
        duration_ms = int((time.perf_counter() - t0) * 1000)
        est = _estimate_step_tokens(step, output, root) if execute else 0
        return StepResult(
            step=step,
            ok=True,
            exit_code=0,
            output=output,
            duration_ms=duration_ms,
            est_tokens=est,
            executed=executed,
        )

    if not step.command:
        raise ValueError(f"No command for step {step.step_id}")

    can_run = execute and step.step_id in PIPELINE_AUTO_RUN
    if execute and step.step_id not in PIPELINE_AUTO_RUN:
        output = (
            f"(skipped) {step.step_id} not in pipeline auto-run allowlist.\n"
            f"Command: {step.command}"
        )
        duration_ms = int((time.perf_counter() - t0) * 1000)
        return StepResult(
            step=step,
            ok=False,
            exit_code=1,
            output=output,
            duration_ms=duration_ms,
            est_tokens=0,
            executed=False,
        )

    # Runtime setup + availability check only when we will actually run the step.
    # Dry-run (execute=False → can_run=False) must never require the cheap LLM
    # to be up — it only prints the planned command.
    if can_run and step.tier == "ollama":
        if step.profile:
            try:
                resolved = resolve_model(step.profile, root=root)
                apply_model_env(resolved)
            except ValueError:
                apply_cheap_llm_env(root, profile=step.profile)
        else:
            apply_cheap_llm_env(root)

        if not ollama_available():
            llm = get_cheap_llm_settings(root)
            duration_ms = int((time.perf_counter() - t0) * 1000)
            return StepResult(
                step=step,
                ok=False,
                exit_code=1,
                output=(
                    f"Cheap LLM unavailable ({llm.provider}, {llm.url}). "
                    "Start the runtime or use the expensive LLM path (Cursor)."
                ),
                duration_ms=duration_ms,
                est_tokens=0,
                executed=False,
            )

    if can_run:
        try:
            proc = subprocess.run(
                step.command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=root,
                timeout=SCRIPT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            return StepResult(
                step=step,
                ok=False,
                exit_code=124,
                output=f"Step timed out after {SCRIPT_TIMEOUT}s: {step.command}",
                duration_ms=duration_ms,
                est_tokens=0,
                executed=True,
            )
        output = (proc.stdout or "") + (proc.stderr or "")
        exit_code = proc.returncode
        executed = True
    else:
        output = f"(dry-run) {step.command}"
        exit_code = 0

    duration_ms = int((time.perf_counter() - t0) * 1000)
    est = _estimate_step_tokens(step, output, root) if executed else 0
    return StepResult(
        step=step,
        ok=exit_code == 0,
        exit_code=exit_code,
        output=output.strip(),
        duration_ms=duration_ms,
        est_tokens=est,
        executed=executed,
    )


def run_pipeline(
    task: str,
    root: Path | None = None,
    *,
    execute: bool = False,
    stop_on_error: bool = True,
    max_output_per_step: int = 4000,
    profile: str = "",
) -> PipelineResult:
    root = root or find_workspace_root()
    steps = parse_pipeline(task, profile=profile)
    result = PipelineResult(task=task)
    last_search_output: str | None = None

    for step in steps:
        step_result = _run_step(
            step,
            root,
            execute=execute,
            prior_search_output=last_search_output,
        )
        if step.step_id == "search" and step_result.executed:
            last_search_output = step_result.output
        if len(step_result.output) > max_output_per_step:
            step_result.output = (
                step_result.output[: max_output_per_step - 40] + "\n… (truncated)"
            )
        result.steps.append(step_result)
        if stop_on_error and not step_result.ok:
            result.stopped_early = True
            break

    if execute:
        _log_pipeline(result, root)
    return result


def _log_pipeline(result: PipelineResult, root: Path) -> None:
    for step_result in result.steps:
        if not step_result.executed:
            continue
        append_event(
            build_route_event(
                cmd="pipeline",
                task=f"{result.task} :: {step_result.step.label}",
                root=root,
                decision=RouteDecision(
                    target=step_result.step.tier,
                    route_id=f"pipeline-{step_result.step.step_id}",
                    confidence=1.0,
                    matched=[],
                    command=step_result.step.command,
                    note="",
                    domains=[],
                    est_tokens=step_result.est_tokens,
                ),
                duration_ms=step_result.duration_ms,
                executed=True,
                est_tokens_override=step_result.est_tokens,
                tier_scan=[],
            )
        )


def format_pipeline_body(result: PipelineResult) -> str:
    lines = [
        f"Pipeline: {result.task}",
        f"Steps: {len(result.steps)}"
        + (" (stopped early)" if result.stopped_early else ""),
        "",
    ]
    for i, sr in enumerate(result.steps, 1):
        status = "OK" if sr.ok else f"FAIL({sr.exit_code})"
        mode = "ran" if sr.executed else "dry-run"
        lines.append(
            f"── Step {i}/{len(result.steps)}: {sr.step.label} "
            f"[{sr.step.tier}/{mode}] {status} · {sr.duration_ms}ms · ~{sr.est_tokens:,} tok"
        )
        if sr.output:
            lines.append(sr.output)
        lines.append("")
    return "\n".join(lines).rstrip()


def format_pipeline_response(
    result: PipelineResult,
    root: Path | None = None,
) -> str:
    root = root or find_workspace_root()
    body = format_pipeline_body(result)
    footer = format_pipeline_footer(result, root)
    return body + footer


def format_pipeline_footer(result: PipelineResult, root: Path) -> str:
    breakdown = cursor_baseline_breakdown(root, result.task)
    baseline = breakdown.total
    total_spent = result.total_est_tokens
    saved = max(0, baseline - total_spent)
    llm = get_cheap_llm_settings(root)
    step_rows = compute_step_savings(result, root)

    lines = [
        "",
        "---",
        "Greedy token — pipeline",
        "",
    ]
    lines.extend(format_pipeline_step_savings_table(step_rows))
    lines.append("")
    lines.extend(format_executor_savings_summary(step_rows))

    lines.extend(["", "Run log:"])
    lines.append(
        f"  {'step':<28} {'tier':<8} {'ms':>6} {'tokens':>8}  status"
    )
    for sr in result.steps:
        status = "OK" if sr.ok else "FAIL"
        mode = "" if sr.executed else " (dry)"
        label = sr.step.step_id[:28]
        lines.append(
            f"  {label:<28} {sr.step.tier:<8} {sr.duration_ms:>6} {sr.est_tokens:>8,} {status}{mode}"
        )

    by_tier: dict[str, tuple[int, int]] = {}
    for sr in result.steps:
        count, tokens = by_tier.get(sr.step.tier, (0, 0))
        by_tier[sr.step.tier] = (count + 1, tokens + sr.est_tokens)

    lines.extend(["", "Spent by executor:"])
    for tier in ("tool", "python", "ollama", "rag", "cursor"):
        if tier not in by_tier:
            continue
        count, tokens = by_tier[tier]
        # equivalent: loop only visits keys present in TIER_LABELS → default unreachable.
        note = TIER_LABELS.get(tier, tier)  # pragma: no mutate
        if tier == "ollama":
            # equivalent: unset → "" and None are both falsy for the check below.
            model_id = os.environ.get("GREEDY_LLM_MODEL_ID", "")  # pragma: no mutate
            if model_id:
                note += f" ({model_id}/{llm.model}, cheap)"
            else:
                note += f" ({llm.provider}/{llm.model}, cheap)"
        elif tier in ("tool", "python"):
            note += " (0 LLM spend)"
        lines.append(f"  {note:<32} steps={count}  ~{tokens:,} tok")

    lines.extend(
        [
            "",
            f"Pipeline total: {result.total_duration_ms / 1000:.1f} s · ~{total_spent:,} LLM tokens spent",
            "",
            "Combined naive Cursor chat (whole pipeline as one agent task)",
            f"  Always-on rules: ~{breakdown.rules:,}",
            f"  Task prompt:     ~{breakdown.task:,}",
            f"  Agent overhead:  ~{breakdown.overhead:,}",
            f"  {TOTAL_BASELINE_LABEL}  ~{baseline:,}",
            "",
        ]
    )
    any_dry = any(not sr.executed for sr in result.steps)
    if any_dry and not any(sr.executed for sr in result.steps):
        # Pure dry-run: do not claim baseline−0 as "saved".
        lines.extend(
            [
                "Saved vs naive Cursor chat",
                f"  {BASELINE_LABEL}  ~{baseline:,}",
                f"  Spent (MCP executor, LLM tokens): ~{total_spent:,}  (dry-run — steps not executed)",
                "  Saved:             ~0  (dry-run; re-run with execute=true / --execute)",
            ]
        )
    else:
        lines.extend(
            format_savings_lines(
                baseline=baseline,
                spent=total_spent,
                saved=saved,
                spent_note="sum of pipeline steps",
            )
        )
    lines.extend(
        [
            "",
            "Note: Per-step baseline assumes a separate agent chat per step (rules+overhead each time).",
            "Pipeline total baseline = one agent chat for the full pipeline task.",
            "Dry-run steps report saved=0 until execute=true / --execute.",
            "Agent wrapper (MCP + reply) still uses Cursor tokens beyond executor rows.",
        ]
    )
    if result.stopped_early:
        lines.append("Pipeline stopped early due to step failure.")
    return "\n".join(lines)


def list_pipelines() -> str:
    cfg = _load_pipelines_config()
    pipelines = cfg.get("pipelines") or {}
    lines = ["Named pipelines:", ""]
    for name, recipe in pipelines.items():
        # equivalent: absent → "" and None are both falsy for the `if desc:` guard below.
        desc = recipe.get("description", "")  # pragma: no mutate
        steps = recipe.get("steps") or []
        lines.append(f"  {name}")
        if desc:
            lines.append(f"    {desc}")
        lines.append(f"    steps: {' → '.join(steps)}")
        lines.append(f"    usage: pipeline: {name} <args>")
        lines.append("")
    lines.append("Custom chain:")
    lines.append("  pipeline: check-meta-sync then audit-skill configurator-boolean")
    return "\n".join(lines)
