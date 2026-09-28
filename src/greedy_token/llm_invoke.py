"""Profile-based LLM invoke with optional escalation — library + CLI backend."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from greedy_token.calibration import SOURCE_FIXED
from greedy_token.cheap_llm import MalformedResponseError
from greedy_token.expensive_llm import llm_chat
from greedy_token.model_select import (
    ModelSpec,
    ResolvedModel,
    apply_model_env,
    escalation_chain_from,
    get_llm_registry,
    resolve_model,
)
from greedy_token.result_contract import RESULT_NOT_EVALUATED
from greedy_token.result_gate import evaluate_result_gate
from greedy_token.router import RouteDecision
from greedy_token.spend_guard import (
    SpendReservation,
    estimate_cost_usd,
    reserve_metered_call,
)
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
    useful: bool = True
    spend_ref: str = ""


def _output_weak(text: str, *, min_len: int = 8) -> bool:
    stripped = text.strip()
    if len(stripped) < min_len:
        return True
    if stripped.lower() in ("null", "none", "n/a", "error"):
        return True
    return False


# Provider error text can echo request secrets back — it must never reach the
# public RuntimeError or telemetry verbatim.
_REDACT_URL_USERINFO = re.compile(r"(https?://)[^/@\s:]+(:[^/@\s]*)?@")
_REDACT_AUTH_HEADER = re.compile(
    r"(?i)\b(authorization|proxy-authorization)([\"']?\s*[:=]\s*[\"']?)"
    r"(basic|bearer|token)?\s*[^\s\"'&,;]+"
)
_REDACT_KEY_FIELD = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|secret|password|passwd)"
    r"([\"']?\s*[=:]\s*[\"']?)[^\s\"'&,;]+"
)
_REDACT_KEY_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_\-]{4,}\b")


def _redact_error(text: str, *, secrets: tuple[str, ...] = ()) -> str:
    """Strip credentials a provider error may echo before it is surfaced."""
    for secret in secrets:
        if secret and secret in text:
            text = text.replace(secret, "<redacted>")
    text = _REDACT_URL_USERINFO.sub(r"\1<redacted>@", text)
    text = _REDACT_AUTH_HEADER.sub(r"\1\2<redacted>", text)
    text = _REDACT_KEY_FIELD.sub(r"\1\2<redacted>", text)
    text = _REDACT_KEY_TOKEN.sub("<redacted>", text)
    return text


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
        # equivalent: re.I makes the alternation case-insensitive, so
        # uppercasing the word list cannot change what it matches.
        if re.search(r"\b(unsure|unknown|cannot determine)\b", text, re.I):
            return True
    return False


def _invoke_tier(resolved: ResolvedModel) -> str:
    return "ollama" if resolved.billing_tier == "cheap" else "cursor"


def _settle_call_cost(
    spec: ModelSpec,
    reservation: SpendReservation | None,
    eval_tokens: int | None,
) -> float:
    """Actual cost of a completed call, then close the reservation with it.

    Usage the provider did not report still owes the estimate the reservation
    was made for — a paid call never records zero spend.
    """
    cost = estimate_cost_usd(spec, eval_tokens)
    if not math.isfinite(cost):
        cost = 0.0
    if reservation is not None:
        if eval_tokens is None:
            cost = max(cost, reservation.est_usd)
        reservation.settle(cost)
    return cost


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
        # equivalent: falsy-for-falsy arg variants (started / result_status /
        # tier / ok → None) produce an identical observable gate verdict —
        # `result_status or RESULT_NOT_EVALUATED` normalizes None, `tier` and
        # `continue_chain` are stored on GateDecision but never consumed by
        # the event builders, and `not ok` treats None as False anyway.
        gate = evaluate_result_gate(
            started=False,
            result_status=RESULT_NOT_EVALUATED,
            tier=tier,
            ok=False,
        )
        # equivalent: dropping/None-ing `est_tokens_override` or the decision's
        # est_tokens arg is unobservable here — the override mirrors
        # decision.est_tokens=prompt_tokens, so build_route_event falls back
        # to the same value either way.
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
                input_tokens=prompt_tokens,
            )
        )
    else:
        for call in calls:
            cand = call.model
            tier = _invoke_tier(cand)
            est = (call.eval_tokens or 0) + prompt_tokens
            # equivalent: falsy-for-falsy gate args (result_status / tier →
            # None) are unobservable — the status normalizes via `or` and the
            # tier is stored on GateDecision without event consumers.
            gate = evaluate_result_gate(
                started=True,
                result_status=RESULT_NOT_EVALUATED,
                tier=tier,
                ok=True,
                output_useful=call.served and call.useful,
            )
            # equivalent: the est/flag variants inside this call are
            # unobservable — est_tokens_override mirrors decision.est_tokens,
            # and execution_requested only affects the phase when `executed`
            # is False, which never happens for completed calls.
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
                    spend_ref=call.spend_ref or None,
                    input_tokens=prompt_tokens,
                )
            )
    outcome_tier = _invoke_tier(calls[-1].model if calls else first)
    # equivalent: the outcome event hardcodes est_tokens=0, so the third
    # _invoke_decision argument is never read here.
    append_event(
        build_outcome_event(
            task=task,
            root=effective_root,
            decision=_invoke_decision(profile, outcome_tier, 0),
            # The serving call's gate rules the outcome: a delivered response
            # whose output was rejected (empty/weak) is a failure, not a
            # success — "delivered" only means a provider answered.
            outcome=gate.outcome,
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
    # equivalent: last_error is rewritten on every loop iteration that fails
    # to deliver, and `or` treats any falsy initial value identically — the
    # "" initial value is never read while falsy-behavior differs.
    last_error = ""
    operation_id = new_operation_id()

    candidates: list[ResolvedModel] = [current]
    if allow_escalate:
        candidates.extend(escalation_chain_from(current, root=root))

    # equivalent: text is reassigned from every llm_chat return before any
    # read (the weak-output check and InvokeResult both see the provider's
    # value), and the raise path never reads it — a None/"XXXX" default is
    # unobservable.
    text = ""
    # equivalent: eval_tokens is reassigned from every llm_chat return before
    # InvokeResult reads it; on the failure path RuntimeError is raised
    # without ever reading the initial value.
    eval_tokens: int | None = None
    # equivalent: `used` is reassigned to the serving candidate on the only
    # path that builds InvokeResult; a None default cannot surface.
    used: ResolvedModel = current
    # Spend accrues for every completed provider call, not only the model that
    # served — an escalated-away attempt still consumed tokens.
    cost = 0.0
    calls: list[_ProviderCall] = []
    # equivalent: `delivered` is read only through `if not delivered`
    # truthiness, so a None initial value behaves identically to False.
    delivered = False

    for index, candidate in enumerate(candidates):
        attempts.append(candidate.model_id)
        reservation: SpendReservation | None = None
        if candidate.spec.billing == "metered":
            # ADR-0002: every metered call is spend-guarded — expensive tier
            # keeps the expensive opt-in path, metered cheap needs the
            # metered opt-in; both share the daily/monthly caps. Check and
            # reservation are atomic, so this call's estimated spend is
            # already visible to the next candidate and concurrent invokes.
            est = estimate_cost_usd(
                candidate.spec,
                count_tokens(user).tokens + count_tokens(system).tokens,
            )
            reservation = reserve_metered_call(
                candidate.spec,
                root=root,
                cli_allow=allow_expensive,
                est_cost_usd=est,
                operation_id=operation_id,
            )
            if not reservation.allowed:
                last_error = reservation.reason
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
        except MalformedResponseError as exc:
            # A response did arrive — tokens may already be billed — so the
            # attempt is recorded as a structured failure (spend included),
            # not silently swallowed like a transport error.
            call_ms = int((time.perf_counter() - call_t0) * 1000)
            call_cost = _settle_call_cost(candidate.spec, reservation, exc.eval_tokens)
            cost += call_cost
            calls.append(
                _ProviderCall(
                    model=candidate,
                    eval_tokens=exc.eval_tokens,
                    cost_usd=call_cost,
                    duration_ms=call_ms,
                    # equivalent: this call's `served` stays False forever, so
                    # the gate's `served and useful` is False no matter what
                    # useful holds — None/True/dropped are all unobservable.
                    useful=False,
                    spend_ref=reservation.reservation_id if reservation else "",
                )
            )
            last_error = _redact_error(
                str(exc), secrets=(candidate.spec.api_key, candidate.spec.url)
            )
            continue
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            # No billable response observed — free the reservation.
            if reservation is not None:
                reservation.release()
            last_error = _redact_error(
                str(exc), secrets=(candidate.spec.api_key, candidate.spec.url)
            )
            continue
        call_ms = int((time.perf_counter() - call_t0) * 1000)
        call_cost = _settle_call_cost(candidate.spec, reservation, eval_tokens)
        cost += call_cost
        calls.append(
            _ProviderCall(
                model=candidate,
                eval_tokens=eval_tokens,
                cost_usd=call_cost,
                duration_ms=call_ms,
                useful=not _output_weak(text),
                spend_ref=reservation.reservation_id if reservation else "",
            )
        )

        used = candidate
        if candidate.model_id != current.model_id:
            escalated_from = current.model_id

        registry = get_llm_registry(root)
        wants_more = allow_escalate and _should_escalate(
            text,
            profile=profile,
            triggers=registry.escalation.triggers,
        )
        if wants_more and index + 1 < len(candidates):
            continue
        if wants_more and not profile.endswith(":escalate"):
            # The final candidate's output was rejected by the configured
            # triggers and no model is left — a weak answer is a failed
            # operation, not a success that earns savings.
            last_error = (
                f"final model {candidate.model_id} output rejected "
                "by escalation triggers"
            )
            break
        calls[-1].served = True
        delivered = True
        break

    if not delivered:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        # equivalent: every loop exit that reaches this line leaves
        # last_error non-empty (denied reservation, provider error, rejected
        # output), so the or-default literal is unreachable dead code.
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
