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
    # ADR-0001: tier is derived — "cheap" → free billing, "expensive" → metered
    # billing with unknown cost (conservatively expensive).
    return ModelSpec(
        id=model_id, enabled=enabled, provider="ollama", url="http://x", model=f"m-{model_id}",
        profiles=tuple(profiles), billing="free" if tier == "cheap" else "metered",
    )


def _registry(**kw) -> LlmRegistry:
    # cheap_models/expensive_models kwargs are merged into the unified pool
    # (ADR-0001 phase 2); tier still derives from each spec's attributes.
    cheap = tuple(kw.pop("cheap_models", (_spec("fast"),)))
    expensive = tuple(kw.pop("expensive_models", ()))
    base = dict(
        policy="auto", cheap_selection="fixed", cheap_default_id="fast",
        expensive_opt_in=False, expensive_selection="fixed", expensive_default_id="yandex-lite",
        daily_cap_usd=5.0,
        escalation=EscalationConfig(enabled=True, chain=("fast",), triggers=("empty_output",), max_steps=2),
        models=cheap + expensive, source="test",
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
    assert _parse_model_entry({}, section="cheap") is None

    monkeypatch.setenv("MY_KEY_ENV", "sk-from-env")
    spec = _parse_model_entry(
        {"id": "m1", "api_key_env": "MY_KEY_ENV", "cost_per_1m_usd": "not-a-number"}, section="cheap"
    )
    assert spec is not None
    assert spec.api_key == "sk-from-env"
    assert spec.cost_per_1m_usd is None  # unparsable → unknown (ADR-0001)

    exp = _parse_model_entry({"id": "y", "cost_per_1m_usd": 12}, section="expensive")
    assert exp is not None and exp.provider == "yandex_gpt" and exp.cost_per_1m_usd == 12.0


@allure.title("derive_tier (ADR-0001): free, metered vs threshold, unknown cost")
def test_derive_tier() -> None:
    free = _spec("f")  # billing=free
    assert ms.derive_tier(free) == "cheap"

    metered_cheap = _parse_model_entry(
        {"id": "mini", "billing": "metered", "cost_per_1m_usd": 0.05}, section="cheap"
    )
    assert ms.derive_tier(metered_cheap) == "cheap"  # 0.05 <= default 0.2

    metered_over = _parse_model_entry(
        {"id": "big", "billing": "metered", "cost_per_1m_usd": 2.5}, section="cheap"
    )
    assert ms.derive_tier(metered_over) == "expensive"
    # custom threshold flips it back to cheap
    assert ms.derive_tier(metered_over, cheap_cost_threshold_per_1m_usd=3.0) == "cheap"

    unknown_cost = _spec("y", tier="expensive")  # metered, cost None
    assert ms.derive_tier(unknown_cost) == "expensive"
    # locality never affects the tier
    assert ms.derive_tier(ms.ModelSpec(
        id="loc", enabled=True, provider="ollama", url="http://x", model="m",
        profiles=("*",), locality="remote", billing="free",
    )) == "cheap"


@allure.title("attribute parsing: billing/locality defaults, explicit overrides")
def test_parse_model_entry_attributes() -> None:
    # cheap section, no cost → free + local (default ollama url is localhost)
    plain = _parse_model_entry({"id": "a"}, section="cheap")
    assert plain.billing == "free" and plain.locality == "local"

    # cheap section, cost > 0 → metered by default
    paid = _parse_model_entry(
        {"id": "b", "url": "https://api.example.com/v1", "cost_per_1m_usd": 0.15},
        section="cheap",
    )
    assert paid.billing == "metered" and paid.locality == "remote"

    # expensive section without cost → metered (legacy shim), no url → remote
    legacy_exp = _parse_model_entry({"id": "y", "model": "yg"}, section="expensive")
    assert legacy_exp.billing == "metered" and legacy_exp.cost_per_1m_usd is None
    assert legacy_exp.locality == "remote"
    assert ms.derive_tier(legacy_exp) == "expensive"

    # explicit attributes win over inference
    explicit = _parse_model_entry(
        {"id": "c", "billing": "metered", "locality": "remote"}, section="cheap"
    )
    assert explicit.billing == "metered" and explicit.locality == "remote"

    # token_price legacy alias still read
    alias = _parse_model_entry({"id": "d", "token_price": 7}, section="cheap")
    assert alias.cost_per_1m_usd == 7.0


@allure.title("_normalize_locality/_normalize_billing/_infer_locality branches")
def test_attribute_normalizers() -> None:
    assert ms._normalize_locality(None) is None
    assert ms._normalize_locality("bogus") is None
    assert ms._normalize_locality(" Local ") == "local"
    assert ms._normalize_billing(123) is None
    assert ms._normalize_billing("junk") is None
    assert ms._normalize_billing("Metered") == "metered"

    assert ms._infer_locality("") == "remote"
    assert ms._infer_locality("http://localhost:11434") == "local"
    assert ms._infer_locality("http://127.0.0.1:8000") == "local"
    assert ms._infer_locality("https://api.openai.com/v1") == "remote"


@allure.title("llm.cheap_cost_threshold_per_1m_usd parsed with default fallback")
def test_threshold_parsing() -> None:
    legacy = CheapLlmSettings("ollama", "http://x", "m", "s")
    reg = _parse_registry(
        {"cheap_cost_threshold_per_1m_usd": 0.5, "cheap": {"models": [{"id": "f"}]}},
        legacy_cheap=legacy, source="test",
    )
    assert reg.cheap_cost_threshold_per_1m_usd == 0.5

    reg_bad = _parse_registry(
        {"cheap_cost_threshold_per_1m_usd": "junk", "cheap": {"models": [{"id": "f"}]}},
        legacy_cheap=legacy, source="test",
    )
    assert reg_bad.cheap_cost_threshold_per_1m_usd == ms.DEFAULT_CHEAP_COST_THRESHOLD_PER_1M_USD

    # tier_of applies the registry threshold, not the default
    spec = _parse_model_entry({"id": "m", "billing": "metered", "cost_per_1m_usd": 0.4}, section="cheap")
    assert reg.tier_of(spec) == "cheap"        # 0.4 <= 0.5
    assert reg_bad.tier_of(spec) == "expensive"  # 0.4 > 0.2


@allure.title("unified pool: tier from attributes, not from the config section")
def test_unified_pool_derived_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = CheapLlmSettings("ollama", "http://x", "m", "s")
    cfg = {
        "cheap": {
            "models": [
                {"id": "fast", "model": "m7"},
                # declared cheap, but priced above the threshold → derives expensive
                {"id": "pricey", "model": "big", "cost_per_1m_usd": 2.5},
            ]
        },
        "expensive": {
            # declared expensive, but sub-threshold metered cost → derives cheap
            "models": [{"id": "groq", "model": "llama", "cost_per_1m_usd": 0.05}],
        },
    }
    reg = _parse_registry(cfg, legacy_cheap=legacy, source="test")
    assert [m.id for m in reg.models] == ["fast", "pricey", "groq"]
    assert [m.id for m in reg.cheap_models] == ["fast", "groq"]
    assert [m.id for m in reg.expensive_models] == ["pricey"]
    assert [m.id for m in ms._enabled_pool(reg, "cheap")] == ["fast", "groq"]
    assert [m.id for m in ms._enabled_pool(reg, "expensive")] == ["pricey"]
    # default escalation chain follows the derived cheap pool
    assert reg.escalation.chain == ("fast", "groq")

    # resolve under cheap_only can now pick the derived-cheap ex-"expensive" model
    monkeypatch.setattr(ms, "get_llm_registry", lambda root=None: reg)
    monkeypatch.delenv("GREEDY_LLM_MODEL_ID", raising=False)
    resolved = resolve_model("", root=None)
    assert resolved.model_id == "fast"
    assert resolved.billing_tier == "cheap"


@allure.title("llm.models[] unified list: junk skipped, sections deduped, no legacy default")
def test_parse_registry_unified_models_list() -> None:
    legacy = CheapLlmSettings("ollama", "http://legacy", "legacy-m", "s")
    cfg = {
        "models": [
            "junk",
            {"no_id": True},
            {"id": "fast", "model": "m7"},
            {"id": "paid", "model": "big", "billing": "metered", "cost_per_1m_usd": 2.5},
        ],
        # deprecated section lists still read; duplicate id is skipped
        "cheap": {"models": [{"id": "fast", "model": "shadowed"}, {"id": "extra", "model": "mx"}]},
        "expensive": {"models": [{"id": "y", "model": "yg"}]},
    }
    reg = _parse_registry(cfg, legacy_cheap=legacy, source="test")
    assert [m.id for m in reg.models] == ["fast", "paid", "extra", "y"]
    # first occurrence wins — llm.models[] entry, not the section duplicate
    assert next(m for m in reg.models if m.id == "fast").model == "m7"
    # llm.models[] present → no injected legacy "default" model
    assert all(m.id != "default" for m in reg.models)
    assert [m.id for m in reg.cheap_models] == ["fast", "extra"]
    assert [m.id for m in reg.expensive_models] == ["paid", "y"]

    # unified list alone (no sections) also suppresses the legacy default
    reg2 = _parse_registry({"models": [{"id": "solo", "model": "m"}]}, legacy_cheap=legacy, source="test")
    assert [m.id for m in reg2.models] == ["solo"]
    assert reg2.cheap_default_id == "solo"


@allure.title("cheap default falls back to the whole pool when nothing derives cheap")
def test_default_id_fallback_no_derived_cheap() -> None:
    legacy = CheapLlmSettings("ollama", "http://x", "m", "s")
    cfg = {"cheap": {"models": [{"id": "pricey", "model": "big", "cost_per_1m_usd": 5.0}]}}
    reg = _parse_registry(cfg, legacy_cheap=legacy, source="test")
    assert reg.cheap_models == ()
    assert reg.cheap_default_id == "pricey"  # falls back to models[0]
    assert reg.expensive_default_id == "pricey"  # derived expensive pool


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
