from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

# BASE_CURSOR_OVERHEAD re-exported for backward compatibility; the resolved
# overhead (calibrated config → default-estimate) comes from cursor_overhead().
from greedy_token.baseline import (  # noqa: F401
    BASE_CURSOR_OVERHEAD,
    baseline_source,
    cursor_overhead,
    uncalibrated_nudge,
)
from greedy_token.calibration import (
    SOURCE_CALIBRATED,
    SOURCE_FORMULA,
    confidence_for_score,
)
from greedy_token.paths import find_workspace_root, load_routes_config
from greedy_token.tokens import count_tokens
from greedy_token.tool_paths import rg_path_for_shell, root_cd_prefix, sh_quote
from greedy_token.wrappers import ollama_available, wrapper_for_command

TIER_ORDER = ("tool", "python", "ollama", "rag", "cursor")

COMPLEXITY_BY_TARGET = {
    "tool": "low",
    "python": "low",
    "ollama": "medium",
    "rag": "low",
    "cursor": "high",
}
# Rough fallback when docs/rag index is empty / unavailable (route pre-flight only).
RAG_READ_TOKENS_FALLBACK = 1800

# Edit / wiring verbs — tool/rag alone is thin context → lower confidence for hook gate.
EDIT_VERBS = re.compile(
    r"\b(implement|refactor|fix|add|wiring|migrate|patch|rewrite|"
    r"почини|исправь|добавь|рефактор|внедри|сделай)\b",
    re.IGNORECASE,
)
THIN_CONTEXT_NOTE = "thin context for edit task — prefer cursor: + pin files"


def has_edit_verbs(task: str) -> bool:
    # equivalent: the "" default only applies to a falsy task, and neither "" nor
    # any junk-string default matches an edit verb → result is False either way.
    return bool(EDIT_VERBS.search(task or ""))  # pragma: no mutate


@dataclass
class RouteDecision:
    target: str
    route_id: str
    confidence: float
    matched: list[str]
    command: str | None
    note: str
    domains: list[str]
    complexity: str = "medium"
    est_tokens: int = 0
    rationale: str = ""
    read_only: bool = False
    tool: str | None = None
    shadow_route_id: str | None = None
    raw_score: float = 0.0
    confidence_source: str = SOURCE_FORMULA
    calibration_n: int = 0


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _score_patterns(text: str, patterns: list[str]) -> tuple[float, list[str]]:
    matched: list[str] = []
    score = 0.0
    for pat in patterns:
        p = pat.lower()
        if p in text:
            matched.append(pat)
            score += 1.0 + min(len(p) / 20.0, 2.0)
    return score, matched


def _parse_shadow_until(route: dict) -> datetime | None:
    # equivalent: any non-empty junk default parses to the same None result.
    shadow_until = str(route.get("shadow_until") or "").strip()  # pragma: no mutate
    if not shadow_until:
        return None
    try:
        # datetime.fromisoformat (Python 3.11+) parses a trailing "Z" natively.
        until = datetime.fromisoformat(shadow_until)
    except ValueError:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until


def _now() -> datetime:
    """Current UTC time. Indirected so shadow-window tests are deterministic."""
    return datetime.now(timezone.utc)


def _route_status(route: dict) -> str:
    """active | shadow | inactive — shadow = log-only match until shadow_until."""
    until = _parse_shadow_until(route)
    if until is not None and _now() < until:
        return "shadow"
    if route.get("enabled") is False:
        return "inactive"
    return "active"


def _route_active(route: dict) -> bool:
    return _route_status(route) == "active"


SEARCH_PREFIXES = (
    r"^find\s+",
    r"^search\s+for\s+",
    r"^grep\s+(for\s+)?",
    r"^where\s+is\s+",
    r"^locate\s+",
    r"^look\s+for\s+",
    r"^rg\s+",
    r"^найди\s+",
    r"^найти\s+",
    r"^где\s+(лежит|находится|файл)\s+",
    r"^покажи\s+где\s+",
)

