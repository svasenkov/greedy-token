"""Spend ledger: durable metered-spend accounting independent of telemetry.

Regression coverage for the audit finding that the hard cap read persisted
telemetry after each call — so intra-chain and concurrent calls could both
pass a cap they exceeded together, and GREEDY_TOKEN_LOG=0 disabled billing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

import allure
from greedy_token import spend_guard
from greedy_token.model_select import ModelSpec
from greedy_token.spend_ledger import (
    ledger_spend_by_tier,
    ledger_spend_usd,
    metered_spend_usd,
    release_spend,
    reserve_spend,
    settle_spend,
    spend_log_path,
    usage_metered_spend_usd,
)

pytestmark = [
    allure.epic("Spend guard"),
    allure.parent_suite("Spend guard"),
    allure.feature("Metered bulk tier (ADR-0002)"),
    allure.suite("Spend ledger"),
]


def _spec(**kw) -> ModelSpec:
    base = {
        "id": "bulk",
        "enabled": True,
        "provider": "openai_compat",
        "url": "https://x",
        "model": "m",
        "profiles": ("*",),
        "locality": "remote",
        "billing": "metered",
        "cost_per_1m_usd": 0.2,
    }
    base.update(kw)
    return ModelSpec(**base)  # type: ignore[arg-type]


@pytest.fixture
def metered_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "greedy_token.settings.user_config_path", lambda: tmp_path / "missing.yaml"
    )
    monkeypatch.setattr(
        "greedy_token.model_select.user_config_path", lambda: tmp_path / "missing.yaml"
    )
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(tmp_path))
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("GREEDY_TOKEN_SPEND_LOG", str(tmp_path / "spend.jsonl"))
    (tmp_path / ".greedy-token.yaml").write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "metered": {"opt_in": True},
                    "expensive": {"daily_cap_usd": 0.15, "opt_in": True},
                    "cheap": {
                        "models": [
                            {
                                "id": "bulk",
                                "enabled": True,
                                "model": "m",
                                "profiles": ["p"],
                                "billing": "metered",
                                "cost_per_1m_usd": 0.2,
                            }
                        ]
                    },
                    "escalation": {"enabled": False},
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "phase-manifest.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _usage_event(*, cost: float, spend_ref: str | None = None, ts: str | None = None) -> dict:
    event = {
        "ts": ts or datetime.now(UTC).isoformat(),
        "cost_usd": cost,
        "billing": {"tier": "metered", "cost_usd": cost},
    }
    if spend_ref:
        event["spend_ref"] = spend_ref
    return event


@allure.title("reserve→settle counts actual cost once; pending counts the estimate")
def test_reserve_settle_once(metered_root: Path) -> None:
    reserve_spend(reservation_id="r1", model_id="bulk", est_usd=0.05)
    assert ledger_spend_usd() == pytest.approx(0.05)  # pending at estimate
    settle_spend("r1", cost_usd=0.07)
    # Settled at actual — not est+actual and not pending anymore.
    assert ledger_spend_usd() == pytest.approx(0.07)
    assert ledger_spend_usd(include_pending=False) == pytest.approx(0.07)


@allure.title("release frees a reservation; stale pending reservations expire")
def test_release_and_stale_pending(metered_root: Path) -> None:
    reserve_spend(reservation_id="r2", model_id="bulk", est_usd=0.05)
    release_spend("r2")
    assert ledger_spend_usd() == 0.0

    old_ts = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
    with spend_log_path().open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": old_ts,
                    "kind": "reserve",
                    "id": "stale1",
                    "model_id": "bulk",
                    "est_usd": 9.0,
                }
            )
            + "\n"
        )
    assert ledger_spend_usd() == 0.0
    assert ledger_spend_usd(include_pending=False) == 0.0


@allure.title("usage events carrying spend_ref are not double counted")
def test_spend_ref_dedup(metered_root: Path) -> None:
    usage = metered_root / "usage.jsonl"
    usage.write_text(json.dumps(_usage_event(cost=0.1, spend_ref="r3")) + "\n", encoding="utf-8")
    reserve_spend(reservation_id="r3", model_id="bulk", est_usd=0.1)
    settle_spend("r3", cost_usd=0.1)
    # The usage event is the ledger's mirror — 0.1 total, not 0.2.
    assert metered_spend_usd() == pytest.approx(0.1)

    with usage.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_usage_event(cost=0.02)) + "\n")
    # A pre-ledger usage event without spend_ref still counts via usage side.
    assert metered_spend_usd() == pytest.approx(0.12)


@allure.title("usage-side reader skips junk lines, non-metered events and spend_ref rows")
def test_usage_side_skips(metered_root: Path) -> None:
    usage = metered_root / "usage.jsonl"
    today = datetime.now(UTC).isoformat()
    usage.write_text(
        "not-json\n"
        + json.dumps({"ts": today, "billing": {"tier": "cheap"}, "cost_usd": 9}) + "\n"
        + json.dumps({"ts": "1999-01-01T00:00:00Z", "billing": {"tier": "metered"}, "cost_usd": 9}) + "\n"
        + json.dumps({"ts": today, "billing": {"tier": "metered"}, "cost_usd": "bad"}) + "\n"
        + json.dumps(_usage_event(cost=0.4, spend_ref="x")) + "\n"
        + json.dumps(_usage_event(cost=0.25)) + "\n",
        encoding="utf-8",
    )
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    # since=None means "all recorded history" — a 1999 row still counts there.
    assert usage_metered_spend_usd() == pytest.approx(9.25)
    assert usage_metered_spend_usd(since=midnight) == pytest.approx(0.25)
    assert usage_metered_spend_usd(log=metered_root / "missing.jsonl") == 0.0


@allure.title("metered accounting persists when GREEDY_TOKEN_LOG=0")
def test_spend_ledger_survives_telemetry_off(metered_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", "0")
    reserve_spend(reservation_id="r4", model_id="bulk", est_usd=0.03)
    settle_spend("r4", cost_usd=0.03)
    assert ledger_spend_usd() == pytest.approx(0.03)


@allure.title("a pending reservation blocks the second call over the cap")
def test_reserve_blocks_concurrent_call(metered_root: Path) -> None:
    spec = _spec()
    first = spend_guard.reserve_metered_call(spec, root=metered_root, est_cost_usd=0.1)
    assert first.allowed
    assert first.reservation_id

    # 0.1 pending + 0.06 estimated > 0.15 daily cap → denied before any HTTP.
    second = spend_guard.reserve_metered_call(spec, root=metered_root, est_cost_usd=0.06)
    assert not second.allowed
    assert "cap" in second.reason

    first.release()
    third = spend_guard.reserve_metered_call(spec, root=metered_root, est_cost_usd=0.06)
    assert third.allowed
    third.release()


@allure.title("settled spend of the first call blocks the next candidate")
def test_settled_spend_blocks_next_call(metered_root: Path) -> None:
    spec = _spec()
    first = spend_guard.reserve_metered_call(spec, root=metered_root, est_cost_usd=0.01)
    assert first.allowed
    first.settle(0.1)
    second = spend_guard.reserve_metered_call(spec, root=metered_root, est_cost_usd=0.06)
    assert not second.allowed
    assert "cap" in second.reason


@allure.title("unknown or non-finite price is denied before opt-in")
def test_unknown_price_denied(metered_root: Path) -> None:
    for bad in (None, 0.0, -1.0, float("nan"), float("inf")):
        spec = _spec(cost_per_1m_usd=bad)
        decision = spend_guard.check_metered_allowed(spec, root=metered_root, cli_allow=True)
        assert not decision.allowed, bad
        assert "price" in decision.reason


@allure.title("ledger spend splits by the billing tier recorded at reserve time")
def test_ledger_tier_split(metered_root: Path) -> None:
    reserve_spend(reservation_id="c1", model_id="bulk", est_usd=0.01, billing_tier="cheap")
    settle_spend("c1", cost_usd=0.02)
    reserve_spend(reservation_id="e1", model_id="big", est_usd=0.03, billing_tier="expensive")
    settle_spend("e1", cost_usd=0.03)
    reserve_spend(reservation_id="u1", model_id="old", est_usd=0.04)  # no tier → expensive

    split = ledger_spend_by_tier()
    assert split["cheap"] == pytest.approx(0.02)
    assert split["expensive"] == pytest.approx(0.03 + 0.04)


@allure.title("free (non-metered) models get a no-op reservation")
def test_free_model_no_reservation(metered_root: Path) -> None:
    spec = _spec(billing="free", cost_per_1m_usd=None)
    reservation = spend_guard.reserve_metered_call(spec, root=metered_root)
    assert reservation.allowed
    assert not reservation.reservation_id
    reservation.settle(9.9)  # no-op — nothing was reserved
    reservation.release()
    assert ledger_spend_usd() == 0.0


@allure.title("spend_log_path: env override wins, GREEDY_TOKEN_HOME is the default base")
def test_spend_log_path(metered_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    assert spend_log_path() == metered_root / "spend.jsonl"
    monkeypatch.delenv("GREEDY_TOKEN_SPEND_LOG")
    home = Path(os.environ["GREEDY_TOKEN_HOME"])
    assert spend_log_path() == home / "spend.jsonl"
