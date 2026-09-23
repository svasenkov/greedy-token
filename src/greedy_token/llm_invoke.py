"""Profile-based LLM invoke with optional escalation — library + CLI backend."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from greedy_token.calibration import SOURCE_FIXED
from greedy_token.expensive_llm import llm_chat
from greedy_token.model_select import (
    ResolvedModel,
    apply_model_env,
    escalation_chain_from,
    get_llm_registry,
    resolve_model,
)
from greedy_token.result_contract import RESULT_NOT_EVALUATED
from greedy_token.result_gate import evaluate_result_gate
from greedy_token.router import RouteDecision
from greedy_token.spend_guard import check_metered_allowed, estimate_cost_usd
from greedy_token.tokens import count_tokens
from greedy_token.usage import (
    append_event,
    build_outcome_event,
    build_route_event,
    new_operation_id,
)


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


@dataclass
class _ProviderCall:
    """One provider call that returned a response inside an invoke chain."""
    model: ResolvedModel
    eval_tokens: int | None
    cost_usd: float
    duration_ms: int
    served: bool = False


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


def _invoke_tier(resolved: ResolvedModel) -> str:
    return "ollama" if resolved.billing_tier == "cheap" else "cursor"


def _invoke_decision(profile: str, tier: str, est_tokens: int) -> RouteDecision:
    return RouteDecision(
        target=tier,
        route_id=f"llm-{profile}",
        confidence=1.0,
        confidence_source=SOURCE_FIXED,
        matched=[profile],
        command=None,
        note="",
        domains=[],
        est_tokens=est_tokens,
    )


def _log_invoke_events(
    *,
    profile: str,
    system: str,
    user: str,
    root: Path | None,
    tags: dict[str, str],
    operation_id: str,
    parent_operation_id: str | None,
    first: ResolvedModel,
    attempts: list[str],
    calls: list[_ProviderCall],
    duration_ms: int,
    succeeded: bool,
) -> None:
    """Usage records for one invoke operation.

    Spend is a fact of each completed provider call, not of the chain's
    outcome: every call that returned a response gets its own request event
    (own model, billing block, cost, gate verdict), and the operation closes
    with one outcome record under the same operation_id. A chain that dies
    after a paid attempt still owes that cost to the log.
    """
    task = f"llm invoke {profile}"
    effective_root = root or Path(".")
    prompt_tokens = count_tokens(system + user).tokens
    if not calls:
        # Spend denial or provider errors before any response: the operation
        # never started — record the refusal with zero spend.
        tier = _invoke_tier(first)
        gate = evaluate_result_gate(
            started=False,
            result_status=RESULT_NOT_EVALUATED,
            tier=tier,
            ok=False,
        )
        append_event(
            build_route_event(
                cmd="llm",
                task=task,
                root=effective_root,
                decision=_invoke_decision(profile, tier, prompt_tokens),
                est_tokens_override=prompt_tokens,
                executed=False,
                execution_requested=True,
                llm_tags=tags,
                profile=profile,
                billing_tier=first.billing_tier,
                cost_usd=0.0,
                model_billing=first.spec.billing,
                llm_attempts=attempts,
                operation_id=operation_id,
                parent_operation_id=parent_operation_id,
                gate=gate,
            )
        )
    else:
        for call in calls:
            cand = call.model
            tier = _invoke_tier(cand)
            est = (call.eval_tokens or 0) + prompt_tokens
            gate = evaluate_result_gate(
                started=True,
                result_status=RESULT_NOT_EVALUATED,
                tier=tier,
                ok=True,
                output_useful=call.served,
            )
            append_event(
                build_route_event(
                    cmd="llm",
                    task=task,
                    root=effective_root,
                    decision=_invoke_decision(profile, tier, est),
                    est_tokens_override=est,
                    duration_ms=call.duration_ms,
                    executed=True,
                    execution_requested=True,
                    llm_tags=tags,
                    model_id=cand.model_id,
                    profile=profile,
                    escalated_from=(
                        first.model_id if cand.model_id != first.model_id else None
                    ),
                    billing_tier=cand.billing_tier,
                    cost_usd=call.cost_usd,
                    model_billing=cand.spec.billing,
                    llm_attempts=attempts,
                    operation_id=operation_id,
                    parent_operation_id=parent_operation_id,
                    gate=gate,
                )
            )
    outcome_tier = _invoke_tier(calls[-1].model if calls else first)
    append_event(
        build_outcome_event(
            task=task,
            root=effective_root,
            decision=_invoke_decision(profile, outcome_tier, 0),
            outcome="success" if succeeded else "failure",
            layer="executor",
            duration_ms=duration_ms,
            attempts=len(attempts),
            retries=len(attempts) - 1,
            escalations=list(attempts[1:]),
            operation_id=operation_id,
            parent_operation_id=parent_operation_id,
            gate=gate,
        )
    )


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
    parent_operation_id: str | None = None,
    resolved: ResolvedModel | None = None,
) -> InvokeResult:
    """Run LLM for *profile* with optional escalation chain.

    *resolved* brings an already-resolved model (e.g. the doctor benchmark)
    through the same guard → call → telemetry path as a profile-resolved one.
    """
    t0 = time.perf_counter()
    tags = tags or {}
    current = resolved if resolved is not None else resolve_model(profile, root=root)
    attempts: list[str] = []
    escalated_from = ""
    last_error = ""
    operation_id = new_operation_id()

    candidates: list[ResolvedModel] = [current]
    if allow_escalate:
        candidates.extend(escalation_chain_from(current, root=root))

    text = ""
    eval_tokens: int | None = None
    used: ResolvedModel = current
    # Spend accrues for every completed provider call, not only the model that
    # served — an escalated-away attempt still consumed tokens.
    cost = 0.0
    calls: list[_ProviderCall] = []

    for candidate in candidates:
        attempts.append(candidate.model_id)
        if candidate.spec.billing == "metered":
            # ADR-0002: every metered call is spend-guarded — expensive tier
            # keeps the expensive opt-in path, metered cheap needs the
            # metered opt-in; both share the daily/monthly caps.
            est = estimate_cost_usd(
                candidate.spec,
                count_tokens(user).tokens + count_tokens(system).tokens,
            )
            decision = check_metered_allowed(
                candidate.spec,
                root=root,
                cli_allow=allow_expensive,
                est_cost_usd=est,
            )
            if not decision.allowed:
                last_error = decision.reason
                continue

        apply_model_env(candidate)
        call_t0 = time.perf_counter()
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
        call_ms = int((time.perf_counter() - call_t0) * 1000)
        call_cost = estimate_cost_usd(candidate.spec, eval_tokens)
        cost += call_cost
        calls.append(
            _ProviderCall(
                model=candidate,
                eval_tokens=eval_tokens,
                cost_usd=call_cost,
                duration_ms=call_ms,
            )
        )

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
        calls[-1].served = True
        break
    else:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        msg = last_error or "all models in escalation chain failed"
        if log:
            _log_invoke_events(
                profile=profile,
                system=system,
                user=user,
                root=root,
                tags=tags,
                operation_id=operation_id,
                parent_operation_id=parent_operation_id,
                first=current,
                attempts=attempts,
                calls=calls,
                duration_ms=duration_ms,
                succeeded=False,
            )
        raise RuntimeError(f"LLM invoke failed for profile {profile!r}: {msg}")

    duration_ms = int((time.perf_counter() - t0) * 1000)

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
        _log_invoke_events(
            profile=profile,
            system=system,
            user=user,
            root=root,
            tags=tags,
            operation_id=operation_id,
            parent_operation_id=parent_operation_id,
            first=current,
            attempts=attempts,
            calls=calls,
            duration_ms=duration_ms,
            succeeded=True,
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
