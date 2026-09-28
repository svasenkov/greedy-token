"""Split budget ledger: metered USD (hard) + Cursor estimate USD (soft)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from greedy_token.budget_config import BudgetMode, BudgetSettings, get_budget_settings
from greedy_token.usage import load_events, log_path

BillingTier = Literal["metered", "cheap", "cursor_estimate"]


@dataclass(frozen=True)
class BudgetSnapshot:
    metered_spent_usd: float
    metered_cap_usd: float
    metered_remaining_usd: float
    metered_pct: float
    cursor_est_spent_usd: float
    cursor_est_cap_usd: float
    cursor_est_remaining_usd: float
    cursor_est_pct: float
    mode: BudgetMode
    period_label: str
    show_both: bool
    warn_at_pct: float
    # ADR-0002 split of metered spend by derived tier (billing_tier field):
    # cheap bulk APIs vs expensive escalations.
    metered_cheap_spent_usd: float = 0.0
    metered_expensive_spent_usd: float = 0.0


def _period_start(settings: BudgetSettings) -> datetime:
    now = datetime.now(UTC)
    if settings.period == "rolling_30d":
        return now - timedelta(days=30)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _period_label(settings: BudgetSettings) -> str:
    now = datetime.now(UTC)
    if settings.period == "rolling_30d":
        return "30d"
    return now.strftime("%b")


def _billing_tier_from_event(event: dict) -> BillingTier:
    billing = event.get("billing")
    if isinstance(billing, dict):
        tier = str(billing.get("tier", "")).strip().lower()
        if tier in ("metered", "cheap", "cursor_estimate"):
            return tier  # type: ignore[return-value]

    billing_tier = str(event.get("billing_tier", "")).strip().lower()
    if billing_tier == "expensive":
        return "metered"
    if billing_tier == "cheap":
        return "cheap"

    selected = str(event.get("selected_tier", "")).strip().lower()
    if selected == "cursor":
        return "cursor_estimate"
    if selected in ("tool", "python", "rag"):
        return "cheap"
    if selected == "ollama":
        return "cheap"
    return "cursor_estimate"


def _cost_from_event(event: dict, *, cursor_rate: float) -> float:
    billing = event.get("billing")
    for raw in (
        billing.get("cost_usd") if isinstance(billing, dict) else None,
        event.get("cost_usd"),
    ):
        if raw is None:
            continue
        try:
            cost = float(raw)
        except (TypeError, ValueError):
            continue
        # A poisoned record (NaN/inf) must not poison the budget — NaN would
        # propagate into the monthly sum and defeat every cap comparison.
        if math.isfinite(cost):
            return cost

    tier = _billing_tier_from_event(event)
    if tier == "cursor_estimate":
        baseline = int(event.get("cursor_baseline") or 0)
        return (baseline / 1_000_000) * cursor_rate
    return 0.0


def _midnight_utc() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def cursor_estimate_spend_usd(events: list[dict], *, cursor_rate: float) -> float:
    """Cursor-estimate spend inside an already window-filtered event list —
    the window counterpart of ``aggregate_budget``'s period-scoped figure."""
    return sum(
        _cost_from_event(event, cursor_rate=cursor_rate)
        for event in events
        if _billing_tier_from_event(event) == "cursor_estimate"
    )


def metered_spent_today(path: Path | None = None) -> float:
    # Same accounting source the spend guard uses: durable ledger rows plus
    # usage events that predate it. *path* narrows the usage side for tests.
    from greedy_token.spend_ledger import metered_spend_usd, usage_metered_spend_usd

    if path is not None:
        from greedy_token.spend_ledger import ledger_spend_usd

        return ledger_spend_usd(since=_midnight_utc()) + usage_metered_spend_usd(
            since=_midnight_utc(), log=path
        )
    return metered_spend_usd(since=_midnight_utc())


