"""Split budget config: metered API (hard cap) + Cursor estimate (soft cap)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from greedy_token.settings import _read_yaml, _section, user_config_path, workspace_config_path

BudgetMode = Literal["normal", "warn", "exhausted"]


@dataclass(frozen=True)
class BudgetSettings:
    metered_monthly_cap_usd: float
    metered_daily_cap_usd: float
    cursor_monthly_estimate_cap_usd: float
    cursor_usd_per_1m_tokens: float
    show_both: bool
    warn_at_pct: float
    period: Literal["calendar_month", "rolling_30d"]
    source: str = "default"


def _float(value: Any, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _merge_budget(user_cfg: dict[str, Any], workspace_cfg: dict[str, Any]) -> dict[str, Any]:
    user_b = _section(user_cfg, "budget")
    ws_b = _section(workspace_cfg, "budget")
    merged: dict[str, Any] = {}
    for key in set(user_b) | set(ws_b):
        u_val = user_b.get(key)
        w_val = ws_b.get(key)
        if isinstance(u_val, dict) and isinstance(w_val, dict):
            merged[key] = {**u_val, **w_val}
        elif w_val is not None:
            merged[key] = w_val
        else:
            merged[key] = u_val
    return merged


def _parse_budget(budget_cfg: dict[str, Any], *, source: str) -> BudgetSettings:
    metered = _section(budget_cfg, "metered")
    cursor = _section(budget_cfg, "cursor")
    display = _section(budget_cfg, "display")

    period_raw = str(budget_cfg.get("period", "calendar_month")).strip().lower()
    period: Literal["calendar_month", "rolling_30d"] = (
        "rolling_30d" if period_raw == "rolling_30d" else "calendar_month"
    )

    return BudgetSettings(
        metered_monthly_cap_usd=_float(metered.get("monthly_cap_usd"), 50.0),
        metered_daily_cap_usd=_float(metered.get("daily_cap_usd"), 5.0),
        cursor_monthly_estimate_cap_usd=_float(cursor.get("monthly_estimate_cap_usd"), 30.0),
        cursor_usd_per_1m_tokens=_float(cursor.get("usd_per_1m_tokens"), 15.0),
        show_both=bool(display.get("show_both", True)),
        warn_at_pct=_float(display.get("warn_at_pct"), 80.0),
        period=period,
        source=source,
    )


def get_budget_settings(root: Path | None = None) -> BudgetSettings:
    user_cfg = _read_yaml(user_config_path())
    workspace_cfg: dict[str, Any] = {}
    if root is not None:
        workspace_cfg = _read_yaml(workspace_config_path(root))
    else:
        try:
            from greedy_token.paths import find_workspace_root

            workspace_cfg = _read_yaml(workspace_config_path(find_workspace_root()))
        except SystemExit:
            pass

    merged = _merge_budget(user_cfg, workspace_cfg)
    source = "workspace" if _section(workspace_cfg, "budget") else (
        "user" if _section(user_cfg, "budget") else "default"
    )

    settings = _parse_budget(merged, source=source)

    # Env overrides for tests / emergency
    if os.environ.get("GREEDY_BUDGET_METERED_MONTHLY_CAP", "").strip():
        settings = BudgetSettings(
            metered_monthly_cap_usd=_float(os.environ["GREEDY_BUDGET_METERED_MONTHLY_CAP"], settings.metered_monthly_cap_usd),
            metered_daily_cap_usd=settings.metered_daily_cap_usd,
            cursor_monthly_estimate_cap_usd=settings.cursor_monthly_estimate_cap_usd,
            cursor_usd_per_1m_tokens=settings.cursor_usd_per_1m_tokens,
            show_both=settings.show_both,
            warn_at_pct=settings.warn_at_pct,
            period=settings.period,
            source="env",
        )
    if os.environ.get("GREEDY_BUDGET_METERED_OVERRIDE", "").strip():
        spent = _float(os.environ["GREEDY_BUDGET_METERED_OVERRIDE"], 0.0)
        cap = max(spent, 0.01)
        settings = BudgetSettings(
            metered_monthly_cap_usd=cap,
            metered_daily_cap_usd=settings.metered_daily_cap_usd,
            cursor_monthly_estimate_cap_usd=settings.cursor_monthly_estimate_cap_usd,
            cursor_usd_per_1m_tokens=settings.cursor_usd_per_1m_tokens,
            show_both=settings.show_both,
            warn_at_pct=settings.warn_at_pct,
            period=settings.period,
            source="env",
        )

    return settings
