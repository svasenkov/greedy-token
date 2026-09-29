"""Public-contract tests for split-budget modules (fail_under=100)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import allure
import greedy_token.budget as budget
import greedy_token.budget_config as bc
import greedy_token.budget_ledger as bl
import greedy_token.budget_policy as bp
from greedy_token.budget_config import BudgetSettings
from greedy_token.budget_ledger import BudgetSnapshot
from greedy_token.calibration import SOURCE_FIXED
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


@allure.title("metered_spent_today without a path reads the env spend ledger + usage log")
def test_metered_spent_today_default_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.spend_ledger import reserve_spend, settle_spend

    monkeypatch.setenv("GREEDY_TOKEN_SPEND_LOG", str(tmp_path / "spend.jsonl"))
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(tmp_path / "usage.jsonl"))
    reserve_spend(reservation_id="t1", model_id="m", est_usd=0.01)
    settle_spend("t1", cost_usd=0.03)
    # path=None → ledger + usage env sources, not a test path.
    assert bl.metered_spent_today() == pytest.approx(0.03)


@allure.title("aggregate_budget skips metered events already mirrored into the spend ledger")
def test_aggregate_budget_spend_ref_dedup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_SPEND_LOG", str(tmp_path / "spend.jsonl"))
    log = tmp_path / "usage.jsonl"
    now = bl._today_utc()
    log.write_text(
        json.dumps({"ts": f"{now}T10:00:00Z", "billing": {"tier": "metered", "cost_usd": 0.5}}) + "\n"
        # spend_ref marks the event as already counted in spend.jsonl —
        # adding its 9.0 here would double-count the same paid call.
        + json.dumps(
            {"ts": f"{now}T11:00:00Z", "billing": {"tier": "metered", "cost_usd": 9.0}, "spend_ref": "rX"}
        )
        + "\n",
        encoding="utf-8",
    )
    snap = bl.aggregate_budget(path=log, settings=_settings())
    assert snap.metered_spent_usd == pytest.approx(0.5)


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


# ---------------------------------------------------------------------------
# Mutation-hardening: exact-output asserts for footer formatting and events.
# ---------------------------------------------------------------------------


@pytest.fixture
def footer_env(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Deterministic footer rendering: no policy extras, ollama offline."""
    monkeypatch.setattr(budget, "ollama_available", lambda: False)
    monkeypatch.setattr(budget, "_policy_footer_lines", lambda root: [])
    monkeypatch.delenv("GREEDY_LLM_MODEL_ID", raising=False)
    monkeypatch.delenv("GREEDY_TOKEN_LOG", raising=False)
    return minimal_workspace


@allure.title("_billing_short renders every tier label exactly")
def test_billing_short_labels(footer_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert budget._billing_short("tool") == "free tier"
    assert budget._billing_short("python") == "free tier"
    assert budget._billing_short("rag") == "docs/rag"
    assert budget._billing_short("rag", rag_hits=3) == "docs/rag, 3 chunk(s)"
    assert budget._billing_short("cursor") == "expensive LLM"
    assert budget._billing_short("zzz") == "zzz"
    llm = budget.get_cheap_llm_settings()
    assert budget._billing_short("ollama", ollama_eval_tokens=1234, root=footer_env) == (
        f"cheap LLM ({llm.model}, ~1,234 eval, local free)"
    )
    assert budget._billing_short("ollama", root=footer_env) == (
        f"cheap LLM ({llm.model}, local free)"
    )
    monkeypatch.setenv("GREEDY_LLM_MODEL_ID", "m9")
    assert budget._billing_short("ollama", root=footer_env) == (
        f"cheap LLM (m9/{llm.model}, local free)"
    )


@allure.title("_cheap_billing_note distinguishes metered from local free")
def test_cheap_billing_note(footer_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert budget._cheap_billing_note(footer_env) == "local free"
    monkeypatch.setenv("GREEDY_LLM_MODEL_ID", "unknown-id")
    assert budget._cheap_billing_note(footer_env) == "local free"
    import yaml

    (footer_env / ".greedy-token.yaml").write_text(yaml.safe_dump({
        "llm": {"cheap": {"models": [{
            "id": "metered-m", "enabled": True, "model": "x",
            "profiles": ["*"], "billing": "metered", "cost_per_1m_usd": 0.1,
        }]}}
    }), encoding="utf-8")
    monkeypatch.setenv("GREEDY_LLM_MODEL_ID", "metered-m")
    assert budget._cheap_billing_note(footer_env) == "metered"


@allure.title("spent_hint renders every branch exactly")
def test_spent_hint_variants() -> None:
    assert budget.spent_hint("tool", 0) == "script — 0 LLM spend"
    assert budget.spent_hint("tool", 0, "rg") == "ripgrep on disk — 0 LLM spend"
    assert budget.spent_hint("python", 0, "rg") == "ripgrep on disk — 0 LLM spend"
    assert budget.spent_hint("ollama", 5) == "cheap LLM — local/cheap spend"
    assert budget.spent_hint("rag", 0) == "docs/rag — no chunks counted"
    assert budget.spent_hint("rag", 1) == "docs/rag chunks read into context"
    assert budget.spent_hint("cursor", 9) == "expensive LLM path — same order as baseline"
    assert budget.spent_hint("zzz", 5) == ""


@allure.title("format_spent_line pins label, indent and hint join")
def test_format_spent_line_exact() -> None:
    assert budget.format_spent_line(1234, tier="tool", executor_sub="rg") == (
        "  Spent (MCP executor, LLM tokens): ~1,234  (ripgrep on disk — 0 LLM spend)"
    )
    assert budget.format_spent_line(0, tier="zzz") == (
        "  Spent (MCP executor, LLM tokens): ~0"
    )
    assert budget.format_spent_line(5, note="custom", indent="-") == (
        "-Spent (MCP executor, LLM tokens): ~5  (custom)"
    )


@allure.title("format_savings_lines pins every field and the fallback saved math")
def test_format_savings_lines_exact() -> None:
    lines = budget.format_savings_lines(
        baseline=100, spent=30, title="T", tier="cursor",
        executor_sub="cursor", source="measured",
    )
    assert lines == [
        "T (baseline: measured)",
        "  Baseline (naive agent chat):  ~100  (measured)",
        "  Spent (MCP executor, LLM tokens): ~30"
        "  (expensive LLM path — same order as baseline)",
        "  Saved:             ~70  (= baseline − spent; baseline: measured)",
    ]


@allure.title("_format_tier_alternatives pins labels, order, suffixes and selected est")
def test_tier_alternatives_exact(footer_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.router import route_task_all_tiers

    scan = dict(route_task_all_tiers("probe task", footer_env))
    lines = budget._format_tier_alternatives(
        "probe task", footer_env, "tool", selected_spent=0
    )
    suffix_by_tier = {
        "tool": "  ← this call",
        "python": "  · 0 LLM",
        "ollama": "  · unavailable (would fall back to expensive LLM)",
    }
    label_by_tier = {
        "tool": "rg (disk search)",
        "python": "python (script)",
        "ollama": "ollama (cheap LLM)",
        "rag": "rag (docs/rag read)",
        "cursor": "cursor (expensive LLM)",
    }
    expected = ["Tier alternatives (estimated):"]
    for tier, _dec in scan.items():
        est = 0 if tier == "tool" else _dec.est_tokens
        expected.append(
            f"  {label_by_tier[tier]:<26} ~{est:>6,}{suffix_by_tier.get(tier, '')}"
        )
    assert lines == expected
    # ollama online renders the provider/model suffix instead
    monkeypatch.setattr(budget, "ollama_available", lambda: True)
    llm = budget.get_cheap_llm_settings()
    lines = budget._format_tier_alternatives(
        "probe task", footer_env, "cursor", selected_spent=999
    )
    row = next(line for line in lines if "ollama" in line)
    assert row.endswith(f"  · {llm.provider}/{llm.model}, cheap")
    cursor_row = next(line for line in lines if "cursor (expensive LLM)" in line)
    assert cursor_row.endswith("~   999  ← this call")


@allure.title("_build_tool_footer_context: executed=False keeps saved as a potential")
def test_footer_context_fields(footer_env: Path) -> None:
    ctx = budget._build_tool_footer_context(
        "probe task", footer_env, tier="tool", est_tokens=0,
        route_id="mcp-search", executor_sub="rg", duration_ms=42, executed=False,
    )
    assert ctx.saved == 0
    assert ctx.saved_note.endswith("if run)")
    assert "not executed" in ctx.saved_note
    assert ctx.time_saved is None
    assert ctx.sub_label == "ripgrep on disk"
    assert ctx.billing_short == "free tier"
    assert ctx.breakdown.rules == 6
    assert ctx.breakdown.task == budget.count_tokens("probe task").tokens
    assert ctx.breakdown.overhead == 6000
    assert ctx.breakdown.source == "default-estimate"
    assert ctx.baseline == ctx.breakdown.total
    # a failed executed call zeroes saved without the "not executed" note
    ctx = budget._build_tool_footer_context(
        "probe task", footer_env, tier="tool", est_tokens=0,
        executor_sub="rg", task_success=False, executed=True,
    )
    assert ctx.saved == 0 and ctx.saved_note == "" and ctx.time_saved is None


@allure.title("_resolve_footer_style: explicit arg wins, else settings")
def test_resolve_footer_style(footer_env: Path) -> None:
    assert budget._resolve_footer_style(footer_env, "markdown") == "markdown"
    from greedy_token.settings import get_footer_settings

    assert budget._resolve_footer_style(footer_env, None) == get_footer_settings(
        footer_env
    ).style


@allure.title("_policy_footer_lines returns extras and swallows errors")
def test_policy_footer_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "greedy_token.budget_policy.policy_footer_extras",
        lambda *, root=None: ["budget row"],
    )
    assert budget._policy_footer_lines(tmp_path) == ["budget row"]
    monkeypatch.setattr(
        "greedy_token.budget_policy.policy_footer_extras",
        lambda *, root=None: (_ for _ in ()).throw(ValueError("x")),
    )
    assert budget._policy_footer_lines(tmp_path) == []


@allure.title("compact footer: exact rendered text")
def test_footer_compact_exact(footer_env: Path) -> None:
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="tool", est_tokens=0,
        route_id="mcp-search", executor_sub="rg", duration_ms=42, style="compact",
    )
    assert footer == (
        "\n---\n"
        "> **Greedy token** · `rg` · 42ms · saved **~6,008** · ~16s"
        " (baseline: default-estimate)\n"
        "> spent ~0 · naive ~6,008 · free tier · mcp-search\n"
        "---"
    )


@allure.title("markdown footer: exact rendered text")
def test_footer_markdown_exact(footer_env: Path) -> None:
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="tool", est_tokens=0,
        route_id="mcp-search", executor_sub="rg", duration_ms=42, style="markdown",
    )
    assert footer == (
        "\n---\n"
        "### Greedy token · `rg` · 42ms\n"
        "\n"
        "| | tokens | time |\n"
        "|:--|--:|--:|\n"
        "| spent | ~0 | 42ms |\n"
        "| naive agent chat (default-estimate) | ~6,008 | ~16s (default-estimate) |\n"
        "| **saved** (baseline: default-estimate) | **~6,008** | **~16s** |\n"
        "\n"
        "free tier · `mcp-search`\n"
        "---"
    )


