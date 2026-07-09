"""User/workspace cheap LLM settings (config key cheap_llm) — single source of truth."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

CheapLlmProvider = Literal["ollama", "openai_compat"]

DEFAULT_CHEAP_LLM_PROVIDER: CheapLlmProvider = "ollama"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:7b-instruct-q4_K_M"

# Backward-compat aliases
DEFAULT_CHEAP_LLM_URL = DEFAULT_OLLAMA_URL
DEFAULT_CHEAP_LLM_MODEL = DEFAULT_OLLAMA_MODEL


@dataclass(frozen=True)
class CheapLlmSettings:
    provider: CheapLlmProvider
    url: str
    model: str
    source: str


@dataclass(frozen=True)
class OllamaSettings:
    url: str
    model: str
    source: str


def user_config_path() -> Path:
    return Path.home() / ".greedy-token" / "config.yaml"


def workspace_config_path(root: Path | None = None) -> Path:
    if root is None:
        from greedy_token.paths import find_monorepo_root

        root = find_monorepo_root()
    return root / ".greedy-token.yaml"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _section(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    section = cfg.get(name)
    return section if isinstance(section, dict) else {}


def _normalize_provider(value: str | None) -> CheapLlmProvider | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized in ("ollama", "openai_compat"):
        return normalized  # type: ignore[return-value]
    return None


def _apply_level(
    *,
    provider: CheapLlmProvider,
    url: str,
    model: str,
    source: str,
    cheap_llm: dict[str, Any],
    ollama: dict[str, Any],
    level: str,
) -> tuple[CheapLlmProvider, str, str, str]:
    if ollama.get("url"):
        url = str(ollama["url"]).strip()
        source = level
    if ollama.get("model"):
        model = str(ollama["model"]).strip()
        source = level

    next_provider = _normalize_provider(cheap_llm.get("provider"))
    if next_provider:
        provider = next_provider
        source = level
    if cheap_llm.get("url"):
        url = str(cheap_llm["url"]).strip()
        source = level
    if cheap_llm.get("model"):
        model = str(cheap_llm["model"]).strip()
        source = level

    return provider, url.rstrip("/"), model, source


def _resolve_cheap_llm(
    *,
    user_cfg: dict[str, Any],
    workspace_cfg: dict[str, Any],
    root: Path | None = None,
) -> CheapLlmSettings:
    provider: CheapLlmProvider = DEFAULT_CHEAP_LLM_PROVIDER
    url = DEFAULT_CHEAP_LLM_URL
    model = DEFAULT_CHEAP_LLM_MODEL
    source = "default"

    provider, url, model, source = _apply_level(
        provider=provider,
        url=url,
        model=model,
        source=source,
        cheap_llm=_section(user_cfg, "cheap_llm"),
        ollama=_section(user_cfg, "ollama"),
        level="user",
    )
    provider, url, model, source = _apply_level(
        provider=provider,
        url=url,
        model=model,
        source=source,
        cheap_llm=_section(workspace_cfg, "cheap_llm"),
        ollama=_section(workspace_cfg, "ollama"),
        level="workspace",
    )

    env_provider = _normalize_provider(os.environ.get("CHEAP_LLM_PROVIDER", ""))
    if env_provider:
        provider = env_provider
        source = "env"
    if os.environ.get("CHEAP_LLM_URL", "").strip():
        url = os.environ["CHEAP_LLM_URL"].strip().rstrip("/")
        source = "env"
    elif os.environ.get("OLLAMA_URL", "").strip():
        url = os.environ["OLLAMA_URL"].strip().rstrip("/")
        source = "env"
    if os.environ.get("CHEAP_LLM_MODEL", "").strip():
        model = os.environ["CHEAP_LLM_MODEL"].strip()
        source = "env"
    elif os.environ.get("OLLAMA_MODEL", "").strip():
        model = os.environ["OLLAMA_MODEL"].strip()
        source = "env"

    return CheapLlmSettings(provider=provider, url=url, model=model, source=source)


def get_cheap_llm_settings(root: Path | None = None) -> CheapLlmSettings:
    user_cfg = _read_yaml(user_config_path())
    workspace_cfg: dict[str, Any] = {}
    if root is not None:
        workspace_cfg = _read_yaml(workspace_config_path(root))
    else:
        try:
            from greedy_token.paths import find_monorepo_root

            workspace_cfg = _read_yaml(workspace_config_path(find_monorepo_root()))
        except SystemExit:
            pass
    return _resolve_cheap_llm(user_cfg=user_cfg, workspace_cfg=workspace_cfg, root=root)


def get_ollama_settings(root: Path | None = None) -> OllamaSettings:
    """Backward-compat alias — url/model from cheap_llm settings."""
    settings = get_cheap_llm_settings(root)
    return OllamaSettings(url=settings.url, model=settings.model, source=settings.source)


# Backward-compat alias for tests and internal callers
_resolve_ollama = _resolve_cheap_llm


def apply_cheap_llm_env(root: Path | None = None) -> CheapLlmSettings:
    """Export resolved settings into os.environ for shell wrappers."""
    settings = get_cheap_llm_settings(root)
    os.environ.setdefault("CHEAP_LLM_PROVIDER", settings.provider)
    os.environ.setdefault("CHEAP_LLM_URL", settings.url)
    os.environ.setdefault("CHEAP_LLM_MODEL", settings.model)
    os.environ.setdefault("OLLAMA_URL", settings.url)
    os.environ.setdefault("OLLAMA_MODEL", settings.model)
    return settings


def apply_ollama_env(root: Path | None = None) -> OllamaSettings:
    """Backward-compat alias."""
    settings = apply_cheap_llm_env(root)
    return OllamaSettings(url=settings.url, model=settings.model, source=settings.source)


def _provider_label(provider: CheapLlmProvider) -> str:
    return "OpenAI-compatible" if provider == "openai_compat" else "Ollama"


def format_config(settings: CheapLlmSettings | OllamaSettings | None = None, *, root: Path | None = None) -> str:
    if settings is None:
        settings = get_cheap_llm_settings(root)
    elif isinstance(settings, OllamaSettings):
        settings = CheapLlmSettings(
            provider=DEFAULT_CHEAP_LLM_PROVIDER,
            url=settings.url,
            model=settings.model,
            source=settings.source,
        )
    user_path = user_config_path()
    workspace_path = workspace_config_path(root) if root else None
    lines = [
        "greedy-token cheap LLM settings",
        "",
        f"  provider: {_provider_label(settings.provider)} ({settings.provider})",
        f"  url:      {settings.url}",
        f"  model:    {settings.model}",
        f"  source:   {settings.source}",
        "",
        "Config files (low → high priority):",
        "  1. defaults",
        f"  2. {user_path}",
    ]
    if workspace_path is not None:
        lines.append(f"  3. {workspace_path}")
    lines.extend(
        [
            "  4. CHEAP_LLM_* / OLLAMA_* env (OLLAMA_* = url/model aliases)",
            "",
            "Create user config:",
            "  greedy-token config init",
        ]
    )
    return "\n".join(lines)


def format_shell_export(settings: CheapLlmSettings | OllamaSettings | None = None, *, root: Path | None = None) -> str:
    if settings is None:
        settings = get_cheap_llm_settings(root)
    elif isinstance(settings, OllamaSettings):
        settings = CheapLlmSettings(
            provider=DEFAULT_CHEAP_LLM_PROVIDER,
            url=settings.url,
            model=settings.model,
            source=settings.source,
        )
    return "\n".join(
        [
            f'export CHEAP_LLM_PROVIDER="{settings.provider}"',
            f'export CHEAP_LLM_URL="{settings.url}"',
            f'export CHEAP_LLM_MODEL="{settings.model}"',
            f'export OLLAMA_URL="{settings.url}"',
            f'export OLLAMA_MODEL="{settings.model}"',
        ]
    )


def init_user_config(
    *,
    url: str | None = None,
    model: str | None = None,
    provider: CheapLlmProvider | None = None,
    force: bool = False,
) -> Path:
    path = user_config_path()
    if path.is_file() and not force:
        raise FileExistsError(f"Config already exists: {path} (use --force to overwrite)")

    resolved_provider = provider or DEFAULT_CHEAP_LLM_PROVIDER
    resolved_url = url or DEFAULT_CHEAP_LLM_URL
    resolved_model = model or DEFAULT_CHEAP_LLM_MODEL

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cheap_llm": {
            "provider": resolved_provider,
            "url": resolved_url,
            "model": resolved_model,
        },
        # Legacy section — scripts reading ollama: still work
        "ollama": {
            "url": resolved_url,
            "model": resolved_model,
        },
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return path


def example_workspace_config() -> str:
    return (
        "# Project-local greedy-token settings (optional)\n"
        "cheap_llm:\n"
        f"  provider: {DEFAULT_CHEAP_LLM_PROVIDER}\n"
        f"  url: {DEFAULT_CHEAP_LLM_URL}\n"
        f"  model: {DEFAULT_CHEAP_LLM_MODEL}\n"
    )
