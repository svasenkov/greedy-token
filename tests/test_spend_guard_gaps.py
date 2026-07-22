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


def _spec(tier: str = "expensive", cost: float | None = 10.0) -> ModelSpec:
    # ADR-0001: tier is derived — "cheap" maps to free billing, "expensive" to
    # metered billing (cost 10.0 default is far above the cheap threshold).
    return ModelSpec(
        id="yandex-lite",
        enabled=True,
        provider="yandex_gpt",  # type: ignore[arg-type]
        url="",
        model="m",
        profiles=("*",),
        locality="remote",
        billing="free" if tier == "cheap" else "metered",
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
        models=(_spec(),),
        source="test",
    )


def _snap(*, cap: float = 0.0, spent: float = 0.0):
    return SimpleNamespace(metered_cap_usd=cap, metered_spent_usd=spent)


@allure.title("_today_utc uses UTC calendar date, not local")
def test_today_utc_is_utc_calendar_day(monkeypatch: pytest.MonkeyPatch) -> None:
    # Near UTC midnight: local calendar day can differ from UTC (kills now(UTC) → now(None)).
    utc_moment = datetime(2024, 6, 2, 0, 30, tzinfo=UTC)
    local_moment = datetime(2024, 6, 1, 23, 30)

    class FakeDatetime:
        UTC = UTC

        @staticmethod
        def now(tz=None):
            return utc_moment if tz is UTC else local_moment

    monkeypatch.setattr(spend_guard, "datetime", FakeDatetime)
    assert spend_guard._today_utc() == "2024-06-02"


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
    # derived-cheap tier (free billing) always allowed, even with opt_in off
    monkeypatch.setattr(spend_guard, "get_llm_registry", lambda root: _registry(opt_in=False))
    assert spend_guard.check_expensive_allowed(_spec(tier="cheap")).allowed is True

    # registry opt_in disabled — exact reason (kills message + allowed=None mutants)
    dec = spend_guard.check_expensive_allowed(_spec())
    assert dec.allowed is False
    assert dec.reason == "expensive LLM disabled (llm.expensive.opt_in=false)"

    # opt_in enabled but user has not opted in via env/cli — exact reason
    monkeypatch.setattr(spend_guard, "get_llm_registry", lambda root: _registry(opt_in=True))
    monkeypatch.delenv(spend_guard.SPEND_ENV, raising=False)
    monkeypatch.delenv(spend_guard.ALLOW_EXPENSIVE_ENV, raising=False)
    dec2 = spend_guard.check_expensive_allowed(_spec())
    assert dec2.allowed is False
    assert dec2.reason == f"expensive LLM opt-in required — set {spend_guard.SPEND_ENV}=1 or --allow-expensive"

    # opted in, daily cap exceeded
    monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 4.9)
    monkeypatch.setattr(spend_guard, "headroom", lambda root=None: _snap(cap=0.0))
    dec3 = spend_guard.check_expensive_allowed(
        _spec(), cli_allow=True, est_cost_usd=0.5
    )
    assert dec3.allowed is False and "daily cap" in dec3.reason

    # opted in, under daily cap but monthly metered cap exceeded
    monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 0.0)
    monkeypatch.setattr(spend_guard, "headroom", lambda root=None: _snap(cap=50.0, spent=49.9))
    dec4 = spend_guard.check_expensive_allowed(_spec(), cli_allow=True, est_cost_usd=1.0)
    assert dec4.allowed is False and "monthly metered cap" in dec4.reason

    # opted in, everything under caps → allowed
    monkeypatch.setattr(spend_guard, "headroom", lambda root=None: _snap(cap=50.0, spent=1.0))
    dec5 = spend_guard.check_expensive_allowed(_spec(), cli_allow=True, est_cost_usd=0.1)
    assert dec5.allowed is True


@allure.title("_load_today_spend: break-vs-continue on every filter and falsy cost")
def test_load_today_spend_skips_before_counting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A skip-triggering line precedes each counted line, so a `continue -> break`
    # mutation on any filter would drop the later valid rows and undercount.
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    real = tmp_path / "usage.jsonl"
    real.write_text(
        "\n"  # empty line -> `if not line`
        "not-json\n"  # JSONDecodeError
        + json.dumps({"ts": "1999-01-01T00:00:00Z", "billing_tier": "expensive", "cost_usd": 9}) + "\n"  # old ts
        + json.dumps({"ts": f"{today}T03:00:00Z", "billing_tier": "cheap", "cost_usd": 9}) + "\n"  # non-expensive
        + json.dumps({"ts": f"{today}T04:00:00Z", "billing_tier": "expensive", "cost_usd": 0}) + "\n"  # falsy cost
        + json.dumps({"ts": f"{today}T05:00:00Z", "billing_tier": "expensive", "cost_usd": 2.0}) + "\n",
        encoding="utf-8",
    )
    missing = tmp_path / "does-not-exist.jsonl"
    # Missing archive first: a `continue -> break` on `not is_file` would skip `real`.
    monkeypatch.setattr(spend_guard, "log_archive_paths", lambda path: [missing, real])
    # cost_usd=0 must contribute exactly 0 (kills `or 0 -> or 1`): total is 2.0, not 3.0.
    assert spend_guard._load_today_spend() == pytest.approx(2.0)