@allure.title("full footer: exact rendered text")
def test_footer_full_exact(footer_env: Path) -> None:
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="tool", est_tokens=0,
        route_id="mcp-search", executor_sub="rg", duration_ms=42, style="full",
    )
    assert footer == (
        "\n---\n"
        "Greedy token\n"
        "\n"
        "This call\n"
        "  Executor: rg — ripgrep on disk\n"
        "  Route: mcp-search\n"
        "  Duration: 42 ms\n"
        "  Spent (MCP executor, LLM tokens): ~0  (ripgrep on disk — 0 LLM spend)\n"
        "  Billing: free tier — not expensive LLM\n"
        "\n"
        "Agent chat (naive — same task, no MCP tool)\n"
        "  Always-on rules: ~6  (measured)\n"
        "  Task prompt:     ~2  (measured)\n"
        "  Agent overhead:  ~6,000  (default-estimate)\n"
        "  Total (naive agent chat):  ~6,008\n"
        "  Naive wall-clock: ~16s  (default-estimate)\n"
        "\n"
        "Tier alternatives (estimated):\n"
        "  rg (disk search)           ~     0  ← this call\n"
        "  python (script)            ~     0  · 0 LLM\n"
        "  ollama (cheap LLM)         ~     2  · unavailable (would fall back to"
        " expensive LLM)\n"
        "  rag (docs/rag read)        ~ 1,802\n"
        "  cursor (expensive LLM)     ~ 6,008\n"
        "\n"
        "Saved vs naive agent chat (baseline: default-estimate)\n"
        "  Baseline (naive agent chat):  ~6,008  (default-estimate)\n"
        "  Spent (MCP executor, LLM tokens): ~0  (ripgrep on disk — 0 LLM spend)\n"
        "  Saved:             ~6,008  (= baseline − spent; baseline: default-estimate)\n"
        "  Time saved:      ~16s  (= naive wall-clock − duration; time baseline:"
        " default-estimate)\n"
        "\n"
        "Note: MCP in Agent chat still uses agent tokens for rules + your message +\n"
        "agent reply. Only cheap LLM / rg / rag rows avoid the expensive LLM path.\n"
        "Time saved is an estimate vs a naive agent turn (not a stopwatch)."
    )


@allure.title("format_tool_footer dispatches on the resolved style")
def test_format_tool_footer_dispatch(footer_env: Path) -> None:
    out = budget.format_tool_footer(
        "probe task", footer_env, tier="tool", est_tokens=0, executor_sub="rg"
    )
    assert out.startswith("\n---\n> **Greedy token**")


@allure.title("rag_est_tokens counts body, file chunk and excerpt sources")
def test_rag_est_tokens_sources(footer_env: Path) -> None:
    from greedy_token.rag_search import RagHit
    from greedy_token.tokens import count_tokens

    # body wins over the chunk file entirely
    hits = [
        RagHit(chunk_id="c1", path="nope.md", domain="d", score=1.0,
               excerpt="excerpt", body="body text"),
        RagHit(chunk_id="c2", path="docs/chunk.md", domain="d", score=1.0,
               excerpt="excerpt"),
    ]
    (footer_env / "docs" / "chunk.md").write_bytes("file body words é".encode() + b"\xff")
    expected = count_tokens("body text").tokens + count_tokens(
        (footer_env / "docs" / "chunk.md").read_text(encoding="utf-8", errors="replace")
    ).tokens
    assert budget.rag_est_tokens(hits, footer_env) == expected
    # a missing chunk file falls back to the excerpt
    hits = [RagHit(chunk_id="c3", path="docs/missing.md", domain="d", score=1.0,
                   excerpt="excerpt here")]
    assert budget.rag_est_tokens(hits, footer_env) == count_tokens("excerpt here").tokens


