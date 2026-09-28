"""Opt-in gate and daily spend cap for expensive LLM calls."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from greedy_token.budget_config import get_budget_settings
from greedy_token.budget_ledger import headroom
from greedy_token.model_select import LlmRegistry, ModelSpec, get_llm_registry
from greedy_token.spend_ledger import (
    is_metered_spend_event,
    metered_spend_usd,
    release_spend,
    reserve_spend,
    settle_spend,
    spend_lock,
)

SPEND_ENV = "GREEDY_EXPENSIVE_LLM"
ALLOW_EXPENSIVE_ENV = "GREEDY_ALLOW_EXPENSIVE"
# ADR-0002: opt-in for metered models on the cheap derived tier (bulk APIs).
METERED_ENV = "GREEDY_METERED_LLM"

_TRUTHY = ("1", "true", "yes", "on")

_is_metered_event = is_metered_spend_event


@dataclass(frozen=True)
class SpendDecision:
    allowed: bool
    reason: str = ""


@dataclass
class SpendReservation:
    """One metered provider call's hold on the budget (ADR-0002 hard cap).

    Created by ``reserve_metered_call`` atomically with the cap check:
    ``settle`` records the actual spend once the response is in, ``release``
    frees the estimate when no billable response arrived.  A denied or
    free-model reservation has an empty id and its methods are no-ops.
    """

    allowed: bool
    reason: str = ""
    reservation_id: str = ""
    est_usd: float = 0.0

    def settle(self, cost_usd: float) -> None:
        if self.reservation_id:
            settle_spend(self.reservation_id, cost_usd=cost_usd)

    def release(self) -> None:
        if self.reservation_id:
            release_spend(self.reservation_id)


def _today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _midnight_utc() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _daily_cap_usd(registry: LlmRegistry, *, root: Path | None = None) -> float:
    """Effective daily cap: the tightest of the configured caps.

    ADR-0002 documents one daily cap covering all metered spend, read from
    ``llm.expensive.daily_cap_usd``.  ``budget.metered.daily_cap_usd`` is an
    additional billing-side guardrail — configured (> 0) it can only tighten
    the daily cap, never loosen what the model registry allows.  Both at 0
    means no daily cap."""
    caps = [
        cap
        for cap in (
            registry.daily_cap_usd,
            get_budget_settings(root).metered_daily_cap_usd,
        )
        if cap > 0
    ]
    return min(caps) if caps else 0.0


def _load_today_spend() -> float:
    # Spend is read from the durable ledger (which survives telemetry opt-out
    # and rotation) plus pre-ledger usage events — not from telemetry alone.
    # Fresh pending reservations count, so an in-flight paid call is visible
    # to the next check in this or another process.
    return metered_spend_usd(since=_midnight_utc(), include_pending=True)


def expensive_opt_in(*, root: Path | None = None, cli_flag: bool = False) -> bool:
    registry = get_llm_registry(root)
    if not registry.expensive_opt_in:
        return False
    if cli_flag:
        return True
    # equivalent: default "" vs "XXXX" — unset env; "XXXX" not in accepted tokens → same False.
    env = os.environ.get(SPEND_ENV, "").strip().lower()
    if env in _TRUTHY:
        return True
    # equivalent: default "" vs "XXXX" — unset env; "XXXX" not in accepted tokens → same False.
    env2 = os.environ.get(ALLOW_EXPENSIVE_ENV, "").strip().lower()
    return env2 in _TRUTHY


def metered_opt_in(*, root: Path | None = None, cli_flag: bool = False) -> bool:
    """Opt-in for metered cheap-tier (bulk API) calls — ADR-0002.

    Granted by llm.metered.opt_in config, GREEDY_METERED_LLM env, or the
    --allow-expensive CLI flag (superset permission)."""
    registry = get_llm_registry(root)
    if registry.metered_opt_in:
        return True
    if cli_flag:
        return True
    # equivalent: default "" vs "XXXX" — unset env; "XXXX" not in accepted tokens → same False.
    env = os.environ.get(METERED_ENV, "").strip().lower()
    return env in _TRUTHY


def _price_missing(spec: ModelSpec) -> SpendDecision | None:
    """A metered model with no usable price cannot be capped — a zero or
    missing ``cost_per_1m_usd`` would bill at recorded $0 forever.  ADR-0001
    already derives such a model into the expensive tier; the gate must not
    let it through on an unknown price at all."""
    if spec.billing != "metered":
        return None
    cost = spec.cost_per_1m_usd
    if cost is None or not math.isfinite(cost) or cost <= 0:
        return SpendDecision(
            allowed=False,
            reason=(
                f"metered model {spec.id} has no usable price "
                "(set cost_per_1m_usd) — billing cannot be capped"
            ),
        )
    return None


def check_expensive_allowed(
    spec: ModelSpec,
    *,
    root: Path | None = None,
    cli_allow: bool = False,
    est_cost_usd: float = 0.0,
) -> SpendDecision:
    registry = get_llm_registry(root)
    if registry.tier_of(spec) != "expensive":
        return SpendDecision(allowed=True)
    if not registry.expensive_opt_in:
        return SpendDecision(
            allowed=False,
            reason="expensive LLM disabled (llm.expensive.opt_in=false)",
        )
    if not expensive_opt_in(root=root, cli_flag=cli_allow):
        return SpendDecision(
            allowed=False,
            reason=f"expensive LLM opt-in required — set {SPEND_ENV}=1 or --allow-expensive",
        )
    missing = _price_missing(spec)
    if missing is not None:
        return missing
    spent = _load_today_spend()
    cap = _daily_cap_usd(registry, root=root)
    if cap > 0 and spent + est_cost_usd > cap:
        return SpendDecision(
            allowed=False,
            reason=f"daily cap ${cap:.2f} exceeded (spent ~${spent:.4f})",
        )
    snap = headroom(root=root)
    if snap.metered_cap_usd > 0 and snap.metered_spent_usd + est_cost_usd > snap.metered_cap_usd:
        return SpendDecision(
            allowed=False,
            reason=(
                f"monthly metered cap ${snap.metered_cap_usd:.2f} exceeded "
                f"(spent ~${snap.metered_spent_usd:.4f})"
            ),
        )
    return SpendDecision(allowed=True)


def metered_bulk_ready(root: Path | None = None) -> bool:
    """The bulk (cheap-LLM) tier can be served by a metered cheap model now:
    such a model is enabled in the pool *and* the metered opt-in is granted
    (ADR-0002). Caps are enforced per call, not here."""
    from greedy_token.model_select import metered_cheap_fallback

    if metered_cheap_fallback(root) is None:
        return False
    return metered_opt_in(root=root)


def check_metered_allowed(
    spec: ModelSpec,
    *,
    root: Path | None = None,
    cli_allow: bool = False,
    est_cost_usd: float = 0.0,
) -> SpendDecision:
    """Gate for *every* metered call (ADR-0002).

    Free models pass. Expensive derived tier delegates to the unchanged
    expensive gate. Metered models on the cheap derived tier need the
    metered opt-in plus the same daily/monthly caps.
    """
    if spec.billing != "metered":
        return SpendDecision(allowed=True)
    registry = get_llm_registry(root)
    if registry.tier_of(spec) == "expensive":
        return check_expensive_allowed(
            spec, root=root, cli_allow=cli_allow, est_cost_usd=est_cost_usd
        )
    if not metered_opt_in(root=root, cli_flag=cli_allow):
        return SpendDecision(
            allowed=False,
            reason=(
                "metered LLM opt-in required — set llm.metered.opt_in: true "
                f"or {METERED_ENV}=1"
            ),
        )
    missing = _price_missing(spec)
    if missing is not None:
        return missing
    spent = _load_today_spend()
    cap = _daily_cap_usd(registry, root=root)
    if cap > 0 and spent + est_cost_usd > cap:
        return SpendDecision(
            allowed=False,
            reason=f"daily cap ${cap:.2f} exceeded (spent ~${spent:.4f})",
        )
    snap = headroom(root=root)
    if snap.metered_cap_usd > 0 and snap.metered_spent_usd + est_cost_usd > snap.metered_cap_usd:
        return SpendDecision(
            allowed=False,
            reason=(
                f"monthly metered cap ${snap.metered_cap_usd:.2f} exceeded "
                f"(spent ~${snap.metered_spent_usd:.4f})"
            ),
        )
    return SpendDecision(allowed=True)


def estimate_cost_usd(spec: ModelSpec, eval_tokens: int | None) -> float:
    cost = spec.cost_per_1m_usd
    # equivalent: <= 0 vs < 0 — cost==0 still yields 0.0 product.
    if eval_tokens is None or cost is None or cost <= 0:
        return 0.0
    return (eval_tokens / 1_000_000) * cost


def reserve_metered_call(
    spec: ModelSpec,
    *,
    root: Path | None = None,
    cli_allow: bool = False,
    est_cost_usd: float = 0.0,
    operation_id: str = "",
) -> SpendReservation:
    """Cap check + spend reservation as one atomic step (ADR-0002 hard cap).

    The decision and the reservation row happen under the spend ledger's
    cross-process lock, so a concurrent invoke — or the next candidate of the
    same escalation chain — counts this call's estimated spend before its own
    check.  The caller must ``settle(actual_cost)`` once the provider answers
    or ``release()`` when the call never produced a billable response.
    """
    if spec.billing != "metered":
        return SpendReservation(allowed=True)
    try:
        with spend_lock():
            decision = check_metered_allowed(
                spec,
                root=root,
                cli_allow=cli_allow,
                est_cost_usd=est_cost_usd,
            )
            if not decision.allowed:
                return SpendReservation(allowed=False, reason=decision.reason)
            reservation_id = uuid4().hex
            try:
                reserve_spend(
                    reservation_id=reservation_id,
                    model_id=spec.id,
                    est_usd=est_cost_usd,
                    operation_id=operation_id,
                    billing_tier=get_llm_registry(root).tier_of(spec),
                )
            except OSError as exc:
                # A reservation we cannot persist is a check we cannot trust —
                # fail closed rather than run an unaccounted paid call.
                return SpendReservation(
                    allowed=False, reason=f"spend ledger write failed: {exc}"
                )
    except OSError as exc:
        # A lock we cannot take is a cap check we cannot trust — same
        # fail-closed stance as an unwritable ledger.
        return SpendReservation(allowed=False, reason=f"spend lock failed: {exc}")
    # equivalent: est > 0 vs >= 0 — est==0 stores 0.0 on either branch.
    return SpendReservation(
        allowed=True,
        reservation_id=reservation_id,
        est_usd=est_cost_usd if math.isfinite(est_cost_usd) and est_cost_usd > 0 else 0.0,
    )
