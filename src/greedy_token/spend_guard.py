"""Opt-in gate and daily spend cap for expensive LLM calls."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from greedy_token.budget_ledger import headroom
from greedy_token.model_select import ModelSpec, get_llm_registry
from greedy_token.usage import log_archive_paths, log_path

SPEND_ENV = "GREEDY_EXPENSIVE_LLM"
ALLOW_EXPENSIVE_ENV = "GREEDY_ALLOW_EXPENSIVE"
# ADR-0002: opt-in for metered models on the cheap derived tier (bulk APIs).
METERED_ENV = "GREEDY_METERED_LLM"

_TRUTHY = ("1", "true", "yes", "on")


@dataclass(frozen=True)
class SpendDecision:
    allowed: bool
    reason: str = ""


def _today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _is_metered_event(event: dict) -> bool:
    """Metered spend event: v2 billing block tier "metered" (ADR-0002 — set
    for every metered call, cheap or expensive derived tier) or the legacy
    marker billing_tier == "expensive" (pre-v2 events had no block)."""
    billing = event.get("billing")
    # equivalent: default "" vs None/dropped/"XXXX" only when tier key is absent;
    # str(...) of any default never equals "metered" → same False branch.
    if isinstance(billing, dict) and str(billing.get("tier", "")).strip().lower() == "metered":
        return True
    return event.get("billing_tier") == "expensive"


def _load_today_spend() -> float:
    # A mid-day log rotation moves earlier events into usage.jsonl.1, .2, …
    # Reading only the active file would undercount today's spend and let the
    # daily cap be bypassed, so scan the active log plus every rotated archive.
    day = _today_utc()
    total = 0.0
    for path in log_archive_paths(log_path()):
        if not path.is_file():
            continue
        # equivalent: encoding=None/"UTF-8" decode identically on UTF-8 locale.
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            # equivalent: default "" vs None/dropped/"XXXX" only when ts key is absent;
            # str(...) still won't start with today's date → same skip branch.
            ts = str(event.get("ts", ""))
            if not ts.startswith(day):
                continue
            if not _is_metered_event(event):
                continue
            try:
                total += float(event.get("cost_usd") or 0)
            except (TypeError, ValueError):
                pass
    return total


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
    spent = _load_today_spend()
    cap = registry.daily_cap_usd
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
    spent = _load_today_spend()
    cap = registry.daily_cap_usd
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