@allure.title("expensive_opt_in: every accepted env token for both env vars")
@pytest.mark.parametrize("token", ["1", "true", "yes", "on"])
def test_expensive_opt_in_env_tokens(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spend_guard, "get_llm_registry", lambda root: _registry(opt_in=True))
    monkeypatch.delenv(spend_guard.SPEND_ENV, raising=False)
    monkeypatch.delenv(spend_guard.ALLOW_EXPENSIVE_ENV, raising=False)

    monkeypatch.setenv(spend_guard.SPEND_ENV, token)
    assert spend_guard.expensive_opt_in() is True
    monkeypatch.setenv(spend_guard.SPEND_ENV, token.upper())  # .lower() normalizes
    assert spend_guard.expensive_opt_in() is True

    monkeypatch.delenv(spend_guard.SPEND_ENV, raising=False)
    monkeypatch.setenv(spend_guard.ALLOW_EXPENSIVE_ENV, token)
    assert spend_guard.expensive_opt_in() is True
    monkeypatch.setenv(spend_guard.ALLOW_EXPENSIVE_ENV, token.upper())
    assert spend_guard.expensive_opt_in() is True


@allure.title("check_expensive_allowed: cap boundaries are strict and default est is zero")
def test_check_expensive_allowed_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spend_guard, "get_llm_registry", lambda root: _registry(opt_in=True, daily_cap=5.0))
    monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 0.0)
    monkeypatch.setattr(spend_guard, "headroom", lambda root=None: _snap(cap=0.0))

    # daily cap == 0 means "no cap": spend over 0 must still be allowed
    # (kills `cap > 0 -> cap >= 0`).
    monkeypatch.setattr(spend_guard, "get_llm_registry", lambda root: _registry(opt_in=True, daily_cap=0.0))
    monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 3.0)
    assert spend_guard.check_expensive_allowed(_spec(), cli_allow=True, est_cost_usd=3.0).allowed is True

    # cap == 1.0 is a real cap; exceeding it denies (kills `cap > 0 -> cap > 1`).
    monkeypatch.setattr(spend_guard, "get_llm_registry", lambda root: _registry(opt_in=True, daily_cap=1.0))
    monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 1.0)
    assert spend_guard.check_expensive_allowed(_spec(), cli_allow=True, est_cost_usd=0.5).allowed is False

    # exact boundary spent+est == cap is allowed (kills `> cap -> >= cap`).
    monkeypatch.setattr(spend_guard, "get_llm_registry", lambda root: _registry(opt_in=True, daily_cap=5.0))
    monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 4.5)
    assert spend_guard.check_expensive_allowed(_spec(), cli_allow=True, est_cost_usd=0.5).allowed is True

    # default est_cost_usd is 0.0, not 1.0: at spent == cap, omitting est stays allowed.
    monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 5.0)
    assert spend_guard.check_expensive_allowed(_spec(), cli_allow=True).allowed is True


@allure.title("check_expensive_allowed: metered cap boundaries are strict")
def test_check_expensive_allowed_metered_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spend_guard, "get_llm_registry", lambda root: _registry(opt_in=True, daily_cap=0.0))
    monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 0.0)

    # metered cap == 0 means "no cap" (kills `metered_cap_usd > 0 -> >= 0`).
    monkeypatch.setattr(spend_guard, "headroom", lambda root=None: _snap(cap=0.0, spent=3.0))
    assert spend_guard.check_expensive_allowed(_spec(), cli_allow=True, est_cost_usd=3.0).allowed is True

    # metered cap == 1.0 is real; exceeding denies (kills `> 0 -> > 1`).
    monkeypatch.setattr(spend_guard, "headroom", lambda root=None: _snap(cap=1.0, spent=1.0))
    assert spend_guard.check_expensive_allowed(_spec(), cli_allow=True, est_cost_usd=0.5).allowed is False

    # exact boundary is allowed (kills `> cap -> >= cap`).
    monkeypatch.setattr(spend_guard, "headroom", lambda root=None: _snap(cap=10.0, spent=9.5))
    assert spend_guard.check_expensive_allowed(_spec(), cli_allow=True, est_cost_usd=0.5).allowed is True


@allure.title("check_expensive_allowed threads root into registry, opt-in and headroom")
def test_check_expensive_allowed_passes_root(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = Path("/tmp/greedy-token-sentinel-root")
    seen: dict[str, object] = {}

    def fake_registry(root):
        seen["registry"] = root
        return _registry(opt_in=True, daily_cap=0.0)

    def fake_opt_in(*, root=None, cli_flag=False):
        seen["opt_in"] = root
        return True

    def fake_headroom(root=None):
        seen["headroom"] = root
        return _snap(cap=0.0)

    monkeypatch.setattr(spend_guard, "get_llm_registry", fake_registry)
    monkeypatch.setattr(spend_guard, "expensive_opt_in", fake_opt_in)
    monkeypatch.setattr(spend_guard, "headroom", fake_headroom)
    monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 0.0)

    spend_guard.check_expensive_allowed(_spec(), root=sentinel, cli_allow=False)
    assert seen["registry"] == sentinel
    assert seen["opt_in"] == sentinel
    assert seen["headroom"] == sentinel


@allure.title("estimate_cost_usd: zero and negative per-1M cost yield zero")
def test_estimate_cost_usd_edges() -> None:
    # cost_per_1m == 0 -> 0.0 (kills `<= 0 -> <= 1` which would compute for cost=1).
    assert spend_guard.estimate_cost_usd(_spec(cost=1.0), 1_000_000) == pytest.approx(1.0)
    assert spend_guard.estimate_cost_usd(_spec(cost=0.5), 1_000_000) == pytest.approx(0.5)