def aggregate_budget(
    *,
    root: Path | None = None,
    path: Path | None = None,
    settings: BudgetSettings | None = None,
) -> BudgetSnapshot:
    settings = settings or get_budget_settings(root)
    log = path or log_path()
    since = _period_start(settings)
    events, _ = load_events(log, since=since)

    metered_spent = 0.0
    metered_cheap = 0.0
    metered_expensive = 0.0
    cursor_est_spent = 0.0

    for event in events:
        tier = _billing_tier_from_event(event)
        cost = _cost_from_event(event, cursor_rate=settings.cursor_usd_per_1m_tokens)
        if tier == "metered":
            # Events carrying spend_ref are already accounted in the spend
            # ledger — counting them here would double the spend.
            if event.get("spend_ref"):
                continue
            metered_spent += cost
            # ADR-0002: split by derived tier — metered cheap bulk vs expensive.
            if event.get("billing_tier") == "cheap":
                metered_cheap += cost
            else:
                metered_expensive += cost
        elif tier == "cursor_estimate":
            cursor_est_spent += cost

    # Durable spend ledger: calls accounted there (incl. calls made while
    # telemetry was off) join the usage-log pre-ledger spend above.
    from greedy_token.spend_ledger import ledger_spend_by_tier

    ledger_split = ledger_spend_by_tier(since=since)
    metered_spent += ledger_split["cheap"] + ledger_split["expensive"]
    metered_cheap += ledger_split["cheap"]
    metered_expensive += ledger_split["expensive"]

    metered_cap = settings.metered_monthly_cap_usd
    cursor_cap = settings.cursor_monthly_estimate_cap_usd

    metered_remaining = max(0.0, metered_cap - metered_spent)
    cursor_remaining = max(0.0, cursor_cap - cursor_est_spent)

    metered_pct = (metered_spent / metered_cap * 100) if metered_cap > 0 else 0.0
    cursor_pct = (cursor_est_spent / cursor_cap * 100) if cursor_cap > 0 else 0.0

    mode: BudgetMode = "normal"
    if metered_cap > 0 and metered_spent >= metered_cap:
        mode = "exhausted"
    elif metered_pct >= settings.warn_at_pct or cursor_pct >= settings.warn_at_pct:
        mode = "warn"

    return BudgetSnapshot(
        metered_spent_usd=round(metered_spent, 4),
        metered_cap_usd=metered_cap,
        metered_remaining_usd=round(metered_remaining, 4),
        metered_pct=round(metered_pct, 1),
        cursor_est_spent_usd=round(cursor_est_spent, 2),
        cursor_est_cap_usd=cursor_cap,
        cursor_est_remaining_usd=round(cursor_remaining, 2),
        cursor_est_pct=round(cursor_pct, 1),
        mode=mode,
        period_label=_period_label(settings),
        show_both=settings.show_both,
        warn_at_pct=settings.warn_at_pct,
        metered_cheap_spent_usd=round(metered_cheap, 4),
        metered_expensive_spent_usd=round(metered_expensive, 4),
    )


def headroom(*, root: Path | None = None) -> BudgetSnapshot:
    return aggregate_budget(root=root)


def metered_budget_exhausted(*, root: Path | None = None) -> bool:
    snap = headroom(root=root)
    return snap.metered_cap_usd > 0 and snap.metered_spent_usd >= snap.metered_cap_usd


def cursor_budget_warn(*, root: Path | None = None) -> bool:
    snap = headroom(root=root)
    return snap.cursor_est_cap_usd > 0 and snap.cursor_est_pct >= snap.warn_at_pct


def format_budget_line(*, root: Path | None = None, compact: bool = True) -> str:
    snap = headroom(root=root)
    warn = " ⚠" if snap.mode in ("warn", "exhausted") else ""
    if compact:
        return (
            f"Budget ({snap.period_label}): "
            f"metered ${snap.metered_spent_usd:.2f}/${snap.metered_cap_usd:.0f} "
            f"({snap.metered_pct:.0f}%) · "
            f"cursor est. ~${snap.cursor_est_spent_usd:.0f}/${snap.cursor_est_cap_usd:.0f} "
            f"({snap.cursor_est_pct:.0f}%){warn}"
        )
    lines = [
        f"Budget ({snap.period_label})",
        f"  Metered API:    ${snap.metered_spent_usd:.4f} / ${snap.metered_cap_usd:.2f} "
        f"({snap.metered_pct:.1f}%) — hard cap",
        f"    cheap bulk:   ${snap.metered_cheap_spent_usd:.4f} · "
        f"expensive: ${snap.metered_expensive_spent_usd:.4f}",
        f"  Cursor estimate: ~${snap.cursor_est_spent_usd:.2f} / ${snap.cursor_est_cap_usd:.2f} "
        f"({snap.cursor_est_pct:.1f}%) — soft limit",
    ]
    if snap.mode == "exhausted":
        lines.append("  Status: metered budget exhausted — escalation blocked")
    elif snap.mode == "warn":
        lines.append(f"  Status: approaching cap (warn at {snap.warn_at_pct:.0f}%)")
    return "\n".join(lines)


def format_budget_statusline(*, root: Path | None = None) -> str:
    snap = headroom(root=root)
    warn = "⚠" if snap.mode in ("warn", "exhausted") else ""
    return (
        f"M:${snap.metered_spent_usd:.0f}/${snap.metered_cap_usd:.0f} "
        f"C:~${snap.cursor_est_spent_usd:.0f}/${snap.cursor_est_cap_usd:.0f}{warn}"
    )


def build_billing_event_fields(
    *,
    billing_tier: str,
    cost_usd: float | None = None,
    model_id: str | None = None,
) -> dict:
    """v2 billing block for usage events."""
    tier_map = {
        "expensive": "metered",
        "cheap": "cheap",
        "cursor": "cursor_estimate",
        "metered": "metered",
        "cursor_estimate": "cursor_estimate",
    }
    tier = tier_map.get(billing_tier, billing_tier)
    billing: dict = {"tier": tier}
    if cost_usd is not None:
        billing["cost_usd"] = round(cost_usd, 6)
    if model_id:
        billing["model_id"] = model_id
    return {"v": 2, "billing": billing}
