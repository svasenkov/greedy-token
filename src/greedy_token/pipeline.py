"""Multi-step pipeline: python → ollama → rag with unified stats."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from greedy_token.budget import (
    TOTAL_BASELINE_LABEL,
    TIER_LABELS,
    cursor_baseline,
    cursor_baseline_breakdown,
    format_savings_lines,
    spent_hint,
)
from greedy_token.code_search import search_code
from greedy_token.paths import find_monorepo_root
from greedy_token.rag_search import format_hits, search_rag
from greedy_token.router import RouteDecision
from greedy_token.settings import get_cheap_llm_settings
from greedy_token.tokens import count_tokens
from greedy_token.tool_paths import SCRIPT_TIMEOUT
from greedy_token.usage import append_event, build_route_event
from greedy_token.wrappers import WRAPPERS, ollama_available, resolve_wrapper_command

PIPELINE_SPLIT = re.compile(r"\s+then\s+|\s*→\s*|\s*->\s*|\s*;\s*", re.IGNORECASE)

# Safe to auto-run from MCP (read-only or stdout-only).
PIPELINE_AUTO_RUN = frozenset(
    {
        "check-meta-sync",
        "audit-skill",
        "classify-file",
        "search",
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
        saved = max(0, baseline - spent)
        sub = _executor_sub_for_step(sr)
        billing = spent_hint(sr.step.tier, spent, sub)
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
        label = TIER_LABELS.get(tier, tier)
        lines.append(
            f"  {label:<28} steps={count}  spent ~{spent:,}  saved ~{saved:,}"
        )
    return lines


def _load_pipelines_config() -> dict:
    path = Path(__file__).parent / "config" / "pipelines.yaml"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _split_recipe_params(
    params: list[str], known: list[str]
) -> tuple[list[str], dict[str, str]]:
    """Split recipe args into positionals and key=value for known placeholders.

    Supports both ``pipeline: search-rag baseUrl foo.html`` and
    ``pipeline: search-rag baseUrl path=foo.html`` (agent / rule style).
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


def _expand_named_pipeline(text: str) -> str:
    cfg = _load_pipelines_config()
    pipelines = cfg.get("pipelines") or {}
    tokens = text.split()
    if not tokens:
        return text
    name = tokens[0]
    if name not in pipelines:
        return text
    recipe = pipelines[name]
    steps: list[str] = recipe.get("steps") or []
    known: list[str] = []
    for step_tpl in steps:
        for ph in re.findall(r"\{(\w+)\}", step_tpl):
            if ph not in known:
                known.append(ph)
    positional, kwargs = _split_recipe_params(tokens[1:], known)
    expanded: list[str] = []
    param_i = 0
    global_mapping: dict[str, str] = {}
    for step_tpl in steps:
        if "{" in step_tpl:
            # one placeholder per step: {skill}, {query}, {path}
            placeholders = re.findall(r"\{(\w+)\}", step_tpl)
            mapping: dict[str, str] = {}
            for ph in placeholders:
                if ph in global_mapping:
                    mapping[ph] = global_mapping[ph]
                    continue
                if ph in kwargs:
                    mapping[ph] = kwargs[ph]
                    global_mapping[ph] = kwargs[ph]
                    continue
                if param_i >= len(positional):
                    raise ValueError(
                        f"Pipeline {name!r} needs more args. "
                        f"Usage: pipeline: {name} <{'> <'.join(known)}>"
                        + (f" (or {known[-1]}=…)" if known else "")
                    )
                mapping[ph] = positional[param_i]
                global_mapping[ph] = positional[param_i]
                param_i += 1
            expanded.append(step_tpl.format(**mapping))
        else:
            expanded.append(step_tpl)
    if param_i < len(positional):
        extra = " ".join(positional[param_i:])
        raise ValueError(
            f"Pipeline {name!r} got unexpected extra args: {extra!r}. "
            f"Usage: pipeline: {name} <{'> <'.join(known)}>"
            + (f" (or {known[-1]}=…)" if known else "")
        )
    return " then ".join(expanded)


def parse_pipeline(task: str) -> list[PipelineStep]:
    text = task.strip()
    if text.lower().startswith("pipeline:"):
        text = text.split(":", 1)[1].strip()
    text = _expand_named_pipeline(text)
    segments = [s.strip() for s in PIPELINE_SPLIT.split(text) if s.strip()]
    if not segments:
        raise ValueError("Empty pipeline. Example: check-meta-sync then audit-skill configurator-boolean")
    return [_parse_segment(seg) for seg in segments]


def _parse_segment(segment: str) -> PipelineStep:
    segment = segment.strip()
    if segment.startswith("search "):
        return _parse_search_segment(segment)
    if segment.startswith("rag "):
        query = segment[4:].strip()
        return PipelineStep(
            step_id="rag",
            tier="rag",
            label=f"rag: {query}",
            args=query,
        )
    parts = segment.split(maxsplit=1)
    step_id = parts[0]
    args = parts[1] if len(parts) > 1 else ""
    if step_id not in WRAPPERS:
        raise ValueError(
            f"Unknown step {step_id!r}. Known: {', '.join(sorted(WRAPPERS))}, search, rag"
        )
    wrapper = WRAPPERS[step_id]
    tier = "ollama" if wrapper.requires_ollama else "python"
    resolved_args = _resolve_wrapper_args(step_id, args)
    root = find_monorepo_root()
    command = resolve_wrapper_command(step_id, root, extra_args=resolved_args)
    return PipelineStep(
        step_id=step_id,
        tier=tier,
        label=f"{step_id} {resolved_args}".strip(),
        command=command,
        args=resolved_args,
    )


def _parse_search_segment(segment: str) -> PipelineStep:
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
    )


def _resolve_wrapper_args(step_id: str, args: str) -> str:
    root = find_monorepo_root()
    arg = args.strip()
    if step_id != "audit-skill":
        return arg
    if not arg:
        raise ValueError("audit-skill needs skill name (e.g. configurator-boolean)")
    if arg.endswith(".md") or "/" in arg:
        path = Path(arg)
        if not path.is_file():
            path = root / arg
        if not path.is_file():
            raise FileNotFoundError(f"SKILL.md not found: {arg}")
        return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    skill_path = root / ".cursor/skills" / arg / "SKILL.md"
    if not skill_path.is_file():
        raise FileNotFoundError(f"Skill not found: {arg} → {skill_path}")
    return str(skill_path.relative_to(root))


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
                extra = count_tokens(p.read_text(encoding="utf-8", errors="replace")).tokens
        return extra + count_tokens(output).tokens
    return count_tokens(output).tokens


def _run_step(step: PipelineStep, root: Path, *, execute: bool) -> StepResult:
    t0 = time.perf_counter()
    executed = False
    output = ""
    exit_code = 0

    if step.step_id == "search":
        query, _, path = (step.args + "\t").partition("\t")
        path = path.strip() or None
        engine = ""
        if execute:
            result = search_code(query, root, path=path)
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

    if step.step_id == "rag":
        if execute:
            hits = search_rag(step.args, root, limit=5)
            output = format_hits(step.args, hits)
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
    wrapper = WRAPPERS[step.step_id]
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

    if step.tier == "ollama" and not ollama_available():
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
) -> PipelineResult:
    root = root or find_monorepo_root()
    steps = parse_pipeline(task)
    result = PipelineResult(task=task)

    for step in steps:
        step_result = _run_step(step, root, execute=execute)
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
    root = root or find_monorepo_root()
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
        note = TIER_LABELS.get(tier, tier)
        if tier == "ollama":
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
        desc = recipe.get("description", "")
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