# Filler stripped from "find X in Y Z" — keep identifiers (baseUrl), drop scope words.
SEARCH_FILLER = frozenset(
    {
        "in",
        "the",
        "a",
        "an",
        "for",
        "from",
        "to",
        "of",
        "and",
        "or",
        "with",
        "about",
        "is",
        "are",
        "all",
        "any",
        "config",
        "configuration",
        "configurator",
        "project",
        "code",
        "file",
        "files",
        "repo",
        "workspace",
        "where",
        "в",
        "для",
        "из",
        "по",
        "где",
        "найди",
        "найти",
    }
)


def _strip_search_prefix(text: str) -> str:
    for prefix in SEARCH_PREFIXES:
        m = re.match(prefix, text, flags=re.IGNORECASE)
        if m:
            return text[m.end() :].strip()
    return text.strip()


def _score_search_token(token: str) -> float:
    score = min(len(token), 24)
    if re.search(r"[a-z][A-Z]", token):
        score += 12
    if token.isupper() and len(token) > 2:
        score += 6
    if any(ch in token for ch in ".-_/"):
        score += 4
    if token.isdigit():
        score -= 8
    return score


def _extract_search_query(task: str) -> str:
    text = _strip_search_prefix(task.strip())
    if not text:
        return task.strip()

    quoted = re.findall(r'["\']([^"\']+)["\']', text)
    if quoted:
        return quoted[0].strip()

    text = text.strip('"').strip("'")

    candidates: list[tuple[float, str]] = []
    for token in re.findall(r"[\w@./:-]+", text):
        key = token.lower()
        if key in SEARCH_FILLER or len(token) < 2:
            continue
        candidates.append((_score_search_token(token), token))

    if candidates:
        # equivalent: .lower()/.upper() induce the same tie-break ordering.
        candidates.sort(key=lambda item: (-item[0], -len(item[1]), item[1].lower()))  # pragma: no mutate
        return candidates[0][1]

    return text


def _build_tool_command(route: dict, task: str, root: Path) -> str:
    # equivalent: the "rg" default is only compared against "jq" below, so any
    # non-"jq" default routes to the same ripgrep branch.
    tool = (route.get("tool") or "rg").lower()  # pragma: no mutate
    query = _extract_search_query(task)
    if tool == "jq":
        path_hint = route.get("json_path") or "docs/phase-manifest.json"
        return (
            f"{root_cd_prefix(root)} jq -r {sh_quote(route.get('jq_filter', '.'))} "
            f"{sh_quote(path_hint)}"
        )
    globs = route.get("globs") or [
        "!.git/**",
        "!node_modules/**",
        "!build/**",
        "!.venv/**",
        "!.cursor/hooks/**",
    ]
    search_paths = route.get("search_paths") or ["."]
    max_count = route.get("max_count", 50)
    glob_flags = " ".join(f"-g {sh_quote(g)}" for g in globs)
    paths = " ".join(search_paths)
    return (
        f"{root_cd_prefix(root)} {rg_path_for_shell()} -n --max-columns 200 -F {sh_quote(query)} "
        f"{glob_flags} --max-count {max_count} {paths}"
    )


