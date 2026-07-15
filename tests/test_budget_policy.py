from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from greedy_token.budget_config import get_budget_settings
from greedy_token.budget_ledger import (
    _billing_tier_from_event,
    _cost_from_event,
    aggregate_budget,
    metered_spent_today,
)
from greedy_token.budget_policy import apply_budget_policy, policy_footer_extras
from greedy_token.router import RouteDecision


def test_budget_settings_defaults() -> None:
    settings = get_budget_settings()
    assert settings.metered_monthly_cap_usd == 50.0
    assert settings.cursor_monthly_estimate_cap_usd == 30.0


def test_billing_tier_from_event_v2() -> None:
    event = {"billing": {"tier": "metered"}}
    assert _billing_tier_from_event(event) == "metered"
    event2 = {"billing_tier": "expensive"}
    assert _billing_tier_from_event(event2) == "metered"
    event3 = {"selected_tier": "cursor"}
    assert _billing_tier_from_event(event3) == "cursor_estimate"


def test_cost_from_event_cursor_estimate() -> None:
    event = {"selected_tier": "cursor", "cursor_baseline": 1_000_000}
    cost = _cost_from_event(event, cursor_rate=15.0)
    assert cost == 15.0


def test_metered_spent_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    log.write_text(
        json.dumps({"ts": f"{day}T12:00:00Z", "billing_tier": "expensive", "cost_usd": 0.5}) + "\n",
        encoding="utf-8",
    )
    assert metered_spent_today(log) == 0.5


def test_policy_footer_extras() -> None:
    lines = policy_footer_extras()
    assert len(lines) >= 1
    assert any("Budget" in line or "Local" in line for line in lines)


def test_apply_budget_policy_metered_exhausted(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREEDY_BUDGET_METERED_OVERRIDE", "5")
    log_path = Path.home() / ".greedy-token" / "usage.jsonl"
    # use tmp log instead
    import greedy_token.usage as usage_mod

    tmp_log = minimal_workspace / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(tmp_log))
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    tmp_log.write_text(
        json.dumps({"ts": f"{day}T12:00:00Z", "billing_tier": "expensive", "cost_usd": 5.0}) + "\n",
        encoding="utf-8",
    )

    decision = RouteDecision(
        target="cursor",
        route_id="cursor-fallback",
        confidence=0.35,
        matched=[],
        command=None,
        note="",
        domains=[],
        complexity="medium",
        est_tokens=8000,
        rationale="fallback",
    )
    result = apply_budget_policy(decision, "classify files bulk", minimal_workspace)
    assert result.target != "cursor" or "budget" in (result.note or "").lower() or "Budget" in result.rationale
