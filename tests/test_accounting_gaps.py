"""Regression: usage accounting, spend caps, and invoke honesty (audit batch 4)."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

import allure
from greedy_token import budget_ledger, llm_invoke, spend_guard, usage
from greedy_token.budget_config import get_budget_settings
from greedy_token.llm_invoke import invoke_profile
from greedy_token.model_select import resolve_model

pytestmark = [
    allure.epic("Usage accounting"),
    allure.parent_suite("Usage accounting"),
    allure.feature("Accounting gaps"),
    allure.suite("Accounting gaps"),
]

_REPO = Path(__file__).resolve().parents[1]


def _write_cfg(root: Path, cfg: dict) -> None:
    (root / ".greedy-token.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _metered_workspace(
    root: Path,
    *,
    expensive_cap: float = 5.0,
    budget_daily: float | None = None,
) -> Path:
    cfg: dict = {
        "llm": {
            "cheap_cost_threshold_per_1m_usd": 2,
            "metered": {"opt_in": True},
            "expensive": {"opt_in": True, "daily_cap_usd": expensive_cap},
            "models": [
                {
                    "id": "bulk",
                    "enabled": True,
                    "provider": "openai_compat",
                    "url": "https://audit.invalid",
                    "model": "bulk-m",
                    "profiles": ["p"],
                    "billing": "metered",
                    "cost_per_1m_usd": 0.1,
                }
            ],
            "escalation": {"enabled": False},
        },
    }
    if budget_daily is not None:
        cfg["budget"] = {"metered": {"daily_cap_usd": budget_daily, "monthly_cap_usd": 100}}
    _write_cfg(root, cfg)
    return root


@allure.title("parse_since accepts ISO timestamps with a trailing Z")
def test_parse_since_accepts_trailing_z() -> None:
    parsed = usage.parse_since("2026-09-01T00:00:00Z")
    assert parsed == datetime(2026, 9, 1, tzinfo=UTC)
    assert usage.parse_since("2026-09-01t00:00:00z") == parsed
    # The existing shorthands still work and unbounded stays unbounded.
    assert usage.parse_since("all") is None
    assert usage.parse_since("2026-09-01") == parsed


@allure.title("budget.metered.daily_cap_usd is a real cap once configured")
def test_budget_metered_daily_cap_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The field was parsed into BudgetSettings but never consulted by the
    spend guard — a configured daily cap must not be silently ignored."""
    ws = _metered_workspace(tmp_path, expensive_cap=5.0, budget_daily=0.01)
    assert get_budget_settings(ws).metered_daily_cap_usd == pytest.approx(0.01)

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    log.write_text(
        json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(),
                "cost_usd": 0.02,
                "billing": {"tier": "metered", "cost_usd": 0.02},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    spec = resolve_model("p", root=ws).spec
    verdict = spend_guard.check_metered_allowed(spec, root=ws, est_cost_usd=0.001)
    assert verdict.allowed is False
    assert "daily cap" in verdict.reason


@allure.title("the unconfigured budget daily default never tightens the llm cap")
def test_budget_metered_daily_default_does_not_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """budget.metered.daily_cap_usd defaults to 5.0 — when the key is absent it
    must not silently shrink an explicit llm.expensive.daily_cap_usd of 10."""
    ws = _metered_workspace(tmp_path, expensive_cap=10.0)
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    log.write_text(
        json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(),
                "cost_usd": 7.0,
                "billing": {"tier": "metered", "cost_usd": 7.0},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    spec = resolve_model("p", root=ws).spec
    verdict = spend_guard.check_metered_allowed(spec, root=ws, est_cost_usd=0.001)
    assert verdict.allowed is True


@allure.title("upstream provider errors never leak credentials into RuntimeError")
def test_invoke_error_redacts_upstream_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cfg(
        tmp_path,
        {
            "llm": {
                "cheap": {
                    "models": [
                        {
                            "id": "fast",
                            "enabled": True,
                            "model": "m7",
                            "profiles": ["p"],
                            "api_key": "sk-secret-value-123",
                            "url": "https://user:endpoint-pass-9@audit.invalid",
                        }
                    ]
                },
                "escalation": {"enabled": False},
            }
        },
    )

    def boom(*_a: object, **_k: object):
        raise RuntimeError(
            "provider 401: Authorization: Bearer sk-secret-value-123 "
            "for https://user:endpoint-pass-9@audit.invalid/v1/chat"
        )

    monkeypatch.setattr(llm_invoke, "llm_chat", boom)
    with pytest.raises(RuntimeError, match="LLM invoke failed") as raised:
        invoke_profile("p", system="s", user="u", root=tmp_path, allow_escalate=False, log=False)
    message = str(raised.value)
    assert "sk-secret-value-123" not in message
    assert "endpoint-pass-9" not in message
    assert "redacted" in message


@allure.title("a delivered-but-empty answer records a failure outcome, not success")
def test_invoke_empty_delivered_records_failure_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cfg(
        tmp_path,
        {
            "llm": {
                "cheap": {
                    "models": [{"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]}]
                },
                "escalation": {"enabled": False},
            }
        },
    )
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("", 5))
    result = invoke_profile("p", system="s", user="u", root=tmp_path, allow_escalate=False)
    assert result.text == ""
    rows = [
        json.loads(line)
        for line in (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    outcome = next(row for row in rows if row.get("event") == "route_outcome")
    assert outcome["outcome"] == "failure"
    assert outcome["gate_reason"] == "output_empty"
    assert sum(row.get("cursor_saved", 0) for row in rows) == 0


@allure.title("identical replayed records with one operation_id count once")
def test_aggregate_events_dedupes_identical_records() -> None:
    row = {
        "ts": datetime.now(UTC).isoformat(),
        "operation_id": "same-op",
        "selected_tier": "tool",
        "est_tokens": 1,
        "cursor_baseline": 101,
        "cursor_saved": 100,
    }
    twice = usage.aggregate_events([row, row])
    assert twice.operations == 1
    assert twice.to_dict()["totals"]["saved_vs_cursor"] == 100
    assert twice.quality["cheap_hits"] == 1

    outcome = {
        "ts": datetime.now(UTC).isoformat(),
        "event": "route_outcome",
        "operation_id": "same-op",
        "selected_tier": "tool",
        "outcome": "success",
    }
    summary = usage.aggregate_events([row, outcome, dict(outcome)])
    assert summary.outcome_records == 1


@allure.title("same operation_id with changed fields is a retry — counted twice")
def test_aggregate_events_same_op_different_fields_counts_both() -> None:
    # Design pin: dedup removes only byte-identical replayed lines.  A retried
    # call reuses the operation_id but is a real second call — new timestamp,
    # new cost — and must contribute to tier totals again while the operation
    # count stays 1.
    base = {
        "ts": datetime.now(UTC).isoformat(),
        "operation_id": "retried-op",
        "selected_tier": "tool",
        "est_tokens": 10,
        "cursor_baseline": 110,
        "cursor_saved": 100,
    }
    retry = {**base, "ts": datetime.now(UTC).isoformat(), "est_tokens": 12}
    summary = usage.aggregate_events([base, retry])
    assert summary.operations == 1
    assert summary.events == 2
    assert summary.by_tier["tool"].count == 2
    assert summary.by_tier["tool"].est_tokens == 22
    assert summary.by_tier["tool"].saved_vs_cursor == 200


@allure.title("v2 billing-block-only metered cost still reaches the daily sum")
def test_metered_daily_counts_billing_block_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    log.write_text(
        json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(),
                "billing": {"tier": "metered", "cost_usd": 1.0},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert spend_guard._load_today_spend() == pytest.approx(1.0)


@allure.title("non-finite logged costs cannot poison daily or monthly spend")
def test_non_finite_spend_is_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    nan_event = {
        "ts": datetime.now(UTC).isoformat(),
        "billing": {"tier": "metered", "cost_usd": float("nan")},
        "cost_usd": float("nan"),
    }
    log.write_text(json.dumps(nan_event) + "\n", encoding="utf-8")
    daily = spend_guard._load_today_spend()
    assert math.isfinite(daily) and daily == 0.0
    monthly = budget_ledger.aggregate_budget(root=tmp_path).metered_spent_usd
    assert math.isfinite(monthly) and monthly == 0.0


@allure.title("a +offset timestamp whose UTC date is today counts for today")
def test_utc_offset_event_counts_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    today = datetime.now(UTC).date()
    offset_ts = (
        datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        .isoformat()
        .replace("+00:00", "+14:00")
    )
    log.write_text(
        json.dumps(
            {
                "ts": offset_ts,
                "billing": {"tier": "metered", "cost_usd": 0.06},
                "cost_usd": 0.06,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert spend_guard._load_today_spend() == pytest.approx(0.06)


@allure.title("sdist manifest ships the dev assets the bundled tests import")
def test_manifest_includes_dev_assets_and_excludes_bytecode() -> None:
    manifest = (_REPO / "MANIFEST.in").read_text(encoding="utf-8")
    # tests/ ship in the sdist — the suite they carry needs these too.
    assert "recursive-include tests" in manifest
    for needed in ("bench", "scripts", ".github"):
        assert (
            f"recursive-include {needed} " in manifest
            or manifest.find(f"recursive-include {needed}\n") >= 0
        )
    assert "constraints-ci.txt" in manifest
    assert "global-exclude __pycache__" in manifest
    assert "global-exclude *.py[co]" in manifest


@allure.title("CI constraints pin the sdist build backend")
def test_constraints_pin_setuptools() -> None:
    constraints = (_REPO / "constraints-ci.txt").read_text(encoding="utf-8")
    assert "setuptools==" in constraints