def _token_estimate_for_route(
    target: str,
    *,
    task: str,
    root: Path,
) -> tuple[str, int, str]:
    task_tokens = count_tokens(task).tokens
    complexity = COMPLEXITY_BY_TARGET.get(target, "medium")

    if target == "tool":
        return (
            complexity,
            0,
            "Mechanical search — ripgrep/jq, zero LLM tokens.",
        )
    if target == "python":
        return (
            complexity,
            0,
            "Deterministic shell/Python script — no agent context.",
        )
    if target == "ollama":
        if ollama_available():
            # Local/cheap LLM still spends tokens (not Cursor API $).
            return (
                complexity,
                max(task_tokens, 1),
                "Cheap LLM — bulk work off expensive path; local/cheap spend.",
            )
        return (
            "medium",
            task_tokens + cursor_overhead(),
            "Cheap LLM unavailable — would fall back to expensive Cursor path.",
        )
    if target == "rag":
        from greedy_token.budget import rag_est_tokens
        from greedy_token.rag_search import search_rag

        hits = search_rag(task, root, limit=5)
        rag_tokens = rag_est_tokens(hits, root) if hits else RAG_READ_TOKENS_FALLBACK
        return (
            complexity,
            rag_tokens + task_tokens,
            "Read docs/rag chunk(s) — small context vs full agent chat.",
        )

    from greedy_token.context_audit import audit_context

    rules_tokens = sum(i.estimate.tokens for i in audit_context(root) if i.always_on)
    return (
        complexity,
        rules_tokens + task_tokens + cursor_overhead(),
        "Wiring/architecture — requires expensive LLM (Cursor agent chat with rules context).",
    )


def _decision_from_route(
    route: dict,
    *,
    score: float,
    matched: list[str],
    task: str,
    root: Path,
) -> RouteDecision:
    target = route["target"]
    # Telemetry-calibrated when the score bucket has enough events, else the
    # legacy formula min(0.95, 0.45 + score * 0.12) marked "uncalibrated".
    calibration = confidence_for_score(score)
    confidence = calibration.confidence
    # Tool routes are forced read-only below; for every other tier the default is
    # False, so a literal False here is equivalent to `target == "tool"`.
    # equivalent: bool() of the missing-key default is False whether it is False/None/absent.
    read_only = bool(route.get("read_only", False))  # pragma: no mutate
    command = route.get("command")
    if target == "tool":
        command = _build_tool_command(route, task, root)
        read_only = True

    complexity, est_tokens, rationale = _token_estimate_for_route(
        target,
        task=task,
        root=root,
    )
    note = (route.get("note") or "").strip()
    if note and note not in rationale:
        rationale = f"{rationale} {note}".strip()

    wrapper = wrapper_for_command(route.get("command"))
    if wrapper and wrapper.requires_ollama and not ollama_available():
        rationale = (
            f"{rationale} Ollama optional but currently unavailable."
        ).strip()

    return RouteDecision(
        target=target,
        route_id=route["id"],
        confidence=confidence,
        matched=matched,
        command=command,
        note=note,
        domains=route.get("domains") or [],
        complexity=complexity,
        est_tokens=est_tokens,
        rationale=rationale,
        read_only=read_only,
        tool=route.get("tool"),
        raw_score=score,
        confidence_source=calibration.source,
        calibration_n=calibration.n,
    )


def _best_in_tier(routes: list[dict], text: str, task: str, root: Path) -> RouteDecision | None:
    best: RouteDecision | None = None
    best_score = 0.0
    for route in routes:
        if not _route_active(route):
            continue
        score, matched = _score_patterns(text, route.get("patterns", []))
        # equivalent: scores are never negative, and a 0 score never beats best_score.
        if score <= 0:  # pragma: no mutate
            continue
        decision = _decision_from_route(route, score=score, matched=matched, task=task, root=root)
        if score > best_score:
            best = decision
            best_score = score
    return best


def _best_shadow_match(routes: list[dict], text: str) -> tuple[str | None, float]:
    best_id: str | None = None
    best_score = 0.0
    for route in routes:
        if _route_status(route) != "shadow":
            continue
        score, _matched = _score_patterns(text, route.get("patterns", []))
        if score > best_score:
            best_score = score
            best_id = route.get("id")
    return best_id, best_score


def _with_shadow(decision: RouteDecision, shadow_route_id: str | None) -> RouteDecision:
    if not shadow_route_id or decision.shadow_route_id == shadow_route_id:
        return decision
    return replace(decision, shadow_route_id=shadow_route_id)


