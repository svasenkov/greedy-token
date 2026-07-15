"""Split budget ledger: metered USD (hard) + Cursor estimate USD (soft)."""

from __future__ import annotations

import json
import os
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
    if isinstance(billing, dict) and billing.get("cost_usd") is not None:
        try:
            return float(billing["cost_usd"])
        except (TypeError, ValueError):
            pass
    if event.get("cost_usd") is not None:
        try:
            return float(event["cost_usd"])
        except (TypeError, ValueError):
            pass

    tier = _billing_tier_from_event(event)
    if tier == "cursor_estimate":
        baseline = int(event.get("cursor_baseline") or 0)
        return (baseline / 1_000_000) * cursor_rate
    return 0.0


def _today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def metered_spent_today(path: Path | None = None) -> float:
    log = path or log_path()
    if not log.is_file():
        return 0.0
    day = _today_utc()
    total = 0.0
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = str(event.get("ts", ""))
        if not ts.startswith(day):
            continue
        if _billing_tier_from_event(event) != "metered":
            continue
        total += _cost_from_event(event, cursor_rate=0.0)
    return total


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
    cursor_est_spent = 0.0

    for event in events:
        tier = _billing_tier_from_event(event)
        cost = _cost_from_event(event, cursor_rate=settings.cursor_usd_per_1m_tokens)
        if tier == "metered":
            metered_spent += cost
        elif tier == "cursor_estimate":
            cursor_est_spent += cost

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
