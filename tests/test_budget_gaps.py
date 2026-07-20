"""Public-contract tests for split-budget modules (fail_under=100)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import allure
import pytest

import greedy_token.budget as budget
import greedy_token.budget_config as bc
import greedy_token.budget_ledger as bl
import greedy_token.budget_policy as bp
from greedy_token.budget_config import BudgetSettings
from greedy_token.budget_ledger import BudgetSnapshot
from greedy_token.router import RouteDecision

pytestmark = [
    allure.epic("Budget"),
    allure.parent_suite("Budget"),
    allure.feature("Split budget"),
    allure.suite("Budget gaps"),
]


def _settings(**kw) -> BudgetSettings:
    base = dict(
        metered_monthly_cap_usd=50.0, metered_daily_cap_usd=5.0,
        cursor_monthly_estimate_cap_usd=30.0, cursor_usd_per_1m_tokens=15.0,
        show_both=True, warn_at_pct=80.0, period="calendar_month", source="default",
    )
    base.update(kw)
    return BudgetSettings(**base)


def _snap(**kw) -> BudgetSnapshot:
    base = dict(
        metered_spent_usd=1.0, metered_cap_usd=50.0, metered_remaining_usd=49.0, metered_pct=2.0,
        cursor_est_spent_usd=1.0, cursor_est_cap_usd=30.0, cursor_est_remaining_usd=29.0, cursor_est_pct=3.0,
        mode="normal", period_label="Jul", show_both=True, warn_at_pct=80.0,
    )
    base.update(kw)
    return BudgetSnapshot(**base)


# ---- budget_config -------------------------------------------------------


@allure.title("_float falls back on None and bad values")
def test_float_fallback() -> None:
    assert bc._float(None, 3.0) == 3.0
    assert bc._float("nan-ish", 2.0) == 2.0
    assert bc._float("5", 0.0) == 5.0


@allure.title("_merge_budget merges nested dicts and prefers workspace scalars")
def test_merge_budget() -> None:
    user = {"budget": {"metered": {"a": 1}, "only_user": 9, "both": 1}}
    ws = {"budget": {"metered": {"b": 2}, "both": 5}}
    merged = bc._merge_budget(user, ws)
    assert merged["metered"] == {"a": 1, "b": 2}
    assert merged["only_user"] == 9
    assert merged["both"] == 5


@allure.title("get_budget_settings tolerates missing workspace root")
def test_get_budget_settings_no_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bc, "user_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(
        "greedy_token.paths.find_workspace_root",
        lambda: (_ for _ in ()).throw(SystemExit(1)),
    )
    for var in ("GREEDY_BUDGET_METERED_MONTHLY_CAP", "GREEDY_BUDGET_METERED_OVERRIDE"):
        monkeypatch.delenv(var, raising=False)
    settings = bc.get_budget_settings(root=None)
    assert settings.source == "default"


# ---- budget_ledger -------------------------------------------------------


@allure.title("rolling_30d period start and label")
def test_rolling_30d_period() -> None:
    s = _settings(period="rolling_30d")
    assert bl._period_label(s) == "30d"
    assert bl._period_start(s) < bl.datetime.now(bl.UTC)


@allure.title("_billing_tier_from_event covers dict, legacy, selected fallbacks")
def test_billing_tier_from_event() -> None:
    assert bl._billing_tier_from_event({"billing": {"tier": "junk"}, "selected_tier": ""}) == "cursor_estimate"
    assert bl._billing_tier_from_event({"billing_tier": "cheap"}) == "cheap"
    assert bl._billing_tier_from_event({"selected_tier": "python"}) == "cheap"
    assert bl._billing_tier_from_event({"selected_tier": "ollama"}) == "cheap"
    assert bl._billing_tier_from_event({}) == "cursor_estimate"


@allure.title("_cost_from_event ignores malformed cost fields")
def test_cost_from_event_malformed() -> None:
    assert bl._cost_from_event({"billing": {"cost_usd": "bad"}, "cost_usd": "also-bad", "selected_tier": "python"}, cursor_rate=15.0) == 0.0


@allure.title("metered_spent_today handles missing file, junk, and filters")
def test_metered_spent_today(tmp_path: Path) -> None:
    assert bl.metered_spent_today(tmp_path / "none.jsonl") == 0.0

    log = tmp_path / "usage.jsonl"
    today = bl._today_utc()
    lines = [
        "",
        "{ not json",
        json.dumps({"ts": "1999-01-01", "billing": {"tier": "metered", "cost_usd": 9.0}}),
        json.dumps({"ts": today, "selected_tier": "cursor"}),
        json.dumps({"ts": today, "billing": {"tier": "metered", "cost_usd": 1.25}}),
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert bl.metered_spent_today(log) == pytest.approx(1.25)


@allure.title("format_budget_line non-compact reflects exhausted and warn")
def test_format_budget_line_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bl, "headroom", lambda **k: _snap(mode="exhausted"))
    out = bl.format_budget_line(compact=False)
    assert "exhausted" in out and out.startswith("Budget")

    monkeypatch.setattr(bl, "headroom", lambda **k: _snap(mode="warn"))
    assert "approaching cap" in bl.format_budget_line(compact=False)


@allure.title("format_budget_statusline renders compact metered/cursor")
def test_format_budget_statusline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bl, "headroom", lambda **k: _snap(mode="warn"))
    line = bl.format_budget_statusline()
    assert line.startswith("M:$") and "C:~$" in line and "⚠" in line


# ---- budget.py footer ----------------------------------------------------


@allure.title("_policy_footer_lines swallows errors")
def test_policy_footer_lines_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**k):
        raise RuntimeError("nope")

    monkeypatch.setattr("greedy_token.budget_policy.policy_footer_extras", boom)
    assert budget._policy_footer_lines(Path("/tmp")) == []


# ---- budget_policy -------------------------------------------------------


def _decision(**kw) -> RouteDecision:
    base = dict(
        target="cursor", route_id="r1", confidence=1.0, matched=["x"], command=None,
        note="", domains=[], complexity="medium", est_tokens=0, rationale="do it",
    )
    base.update(kw)
    return RouteDecision(**base)


@pytest.fixture
def policy_env(monkeypatch: pytest.MonkeyPatch):
    """Neutralise all external signals; individual tests override as needed."""
    monkeypatch.setattr(bp, "headroom", lambda **k: _snap(mode="normal"))
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: False)
    monkeypatch.setattr(bp, "cursor_budget_warn", lambda **k: False)
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [])
    monkeypatch.setattr(bp, "run_doctor", lambda **k: _rep(deprecated=False))
    monkeypatch.setattr("greedy_token.wrappers.ollama_available", lambda: True)
    return monkeypatch


def _rep(*, deprecated: bool):
    from greedy_token.resource_probe import DoctorReport, HardwareProfile

    return DoctorReport(
        hardware=HardwareProfile("low", 8, 4, 0, 4, "cpu", "Linux"),
        ollama_available=True, ollama_url="", installed=[], configured_model="m",
        recommended=["qwen2.5-coder:7b"], deprecated_installed=["old:7b"] if deprecated else [],
        avoid_installed=[],
    )


@allure.title("policy None resolves from registry then falls back on error")
def test_policy_resolution(policy_env, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "greedy_token.model_select.get_llm_registry",
        lambda root: SimpleNamespace(policy="hybrid"),
    )
    assert bp.apply_budget_policy(_decision(), "task", Path("/tmp"), policy=None) == _decision()

    monkeypatch.setattr(
        "greedy_token.model_select.get_llm_registry",
        lambda root: (_ for _ in ()).throw(ValueError("no reg")),
    )
    assert bp.apply_budget_policy(_decision(), "task", Path("/tmp"), policy=None) == _decision()


@allure.title("metered exhausted returns ollama when locally available")
def test_metered_exhausted_ollama_available(policy_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: True)
    ollama_alt = _decision(target="ollama", matched=["o"], rationale="use ollama")
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [(1.0, ollama_alt)])
    out = bp.apply_budget_policy(_decision(complexity="medium"), "task", Path("/tmp"), policy="auto")
    assert out.target == "ollama" and out.note == "budget_policy: metered exhausted"


@allure.title("metered exhausted with no matching alt falls through unchanged")
def test_metered_exhausted_no_alt(policy_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: True)
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [])
    out = bp.apply_budget_policy(_decision(complexity="medium"), "plain task", Path("/tmp"), policy="auto")
    assert out == _decision(complexity="medium")


@allure.title("cursor warn skips non-ollama alts before selecting local LLM")
def test_cursor_warn_skips_non_ollama(policy_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp, "cursor_budget_warn", lambda **k: True)
    rag_alt = _decision(target="rag", matched=["r"])
    ollama_alt = _decision(target="ollama", matched=["o"], rationale="use ollama")
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [(1.0, rag_alt), (1.0, ollama_alt)])
    out = bp.apply_budget_policy(_decision(complexity="medium"), "task", Path("/tmp"), policy="auto")
    assert out.target == "ollama" and out.note == "budget_policy: cursor warn"


@allure.title("hybrid escalation falls through when no ollama available")
def test_hybrid_escalation_fallthrough(policy_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: True)
    rag_alt = _decision(target="rag", matched=["r"])
    ollama_alt = _decision(target="ollama", matched=["o"])
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [(1.0, rag_alt), (1.0, ollama_alt)])
    monkeypatch.setattr("greedy_token.wrappers.ollama_available", lambda: False)
    out = bp.apply_budget_policy(
        _decision(complexity="high"), "please escalate this", Path("/tmp"), policy="hybrid"
    )
    assert out.target == "cursor"


@allure.title("metered exhausted reroutes cursor→cheaper tier, skipping unavailable ollama")
def test_metered_exhausted_reroute(policy_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: True)
    ollama_alt = _decision(target="ollama", matched=["o"], confidence=1.0, rationale="use ollama")
    rag_alt = _decision(target="rag", matched=["r"], confidence=1.0, rationale="use rag")
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [(1.0, ollama_alt), (1.0, rag_alt)])
    monkeypatch.setattr("greedy_token.wrappers.ollama_available", lambda: False)
    out = bp.apply_budget_policy(_decision(complexity="medium"), "task", Path("/tmp"), policy="auto")
    assert out.target == "rag" and out.note == "budget_policy: metered exhausted"


@allure.title("cursor warn biases medium cursor task to available local LLM")
def test_cursor_warn_bias(policy_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp, "cursor_budget_warn", lambda **k: True)
    ollama_alt = _decision(target="ollama", matched=["o"], rationale="use ollama")
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [(1.0, ollama_alt)])
    out = bp.apply_budget_policy(_decision(complexity="medium"), "task", Path("/tmp"), policy="auto")
    assert out.target == "ollama" and out.note == "budget_policy: cursor warn"


@allure.title("cursor warn falls through when local LLM unavailable")
def test_cursor_warn_unavailable(policy_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp, "cursor_budget_warn", lambda **k: True)
    ollama_alt = _decision(target="ollama", matched=["o"])
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [(1.0, ollama_alt)])
    monkeypatch.setattr("greedy_token.wrappers.ollama_available", lambda: False)
    out = bp.apply_budget_policy(_decision(complexity="medium"), "task", Path("/tmp"), policy="auto")
    assert out.target == "cursor"


@allure.title("hybrid policy blocks escalation without metered headroom")
def test_hybrid_escalation_block(policy_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: True)
    ollama_alt = _decision(target="ollama", matched=["o"], rationale="use ollama")
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [(1.0, ollama_alt)])
    out = bp.apply_budget_policy(
        _decision(complexity="high"), "please escalate this", Path("/tmp"), policy="hybrid"
    )
    assert out.target == "ollama" and out.note == "budget_policy: hybrid"


@allure.title("deprecated local model appends pull hint for ollama route")
def test_deprecated_local_hint(policy_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp, "run_doctor", lambda **k: _rep(deprecated=True))
    out = bp.apply_budget_policy(_decision(target="ollama", complexity="medium"), "task", Path("/tmp"), policy="auto")
    assert "ollama pull qwen2.5-coder:7b" in out.rationale


@allure.title("run_doctor errors are swallowed; exhausted mode annotates note")
def test_doctor_error_and_exhausted_note(policy_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp, "run_doctor", lambda **k: (_ for _ in ()).throw(RuntimeError("probe fail")))
    monkeypatch.setattr(bp, "headroom", lambda **k: _snap(mode="exhausted"))
    out = bp.apply_budget_policy(_decision(target="python", note="base"), "task", Path("/tmp"), policy="auto")
    assert "budget: metered exhausted" in out.note


@allure.title("policy_footer_extras returns lines and swallows errors")
def test_policy_footer_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("greedy_token.budget_ledger.format_budget_line", lambda **k: "budget line")
    monkeypatch.setattr(bp, "local_health_line", lambda: "health line")
    extras = bp.policy_footer_extras(root=None)
    assert extras == ["budget line", "health line"]

    monkeypatch.setattr(
        "greedy_token.budget_ledger.format_budget_line",
        lambda **k: (_ for _ in ()).throw(OSError("io")),
    )
    assert bp.policy_footer_extras(root=None) == []
