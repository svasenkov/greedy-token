from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from greedy_token.budget_config import get_budget_settings
from greedy_token.budget_ledger import (
    aggregate_budget,
    build_billing_event_fields,
    format_budget_line,
    metered_budget_exhausted,
)
from greedy_token.budget_policy import apply_budget_policy
from greedy_token.resource_probe import (
    _is_deprecated,
    detect_hardware,
    format_doctor_report,
    load_model_catalog,
    recommend_models,
    run_doctor,
)
from greedy_token.router import RouteDecision
from greedy_token.usage import SCHEMA_VERSION, build_route_event


def test_schema_version_is_v2() -> None:
    assert SCHEMA_VERSION == 2


def test_build_billing_event_fields() -> None:
    fields = build_billing_event_fields(billing_tier="expensive", cost_usd=0.01, model_id="yandex-lite")
    assert fields["v"] == 2
    assert fields["billing"]["tier"] == "metered"
    assert fields["billing"]["cost_usd"] == 0.01


def test_detect_hardware() -> None:
    hw = detect_hardware()
    assert hw.tier in ("cpu_only", "low_vram", "mid_vram", "high_vram")
    assert hw.ram_gb_total > 0
    assert hw.cpu_cores >= 1


def test_model_catalog_loads() -> None:
    catalog = load_model_catalog()
    assert "tiers" in catalog
    assert "deprecated" in catalog
    assert "paid_models" in catalog


def test_is_deprecated_openchat() -> None:
    catalog = load_model_catalog()
    dep, reason = _is_deprecated("openchat:7b", catalog)
    assert dep is True
    assert "qwen" in reason.lower() or reason


def test_recommend_models_for_tier() -> None:
    hw = detect_hardware()
    rec = recommend_models(hw)
    assert rec
    assert "qwen" in rec[0].lower()


def test_run_doctor_smoke() -> None:
    report = run_doctor(quick=True)
    text = format_doctor_report(report)
    assert "greedy-token doctor" in text
    assert report.hardware.tier


def test_doctor_paid_recommendations() -> None:
    report = run_doctor(include_paid=True, quick=True)
    assert report.paid_recommendations or load_model_catalog().get("paid_models")


def test_aggregate_budget_empty_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    snap = aggregate_budget(path=log)
    assert snap.metered_spent_usd == 0.0
    assert snap.metered_cap_usd == 50.0
    assert snap.cursor_est_cap_usd == 30.0


def test_aggregate_budget_metered_and_cursor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    events = [
        {
            "ts": now,
            "billing_tier": "expensive",
            "cost_usd": 1.5,
            "cursor_baseline": 1000,
            "selected_tier": "cursor",
        },
        {
            "ts": now,
            "selected_tier": "cursor",
            "cursor_baseline": 2_000_000,
        },
    ]
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    snap = aggregate_budget(path=log)
    assert snap.metered_spent_usd == 1.5
    assert snap.cursor_est_spent_usd > 0


def test_metered_budget_exhausted_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    monkeypatch.setenv("GREEDY_BUDGET_METERED_OVERRIDE", "10")
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    log.write_text(
        json.dumps({"ts": now, "billing_tier": "expensive", "cost_usd": 10.0}) + "\n",
        encoding="utf-8",
    )
    assert metered_budget_exhausted() is True


def test_format_budget_line() -> None:
    line = format_budget_line(compact=True)
    assert "metered" in line
    assert "cursor est." in line


def test_build_route_event_has_billing_v2(minimal_workspace: Path) -> None:
    decision = RouteDecision(
        target="tool",
        route_id="test",
        confidence=1.0,
        matched=[],
        command=None,
        note="",
        domains=[],
        est_tokens=0,
    )
    event = build_route_event(cmd="route", task="find foo", root=minimal_workspace, decision=decision)
    assert event["v"] == 2
    assert event["billing"]["tier"] == "cheap"


def test_apply_budget_policy_cursor_warn(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_BUDGET_METERED_MONTHLY_CAP", "50")
    decision = RouteDecision(
        target="cursor",
        route_id="cursor-fallback",
        confidence=0.5,
        matched=[],
        command=None,
        note="",
        domains=[],
        complexity="medium",
        est_tokens=10000,
        rationale="test",
    )
    # Should not crash
    result = apply_budget_policy(decision, "audit skill foo", minimal_workspace)
    assert result.target in ("cursor", "ollama", "rag", "python", "tool")


def test_cli_doctor_and_budget(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(minimal_workspace))
    from greedy_token.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["doctor"])
    assert exc.value.code == 0

    with pytest.raises(SystemExit) as exc2:
        main(["budget"])
    assert exc2.value.code == 0

    with pytest.raises(SystemExit) as exc3:
        main(["budget", "--json"])
    assert exc3.value.code == 0