def route_task_all_tiers(task: str, root: Path | None = None) -> list[tuple[str, RouteDecision]]:
    root = root or find_workspace_root()
    cfg = load_routes_config()
    text = _normalize(task)
    all_routes = cfg.get("routes", [])
    shadow_id, _ = _best_shadow_match(all_routes, text)
    results: list[tuple[str, RouteDecision]] = []

    for tier in TIER_ORDER:
        tier_routes = [r for r in all_routes if r.get("target") == tier]
        best = _best_in_tier(tier_routes, text, task, root)
        if best:
            results.append((tier, _with_shadow(best, shadow_id)))
        else:
            fallback = _fallback_for_tier(tier, task, root, cfg)
            results.append((tier, _with_shadow(fallback, shadow_id)))
    return results


def _fallback_for_tier(tier: str, task: str, root: Path, cfg: dict) -> RouteDecision:
    complexity, est_tokens, rationale = _token_estimate_for_route(
        tier,
        task=task,
        root=root,
    )
    if tier == "cursor":
        fb = cfg.get("cursor_fallback", {})
        rationale = (fb.get("message") or rationale).strip().split("\n")[0]
        return RouteDecision(
            target="cursor",
            route_id="cursor-fallback",
            confidence=0.35,
            matched=[],
            command=None,
            note="",
            domains=[],
            complexity=complexity,
            est_tokens=est_tokens,
            rationale=rationale,
        )
    return RouteDecision(
        target=tier,
        route_id=f"{tier}-none",
        confidence=0.0,
        matched=[],
        command=None,
        note="",
        domains=[],
        complexity=complexity,
        est_tokens=est_tokens,
        rationale="No pattern match in tier.",
    )


def _apply_thin_context_penalty(decision: RouteDecision, task: str) -> RouteDecision:
    """Lower confidence when cheap tier meets edit verbs (hook may skip intercept)."""
    if decision.target not in ("tool", "rag"):
        return decision
    if not has_edit_verbs(task):
        return decision
    new_conf = max(0.15, decision.confidence - 0.35)
    note = decision.note
    if THIN_CONTEXT_NOTE not in note:
        note = f"{note}; {THIN_CONTEXT_NOTE}".strip("; ").strip()
    rationale = decision.rationale
    if THIN_CONTEXT_NOTE not in rationale:
        rationale = f"{rationale} {THIN_CONTEXT_NOTE}".strip()
    return replace(decision, confidence=new_conf, note=note, rationale=rationale)


def route_task(task: str, root: Path | None = None) -> RouteDecision:
    root = root or find_workspace_root()
    cfg = load_routes_config()
    text = _normalize(task)
    all_routes = cfg.get("routes", [])
    shadow_id, _ = _best_shadow_match(all_routes, text)

    for tier in TIER_ORDER:
        tier_routes = [r for r in all_routes if r.get("target") == tier]
        best = _best_in_tier(tier_routes, text, task, root)
        if best:
            if best.target == "ollama" and not ollama_available():
                continue
            from greedy_token.budget_policy import apply_budget_policy

            decided = apply_budget_policy(best, task, root)
            return _with_shadow(_apply_thin_context_penalty(decided, task), shadow_id)

    fb = cfg.get("cursor_fallback", {})
    complexity, est_tokens, rationale = _token_estimate_for_route(
        "cursor",
        task=task,
        root=root,
    )
    message = (fb.get("message") or rationale).strip()
    first_line = message.split("\n")[0]
    return _with_shadow(
        RouteDecision(
            target="cursor",
            route_id="cursor-fallback",
            confidence=0.35,
            matched=[],
            command=None,
            note=message,
            domains=[],
            complexity=complexity,
            est_tokens=est_tokens,
            rationale=first_line,
        ),
        shadow_id,
    )


def _runner_up(task: str, root: Path, selected_target: str) -> tuple[str, RouteDecision] | None:
    """Next-best alternative tier: first matched tier != selected, else cursor fallback."""
    scan = route_task_all_tiers(task, root)
    for tier, decision in scan:
        if tier == selected_target:
            continue
        if decision.matched:
            return tier, decision
    for tier, decision in scan:
        if tier == "cursor" and tier != selected_target:
            return tier, decision
    return None


