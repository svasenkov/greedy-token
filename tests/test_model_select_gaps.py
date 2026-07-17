"""Public-contract tests for model_select parsing / selection / escalation (fail_under=100)."""

from __future__ import annotations

import os
from pathlib import Path

import allure
import pytest

from greedy_token import model_select as ms
from greedy_token.model_select import (
    EscalationConfig,
    LlmRegistry,
    ModelSpec,
    ResolvedModel,
    _merge_configs,
    _normalize_provider,
    _parse_model_entry,
    _parse_profiles,
    _parse_registry,
    _pick_from_pool,
    _warn_env_model_override,
    apply_model_env,
    escalation_chain_from,
    get_llm_registry,
    resolve_model,
)
from greedy_token.settings import CheapLlmSettings

pytestmark = pytest.mark.unit


def _spec(model_id: str, *, tier: str = "cheap", enabled: bool = True, profiles=("*",)) -> ModelSpec:
    return ModelSpec(
        id=model_id, enabled=enabled, provider="ollama", url="http://x", model=f"m-{model_id}",
        profiles=tuple(profiles), tier=tier,  # type: ignore[arg-type]
    )


def _registry(**kw) -> LlmRegistry:
    base = dict(
        policy="auto", cheap_selection="fixed", cheap_default_id="fast",
        expensive_opt_in=False, expensive_selection="fixed", expensive_default_id="yandex-lite",
        daily_cap_usd=5.0,
        escalation=EscalationConfig(enabled=True, chain=("fast",), triggers=("empty_output",), max_steps=2),
        cheap_models=(_spec("fast"),), expensive_models=(), source="test",
    )
    base.update(kw)
    return LlmRegistry(**base)


@allure.title("_normalize_provider and _parse_profiles branches")
def test_provider_and_profiles() -> None:
    assert _normalize_provider(None) is None
    assert _normalize_provider("bogus") is None
    assert _normalize_provider("Ollama") == "ollama"

    assert _parse_profiles(None) == ("*",)
    assert _parse_profiles("  ") == ("*",)
    assert _parse_profiles("solo") == ("solo",)
    assert _parse_profiles(["a", " ", "b"]) == ("a", "b")
    assert _parse_profiles([]) == ("*",)
    assert _parse_profiles(123) == ("*",)


@allure.title("_parse_model_entry: no id, cost fallback, api_key from env")
def test_parse_model_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _parse_model_entry({}, tier="cheap") is None

    monkeypatch.setenv("MY_KEY_ENV", "sk-from-env")
    spec = _parse_model_entry(
        {"id": "m1", "api_key_env": "MY_KEY_ENV", "cost_per_1m_usd": "not-a-number"}, tier="cheap"
    )
    assert spec is not None
    assert spec.api_key == "sk-from-env"
    assert spec.cost_per_1m_usd == 0.0

    exp = _parse_model_entry({"id": "y", "cost_per_1m_usd": 12}, tier="expensive")
    assert exp is not None and exp.provider == "yandex_gpt" and exp.cost_per_1m_usd == 12.0


@allure.title("_pick_from_pool: empty, auto, fixed default, no-match fallback")
def test_pick_from_pool() -> None:
    assert _pick_from_pool([], profile="p", selection="fixed", default_id="x") is None

    pool = [_spec("fast", profiles=("p",)), _spec("big", profiles=("p",))]
    # auto → first matched
    assert _pick_from_pool(pool, profile="p", selection="auto", default_id="big").id == "fast"
    # fixed, default in matched
    assert _pick_from_pool(pool, profile="p", selection="fixed", default_id="big").id == "big"
    # fixed, default NOT in matched → first matched
    assert _pick_from_pool(pool, profile="p", selection="fixed", default_id="ghost").id == "fast"
    # profile set but nothing matches → default lookup over whole pool
    nomatch = [_spec("only", profiles=("q",))]
    assert _pick_from_pool(nomatch, profile="p", selection="fixed", default_id="only").id == "only"
    # no profile → default/first fallback
    assert _pick_from_pool(pool, profile="", selection="fixed", default_id="ghost").id == "fast"