@allure.title("log_tool_usage forwards every field into the route event")
def test_log_tool_usage_fields(footer_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(footer_env / "usage.jsonl"))
    decision = RouteDecision(
        target="tool", route_id="mcp-search", confidence=0.9,
        confidence_source="formula", matched=["x"], command=None,
        note="", domains=[], est_tokens=123,
    )
    budget.log_tool_usage(
        cmd="search", task="probe task", root=footer_env, decision=decision,
        executed=False, est_tokens_override=7, rag_hits=3, duration_ms=9,
        tier_scan=[{"tier": "tool"}], outcome_success=True,
        operation_id="op-9", gate=None,
    )
    rows = [
        json.loads(line)
        for line in (footer_env / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    e = rows[0]
    assert e["cmd"] == "search"
    assert e["task"] == "probe task"
    assert e["root"] == str(footer_env)
    assert e["route_id"] == "mcp-search"
    assert e["selected_tier"] == "tool"
    assert e["est_tokens"] == 7
    assert e["executor"]["rag_hits"] == 3
    assert e["executor"]["executed"] is False
    assert e["duration_ms"] == 9
    assert e["tier_scan"] == [{"tier": "tool"}]
    assert e["operation_id"] == "op-9"
    assert e["phase"] == "recommended"
    assert e["savings_exclusion"] == "not_executed"


@allure.title("cursor_baseline_breakdown exposes rules/task/overhead split")
def test_cursor_baseline_breakdown_fields(footer_env: Path) -> None:
    from greedy_token.tokens import count_tokens

    bd = budget.cursor_baseline_breakdown(footer_env, "probe task")
    assert bd.rules == 6
    assert bd.task == count_tokens("probe task").tokens
    assert bd.overhead == 6000
    assert bd.source == "default-estimate"
    assert bd.total == bd.rules + bd.task + bd.overhead


@allure.title("wrap_mcp_response appends the footer and logs both events")
def test_wrap_mcp_response_exact(footer_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(footer_env / "usage.jsonl"))
    out = budget.wrap_mcp_response(
        "result line", task="probe task", tier="tool", est_tokens=0,
        route_id="mcp-search", root=footer_env, log=True, duration_ms=42,
        executor_sub="rg", outcome="success", outcome_layer="executor",
        attempts=2, retries=1, escalations=["ollama"], result_status="produced",
    )
    assert out == (
        "result line"
        "\n---\n"
        "> **Greedy token** · `rg` · 42ms · saved **~6,008** · ~16s"
        " (baseline: default-estimate)\n"
        "> spent ~0 · naive ~6,008 · free tier · mcp-search\n"
        "---"
    )
    rows = [
        json.loads(line)
        for line in (footer_env / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    req = next(row for row in rows if row.get("cmd") == "mcp")
    assert req["task"] == "probe task"
    assert req["root"] == str(footer_env)
    assert req["route_id"] == "mcp-search"
    assert req["selected_tier"] == "tool"
    assert req["executor"]["executed"] is True
    assert req["est_tokens"] == 0
    assert req["duration_ms"] == 42
    assert req["tier_scan"] == []
    assert req["result_status"] == "produced"
    assert req["operation_id"]
    outcome = next(row for row in rows if row.get("event") == "route_outcome")
    assert outcome["outcome"] == "success"
    assert outcome["outcome_layer"] == "executor"
    assert outcome["attempts"] == 2
    assert outcome["retries"] == 1
    assert outcome["escalations"] == ["ollama"]
    assert outcome["exit_code"] == 0
    assert outcome["operation_id"] == req["operation_id"]


@allure.title("wrap_mcp_response without outcome logs only the request event")
def test_wrap_mcp_response_no_outcome(footer_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(footer_env / "usage.jsonl"))
    out = budget.wrap_mcp_response(
        "body", task="probe task", tier="tool", est_tokens=0,
        route_id="mcp-search", root=footer_env, log=True,
        outcome="failure", outcome_layer="pipeline",
    )
    rows = [
        json.loads(line)
        for line in (footer_env / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    outcome = next(row for row in rows if row.get("event") == "route_outcome")
    assert outcome["outcome"] == "failure"
    assert outcome["outcome_layer"] == "pipeline"
    assert outcome["attempts"] == 1
    assert outcome["exit_code"] == 1
    # failure outcome flips task_success → no savings credited
    assert "saved **~0**" in out


@allure.title("wrap_mcp_response with log=False writes no telemetry")
def test_wrap_mcp_response_no_log(footer_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(footer_env / "usage.jsonl"))
    budget.wrap_mcp_response(
        "body", task="probe task", tier="tool", est_tokens=0, root=footer_env, log=False
    )
    assert not (footer_env / "usage.jsonl").exists()


# ---------------------------------------------------------------------------
# Mutation-hardening round 2: boundary values, root propagation, gate args.
# ---------------------------------------------------------------------------


@allure.title("rag_est_tokens accumulates across mixed sources in order")
def test_rag_est_tokens_accumulates(footer_env: Path) -> None:
    from greedy_token.rag_search import RagHit
    from greedy_token.tokens import count_tokens

    (footer_env / "docs" / "chunk.md").write_text(
        "file body words", encoding="utf-8"
    )
    hits = [
        RagHit(chunk_id="c1", path="docs/chunk.md", domain="d", score=1.0,
               excerpt="ignored excerpt"),
        RagHit(chunk_id="c2", path="none.md", domain="d", score=1.0,
               excerpt="excerpt", body="body text"),
        RagHit(chunk_id="c3", path="docs/missing.md", domain="d", score=1.0,
               excerpt="tail excerpt"),
    ]
    expected = (
        count_tokens("file body words").tokens
        + count_tokens("body text").tokens
        + count_tokens("tail excerpt").tokens
    )
    assert budget.rag_est_tokens(hits, footer_env) == expected


@allure.title("rag_est_tokens pins utf-8 decoding on chunk files")
def test_rag_est_tokens_chunk_encoding(footer_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.rag_search import RagHit
    from greedy_token.tokens import count_tokens

    (footer_env / "docs" / "c.md").write_bytes("olé".encode() + b"\xff")
    seen: list[object] = []
    real_read_text = Path.read_text

    def spy(self: Path, *args, **kwargs):
        if self.name == "c.md":
            seen.append(kwargs.get("encoding", args[0] if args else "<unset>"))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    hits = [RagHit(chunk_id="c", path="docs/c.md", domain="d", score=1.0,
                   excerpt="x")]
    # utf-8 decode: "olé" + U+FFFD replacement char
    assert budget.rag_est_tokens(hits, footer_env) == count_tokens("olé\ufffd").tokens
    assert seen == ["utf-8"]


@allure.title("format_savings_lines: spent_note wins and zero saved stays zero")
def test_format_savings_lines_boundary() -> None:
    lines = budget.format_savings_lines(
        baseline=50, spent=50, title="T", tier="tool", executor_sub="rg",
        spent_note="custom-note", source="measured",
    )
    assert lines[2].endswith("(custom-note)")
    assert lines[3] == (
        "  Saved:             ~0  (= baseline − spent; baseline: measured)"
    )


@allure.title("tier alternatives keep the 0-LLM suffix on non-selected tool/python")
def test_tier_alternatives_tool_python_suffix(footer_env: Path) -> None:
    lines = budget._format_tier_alternatives(
        "probe task", footer_env, "cursor", selected_spent=999
    )
    tool_row = next(line for line in lines if "rg (disk search)" in line)
    assert tool_row.endswith("· 0 LLM")
    python_row = next(line for line in lines if "python (script)" in line)
    assert python_row.endswith("· 0 LLM")


@allure.title("tier alternatives pass the workspace root to the router")
def test_tier_alternatives_root(footer_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []
    real = budget.route_task_all_tiers

    def spy(task: str, root: Path):
        seen.append(root)
        return real(task, root)

    monkeypatch.setattr(budget, "route_task_all_tiers", spy)
    budget._format_tier_alternatives("probe task", footer_env, "tool")
    # the full footer also routes the scan through the same ctx.root
    budget.format_tool_footer(
        "probe task", footer_env, tier="tool", est_tokens=0,
        executor_sub="rg", style="full",
    )
    assert seen == [footer_env, footer_env]


@allure.title("footer context: unknown executor_sub falls back to the raw name")
def test_footer_context_unknown_sub(footer_env: Path) -> None:
    ctx = budget._build_tool_footer_context(
        "probe task", footer_env, tier="tool", est_tokens=0,
        executor_sub="mysub",
    )
    assert ctx.executor_sub == "mysub"
    assert ctx.sub_label == "mysub"


@allure.title("footer context: cursor tier earns no savings and no time saved")
def test_footer_context_cursor_tier(footer_env: Path) -> None:
    ctx = budget._build_tool_footer_context(
        "probe task", footer_env, tier="cursor", est_tokens=100,
        duration_ms=42,
    )
    assert ctx.saved == 0
    assert ctx.time_saved == 0
    assert ctx.billing_short == "expensive LLM"


@allure.title("footer context forwards rag_hits/eval tokens and root into billing")
def test_footer_context_billing_variants(
    footer_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = budget._build_tool_footer_context(
        "probe task", footer_env, tier="rag", est_tokens=50, rag_hits=3,
    )
    assert ctx.billing_short == "docs/rag, 3 chunk(s)"
    ctx = budget._build_tool_footer_context(
        "probe task", footer_env, tier="ollama", est_tokens=50,
        ollama_eval_tokens=1234,
    )
    llm = budget.get_cheap_llm_settings()
    assert ctx.billing_short == f"cheap LLM ({llm.model}, ~1,234 eval, local free)"
    # a metered cheap model under this root flips the note
    import yaml

    (footer_env / ".greedy-token.yaml").write_text(yaml.safe_dump({
        "routes_file": "workspace-routes.yaml",
        "llm": {"cheap": {"models": [{
            "id": "metered-m", "enabled": True, "model": "x",
            "profiles": ["*"], "billing": "metered", "cost_per_1m_usd": 0.1,
        }]}},
    }), encoding="utf-8")
    monkeypatch.setenv("GREEDY_LLM_MODEL_ID", "metered-m")
    ctx = budget._build_tool_footer_context(
        "probe task", footer_env, tier="ollama", est_tokens=50,
    )
    assert ctx.billing_short == "cheap LLM (metered-m/x, metered)"


@allure.title("footer context helpers receive the workspace root")
def test_footer_context_root_propagation(
    footer_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, list] = {"audit": [], "saved": [], "breakdown": []}
    real_audit = budget.audit_context
    real_saved = budget.cursor_saved_for
    real_breakdown = budget.cursor_baseline_breakdown

    monkeypatch.setattr(
        budget, "audit_context",
        lambda root, *a, **k: seen["audit"].append(root) or real_audit(root, *a, **k),
    )
    monkeypatch.setattr(
        budget, "cursor_saved_for",
        lambda root, task, est, tier: (
            seen["saved"].append((root, task, est, tier))
            or real_saved(root, task, est, tier)
        ),
    )
    monkeypatch.setattr(
        budget, "cursor_baseline_breakdown",
        lambda root, task: seen["breakdown"].append((root, task))
        or real_breakdown(root, task),
    )
    budget._build_tool_footer_context(
        "probe task", footer_env, tier="tool", est_tokens=7,
    )
    assert seen["audit"] == [footer_env]
    assert seen["saved"] == [(footer_env, "probe task", 7, "tool")]
    assert seen["breakdown"] == [(footer_env, "probe task")]


@allure.title("compact footer: no duration/route clutter when unset")
def test_footer_compact_minimal(footer_env: Path) -> None:
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="tool", est_tokens=0,
        executor_sub="rg", style="compact",
    )
    assert footer == (
        "\n---\n"
        "> **Greedy token** · `rg` · saved **~6,008**"
        " (baseline: default-estimate)\n"
        "> spent ~0 · naive ~6,008 · free tier\n"
        "---"
    )
    assert "XXXX" not in footer and "Nonems" not in footer


@allure.title("compact footer: zero and one-ms time-saved boundaries")
def test_footer_compact_time_saved_boundary(footer_env: Path) -> None:
    baseline = 6008
    naive_ms = budget.naive_agent_ms(baseline)
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="tool", est_tokens=0,
        executor_sub="rg", duration_ms=naive_ms, style="compact",
    )
    assert "· ~0ms" not in footer
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="tool", est_tokens=0,
        executor_sub="rg", duration_ms=naive_ms - 1, style="compact",
    )
    assert "· ~1ms" in footer


@allure.title("markdown footer: missing duration renders em-dash cells")
def test_footer_markdown_no_duration(footer_env: Path) -> None:
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="tool", est_tokens=0,
        executor_sub="rg", duration_ms=None, style="markdown",
    )
    assert footer == (
        "\n---\n"
        "### Greedy token · `rg`\n"
        "\n"
        "| | tokens | time |\n"
        "|:--|--:|--:|\n"
        "| spent | ~0 | — |\n"
        "| naive agent chat (default-estimate) | ~6,008 | ~16s (default-estimate) |\n"
        "| **saved** (baseline: default-estimate) | **~6,008** | **~—** |\n"
        "\n"
        "free tier\n"
        "---"
    )


@allure.title("full footer: tier-specific billing lines for rag/ollama/cursor/python")
def test_footer_full_tier_billing(footer_env: Path) -> None:
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="rag", est_tokens=50, rag_hits=3,
        executor_sub="rg", style="full",
    )
    assert "  Billing: read docs/rag, 3 chunk(s) — small context vs expensive LLM chat" in footer
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="rag", est_tokens=50, rag_hits=None,
        executor_sub="rg", style="full",
    )
    assert "  Billing: read docs/rag — small context vs expensive LLM chat" in footer
    assert "chunk(s)" not in footer
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="ollama", est_tokens=50,
        ollama_eval_tokens=1234, executor_sub="rg", style="full",
    )
    assert ", ~1,234 eval tokens" in footer
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="ollama", est_tokens=50,
        executor_sub="rg", style="full",
    )
    llm = budget.get_cheap_llm_settings()
    assert (
        f"\n  Billing: cheap LLM ({llm.provider}/{llm.model}, local free)"
        " — not expensive path\n"
    ) in footer
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="cursor", est_tokens=50,
        executor_sub="rg", style="full",
    )
    assert (
        "\n  Billing: expensive LLM (agent chat) — full context + reply\n"
    ) in footer
    footer = budget.format_tool_footer(
        "probe task", footer_env, tier="python", est_tokens=0,
        executor_sub="rg", style="full",
    )
    assert "  Billing: free tier — not expensive LLM" in footer


