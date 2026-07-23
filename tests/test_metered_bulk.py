"""ADR-0002: metered cheap bulk tier — opt-in, caps, telemetry, footers, routing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import allure
import pytest
import yaml

from greedy_token import spend_guard
from greedy_token.model_select import (
    ModelSpec,
    billing_note_for_model,
    derive_tier,
    get_llm_registry,
    metered_cheap_fallback,
)
from tests.allure_reporting import attach_text
from tests.ollama_stub import ollama_stub_server

pytestmark = [
    allure.epic("Spend guard"),
    allure.parent_suite("Spend guard"),
    allure.feature("Metered bulk tier (ADR-0002)"),
    allure.suite("Metered bulk tier"),
]


def _write_cfg(root: Path, cfg: dict) -> None:
    (root / ".greedy-token.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _metered_cheap_cfg(
    *,
    url: str = "http://127.0.0.1:1",
    opt_in: bool | None = None,
    extra_llm: dict | None = None,
) -> dict:
    llm: dict = {
        "models": [
            {
                "id": "bulk-api",
                "enabled": True,
                "provider": "openai_compat",
                "url": url,
                "model": "cheap-remote",
                "billing": "metered",
                "cost_per_1m_usd": 0.05,
                "profiles": ["*"],
            }
        ],
        "escalation": {"enabled": False},
    }
    if opt_in is not None:
        llm["metered"] = {"opt_in": opt_in}
    if extra_llm:
        llm.update(extra_llm)
    return {"llm": llm}


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "greedy_token.settings.user_config_path", lambda: tmp_path / "missing.yaml"
    )
    monkeypatch.setattr(
        "greedy_token.model_select.user_config_path", lambda: tmp_path / "missing.yaml"
    )
    monkeypatch.delenv(spend_guard.METERED_ENV, raising=False)
    monkeypatch.delenv("GREEDY_LLM_MODEL_ID", raising=False)
    return tmp_path


@allure.story("derive_tier boundaries")
@allure.title("Exact threshold cost is cheap; a hair above is expensive")
def test_derive_tier_threshold_boundaries() -> None:
    def spec(cost: float) -> ModelSpec:
        return ModelSpec(
            id="m", enabled=True, provider="openai_compat", url="https://x", model="m",
            profiles=("*",), locality="remote", billing="metered", cost_per_1m_usd=cost,
        )

    assert derive_tier(spec(0.2)) == "cheap"  # <= default threshold 0.2
    assert derive_tier(spec(0.2000001)) == "expensive"
    assert derive_tier(spec(0.05), cheap_cost_threshold_per_1m_usd=0.05) == "cheap"
    assert derive_tier(spec(0.05), cheap_cost_threshold_per_1m_usd=0.049) == "expensive"


@allure.story("Fallback selection")
@allure.title("metered_cheap_fallback picks first enabled metered cheap model only")
def test_metered_cheap_fallback_selection(isolated_root: Path) -> None:
    _write_cfg(isolated_root, {
        "llm": {
            "models": [
                {"id": "local", "enabled": True, "provider": "ollama",
                 "url": "http://localhost:11434", "model": "q7", "billing": "free"},
                {"id": "off", "enabled": False, "provider": "openai_compat",
                 "url": "https://x", "model": "m", "billing": "metered", "cost_per_1m_usd": 0.05},
                {"id": "pricey", "enabled": True, "provider": "openai_compat",
                 "url": "https://x", "model": "m", "billing": "metered", "cost_per_1m_usd": 5.0},
                {"id": "bulk", "enabled": True, "provider": "openai_compat",
                 "url": "https://x", "model": "m", "billing": "metered", "cost_per_1m_usd": 0.1},
            ]
        }
    })
    spec = metered_cheap_fallback(isolated_root)
    assert spec is not None
    assert spec.id == "bulk"  # free skipped, disabled skipped, expensive skipped


@allure.story("Fallback selection")
@allure.title("metered_cheap_fallback is None without a metered cheap model")
def test_metered_cheap_fallback_none(isolated_root: Path) -> None:
    _write_cfg(isolated_root, {
        "llm": {
            "models": [
                {"id": "local", "enabled": True, "provider": "ollama",
                 "url": "http://localhost:11434", "model": "q7", "billing": "free"},
            ]
        }
    })
    assert metered_cheap_fallback(isolated_root) is None


@allure.story("Opt-in")
@allure.title("metered_opt_in: config, env tokens, cli flag, default off")
def test_metered_opt_in(isolated_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(isolated_root, _metered_cheap_cfg())
    assert spend_guard.metered_opt_in(root=isolated_root) is False
    assert spend_guard.metered_opt_in(root=isolated_root, cli_flag=True) is True

    for token in ("1", "true", "yes", "on"):
        monkeypatch.setenv(spend_guard.METERED_ENV, token)
        assert spend_guard.metered_opt_in(root=isolated_root) is True
        monkeypatch.setenv(spend_guard.METERED_ENV, token.upper())
        assert spend_guard.metered_opt_in(root=isolated_root) is True
    monkeypatch.setenv(spend_guard.METERED_ENV, "nope")
    assert spend_guard.metered_opt_in(root=isolated_root) is False
    monkeypatch.delenv(spend_guard.METERED_ENV, raising=False)

    _write_cfg(isolated_root, _metered_cheap_cfg(opt_in=True))
    assert spend_guard.metered_opt_in(root=isolated_root) is True
    assert get_llm_registry(isolated_root).metered_opt_in is True


@allure.story("Spend gate")
@allure.title("check_metered_allowed: free passes, expensive delegates")
def test_check_metered_allowed_free_and_expensive(monkeypatch: pytest.MonkeyPatch) -> None:
    free = ModelSpec(
        id="f", enabled=True, provider="ollama", url="http://localhost:11434",
        model="q7", profiles=("*",), locality="local", billing="free",
    )
    assert spend_guard.check_metered_allowed(free).allowed is True

    pricey = ModelSpec(
        id="p", enabled=True, provider="openai_compat", url="https://x", model="m",
        profiles=("*",), locality="remote", billing="metered", cost_per_1m_usd=5.0,
    )
    sentinel = spend_guard.SpendDecision(allowed=False, reason="delegated-to-expensive")
    monkeypatch.setattr(spend_guard, "check_expensive_allowed", lambda *a, **k: sentinel)
    assert spend_guard.check_metered_allowed(pricey) is sentinel


@allure.story("Spend gate")
@allure.title("check_metered_allowed: metered cheap needs opt-in, exact reason")
def test_check_metered_allowed_opt_in_required(isolated_root: Path) -> None:
    _write_cfg(isolated_root, _metered_cheap_cfg())
    spec = metered_cheap_fallback(isolated_root)
    assert spec is not None
    decision = spend_guard.check_metered_allowed(spec, root=isolated_root)
    assert decision.allowed is False
    assert decision.reason == (
        "metered LLM opt-in required — set llm.metered.opt_in: true "
        f"or {spend_guard.METERED_ENV}=1"
    )
    # --allow-expensive is a superset permission
    ok = spend_guard.check_metered_allowed(spec, root=isolated_root, cli_allow=True)
    assert ok.allowed is True


@allure.story("Spend gate")
@allure.title("check_metered_allowed: daily and monthly cap boundaries")
def test_check_metered_allowed_caps(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cfg(
        isolated_root,
        _metered_cheap_cfg(opt_in=True, extra_llm={"expensive": {"daily_cap_usd": 1.0}}),
    )
    spec = metered_cheap_fallback(isolated_root)
    assert spec is not None

    def snap(cap: float = 0.0, spent: float = 0.0):
        return SimpleNamespace(metered_cap_usd=cap, metered_spent_usd=spent)

    monkeypatch.setattr(spend_guard, "headroom", lambda root=None: snap())
    with allure.step("daily cap: over denies, exact boundary allows"):
        monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 0.9)
        over = spend_guard.check_metered_allowed(spec, root=isolated_root, est_cost_usd=0.2)
        assert over.allowed is False and "daily cap" in over.reason
        exact = spend_guard.check_metered_allowed(spec, root=isolated_root, est_cost_usd=0.1)
        assert exact.allowed is True

    with allure.step("monthly metered cap: over denies, cap 0 = no cap"):
        monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 0.0)
        monkeypatch.setattr(
            spend_guard, "headroom", lambda root=None: snap(cap=10.0, spent=9.99)
        )
        over = spend_guard.check_metered_allowed(spec, root=isolated_root, est_cost_usd=0.02)
        assert over.allowed is False and "monthly metered cap" in over.reason
        monkeypatch.setattr(spend_guard, "headroom", lambda root=None: snap(cap=0.0, spent=99.0))
        assert spend_guard.check_metered_allowed(spec, root=isolated_root).allowed is True


@allure.story("Spend gate")
@allure.title("check_metered_allowed: expensive delegation forwards exact args")
def test_check_metered_allowed_delegation_args(monkeypatch: pytest.MonkeyPatch) -> None:
    pricey = ModelSpec(
        id="p", enabled=True, provider="openai_compat", url="https://x", model="m",
        profiles=("*",), locality="remote", billing="metered", cost_per_1m_usd=5.0,
    )
    sentinel_root = Path("/tmp/greedy-token-metered-root")
    seen: dict[str, object] = {}

    def recorder(spec, *, root=None, cli_allow=False, est_cost_usd=0.0):
        seen.update(spec=spec, root=root, cli_allow=cli_allow, est_cost_usd=est_cost_usd)
        return spend_guard.SpendDecision(allowed=True)

    monkeypatch.setattr(spend_guard, "check_expensive_allowed", recorder)
    spend_guard.check_metered_allowed(
        pricey, root=sentinel_root, cli_allow=True, est_cost_usd=0.75
    )
    assert seen == {
        "spec": pricey,
        "root": sentinel_root,
        "cli_allow": True,
        "est_cost_usd": 0.75,
    }


@allure.story("Spend gate")
@allure.title("check_metered_allowed: cap boundary semantics for metered cheap")
def test_check_metered_allowed_cap_semantics(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def snap(cap: float = 0.0, spent: float = 0.0):
        return SimpleNamespace(metered_cap_usd=cap, metered_spent_usd=spent)

    with allure.step("daily cap 0 = no cap: positive spend still allowed"):
        _write_cfg(
            isolated_root,
            _metered_cheap_cfg(opt_in=True, extra_llm={"expensive": {"daily_cap_usd": 0}}),
        )
        spec = metered_cheap_fallback(isolated_root)
        assert spec is not None
        monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 3.0)
        monkeypatch.setattr(spend_guard, "headroom", lambda root=None: snap())
        ok = spend_guard.check_metered_allowed(spec, root=isolated_root, est_cost_usd=3.0)
        assert ok.allowed is True

    with allure.step("default est_cost_usd is 0.0: at spent == cap staying allowed"):
        _write_cfg(
            isolated_root,
            _metered_cheap_cfg(opt_in=True, extra_llm={"expensive": {"daily_cap_usd": 5.0}}),
        )
        spec = metered_cheap_fallback(isolated_root)
        assert spec is not None
        monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 5.0)
        assert spend_guard.check_metered_allowed(spec, root=isolated_root).allowed is True

    monkeypatch.setattr(spend_guard, "_load_today_spend", lambda: 0.0)
    with allure.step("monthly cap 1.0 is a real cap; exceeding denies"):
        monkeypatch.setattr(spend_guard, "headroom", lambda root=None: snap(cap=1.0, spent=1.0))
        over = spend_guard.check_metered_allowed(spec, root=isolated_root, est_cost_usd=0.5)
        assert over.allowed is False and "monthly metered cap" in over.reason

    with allure.step("monthly exact boundary spent+est == cap is allowed"):
        monkeypatch.setattr(spend_guard, "headroom", lambda root=None: snap(cap=10.0, spent=9.5))
        ok = spend_guard.check_metered_allowed(spec, root=isolated_root, est_cost_usd=0.5)
        assert ok.allowed is True

    with allure.step("headroom receives the caller's root"):
        seen: dict[str, object] = {}

        def fake_headroom(root=None):
            seen["root"] = root
            return snap()

        monkeypatch.setattr(spend_guard, "headroom", fake_headroom)
        spend_guard.check_metered_allowed(spec, root=isolated_root)
        assert seen["root"] == isolated_root


@allure.story("Spend accounting")
@allure.title("_load_today_spend counts v2 metered blocks from cheap-metered events")
def test_load_today_spend_counts_metered_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log.write_text(
        # legacy expensive event (no billing block)
        json.dumps({"ts": f"{today}T01:00:00Z", "billing_tier": "expensive", "cost_usd": 1.0}) + "\n"
        # ADR-0002 metered cheap event: billing_tier stays cheap, block is metered
        + json.dumps({
            "ts": f"{today}T02:00:00Z", "billing_tier": "cheap", "cost_usd": 0.25,
            "v": 2, "billing": {"tier": "metered", "cost_usd": 0.25, "model_id": "bulk"},
        }) + "\n"
        # plain cheap (free) event must not count
        + json.dumps({
            "ts": f"{today}T03:00:00Z", "billing_tier": "cheap", "cost_usd": 9,
            "v": 2, "billing": {"tier": "cheap"},
        }) + "\n",
        encoding="utf-8",
    )
    assert spend_guard._load_today_spend() == pytest.approx(1.25)


@allure.story("Spend accounting")
@allure.title("_is_metered_event: block tier wins, legacy marker kept, junk is not metered")
def test_is_metered_event() -> None:
    assert spend_guard._is_metered_event({"billing": {"tier": "metered"}}) is True
    assert spend_guard._is_metered_event({"billing": {"tier": " METERED "}}) is True
    assert spend_guard._is_metered_event({"billing_tier": "expensive"}) is True
    assert spend_guard._is_metered_event({"billing": {"tier": "cheap"}, "billing_tier": "cheap"}) is False
    assert spend_guard._is_metered_event({"billing": "junk", "billing_tier": "cheap"}) is False
    assert spend_guard._is_metered_event({}) is False


@allure.story("Router availability")
@allure.title("metered_bulk_ready: needs both a fallback model and the opt-in")
def test_metered_bulk_ready(isolated_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(isolated_root, {"llm": {"models": [
        {"id": "local", "enabled": True, "provider": "ollama",
         "url": "http://localhost:11434", "model": "q7", "billing": "free"},
    ]}})
    assert spend_guard.metered_bulk_ready(isolated_root) is False  # no fallback

    _write_cfg(isolated_root, _metered_cheap_cfg())
    assert spend_guard.metered_bulk_ready(isolated_root) is False  # no opt-in

    _write_cfg(isolated_root, _metered_cheap_cfg(opt_in=True))
    assert spend_guard.metered_bulk_ready(isolated_root) is True

    monkeypatch.delenv(spend_guard.METERED_ENV, raising=False)
    _write_cfg(isolated_root, _metered_cheap_cfg())
    monkeypatch.setenv(spend_guard.METERED_ENV, "1")
    assert spend_guard.metered_bulk_ready(isolated_root) is True


@allure.story("Router availability")
@allure.title("Ollama down + metered ready → ollama tier still routes, honest rationale")
def test_route_ollama_tier_with_metered_fallback(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token import router

    monkeypatch.setattr(router, "ollama_available", lambda *a, **k: False)

    with allure.step("no fallback → bulk task falls through past the ollama tier"):
        monkeypatch.setattr(router, "_metered_bulk_ready", lambda root: False)
        decision = router.route_task("classify files batch inventory", minimal_workspace)
        assert decision.target != "ollama"

    with allure.step("metered fallback ready → ollama tier selected, metered rationale"):
        monkeypatch.setattr(router, "_metered_bulk_ready", lambda root: True)
        decision = router.route_task("classify files batch inventory", minimal_workspace)
        attach_text("decision", f"{decision.target} {decision.route_id}\n{decision.rationale}")
        assert decision.target == "ollama"
        assert "metered bulk API fallback (spend-guarded)" in decision.rationale
        assert decision.est_tokens < 1000  # cheap-tier estimate, not cursor fallback math


@allure.story("Router availability")
@allure.title("Ollama up keeps the local rationale; both down keeps expensive fallback text")
def test_route_ollama_estimates_other_branches(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token import router

    with allure.step("local available → local/cheap rationale"):
        monkeypatch.setattr(router, "ollama_available", lambda *a, **k: True)
        _, est, rationale = router._token_estimate_for_route(
            "ollama", task="classify x", root=minimal_workspace
        )
        assert "local/cheap spend" in rationale and est >= 1

    with allure.step("both unavailable → expensive-path estimate"):
        monkeypatch.setattr(router, "ollama_available", lambda *a, **k: False)
        monkeypatch.setattr(router, "_metered_bulk_ready", lambda root: False)
        _, est, rationale = router._token_estimate_for_route(
            "ollama", task="classify x", root=minimal_workspace
        )
        assert "would fall back to expensive Cursor path" in rationale
        assert est > 1000  # includes cursor overhead


@allure.story("Router availability")
@allure.title("router threads the real workspace root into metered_bulk_ready")
def test_router_threads_root_into_metered_check(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token import router

    seen: list[object] = []

    def recorder(root=None):
        seen.append(root)
        return False

    monkeypatch.setattr("greedy_token.spend_guard.metered_bulk_ready", recorder)
    monkeypatch.setattr(router, "ollama_available", lambda *a, **k: False)

    with allure.step("_metered_bulk_ready wrapper passes root through"):
        assert router._metered_bulk_ready(minimal_workspace) is False
        assert seen == [minimal_workspace]

    with allure.step("route_task passes the workspace root, not None"):
        seen.clear()
        router.route_task("classify files batch inventory", minimal_workspace)
        assert minimal_workspace in seen
        assert None not in seen

    with allure.step("_token_estimate_for_route passes the workspace root"):
        seen.clear()
        router._token_estimate_for_route("ollama", task="classify x", root=minimal_workspace)
        assert seen == [minimal_workspace]


@allure.story("Router availability")
@allure.title("Metered-fallback estimate: exact rationale and 1-token floor")
def test_route_metered_estimate_exact(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token import router
    from greedy_token.tokens import count_tokens

    monkeypatch.setattr(router, "ollama_available", lambda *a, **k: False)
    monkeypatch.setattr(router, "_metered_bulk_ready", lambda root: True)

    one_token_task = "classify"
    assert count_tokens(one_token_task).tokens == 1
    complexity, est, rationale = router._token_estimate_for_route(
        "ollama", task=one_token_task, root=minimal_workspace
    )
    assert complexity == "medium"
    assert est == 1  # max(task_tokens, 1) — not inflated to 2
    assert rationale == (
        "Cheap LLM — Ollama down; metered bulk API fallback (spend-guarded)."
    )


@allure.story("Telemetry")
@allure.title("invoke via openai_compat stub: cost_usd + metered billing block, cheap billing_tier")
def test_invoke_metered_cheap_telemetry(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.llm_invoke import invoke_profile

    log = isolated_root / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    with ollama_stub_server() as url:
        _write_cfg(isolated_root, _metered_cheap_cfg(url=url, opt_in=True))
        result = invoke_profile(
            "classify", system="s", user="classify this text", root=isolated_root,
            log=True, allow_escalate=False,
        )
    assert result.tier_billing == "cheap"  # derived tier
    assert result.eval_tokens == 12
    assert result.cost_usd == pytest.approx(12 / 1_000_000 * 0.05)

    event = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    attach_text("event", json.dumps(event, indent=2))
    assert event["billing_tier"] == "cheap"  # legacy field: derived tier (compat)
    assert event["billing"]["tier"] == "metered"  # ADR-0002 block
    assert event["billing"]["cost_usd"] == pytest.approx(result.cost_usd, abs=1e-6)
    assert event["billing"]["model_id"] == "bulk-api"
    assert event["cost_usd"] == pytest.approx(result.cost_usd, abs=1e-6)


@allure.story("Telemetry")
@allure.title("invoke without opt-in fails with the spend-guard reason")
def test_invoke_metered_cheap_blocked(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.llm_invoke import invoke_profile

    monkeypatch.setenv("GREEDY_TOKEN_LOG", "0")
    _write_cfg(isolated_root, _metered_cheap_cfg())
    with pytest.raises(RuntimeError, match="metered LLM opt-in required"):
        invoke_profile(
            "classify", system="s", user="u", root=isolated_root,
            log=False, allow_escalate=False,
        )


@allure.story("Budget split")
@allure.title("aggregate_budget splits metered spend: cheap bulk vs expensive")
def test_budget_metered_split(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.budget_ledger import aggregate_budget, format_budget_line

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    log.write_text(
        json.dumps({
            "ts": now, "billing_tier": "cheap", "cost_usd": 0.3,
            "v": 2, "billing": {"tier": "metered", "cost_usd": 0.3, "model_id": "bulk"},
        }) + "\n"
        + json.dumps({
            "ts": now, "billing_tier": "expensive", "cost_usd": 1.2,
            "v": 2, "billing": {"tier": "metered", "cost_usd": 1.2, "model_id": "pricey"},
        }) + "\n",
        encoding="utf-8",
    )
    snap = aggregate_budget(path=log)
    assert snap.metered_spent_usd == pytest.approx(1.5)
    assert snap.metered_cheap_spent_usd == pytest.approx(0.3)
    assert snap.metered_expensive_spent_usd == pytest.approx(1.2)

    text = format_budget_line(compact=False)
    attach_text("budget verbose", text)
    assert "cheap bulk:   $0.3000 · expensive: $1.2000" in text


@allure.story("Budget split")
@allure.title("budget --json exposes the metered split fields")
def test_budget_json_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from argparse import Namespace

    from greedy_token.cli import cmd_budget

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    log.write_text(
        json.dumps({
            "ts": now, "billing_tier": "cheap", "cost_usd": 0.5,
            "v": 2, "billing": {"tier": "metered", "cost_usd": 0.5},
        }) + "\n",
        encoding="utf-8",
    )
    assert cmd_budget(Namespace(json=True, verbose=False)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["metered_cheap_spent_usd"] == pytest.approx(0.5)
    assert payload["metered_expensive_spent_usd"] == 0.0


@allure.story("Footer honesty")
@allure.title("billing_note_for_model: metered vs local free vs unknown/empty id")
def test_billing_note_for_model(isolated_root: Path) -> None:
    _write_cfg(isolated_root, {"llm": {"models": [
        {"id": "local", "enabled": True, "provider": "ollama",
         "url": "http://localhost:11434", "model": "q7", "billing": "free"},
        {"id": "bulk", "enabled": True, "provider": "openai_compat",
         "url": "https://x", "model": "m", "billing": "metered", "cost_per_1m_usd": 0.05},
    ]}})
    assert billing_note_for_model("bulk", isolated_root) == "metered"
    assert billing_note_for_model("local", isolated_root) == "local free"
    assert billing_note_for_model("ghost", isolated_root) == "local free"
    assert billing_note_for_model("", isolated_root) == "local free"


@allure.story("Footer honesty")
@allure.title("Footer billing label: cheap LLM (…, metered) vs (…, local free)")
def test_footer_metered_label(
    isolated_root: Path, minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.budget import _billing_short, format_tool_footer

    _write_cfg(minimal_workspace, _metered_cheap_cfg(opt_in=True))

    with allure.step("served by the metered model → metered label"):
        monkeypatch.setenv("GREEDY_LLM_MODEL_ID", "bulk-api")
        short = _billing_short("ollama", root=minimal_workspace)
        assert short.endswith(", metered)")
        full = format_tool_footer(
            "classify x", minimal_workspace, tier="ollama", est_tokens=10, style="full"
        )
        attach_text("full footer (metered)", full)
        assert ", metered) — not expensive path" in full

    with allure.step("no served-model id → legacy local free label"):
        monkeypatch.delenv("GREEDY_LLM_MODEL_ID", raising=False)
        short = _billing_short("ollama", root=minimal_workspace)
        assert short.endswith(", local free)")
