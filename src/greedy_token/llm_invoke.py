"""Profile-based LLM invoke with optional escalation — library + CLI backend."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from greedy_token.expensive_llm import llm_chat
from greedy_token.model_select import (
    ResolvedModel,
    apply_model_env,
    escalation_chain_from,
    get_llm_registry,
    resolve_model,
)
from greedy_token.spend_guard import check_expensive_allowed, estimate_cost_usd
from greedy_token.tokens import count_tokens
from greedy_token.usage import append_event, build_route_event
from greedy_token.router import RouteDecision


@dataclass
class InvokeResult:
    text: str
    model_id: str
    profile: str
    tier_billing: str
    escalated_from: str = ""
    eval_tokens: int | None = None
    cost_usd: float = 0.0
    duration_ms: int = 0
    attempts: list[str] = field(default_factory=list)


def _output_weak(text: str, *, min_len: int = 8) -> bool:
    stripped = text.strip()
    if len(stripped) < min_len:
        return True
    if stripped.lower() in ("null", "none", "n/a", "error"):
        return True
    return False


def _json_parse_fail(text: str) -> bool:
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return False
    try:
        json.loads(stripped)
        return False
    except json.JSONDecodeError:
        return True


def _should_escalate(
    text: str,
    *,
    profile: str,
    triggers: tuple[str, ...],
) -> bool:
    if profile.endswith(":escalate"):
        return "explicit_profile" in triggers
    if "empty_output" in triggers and _output_weak(text):
        return True
    if "json_parse_fail" in triggers and _json_parse_fail(text):
        return True
    if "low_confidence" in triggers:
        if re.search(r"\b(unsure|unknown|cannot determine)\b", text, re.I):
            return True
    return False


def invoke_profile(
    profile: str,
    *,
    system: str,
    user: str,
    root: Path | None = None,
    tags: dict[str, str] | None = None,
    allow_escalate: bool = True,
    allow_expensive: bool = False,
    timeout: float = 120.0,
    log: bool = True,
) -> InvokeResult:
    """Run LLM for *profile* with optional escalation chain."""
    t0 = time.perf_counter()
    tags = tags or {}
    current = resolve_model(profile, root=root)
    attempts: list[str] = []
    escalated_from = ""
    last_error = ""

    candidates: list[ResolvedModel] = [current]
    if allow_escalate:
        candidates.extend(escalation_chain_from(current, root=root))

    text = ""
    eval_tokens: int | None = None
    used: ResolvedModel = current

    for candidate in candidates:
        attempts.append(candidate.model_id)
        if candidate.billing_tier == "expensive":
            est = estimate_cost_usd(candidate.spec, count_tokens(user).tokens + count_tokens(system).tokens)
            decision = check_expensive_allowed(
                candidate.spec,
                root=root,
                cli_allow=allow_expensive,
                est_cost_usd=est,
            )
            if not decision.allowed:
                last_error = decision.reason
                continue

        apply_model_env(candidate)
        try:
            text, eval_tokens = llm_chat(
                candidate,
                system=system,
                user=user,
                timeout=timeout,
            )
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            last_error = str(exc)
            continue

        used = candidate
        if candidate.model_id != current.model_id:
            escalated_from = current.model_id

        registry = get_llm_registry(root)
        if allow_escalate and candidate == current and _should_escalate(
            text,
            profile=profile,
            triggers=registry.escalation.triggers,
        ):
            continue
        break
    else:
        msg = last_error or "all models in escalation chain failed"
        raise RuntimeError(f"LLM invoke failed for profile {profile!r}: {msg}")

    cost = estimate_cost_usd(used.spec, eval_tokens)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    est_tokens = (eval_tokens or 0) + count_tokens(system + user).tokens

    result = InvokeResult(
        text=text,
        model_id=used.model_id,
        profile=profile,
        tier_billing=used.billing_tier,
        escalated_from=escalated_from,
        eval_tokens=eval_tokens,
        cost_usd=cost,
        duration_ms=duration_ms,
        attempts=attempts,
    )

    if log:
        task = f"llm invoke {profile}"
        tier = "ollama" if result.tier_billing == "cheap" else "cursor"
        decision = RouteDecision(
            target=tier,
            route_id=f"llm-{profile}",
            confidence=1.0,
            matched=[profile],
            command=None,
            note="",
            domains=[],
            est_tokens=est_tokens,
        )
        append_event(
            build_route_event(
                cmd="llm",
                task=task,
                root=root or Path("."),
                decision=decision,
                est_tokens_override=est_tokens,
                duration_ms=result.duration_ms,
                executed=True,
                llm_tags=tags,
                model_id=result.model_id,
                profile=profile,
                escalated_from=result.escalated_from or None,
                billing_tier=result.tier_billing,
                cost_usd=result.cost_usd,
            )
        )
    return result


def invoke_result_to_dict(result: InvokeResult) -> dict[str, Any]:
    return {
        "ok": True,
        "text": result.text,
        "model_id": result.model_id,
        "profile": result.profile,
        "tier_billing": result.tier_billing,
        "escalated_from": result.escalated_from or None,
        "eval_tokens": result.eval_tokens,
        "cost_usd": result.cost_usd,
        "duration_ms": result.duration_ms,
        "attempts": result.attempts,
    }
