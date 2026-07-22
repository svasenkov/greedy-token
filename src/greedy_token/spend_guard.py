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


@dataclass(frozen=True)
class SpendDecision:
    allowed: bool
    reason: str = ""


def _today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


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
            if event.get("billing_tier") != "expensive":
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
    if env in ("1", "true", "yes", "on"):
        return True
    # equivalent: default "" vs "XXXX" — unset env; "XXXX" not in accepted tokens → same False.
    env2 = os.environ.get(ALLOW_EXPENSIVE_ENV, "").strip().lower()
    return env2 in ("1", "true", "yes", "on")


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


def estimate_cost_usd(spec: ModelSpec, eval_tokens: int | None) -> float:
    cost = spec.cost_per_1m_usd
    # equivalent: <= 0 vs < 0 — cost==0 still yields 0.0 product.
    if eval_tokens is None or cost is None or cost <= 0:
        return 0.0
    return (eval_tokens / 1_000_000) * cost