@allure.title("resolve_model: env id, policy raises, auto fallback to expensive/cheap")
def test_resolve_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # env id match (no profile)
    reg_env = _registry(cheap_models=(_spec("fast"), _spec("special")))
    monkeypatch.setattr(ms, "get_llm_registry", lambda root=None: reg_env)
    monkeypatch.setenv("GREEDY_LLM_MODEL_ID", "special")
    assert resolve_model("", root=None).model_id == "special"
    # env id not found → falls through to normal selection
    monkeypatch.setenv("GREEDY_LLM_MODEL_ID", "ghost")
    assert resolve_model("", root=None).model_id == "fast"
    monkeypatch.delenv("GREEDY_LLM_MODEL_ID", raising=False)

    # expensive_only with no expensive models → raises
    monkeypatch.setattr(ms, "get_llm_registry", lambda root=None: _registry(policy="expensive_only", expensive_models=()))
    with pytest.raises(ValueError, match="expensive_only"):
        resolve_model("p", root=None)

    # cheap_only with all cheap disabled → raises
    monkeypatch.setattr(
        ms, "get_llm_registry", lambda root=None: _registry(policy="cheap_only", cheap_models=(_spec("off", enabled=False),))
    )
    with pytest.raises(ValueError, match="No enabled cheap"):
        resolve_model("p", root=None)

    # auto: profile only on expensive → cheap match rejected, expensive returned
    reg_auto = _registry(
        cheap_models=(_spec("fast", profiles=("other",)),),
        expensive_models=(_spec("yandex-lite", tier="expensive", profiles=("wanted",)),),
    )
    monkeypatch.setattr(ms, "get_llm_registry", lambda root=None: reg_auto)
    assert resolve_model("wanted", root=None).billing_tier == "expensive"

    # auto: no expensive, cheap match rejected on profile → fallback to cheap_match
    reg_cheap_fallback = _registry(cheap_models=(_spec("fast", profiles=("other",)),), expensive_models=())
    monkeypatch.setattr(ms, "get_llm_registry", lambda root=None: reg_cheap_fallback)
    assert resolve_model("wanted", root=None).model_id == "fast"

    # auto: no enabled models at all → raises
    monkeypatch.setattr(
        ms, "get_llm_registry", lambda root=None: _registry(cheap_models=(_spec("x", enabled=False),), expensive_models=())
    )
    with pytest.raises(ValueError, match="No enabled LLM models"):
        resolve_model("p", root=None)


@allure.title("escalation_chain_from: disabled, ordering, skips, max_steps")
def test_escalation_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    start = ResolvedModel(spec=_spec("fast"), settings=CheapLlmSettings("ollama", "u", "m", "s"), profile="p")

    # disabled → []
    monkeypatch.setattr(ms, "get_llm_registry", lambda root=None: _registry(escalation=EscalationConfig(False, (), (), 2)))
    assert escalation_chain_from(start, root=None) == []

    # chain after start, skip missing id, skip expensive when cheap_only
    reg = _registry(
        policy="cheap_only",
        cheap_models=(_spec("fast"), _spec("big")),
        expensive_models=(_spec("yandex", tier="expensive"),),
        escalation=EscalationConfig(True, ("fast", "ghost", "big", "yandex"), ("empty_output",), 5),
    )
    monkeypatch.setattr(ms, "get_llm_registry", lambda root=None: reg)
    chain = escalation_chain_from(start, root=None)
    assert [c.model_id for c in chain] == ["big"]  # ghost missing, yandex skipped (cheap_only)

    # expensive skipped when not opt_in; max_steps limits
    reg2 = _registry(
        policy="auto", expensive_opt_in=False,
        cheap_models=(_spec("fast"), _spec("big")),
        expensive_models=(_spec("yandex", tier="expensive"),),
        escalation=EscalationConfig(True, ("fast", "big", "yandex"), ("empty_output",), 1),
    )
    monkeypatch.setattr(ms, "get_llm_registry", lambda root=None: reg2)
    chain2 = escalation_chain_from(start, root=None)
    assert [c.model_id for c in chain2] == ["big"]  # max_steps=1 stops before yandex

    # start not in chain → start_idx 0
    other = ResolvedModel(spec=_spec("outsider"), settings=CheapLlmSettings("ollama", "u", "m", "s"), profile="p")
    reg3 = _registry(
        expensive_opt_in=True,
        cheap_models=(_spec("fast"), _spec("big")),
        expensive_models=(_spec("yandex", tier="expensive"),),
        escalation=EscalationConfig(True, ("fast", "big", "yandex"), ("empty_output",), 5),
    )
    monkeypatch.setattr(ms, "get_llm_registry", lambda root=None: reg3)
    chain3 = escalation_chain_from(other, root=None)
    assert [c.model_id for c in chain3][:1] == ["fast"]


