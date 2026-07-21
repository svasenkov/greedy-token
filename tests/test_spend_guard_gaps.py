"""Public-contract tests for spend_guard opt-in + daily/metered caps (fail_under=100)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import allure
import pytest

from greedy_token import spend_guard
from greedy_token.model_select import EscalationConfig, LlmRegistry, ModelSpec

pytestmark = [
    allure.epic("Spend guard"),
    allure.parent_suite("Spend guard"),
    allure.feature("Expensive LLM gate"),
    allure.suite("Spend guard"),
]


def _spec(tier: str = "expensive", cost: float = 10.0) -> ModelSpec:
    return ModelSpec(
        id="yandex-lite",
        enabled=True,
        provider="yandex_gpt",  # type: ignore[arg-type]
        url="",
        model="m",
        profiles=("*",),
        tier=tier,  # type: ignore[arg-type]
        cost_per_1m_usd=cost,
    )


def _registry(*, opt_in: bool = True, daily_cap: float = 5.0) -> LlmRegistry:
    return LlmRegistry(
        policy="auto",
        cheap_selection="fixed",
        cheap_default_id="fast",
        expensive_opt_in=opt_in,
        expensive_selection="fixed",
        expensive_default_id="yandex-lite",
        daily_cap_usd=daily_cap,
        escalation=EscalationConfig(enabled=True, chain=(), triggers=(), max_steps=2),
        cheap_models=(),
        expensive_models=(_spec(),),
        source="test",
    )


def _snap(*, cap: float = 0.0, spent: float = 0.0):
    return SimpleNamespace(metered_cap_usd=cap, metered_spent_usd=spent)


@allure.title("_today_utc format and estimate_cost_usd branches")
def test_helpers() -> None:
    assert len(spend_guard._today_utc()) == 10
    assert spend_guard.estimate_cost_usd(_spec(cost=0.0), 100) == 0.0
    assert spend_guard.estimate_cost_usd(_spec(cost=10.0), None) == 0.0
    assert spend_guard.estimate_cost_usd(_spec(cost=1_000_000.0), 1_000_000) == 1_000_000.0


@allure.title("_load_today_spend sums today's metered cost and skips junk")
def test_load_today_spend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    assert spend_guard._load_today_spend() == 0.0  # file missing

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log.write_text(
        "\n"
        "not-json\n"
        + json.dumps({"ts": f"{today}T01:00:00Z", "billing_tier": "expensive", "cost_usd": 1.25}) + "\n"
        + json.dumps({"ts": f"{today}T02:00:00Z", "billing_tier": "expensive", "cost_usd": "bad"}) + "\n"
        + json.dumps({"ts": f"{today}T03:00:00Z", "billing_tier": "cheap", "cost_usd": 9}) + "\n"
        + json.dumps({"ts": "1999-01-01T00:00:00Z", "billing_tier": "expensive", "cost_usd": 9}) + "\n",
        encoding="utf-8",
    )
    assert spend_guard._load_today_spend() == pytest.approx(1.25)


@allure.title("_load_today_spend also sums rotated archives for today")
def test_load_today_spend_rotated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log.write_text(
        json.dumps({"ts": f"{today}T09:00:00Z", "billing_tier": "expensive", "cost_usd": 0.5}) + "\n",
        encoding="utf-8",
    )
    # earlier expensive event pushed into a rotated archive earlier today
    archive = tmp_path / "usage.jsonl.1"
    archive.write_text(
        json.dumps({"ts": f"{today}T01:00:00Z", "billing_tier": "expensive", "cost_usd": 2.0}) + "\n"
        + json.dumps({"ts": "1999-01-01T00:00:00Z", "billing_tier": "expensive", "cost_usd": 9}) + "\n",
        encoding="utf-8",
    )
    assert spend_guard._load_today_spend() == pytest.approx(2.5)


@allure.title("expensive_opt_in: disabled, cli flag, and env vars")
def test_expensive_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(spend_guard.SPEND_ENV, raising=False)
    monkeypatch.delenv(spend_guard.ALLOW_EXPENSIVE_ENV, raising=False)
    monkeypatch.setattr(spend_guard, "get_llm_registry", lambda root: _registry(opt_in=False))
    assert spend_guard.expensive_opt_in() is False

    monkeypatch.setattr(spend_guard, "get_llm_registry", lambda root: _registry(opt_in=True))
    assert spend_guard.expensive_opt_in(cli_flag=True) is True
    assert spend_guard.expensive_opt_in() is False
    monkeypatch.setenv(spend_guard.SPEND_ENV, "yes")
    assert spend_guard.expensive_opt_in() is True
    monkeypatch.setenv(spend_guard.SPEND_ENV, "")
    monkeypatch.setenv(spend_guard.ALLOW_EXPENSIVE_ENV, "on")
    assert spend_guard.expensive_opt_in() is True


@allure.title("check_expensive_allowed: cheap tier, opt-in gates, caps")
def test_check_expensive_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    # cheap tier always allowed
    assert spend_guard.check_expensive_allowed(_spec(tier="cheap")).allowed is True

    # registry opt_in disabled
    monkeypatch.setattr(spend_guard, "get_llm_registry", lambda root: _registry(opt_in=False))
    dec = spend_guard.check_expensive_allowed(_spec())
    assert not dec.allowed and "opt_in" in dec.reason

    # opt_in enabled but user has not opted in via env/cli
    monkeypatch.setattr(spend_guard, "get_llm_registry", lambda root: _registry(opt_in=True))
    monkeypatch.delenv(spend_guard.SPEND_ENV, raising=False)
    monkeypatch.delenv(spend_guard.ALLOW_EXPENSIVE_ENV, raising=False)
    dec2 = spend_guard.check_expensive_allowed(_spec())
    assert not dec2.allowed and "opt-in required" in dec2.reason

    # opted in, daily cap exceeded
    monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 4.9)
    monkeypatch.setattr(spend_guard, "headroom", lambda root=None: _snap(cap=0.0))
    dec3 = spend_guard.check_expensive_allowed(
        _spec(), cli_allow=True, est_cost_usd=0.5
    )
    assert not dec3.allowed and "daily cap" in dec3.reason

    # opted in, under daily cap but monthly metered cap exceeded
    monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 0.0)
    monkeypatch.setattr(spend_guard, "headroom", lambda root=None: _snap(cap=50.0, spent=49.9))
    dec4 = spend_guard.check_expensive_allowed(_spec(), cli_allow=True, est_cost_usd=1.0)
    assert not dec4.allowed and "monthly metered cap" in dec4.reason

    # opted in, everything under caps → allowed
    monkeypatch.setattr(spend_guard, "headroom", lambda root=None: _snap(cap=50.0, spent=1.0))
    dec5 = spend_guard.check_expensive_allowed(_spec(), cli_allow=True, est_cost_usd=0.1)
    assert dec5.allowed is True
