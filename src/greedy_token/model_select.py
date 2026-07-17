"""Multi-model registry and profile-based model selection."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from greedy_token.settings import (
    CheapLlmProvider,
    CheapLlmSettings,
    DEFAULT_CHEAP_LLM_MODEL,
    DEFAULT_CHEAP_LLM_PROVIDER,
    DEFAULT_CHEAP_LLM_URL,
    _read_yaml,
    _resolve_cheap_llm,
    _section,
    user_config_path,
    workspace_config_path,
)

LlmPolicy = Literal["auto", "cheap_only", "expensive_only", "hybrid"]
ModelTier = Literal["cheap", "expensive"]
LlmProvider = Literal["ollama", "openai_compat", "yandex_gpt"]
SelectionMode = Literal["fixed", "auto"]

_env_model_warned = False


@dataclass(frozen=True)
class ModelSpec:
    id: str
    enabled: bool
    provider: LlmProvider
    url: str
    model: str
    profiles: tuple[str, ...]
    tier: ModelTier
    cost_per_1m_usd: float = 0.0
    api_key: str = ""
    api_key_env: str = ""


@dataclass(frozen=True)
class EscalationConfig:
    enabled: bool
    chain: tuple[str, ...]
    triggers: tuple[str, ...]
    max_steps: int


@dataclass(frozen=True)
class LlmRegistry:
    policy: LlmPolicy
    cheap_selection: SelectionMode
    cheap_default_id: str
    expensive_opt_in: bool
    expensive_selection: SelectionMode
    expensive_default_id: str
    daily_cap_usd: float
    escalation: EscalationConfig
    cheap_models: tuple[ModelSpec, ...]
    expensive_models: tuple[ModelSpec, ...]
    source: str = "default"


@dataclass(frozen=True)
class ResolvedModel:
    spec: ModelSpec
    settings: CheapLlmSettings
    profile: str = ""
    billing_tier: ModelTier = "cheap"

    @property
    def model_id(self) -> str:
        return self.spec.id


def _warn_env_model_override() -> None:
    global _env_model_warned
    if _env_model_warned:
        return
    if os.environ.get("CHEAP_LLM_MODEL", "").strip() or os.environ.get("OLLAMA_MODEL", "").strip():
        print(
            "greedy-token: CHEAP_LLM_MODEL / OLLAMA_MODEL env override is deprecated; "
            "use llm.cheap.models[] profiles or GREEDY_LLM_MODEL_ID",
            file=sys.stderr,
        )
        _env_model_warned = True


def _normalize_provider(value: str | None) -> LlmProvider | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized in ("ollama", "openai_compat", "yandex_gpt"):
        return normalized  # type: ignore[return-value]
    return None


def _parse_profiles(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ("*",)
    if isinstance(raw, str):
        return (raw.strip(),) if raw.strip() else ("*",)
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
        return tuple(items) if items else ("*",)
    return ("*",)


def _parse_model_entry(raw: dict[str, Any], *, tier: ModelTier) -> ModelSpec | None:
    model_id = str(raw.get("id", "")).strip()
    if not model_id:
        return None
    provider = _normalize_provider(raw.get("provider")) or (
        "yandex_gpt" if tier == "expensive" else DEFAULT_CHEAP_LLM_PROVIDER
    )
    url = str(raw.get("url", DEFAULT_CHEAP_LLM_URL if tier == "cheap" else "")).strip().rstrip("/")
    model = str(raw.get("model", DEFAULT_CHEAP_LLM_MODEL if tier == "cheap" else "")).strip()
    api_key_env = str(raw.get("api_key_env", "")).strip()
    api_key = str(raw.get("api_key", "")).strip()
    if api_key_env and not api_key:
        api_key = os.environ.get(api_key_env, "").strip()
    cost_raw = raw.get("cost_per_1m_usd", raw.get("token_price", 0))
    try:
        cost = float(cost_raw) if cost_raw is not None else 0.0
    except (TypeError, ValueError):
        cost = 0.0
    return ModelSpec(
        id=model_id,
        enabled=bool(raw.get("enabled", raw.get("turned_on", True))),
        provider=provider,
        url=url,
        model=model,
        profiles=_parse_profiles(raw.get("profiles")),
        tier=tier,
        cost_per_1m_usd=cost,
        api_key=api_key,
        api_key_env=api_key_env,
    )


def _legacy_default_model(cheap: CheapLlmSettings) -> ModelSpec:
    return ModelSpec(
        id="default",
        enabled=True,
        provider=cheap.provider,  # type: ignore[arg-type]
        url=cheap.url,
        model=cheap.model,
        profiles=("*",),
        tier="cheap",
        cost_per_1m_usd=0.0,
        api_key=cheap.api_key,
    )


def _merge_configs(user_cfg: dict[str, Any], workspace_cfg: dict[str, Any]) -> dict[str, Any]:
    llm_user = _section(user_cfg, "llm")
    llm_ws = _section(workspace_cfg, "llm")
    merged: dict[str, Any] = {}
    for key in set(llm_user) | set(llm_ws):
        u_val = llm_user.get(key)
        w_val = llm_ws.get(key)
        if isinstance(u_val, dict) and isinstance(w_val, dict):
            merged[key] = {**u_val, **w_val}
        elif w_val is not None:
            merged[key] = w_val
        else:
            merged[key] = u_val
    return merged


def _parse_registry(
    llm_cfg: dict[str, Any],
    *,
    legacy_cheap: CheapLlmSettings,
    source: str,
) -> LlmRegistry:
    if not llm_cfg:
        default = _legacy_default_model(legacy_cheap)
        return LlmRegistry(
            policy="auto",
            cheap_selection="fixed",
            cheap_default_id=default.id,
            expensive_opt_in=False,
            expensive_selection="fixed",
            expensive_default_id="yandex-lite",
            daily_cap_usd=5.0,
            escalation=EscalationConfig(
                enabled=True,
                chain=(default.id,),
                triggers=("empty_output", "low_confidence", "json_parse_fail", "explicit_profile"),
                max_steps=2,
            ),
            cheap_models=(default,),
            expensive_models=(),
            source=source,
        )

    policy_raw = str(llm_cfg.get("policy", "auto")).strip().lower()
    policy: LlmPolicy = (
        policy_raw
        if policy_raw in ("auto", "cheap_only", "expensive_only", "hybrid")
        else "auto"
    )

    cheap_section = _section(llm_cfg, "cheap")
    expensive_section = _section(llm_cfg, "expensive")
    esc_section = _section(llm_cfg, "escalation")

    cheap_models: list[ModelSpec] = []
    for entry in cheap_section.get("models") or []:
        if isinstance(entry, dict):
            spec = _parse_model_entry(entry, tier="cheap")
            if spec:
                cheap_models.append(spec)
    if not cheap_models:
        cheap_models.append(_legacy_default_model(legacy_cheap))

    expensive_models: list[ModelSpec] = []
    for entry in expensive_section.get("models") or []:
        if isinstance(entry, dict):
            spec = _parse_model_entry(entry, tier="expensive")
            if spec:
                expensive_models.append(spec)

    cheap_sel = str(cheap_section.get("selection", "auto")).strip().lower()
    cheap_selection: SelectionMode = "auto" if cheap_sel == "auto" else "fixed"
    exp_sel = str(expensive_section.get("selection", "fixed")).strip().lower()
    expensive_selection: SelectionMode = "auto" if exp_sel == "auto" else "fixed"

    chain_raw = esc_section.get("chain") or [m.id for m in cheap_models]
    chain = tuple(str(x).strip() for x in chain_raw if str(x).strip())
    triggers_raw = esc_section.get("triggers") or [
        "empty_output",
        "low_confidence",
        "json_parse_fail",
        "explicit_profile",
    ]
    triggers = tuple(str(x).strip() for x in triggers_raw if str(x).strip())

    try:
        max_steps = int(esc_section.get("max_steps", 2))
    except (TypeError, ValueError):
        max_steps = 2

    try:
        daily_cap = float(expensive_section.get("daily_cap_usd", 5))
    except (TypeError, ValueError):
        daily_cap = 5.0

    return LlmRegistry(
        policy=policy,
        cheap_selection=cheap_selection,
        cheap_default_id=str(cheap_section.get("default_id", cheap_models[0].id)).strip(),
        expensive_opt_in=bool(expensive_section.get("opt_in", False)),
        expensive_selection=expensive_selection,
        expensive_default_id=str(
            expensive_section.get("default_id", expensive_models[0].id if expensive_models else "yandex-lite")
        ).strip(),
        daily_cap_usd=daily_cap,
        escalation=EscalationConfig(
            enabled=bool(esc_section.get("enabled", True)),
            chain=chain,
            triggers=triggers,
            max_steps=max(0, max_steps),
        ),
        cheap_models=tuple(cheap_models),
        expensive_models=tuple(expensive_models),
        source=source,
    )


def get_llm_registry(root: Path | None = None) -> LlmRegistry:
    user_cfg = _read_yaml(user_config_path())
    workspace_cfg: dict[str, Any] = {}
    if root is not None:
        workspace_cfg = _read_yaml(workspace_config_path(root))
    legacy = _resolve_cheap_llm(user_cfg=user_cfg, workspace_cfg=workspace_cfg, root=root)
    merged = _merge_configs(user_cfg, workspace_cfg)
    source = "workspace" if merged else ("user" if _section(user_cfg, "llm") else legacy.source)
    return _parse_registry(merged, legacy_cheap=legacy, source=source)


def list_models(root: Path | None = None) -> list[ModelSpec]:
    reg = get_llm_registry(root)
    return list(reg.cheap_models) + list(reg.expensive_models)


def _profile_matches(spec: ModelSpec, profile: str) -> bool:
    if "*" in spec.profiles:
        return True
    base = profile.removesuffix(":escalate")
    return profile in spec.profiles or base in spec.profiles


def _enabled_pool(registry: LlmRegistry, tier: ModelTier) -> list[ModelSpec]:
    if tier == "cheap":
        return [m for m in registry.cheap_models if m.enabled]
    return [m for m in registry.expensive_models if m.enabled]


def _spec_to_settings(spec: ModelSpec, *, source: str) -> CheapLlmSettings:
    provider: CheapLlmProvider
    if spec.provider == "yandex_gpt":
        provider = "openai_compat"
    else:
        provider = spec.provider  # type: ignore[assignment]
    return CheapLlmSettings(
        provider=provider,
        url=spec.url or DEFAULT_CHEAP_LLM_URL,
        model=spec.model,
        source=source,
        api_key=spec.api_key,
    )


def _pick_from_pool(
    pool: list[ModelSpec],
    *,
    profile: str,
    selection: SelectionMode,
    default_id: str,
) -> ModelSpec | None:
    if not pool:
        return None
    if profile:
        matched = [m for m in pool if _profile_matches(m, profile)]
        if matched:
            if selection == "auto":
                return matched[0]
            for mid in (default_id,):
                for m in matched:
                    if m.id == mid:
                        return m
            return matched[0]
    for mid in (default_id, pool[0].id):
        for m in pool:
            if m.id == mid:
                return m
    return pool[0]  # pragma: no cover - pool[0].id always matches above


def resolve_model(
    profile: str = "",
    *,
    root: Path | None = None,
    tier_hint: ModelTier | None = None,
) -> ResolvedModel:
    """Resolve a model by profile, policy, and registry defaults."""
    _warn_env_model_override()
    registry = get_llm_registry(root)
    profile = profile.strip()
    base_profile = profile.removesuffix(":escalate")

    env_id = os.environ.get("GREEDY_LLM_MODEL_ID", "").strip()
    if env_id and not profile:
        for spec in list_models(root):
            if spec.id == env_id and spec.enabled:
                return ResolvedModel(
                    spec=spec,
                    settings=_spec_to_settings(spec, source="env"),
                    profile=profile or base_profile,
                    billing_tier=spec.tier,
                )

    cheap_pool = _enabled_pool(registry, "cheap")
    expensive_pool = _enabled_pool(registry, "expensive")

    if registry.policy == "expensive_only" or tier_hint == "expensive":
        spec = _pick_from_pool(
            expensive_pool,
            profile=profile,
            selection=registry.expensive_selection,
            default_id=registry.expensive_default_id,
        )
        if spec:
            return ResolvedModel(
                spec=spec,
                settings=_spec_to_settings(spec, source=registry.source),
                profile=profile or base_profile,
                billing_tier="expensive",
            )
        raise ValueError("No enabled expensive LLM models (policy=expensive_only)")

    if registry.policy == "cheap_only" or tier_hint == "cheap":
        spec = _pick_from_pool(
            cheap_pool,
            profile=profile,
            selection=registry.cheap_selection,
            default_id=registry.cheap_default_id,
        )
        if spec:
            return ResolvedModel(
                spec=spec,
                settings=_spec_to_settings(spec, source=registry.source),
                profile=profile or base_profile,
                billing_tier="cheap",
            )
        raise ValueError("No enabled cheap LLM models")

    # auto: prefer cheap for profile, unless profile only on expensive
    cheap_match = _pick_from_pool(
        cheap_pool,
        profile=profile,
        selection=registry.cheap_selection,
        default_id=registry.cheap_default_id,
    )
    if cheap_match and (not profile or _profile_matches(cheap_match, profile)):
        return ResolvedModel(
            spec=cheap_match,
            settings=_spec_to_settings(cheap_match, source=registry.source),
            profile=profile or base_profile,
            billing_tier="cheap",
        )

    spec = _pick_from_pool(
        expensive_pool,
        profile=profile,
        selection=registry.expensive_selection,
        default_id=registry.expensive_default_id,
    )
    if spec:
        return ResolvedModel(
            spec=spec,
            settings=_spec_to_settings(spec, source=registry.source),
            profile=profile or base_profile,
            billing_tier="expensive",
        )

    if cheap_match:
        return ResolvedModel(
            spec=cheap_match,
            settings=_spec_to_settings(cheap_match, source=registry.source),
            profile=profile or base_profile,
            billing_tier="cheap",
        )
    raise ValueError("No enabled LLM models in registry")


def escalation_chain_from(
    start: ResolvedModel,
    *,
    root: Path | None = None,
) -> list[ResolvedModel]:
    """Ordered escalation candidates after *start* (enabled models only)."""
    registry = get_llm_registry(root)
    if not registry.escalation.enabled:
        return []
    ids = registry.escalation.chain
    if start.model_id in ids:
        start_idx = ids.index(start.model_id) + 1
    else:
        start_idx = 0
    by_id = {m.id: m for m in list_models(root) if m.enabled}
    out: list[ResolvedModel] = []
    steps = 0
    for mid in ids[start_idx:]:
        if steps >= registry.escalation.max_steps:
            break
        spec = by_id.get(mid)
        if spec is None:
            continue
        if spec.tier == "expensive" and registry.policy == "cheap_only":
            continue
        if spec.tier == "expensive" and not registry.expensive_opt_in:
            continue
        out.append(
            ResolvedModel(
                spec=spec,
                settings=_spec_to_settings(spec, source=registry.source),
                profile=start.profile,
                billing_tier=spec.tier,
            )
        )
        steps += 1
    return out


def apply_model_env(resolved: ResolvedModel) -> None:
    """Export resolved model into os.environ for shell wrappers."""
    s = resolved.settings
    os.environ["GREEDY_LLM_MODEL_ID"] = resolved.model_id
    os.environ["GREEDY_LLM_PROFILE"] = resolved.profile
    os.environ["GREEDY_LLM_TIER"] = resolved.billing_tier
    os.environ.setdefault("CHEAP_LLM_PROVIDER", s.provider)
    os.environ.setdefault("CHEAP_LLM_URL", s.url)
    os.environ.setdefault("CHEAP_LLM_MODEL", s.model)
    if s.api_key:
        os.environ.setdefault("CHEAP_LLM_API_KEY", s.api_key)
    os.environ.setdefault("OLLAMA_URL", s.url)
    os.environ.setdefault("OLLAMA_MODEL", s.model)