@allure.title("_warn_env_model_override warns once then short-circuits")
def test_warn_env_model_override(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(ms, "_env_model_warned", False)
    monkeypatch.setenv("CHEAP_LLM_MODEL", "legacy-m")
    _warn_env_model_override()
    assert "deprecated" in capsys.readouterr().err
    # global flag now set → early return, no second warning
    _warn_env_model_override()
    assert capsys.readouterr().err == ""


@allure.title("_merge_configs merges nested dicts and keeps user-only keys")
def test_merge_configs() -> None:
    user = {"llm": {"cheap": {"a": 1, "b": 2}, "only_user": "u"}}
    ws = {"llm": {"cheap": {"b": 9, "c": 3}}}
    merged = _merge_configs(user, ws)
    assert merged["cheap"] == {"a": 1, "b": 9, "c": 3}
    assert merged["only_user"] == "u"


@allure.title("_parse_registry skips junk entries and recovers from bad numbers")
def test_parse_registry_edges() -> None:
    legacy = CheapLlmSettings("ollama", "http://x", "m", "s")
    cfg = {
        "cheap": {"models": ["not-a-dict", {"no_id": True}, {"id": "fast", "model": "m7"}]},
        "expensive": {"models": ["skip", {}, {"id": "y", "provider": "yandex_gpt", "model": "yg"}]},
        "escalation": {"max_steps": "bad"},
    }
    reg = _parse_registry(cfg, legacy_cheap=legacy, source="test")
    assert [m.id for m in reg.cheap_models] == ["fast"]
    assert [m.id for m in reg.expensive_models] == ["y"]
    assert reg.escalation.max_steps == 2

    reg2 = _parse_registry({"expensive": {"daily_cap_usd": "nope"}}, legacy_cheap=legacy, source="test")
    assert reg2.daily_cap_usd == 5.0


@allure.title("get_llm_registry(None) skips workspace config read")
def test_get_llm_registry_no_root() -> None:
    reg = get_llm_registry(None)
    assert reg.cheap_models


@allure.title("escalation skips expensive when opt-in disabled, keeps cheap")
def test_escalation_skips_expensive_no_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    start = ResolvedModel(spec=_spec("fast"), settings=CheapLlmSettings("ollama", "u", "m", "s"), profile="p")
    reg = _registry(
        policy="auto", expensive_opt_in=False,
        cheap_models=(_spec("fast"), _spec("big")),
        expensive_models=(_spec("yandex", tier="expensive"),),
        escalation=EscalationConfig(True, ("fast", "yandex", "big"), ("empty_output",), 5),
    )
    monkeypatch.setattr(ms, "get_llm_registry", lambda root=None: reg)
    chain = escalation_chain_from(start, root=None)
    assert [c.model_id for c in chain] == ["big"]  # yandex skipped (no opt-in)


@allure.title("apply_model_env exports resolved model into environment")
def test_apply_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = ResolvedModel(
        spec=_spec("fast"),
        settings=CheapLlmSettings("ollama", "http://o:11434", "m-fast", "s", api_key="sk-1"),
        profile="p",
        billing_tier="cheap",
    )
    apply_model_env(resolved)
    assert os.environ["GREEDY_LLM_MODEL_ID"] == "fast"
    assert os.environ["CHEAP_LLM_API_KEY"] == "sk-1"
    assert os.environ["OLLAMA_URL"] == "http://o:11434"