@allure.title("policy footer lines receive the workspace root")
def test_policy_footer_lines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path | None] = []
    monkeypatch.setattr(
        "greedy_token.budget_policy.policy_footer_extras",
        lambda *, root=None: seen.append(root) or [],
    )
    assert budget._policy_footer_lines(tmp_path) == []
    assert seen == [tmp_path]
    # renderers forward ctx.root unchanged
    calls: list[Path] = []
    monkeypatch.setattr(
        budget, "_policy_footer_lines",
        lambda root: calls.append(root) or [f"row {root.name}"],
    )
    for style in ("compact", "markdown", "full"):
        out = budget.format_tool_footer(
            "probe task", tmp_path, tier="tool", est_tokens=0,
            executor_sub="rg", style=style,
        )
        assert "XXXX" not in out
    # markdown style renders no policy extras; compact and full do
    assert calls == [tmp_path, tmp_path]


@allure.title("format_tool_footer reads the style from workspace config")
def test_footer_style_from_config(footer_env: Path) -> None:
    (footer_env / ".greedy-token.yaml").write_text(
        "routes_file: workspace-routes.yaml\nfooter:\n  style: full\n",
        encoding="utf-8",
    )
    out = budget.format_tool_footer(
        "probe task", footer_env, tier="tool", est_tokens=0, executor_sub="rg",
    )
    assert out.startswith("\n---\nGreedy token")
    assert budget._resolve_footer_style(footer_env, None) == "full"


@allure.title("footer style resolution passes the workspace root")
def test_footer_style_root_propagation(
    footer_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path | None] = []
    real = budget.get_footer_settings
    monkeypatch.setattr(
        budget, "get_footer_settings",
        lambda root=None: seen.append(root) or real(root),
    )
    budget.format_tool_footer(
        "probe task", footer_env, tier="tool", est_tokens=0, executor_sub="rg",
    )
    assert seen == [footer_env]