def confidence_label(decision: RouteDecision) -> str:
    """Where the confidence number comes from: telemetry or the raw formula."""
    if decision.confidence_source == SOURCE_CALIBRATED:
        return f"calibrated (n={decision.calibration_n})"
    return "formula (uncalibrated)"


def explain_route(decision: RouteDecision, task: str, root: Path) -> dict:
    """Structured explainability for a route decision (Phase 2: explainable routing)."""
    if decision.matched:
        reason = f"matched {decision.route_id} on: {', '.join(decision.matched)}"
    elif decision.route_id == "cursor-fallback":
        reason = "no cheaper tier matched — Cursor fallback"
    else:
        reason = decision.rationale or f"{decision.target} tier, no explicit pattern"
    if decision.note.startswith("budget_policy"):
        reason = f"{reason}; {decision.note}"

    try:
        from greedy_token.estimator import cursor_saved_for

        saved_est = cursor_saved_for(root, task, decision.est_tokens, decision.target)
    except (ImportError, OSError, ValueError):
        saved_est = 0

    runner = _runner_up(task, root, decision.target)
    runner_up = None
    if runner is not None:
        tier, alt = runner
        runner_up = {
            "tier": tier,
            "route_id": alt.route_id,
            "est_tokens": alt.est_tokens,
        }

    return {
        "selected_tier": decision.target,
        "route_id": decision.route_id,
        "reason": reason,
        "matched": list(decision.matched),
        "confidence": round(decision.confidence, 4),
        "confidence_source": decision.confidence_source,
        "calibration_n": decision.calibration_n,
        "saved_est": saved_est,
        "runner_up": runner_up,
    }


def format_decision(decision: RouteDecision, task: str, root: Path) -> str:
    lines = [
        f"Task: {task}",
        f"Route: {decision.target.upper()}  ({decision.route_id}, {decision.confidence:.0%})",
        f"Confidence: {decision.confidence:.0%} — {confidence_label(decision)}",
        f"Complexity: {decision.complexity}",
        f"Est. tokens: {decision.est_tokens:,}",
        f"Rationale: {decision.rationale}",
    ]
    if decision.matched:
        lines.append(f"Matched: {', '.join(decision.matched)}")
    if decision.shadow_route_id:
        lines.append(f"Shadow match (log-only): {decision.shadow_route_id}")
    if decision.note and decision.note not in decision.rationale:
        lines.append(f"Note: {decision.note}")
    if decision.command:
        cmd = decision.command
        if not cmd.startswith("cd "):
            cmd = f"{root_cd_prefix(root)} {cmd}"
        lines.append(f"Command: {cmd}")
        if decision.read_only:
            lines.append("Execute: read-only (greedy-token run --execute OK)")
        else:
            lines.append("Execute: not read-only — dry-run only; run script manually")
    if decision.target == "rag" and decision.domains:
        lines.append(f"RAG domains: {', '.join(decision.domains)}")
        lines.append(f"Try: greedy-token rag \"{task}\"")
    if decision.target == "cursor":
        lines.append("→ New Cursor chat; skill from docs/skills-map.md if available.")

    exp = explain_route(decision, task, root)
    lines.append(f"Why: {exp['reason']}")
    if exp["runner_up"]:
        ru = exp["runner_up"]
        lines.append(
            f"Runner-up: {ru['tier'].upper()} ({ru['route_id']}, est ~{ru['est_tokens']:,})"
        )
    if exp["saved_est"]:
        lines.append(
            f"Saved est: ~{exp['saved_est']:,} tokens vs Cursor (baseline: {baseline_source()})"
        )
    nudge = uncalibrated_nudge()
    if nudge:
        lines.append(nudge)
    return "\n".join(lines)
