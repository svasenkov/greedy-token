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


def test_mixed_log_stored_and_derived_tiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0001: usage.jsonl mixing stored billing.tier events (written before the
    derive_tier migration) with new derived-tier events must aggregate correctly
    in the budget report and in spend_guard._load_today_spend."""
    from greedy_token import spend_guard
    from greedy_token.budget_ledger import build_billing_event_fields

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    day = datetime.now(UTC).strftime("%Y-%m-%d")

    # Old events, read as stored: v1 legacy field and v2 stored billing block.
    old_v1 = {"ts": f"{day}T01:00:00Z", "billing_tier": "expensive", "cost_usd": 0.5}
    old_v2 = {
        "ts": f"{day}T02:00:00Z",
        "billing_tier": "expensive",
        "cost_usd": 0.25,
        "billing": {"tier": "metered", "cost_usd": 0.25},
    }
    # New events write the *derived* tier into the same schema fields.
    derived_exp = {
        "ts": f"{day}T03:00:00Z",
        "billing_tier": "expensive",
        "cost_usd": 0.75,
        **build_billing_event_fields(billing_tier="expensive", cost_usd=0.75, model_id="pricey"),
    }
    assert derived_exp["billing"] == {"tier": "metered", "cost_usd": 0.75, "model_id": "pricey"}
    # Sub-threshold metered model derives cheap: logged cost stays, but it is
    # accounted under the cheap tier, not the metered ledger.
    derived_cheap = {
        "ts": f"{day}T04:00:00Z",
        "billing_tier": "cheap",
        "cost_usd": 0.01,
        **build_billing_event_fields(billing_tier="cheap", cost_usd=0.01, model_id="groq"),
    }
    cursor_event = {"ts": f"{day}T05:00:00Z", "selected_tier": "cursor", "cursor_baseline": 1_000_000}

    log.write_text(
        "\n".join(json.dumps(e) for e in (old_v1, old_v2, derived_exp, derived_cheap, cursor_event)) + "\n",
        encoding="utf-8",
    )

    snap = aggregate_budget(path=log)
    assert snap.metered_spent_usd == pytest.approx(1.5)  # 0.5 + 0.25 + 0.75, cheap excluded
    settings = get_budget_settings()
    assert snap.cursor_est_spent_usd == pytest.approx(settings.cursor_usd_per_1m_tokens, abs=0.01)

    assert metered_spent_today(log) == pytest.approx(1.5)
    assert spend_guard._load_today_spend() == pytest.approx(1.5)


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