@allure.title("log_tool_usage: outcome_success=False marks the event task_failed")
def test_log_tool_usage_outcome_failed(footer_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(footer_env / "usage.jsonl"))
    decision = RouteDecision(
        target="tool", route_id="mcp-search", confidence=0.9,
        confidence_source="formula", matched=["x"], command=None,
        note="", domains=[], est_tokens=123,
    )
    budget.log_tool_usage(
        cmd="search", task="probe task", root=footer_env, decision=decision,
        executed=True, outcome_success=False,
    )
    rows = [
        json.loads(line)
        for line in (footer_env / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[0]["savings_exclusion"] == "task_failed"
    assert rows[0]["cursor_saved"] == 0


@allure.title("wrap_mcp_response forwards every argument to footer, gate and log")
def test_wrap_mcp_response_forwards_all_fields(
    footer_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {"footer": [], "log": [], "outcome": [], "gate": []}
    real_footer = budget.format_tool_footer
    real_gate = budget.evaluate_result_gate

    monkeypatch.setattr(
        budget, "format_tool_footer",
        lambda *a, **k: captured["footer"].append((a, k)) or real_footer(*a, **k),
    )
    monkeypatch.setattr(
        budget, "log_tool_usage",
        lambda **k: captured["log"].append(k),
    )
    monkeypatch.setattr(
        budget, "append_event",
        lambda e: captured["outcome"].append(e),
    )
    monkeypatch.setattr(
        budget, "evaluate_result_gate",
        lambda **k: captured["gate"].append(k) or real_gate(**k),
    )
    out = budget.wrap_mcp_response(
        "body", task="probe task", tier="tool", est_tokens=7,
        route_id="mcp-search", root=footer_env, log=True, duration_ms=42,
        rag_hits=3, executor_sub="rg", ollama_eval_tokens=9,
        outcome="success", outcome_layer="executor", attempts=2,
        retries=1, escalations=["ollama"], executed=True,
        result_status="produced",
    )
    assert "body" in out and "saved **~6,001**" in out
    footer_args, footer_kwargs = captured["footer"][0]
    assert footer_args == ("probe task", footer_env)
    assert footer_kwargs == {
        "tier": "tool",
        "est_tokens": 7,
        "route_id": "mcp-search",
        "executor_sub": "rg",
        "duration_ms": 42,
        "rag_hits": 3,
        "ollama_eval_tokens": 9,
        "task_success": True,
        "executed": True,
    }
    (log_kwargs,) = captured["log"]
    assert log_kwargs["cmd"] == "mcp"
    assert log_kwargs["task"] == "probe task"
    assert log_kwargs["root"] == footer_env
    assert log_kwargs["executed"] is True
    assert log_kwargs["est_tokens_override"] == 7
    assert log_kwargs["rag_hits"] == 3
    assert log_kwargs["duration_ms"] == 42
    assert log_kwargs["tier_scan"] == []
    assert log_kwargs["outcome_success"] is True
    decision = log_kwargs["decision"]
    assert decision.target == "tool"
    assert decision.route_id == "mcp-search"
    assert decision.confidence == 1.0
    assert decision.confidence_source == SOURCE_FIXED
    assert decision.matched == []
    assert decision.note == ""
    assert decision.domains == []
    assert decision.est_tokens == 7
    gate = log_kwargs["gate"]
    assert gate is not None and gate.action == "accepted"
    (gate_kwargs,) = captured["gate"]
    assert gate_kwargs == {
        "started": True,
        "result_status": "produced",
        "tier": "tool",
        "ok": True,
    }
    (outcome_event,) = captured["outcome"]
    assert outcome_event["root"] == str(footer_env)
    assert outcome_event["outcome"] == "success"
    assert outcome_event["duration_ms"] == 42
    assert outcome_event["operation_id"] == log_kwargs["operation_id"]


@allure.title("wrap_mcp_response: outcome=None skips the gate and keeps task_success None")
def test_wrap_mcp_response_no_outcome_gate(
    footer_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {"footer": [], "log": [], "outcome": [], "gate": []}
    monkeypatch.setattr(
        budget, "format_tool_footer",
        lambda *a, **k: captured["footer"].append((a, k)) or "FOOTER",
    )
    monkeypatch.setattr(
        budget, "log_tool_usage",
        lambda **k: captured["log"].append(k),
    )
    monkeypatch.setattr(
        budget, "append_event",
        lambda e: captured["outcome"].append(e),
    )
    monkeypatch.setattr(
        budget, "evaluate_result_gate",
        lambda **k: captured["gate"].append(k),
    )
    out = budget.wrap_mcp_response(
        "body", task="t", tier="tool", est_tokens=0,
        root=footer_env, log=True,
    )
    assert out == "bodyFOOTER"
    _args, footer_kwargs = captured["footer"][0]
    assert footer_kwargs["task_success"] is None
    assert footer_kwargs["route_id"] == ""
    (log_kwargs,) = captured["log"]
    assert log_kwargs["outcome_success"] is None
    assert captured["gate"] == []
    assert captured["outcome"] == []


@allure.title("wrap_mcp_response resolves a missing root via find_workspace_root")
def test_wrap_mcp_response_root_fallback(
    footer_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        budget, "find_workspace_root",
        lambda: calls.append("x") or footer_env,
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        budget, "format_tool_footer",
        lambda *a, **k: captured.update(args=a, kwargs=k) or "F",
    )
    budget.wrap_mcp_response(
        "body", task="t", tier="tool", est_tokens=0, log=False,
    )
    assert calls == ["x"]
    assert captured["args"][1] == footer_env


@allure.title("wrap_mcp_response strips only trailing body whitespace")
def test_wrap_mcp_response_body_strip(footer_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(budget, "format_tool_footer", lambda *a, **k: "F")
    out = budget.wrap_mcp_response(
        "  pad  ", task="t", tier="tool", est_tokens=0,
        root=footer_env, log=False,
    )
    assert out == "  padF"


@allure.title("wrap_mcp_response: contract tier not_evaluated fails closed")
def test_wrap_mcp_response_contract_tier_unverified(
    footer_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(footer_env / "usage.jsonl"))
    budget.wrap_mcp_response(
        "body", task="probe task", tier="python", est_tokens=0,
        root=footer_env, log=True, result_status="not_evaluated",
        outcome="success",
    )
    rows = [
        json.loads(line)
        for line in (footer_env / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    req = next(row for row in rows if row.get("cmd") == "mcp")
    assert req["result_status"] == "not_evaluated"
    assert req["savings_exclusion"] == "unverified_result"
    assert req["cursor_saved"] == 0


@allure.title("full footer routes the tier scan through ctx.root")
def test_full_footer_tier_scan_root(
    footer_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []
    real = budget.route_task_all_tiers
    monkeypatch.setattr(
        budget, "route_task_all_tiers",
        lambda task, root: seen.append(root) or real(task, root),
    )
    budget.format_tool_footer(
        "probe task", footer_env, tier="tool", est_tokens=0,
        executor_sub="rg", style="full",
    )
    assert seen == [footer_env]


# ---------------------------------------------------------------------------
# Mutation-gap coverage: exact BudgetSnapshot / formatting / tier contracts
# ---------------------------------------------------------------------------


class _FixedDT(datetime):
    @classmethod
    def now(cls, tz=None):
        calls.append(tz)
        return cls(2025, 7, 15, 10, 30, 45, tzinfo=UTC)


calls: list = []


def _pin_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    calls.clear()
    monkeypatch.setattr(bl, "datetime", _FixedDT)


@allure.title("_period_start honours the calendar-month fields and rolling window")
def test_period_start_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_clock(monkeypatch)
    cal = bl._period_start(_settings(period="calendar_month"))
    assert cal == datetime(2025, 7, 1, 0, 0, 0, tzinfo=UTC)
    roll = bl._period_start(_settings(period="rolling_30d"))
    assert roll == datetime(2025, 6, 15, 10, 30, 45, tzinfo=UTC)
    assert calls == [UTC, UTC]


@allure.title("_period_label renders '30d' or the UTC month abbreviation")
def test_period_label_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_clock(monkeypatch)
    assert bl._period_label(_settings(period="rolling_30d")) == "30d"
    assert bl._period_label(_settings()) == "Jul"
    assert calls[-1] is UTC


@allure.title("_today_utc and _midnight_utc are anchored to UTC now")
def test_today_and_midnight(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_clock(monkeypatch)
    assert bl._today_utc() == "2025-07-15"
    assert bl._midnight_utc() == datetime(2025, 7, 15, 0, 0, 0, tzinfo=UTC)
    assert calls == [UTC, UTC]


@allure.title("_billing_tier_from_event maps every spelling to its tier")
def test_billing_tier_all_branches() -> None:
    assert bl._billing_tier_from_event({"billing": {"tier": "metered"}}) == "metered"
    assert bl._billing_tier_from_event({"billing": {"tier": "cheap"}}) == "cheap"
    assert bl._billing_tier_from_event(
        {"billing": {"tier": "cursor_estimate"}}
    ) == "cursor_estimate"
    # Non-dict billing falls through to legacy fields.
    assert bl._billing_tier_from_event(
        {"billing": "x", "billing_tier": "expensive"}
    ) == "metered"
    assert bl._billing_tier_from_event({"selected_tier": "cursor"}) == "cursor_estimate"
    assert bl._billing_tier_from_event({"selected_tier": "tool"}) == "cheap"
    assert bl._billing_tier_from_event({"selected_tier": "rag"}) == "cheap"
    assert bl._billing_tier_from_event({"selected_tier": "python"}) == "cheap"
    assert bl._billing_tier_from_event({"selected_tier": "ollama"}) == "cheap"
    assert bl._billing_tier_from_event({"selected_tier": "zzz"}) == "cursor_estimate"


@allure.title("_cost_from_event prefers explicit cost, survives malformed first field")
def test_cost_from_event_precedence() -> None:
    assert bl._cost_from_event(
        {"billing": {"cost_usd": "bad"}, "cost_usd": 2.5, "selected_tier": "python"},
        cursor_rate=15.0,
    ) == 2.5
    assert bl._cost_from_event(
        {"billing": {"cost_usd": 0.25}, "cost_usd": 9.9}, cursor_rate=15.0
    ) == 0.25
    # cursor_estimate without explicit cost derives from the baseline.
    ev = {"selected_tier": "cursor", "cursor_baseline": 2_000_000}
    assert bl._cost_from_event(ev, cursor_rate=15.0) == pytest.approx(30.0)
    ev0 = {"selected_tier": "cursor"}
    assert bl._cost_from_event(ev0, cursor_rate=15.0) == 0.0


@allure.title("aggregate_budget: every snapshot field is exact with crafted events")
def test_aggregate_budget_exact_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import greedy_token.spend_ledger as sl

    events = [
        # spend_ref first: a `break` mutant would drop everything after it.
        {"billing": {"tier": "metered", "cost_usd": 9.0}, "spend_ref": "r"},
        {"billing": {"tier": "metered", "cost_usd": 0.123456789}, "billing_tier": "cheap"},
        {"billing": {"tier": "metered", "cost_usd": 0.5}, "billing_tier": "cheap"},
        {"billing": {"tier": "metered", "cost_usd": 1.25}},
        {"billing": {"tier": "metered", "cost_usd": 2.5}},
        {"billing": {"tier": "cursor_estimate", "cost_usd": 0.123456}},
        {"billing": {"tier": "cursor_estimate", "cost_usd": 0.2}},
        {"billing": {"tier": "cheap", "cost_usd": 7.0}},  # cheap: not counted
    ]
    load_calls: dict = {}
    monkeypatch.setattr(
        bl, "load_events",
        lambda log, since=None: load_calls.update(log=log, since=since) or (events, None),
    )
    ledger_calls: dict = {}
    monkeypatch.setattr(
        sl, "ledger_spend_by_tier",
        lambda since=None: ledger_calls.update(since=since)
        or {"cheap": 1.00007, "expensive": 2.0},
    )
    _pin_clock(monkeypatch)
    settings = _settings()
    snap = bl.aggregate_budget(path=tmp_path / "u.jsonl", settings=settings)

    assert load_calls["since"] == datetime(2025, 7, 1, tzinfo=UTC)
    assert ledger_calls["since"] == datetime(2025, 7, 1, tzinfo=UTC)
    # metered = 0.623456789 + 3.75 + ledger 3.00007 = 7.373526789
    assert snap.metered_spent_usd == pytest.approx(7.3735)
    assert snap.metered_cap_usd == 50.0
    assert snap.metered_remaining_usd == pytest.approx(42.6265)
    assert snap.metered_pct == pytest.approx(14.7)
    assert snap.metered_cheap_spent_usd == pytest.approx(1.6235)
    assert snap.metered_expensive_spent_usd == pytest.approx(5.75)
    assert snap.cursor_est_spent_usd == pytest.approx(0.32)
    assert snap.cursor_est_cap_usd == 30.0
    assert snap.cursor_est_remaining_usd == pytest.approx(29.68)
    assert snap.cursor_est_pct == pytest.approx(1.1)
    assert snap.mode == "normal"
    assert snap.period_label == "Jul"
    assert snap.show_both is True
    assert snap.warn_at_pct == 80.0


@allure.title("aggregate_budget mode edges: cap=0, exact cap, boundary warn")
def test_aggregate_budget_mode_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import greedy_token.spend_ledger as sl

    def snap_for(events, ledger, **kw):
        monkeypatch.setattr(bl, "load_events", lambda *a, **k: (events, None))
        monkeypatch.setattr(sl, "ledger_spend_by_tier", lambda **k: ledger)
        return bl.aggregate_budget(path=tmp_path / "u", settings=_settings(**kw))

    def metered(usd):
        return {"billing": {"tier": "metered", "cost_usd": usd}}
    with allure.step("zero cap → pct 0 and mode normal, never exhausted"):
        s = snap_for([metered(5.0)], {"cheap": 0, "expensive": 0},
                     metered_monthly_cap_usd=0.0, cursor_monthly_estimate_cap_usd=0.0)
        assert s.metered_pct == 0.0 and s.cursor_est_pct == 0.0
        assert s.mode == "normal"
    with allure.step("sub-dollar cap: spent >= cap → exhausted"):
        s = snap_for([metered(0.6)], {"cheap": 0, "expensive": 0},
                     metered_monthly_cap_usd=0.5)
        assert s.mode == "exhausted"
    with allure.step("spent == cap exactly → exhausted (>= not >)"):
        s = snap_for([metered(50.0)], {"cheap": 0, "expensive": 0})
        assert s.mode == "exhausted"
    with allure.step("only metered at warn threshold → warn (or, not and)"):
        s = snap_for([metered(40.0)], {"cheap": 0, "expensive": 0})
        assert s.metered_pct == 80.0 and s.mode == "warn"
    with allure.step("only cursor at warn threshold → warn"):
        ev = [{"billing": {"tier": "cursor_estimate", "cost_usd": 24.0}}]
        s = snap_for(ev, {"cheap": 0, "expensive": 0})
        assert s.cursor_est_pct == 80.0 and s.mode == "warn"


@allure.title("headroom/metered_budget_exhausted/cursor_budget_warn thread root + edges")
def test_budget_query_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        bl, "aggregate_budget",
        lambda **k: seen.update(k) or _snap(),
    )
    bl.headroom(root=tmp_path)
    assert seen == {"root": tmp_path}

    monkeypatch.setattr(bl, "headroom", lambda **k: seen.update(k) or _snap(
        metered_cap_usd=50.0, metered_spent_usd=50.0,
        cursor_est_cap_usd=30.0, cursor_est_pct=80.0,
    ))
    assert bl.metered_budget_exhausted(root=tmp_path) is True
    assert bl.cursor_budget_warn(root=tmp_path) is True
    assert seen == {"root": tmp_path}

    monkeypatch.setattr(bl, "headroom", lambda **k: _snap(
        metered_cap_usd=0.0, metered_spent_usd=9.0,
        cursor_est_cap_usd=0.0, cursor_est_pct=99.0,
    ))
    # Zero caps must never claim exhaustion/warn.
    assert bl.metered_budget_exhausted() is False
    assert bl.cursor_budget_warn() is False

    monkeypatch.setattr(bl, "headroom", lambda **k: _snap(
        metered_cap_usd=0.5, metered_spent_usd=0.6,
        cursor_est_cap_usd=30.0, cursor_est_pct=79.0,
    ))
    # Fractional cap still counts; strict > on pct must not fire.
    assert bl.metered_budget_exhausted() is True
    assert bl.cursor_budget_warn() is False


@allure.title("metered_spent_today sums ledger + usage sources with midnight cutoff")
def test_metered_spent_today_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import greedy_token.spend_ledger as sl

    _pin_clock(monkeypatch)
    midnight = datetime(2025, 7, 15, tzinfo=UTC)
    ledger_args: dict = {}
    usage_args: dict = {}
    metered_args: dict = {}
    monkeypatch.setattr(
        sl, "ledger_spend_usd",
        lambda since=None: ledger_args.update(since=since) or 1.5,
    )
    monkeypatch.setattr(
        sl, "usage_metered_spend_usd",
        lambda since=None, log=None: usage_args.update(since=since, log=log) or 0.25,
    )
    monkeypatch.setattr(
        sl, "metered_spend_usd",
        lambda since=None: metered_args.update(since=since) or 3.0,
    )
    log = tmp_path / "u.jsonl"
    assert bl.metered_spent_today(log) == pytest.approx(1.75)
    assert ledger_args["since"] == midnight
    assert usage_args == {"since": midnight, "log": log}
    assert metered_args == {}, "path=None source must not run when path is given"
    assert bl.metered_spent_today() == pytest.approx(3.0)
    assert metered_args["since"] == midnight


@allure.title("format_budget_line renders exact compact/verbose output per mode")
def test_format_budget_line_exact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict = {}
    snap = _snap(
        metered_spent_usd=7.3735, metered_cap_usd=50.0, metered_remaining_usd=42.6,
        metered_pct=14.7, cursor_est_spent_usd=0.32, cursor_est_cap_usd=30.0,
        cursor_est_remaining_usd=29.68, cursor_est_pct=1.1, period_label="Jul",
        mode="normal",
    )
    monkeypatch.setattr(bl, "headroom", lambda **k: seen.update(k) or snap)
    with allure.step("compact default, normal → no warning marker"):
        assert bl.format_budget_line(root=tmp_path) == (
            "Budget (Jul): metered $7.37/$50 (15%) · "
            "cursor est. ~$0/$30 (1%)"
        )
        assert seen == {"root": tmp_path}
    with allure.step("compact + warn/exhausted append ' ⚠'"):
        for mode in ("warn", "exhausted"):
            monkeypatch.setattr(
                bl, "headroom", lambda _m=mode, **k: _snap(mode=_m)
            )
            assert bl.format_budget_line().endswith(" ⚠")
    with allure.step("verbose exhausted/warn status lines are exact"):
        monkeypatch.setattr(bl, "headroom", lambda **k: _snap(mode="exhausted"))
        out = bl.format_budget_line(compact=False)
        assert "  Status: metered budget exhausted — escalation blocked" in out
        monkeypatch.setattr(bl, "headroom", lambda **k: _snap(mode="warn", warn_at_pct=80.0))
        out = bl.format_budget_line(compact=False)
        assert "  Status: approaching cap (warn at 80%)" in out


@allure.title("format_budget_statusline renders the compact M/C pair exactly")
def test_format_budget_statusline_exact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        bl, "headroom",
        lambda **k: seen.update(k) or _snap(
            metered_spent_usd=7.5, metered_cap_usd=50.0,
            cursor_est_spent_usd=2.4, cursor_est_cap_usd=30.0, mode="normal",
        ),
    )
    assert bl.format_budget_statusline(root=tmp_path) == "M:$8/$50 C:~$2/$30"
    assert seen == {"root": tmp_path}
    monkeypatch.setattr(bl, "headroom", lambda **k: _snap(
        metered_spent_usd=7.5, metered_cap_usd=50.0,
        cursor_est_spent_usd=2.4, cursor_est_cap_usd=30.0, mode="warn",
    ))
    assert bl.format_budget_statusline() == "M:$8/$50 C:~$2/$30⚠"


@allure.title("build_billing_event_fields maps tiers, rounds cost, passes model")
def test_build_billing_event_fields_exact() -> None:
    for tier in ("expensive", "cheap", "cursor", "metered", "cursor_estimate"):
        out = bl.build_billing_event_fields(billing_tier=tier)
        assert out["v"] == 2
        expected = {"expensive": "metered", "cursor": "cursor_estimate"}.get(tier, tier)
        assert out["billing"]["tier"] == expected
    with allure.step("unknown tier passes through verbatim"):
        assert bl.build_billing_event_fields(billing_tier="zzz")["billing"]["tier"] == "zzz"
    with allure.step("cost rounds to 6 decimals; model_id optional"):
        out = bl.build_billing_event_fields(
            billing_tier="cheap", cost_usd=1.234567891, model_id="m9"
        )
        assert out["billing"] == {"tier": "cheap", "cost_usd": 1.234568, "model_id": "m9"}
        bare = bl.build_billing_event_fields(billing_tier="cheap")
        assert bare["billing"] == {"tier": "cheap"}


# ---------------------------------------------------------------------------
# Mutation-gap coverage: apply_budget_policy plumbing and exact annotations
# ---------------------------------------------------------------------------


@allure.title("apply_budget_policy resolves policy through the registry, threading root")
def test_policy_registry_called_with_root(
    policy_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    calls: list = []
    monkeypatch.setattr(
        "greedy_token.model_select.get_llm_registry",
        lambda root: calls.append(root) or SimpleNamespace(policy="hybrid"),
    )
    bp.apply_budget_policy(_decision(), "task", tmp_path, policy=None)
    assert calls == [tmp_path]


@allure.title("resolved registry policy drives the hybrid escalation block")
def test_policy_registry_value_used(
    policy_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "greedy_token.model_select.get_llm_registry",
        lambda root: SimpleNamespace(policy="hybrid"),
    )
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: True)
    ollama_alt = _decision(target="ollama", matched=["o"], rationale="r")
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [(1.0, ollama_alt)])
    out = bp.apply_budget_policy(
        _decision(complexity="high"), "please escalate", tmp_path, policy=None
    )
    assert out.target == "ollama" and out.note == "budget_policy: hybrid"


@allure.title("registry failure falls back to policy 'auto'")
def test_policy_registry_fallback_auto(
    policy_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "greedy_token.model_select.get_llm_registry",
        lambda root: (_ for _ in ()).throw(ValueError("no reg")),
    )
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: True)
    ollama_alt = _decision(target="ollama", matched=["o"], rationale="r")
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [(1.0, ollama_alt)])
    out = bp.apply_budget_policy(
        _decision(complexity="high"), "please escalate", tmp_path, policy=None
    )
    # "auto" is inside the hybrid-policy allowlist — the fallback must produce it.
    assert out.target == "ollama" and out.note == "budget_policy: hybrid"


@allure.title("headroom and budget queries thread the workspace root")
def test_policy_snapshot_root(
    policy_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    roots: dict = {}
    monkeypatch.setattr(
        bp, "headroom", lambda **k: roots.update(headroom=k["root"]) or _snap()
    )
    monkeypatch.setattr(
        bp, "metered_budget_exhausted",
        lambda **k: roots.update(metered=k["root"]) or False,
    )
    monkeypatch.setattr(
        bp, "cursor_budget_warn", lambda **k: roots.update(cursor=k["root"]) or False
    )
    bp.apply_budget_policy(_decision(), "task", tmp_path, policy="auto")
    assert roots == {"headroom": tmp_path, "metered": tmp_path, "cursor": tmp_path}


@allure.title("metered exhausted selects each fallback tier in order, threading args")
def test_policy_metered_tier_order(
    policy_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: True)
    seen: list = []
    python_alt = _decision(target="python", matched=["p"], confidence=0.5, rationale="rp")
    tool_alt = _decision(target="tool", matched=["t"], confidence=0.5, rationale="rt")
    monkeypatch.setattr(
        bp, "route_task_all_tiers",
        lambda task, root: seen.append((task, root)) or [(1.0, python_alt), (1.0, tool_alt)],
    )
    with allure.step("no ollama/rag alts → first match wins: python"):
        out = bp.apply_budget_policy(
            _decision(complexity="medium"), "my task", tmp_path, policy="auto"
        )
        assert out.target == "python"
        assert out.note == "budget_policy: metered exhausted"
        assert out.rationale == "rp Budget: metered cap reached — prefer python."
        # Called once per candidate tier (ollama, rag, python) until a match.
        assert seen == [("my task", tmp_path)] * 3
    with allure.step("python absent → tool selected"):
        seen.clear()
        monkeypatch.setattr(
            bp, "route_task_all_tiers", lambda *a, **k: [(1.0, tool_alt)]
        )
        out = bp.apply_budget_policy(
            _decision(complexity="medium"), "my task", tmp_path, policy="auto"
        )
        assert out.target == "tool"
        assert out.rationale == "rt Budget: metered cap reached — prefer tool."


@allure.title("metered exhausted requires positive confidence and match")
def test_policy_metered_alt_gates(
    policy_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: True)
    zero_conf = _decision(target="python", matched=["p"], confidence=0.0)
    unmatched = _decision(target="rag", matched=[], confidence=0.9)
    monkeypatch.setattr(
        bp, "route_task_all_tiers", lambda *a, **k: [(1.0, zero_conf), (1.0, unmatched)]
    )
    out = bp.apply_budget_policy(
        _decision(complexity="medium"), "task", tmp_path, policy="auto"
    )
    assert out.target == "cursor"


@allure.title("unavailable ollama alt does not stop the scan of later alts")
def test_policy_metered_ollama_continue(
    policy_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: True)
    o1 = _decision(target="ollama", matched=["o1"], rationale="r1")
    o2 = _decision(target="ollama", matched=["o2"], rationale="r2")
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [(1.0, o1), (1.0, o2)])
    availability = iter([False, True])
    monkeypatch.setattr(
        "greedy_token.wrappers.ollama_available", lambda: next(availability)
    )
    out = bp.apply_budget_policy(
        _decision(complexity="medium"), "task", tmp_path, policy="auto"
    )
    # continue, not break: the second ollama candidate is still reachable.
    assert out.target == "ollama" and out.matched == ["o2"]


@allure.title("cursor warn biases only a medium cursor decision, threading args")
def test_policy_cursor_warn_gates(
    policy_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(bp, "cursor_budget_warn", lambda **k: True)
    ollama_alt = _decision(target="ollama", matched=["o"], rationale="ro")
    seen: list = []
    monkeypatch.setattr(
        bp, "route_task_all_tiers",
        lambda task, root: seen.append((task, root)) or [(1.0, ollama_alt)],
    )
    with allure.step("medium cursor → biased to ollama with exact note/rationale"):
        out = bp.apply_budget_policy(
            _decision(complexity="medium"), "t2", tmp_path, policy="auto"
        )
        assert out.target == "ollama"
        assert out.note == "budget_policy: cursor warn"
        assert out.rationale == "ro Budget: cursor est. high — prefer local LLM."
        assert seen == [("t2", tmp_path)]
    with allure.step("non-cursor decision → warn block never fires"):
        seen.clear()
        out = bp.apply_budget_policy(
            _decision(target="rag", complexity="medium"), "t2", tmp_path, policy="auto"
        )
        assert out.target == "rag" and seen == []


@allure.title("hybrid policy reroutes cursor escalation only when metered is out")
def test_policy_hybrid_gates(
    policy_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ollama_alt = _decision(target="ollama", matched=["o"], rationale="rh")
    monkeypatch.setattr(bp, "route_task_all_tiers", lambda *a, **k: [(1.0, ollama_alt)])
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: False)
    with allure.step("metered has headroom → no hybrid reroute"):
        out = bp.apply_budget_policy(
            _decision(complexity="high"), "please escalate", tmp_path, policy="hybrid"
        )
        assert out.target == "cursor"
    with allure.step("each allowlisted policy fires; 'zzz' does not"):
        monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: True)
        for pol in ("hybrid", "auto", "cheap_only"):
            out = bp.apply_budget_policy(
                _decision(complexity="high"), "please escalate", tmp_path, policy=pol
            )
            assert out.target == "ollama", pol
            assert out.rationale == "rh Budget: no metered headroom for escalation."
        out = bp.apply_budget_policy(
            _decision(complexity="high"), "please escalate", tmp_path, policy="zzz"
        )
        assert out.target == "cursor"
    with allure.step("non-escalate task → hybrid block skips"):
        out = bp.apply_budget_policy(
            _decision(complexity="high"), "plain task", tmp_path, policy="hybrid"
        )
        assert out.target == "cursor"
    with allure.step("non-cursor decision → hybrid block skips"):
        out = bp.apply_budget_policy(
            _decision(target="python", complexity="high"), "please escalate",
            tmp_path, policy="hybrid",
        )
        assert out.target == "python"


@allure.title("hybrid reroute requires an ollama-target alt that matched")
def test_policy_hybrid_alt_gate(
    policy_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(bp, "metered_budget_exhausted", lambda **k: True)
    non_ollama = _decision(target="rag", matched=["r"])
    ollama_unmatched = _decision(target="ollama", matched=[])
    monkeypatch.setattr(
        bp, "route_task_all_tiers",
        lambda *a, **k: [(1.0, non_ollama), (1.0, ollama_unmatched)],
    )
    out = bp.apply_budget_policy(
        _decision(complexity="high"), "please escalate", tmp_path, policy="hybrid"
    )
    assert out.target == "cursor"


@allure.title("run_doctor is probed with quick=True; deprecated hint uses first rec")
def test_policy_doctor_contract(
    policy_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list = []
    monkeypatch.setattr(
        bp, "run_doctor", lambda **k: calls.append(k) or _rep(deprecated=True)
    )
    out = bp.apply_budget_policy(
        _decision(target="ollama", rationale="base"), "task", tmp_path, policy="auto"
    )
    assert calls == [{"quick": True}]
    assert out.rationale == "base Local: deprecated model — consider ollama pull qwen2.5-coder:7b."
    with allure.step("deprecated list but non-ollama target → rationale untouched"):
        out = bp.apply_budget_policy(
            _decision(target="python", rationale="base"), "task", tmp_path, policy="auto"
        )
        assert out.rationale == "base"
    with allure.step("empty recommended list renders the bare hint"):
        rep = _rep(deprecated=True)
        rep.recommended.clear()
        monkeypatch.setattr(bp, "run_doctor", lambda **k: rep)
        out = bp.apply_budget_policy(
            _decision(target="ollama", rationale="base"), "task", tmp_path, policy="auto"
        )
        assert out.rationale == "base Local: deprecated model — consider ollama pull ."


@allure.title("exhausted snapshot appends the budget note exactly once")
def test_policy_exhausted_note(
    policy_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(bp, "headroom", lambda **k: _snap(mode="exhausted"))
    with allure.step("clean note gets the marker appended"):
        out = bp.apply_budget_policy(
            _decision(note="base note"), "task", tmp_path, policy="auto"
        )
        assert out.note == "base note budget: metered exhausted"
    with allure.step("policy note is not duplicated"):
        out = bp.apply_budget_policy(
            _decision(note="budget_policy: metered exhausted"), "task", tmp_path,
            policy="auto",
        )
        assert out.note == "budget_policy: metered exhausted"


@allure.title("policy_footer_extras threads root and compact flag, swallows errors")
def test_policy_footer_extras_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "greedy_token.budget_ledger.format_budget_line",
        lambda **k: seen.update(k) or "budget line",
    )
    monkeypatch.setattr(bp, "local_health_line", lambda: "health")
    assert bp.policy_footer_extras(root=tmp_path) == ["budget line", "health"]
    assert seen == {"root": tmp_path, "compact": True}
