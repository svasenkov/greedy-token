"""User/workspace cheap LLM settings (config key cheap_llm) — single source of truth."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

CheapLlmProvider = Literal["ollama", "openai_compat"]
FooterStyle = Literal["compact", "markdown", "full"]
DEFAULT_FOOTER_STYLE: FooterStyle = "compact"

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
    api_key: str = ""


@dataclass(frozen=True)
class OllamaSettings:
    url: str
    model: str
    source: str


@dataclass(frozen=True)
class FooterSettings:
    style: FooterStyle
    source: str


SearchContextMode = Literal["none", "snippet", "file"]
DEFAULT_SEARCH_CONTEXT: SearchContextMode = "snippet"
DEFAULT_MAX_CONTEXT_TOKENS = 2000
DEFAULT_MAX_SNIPPET_FILES = 3
DEFAULT_CONTEXT_LINES = 15


@dataclass(frozen=True)
class SearchSettings:
    context: SearchContextMode
    max_context_tokens: int
    max_snippet_files: int
    context_lines: int
    source: str


def user_config_path() -> Path:
    return Path.home() / ".greedy-token" / "config.yaml"


def workspace_config_path(root: Path | None = None) -> Path:
    if root is None:
        from greedy_token.paths import find_workspace_root

        root = find_workspace_root()
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


def _normalize_footer_style(value: str | None) -> FooterStyle | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized in ("compact", "markdown", "full"):
        return normalized  # type: ignore[return-value]
    return None


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
    api_key: str,
    source: str,
    cheap_llm: dict[str, Any],
    ollama: dict[str, Any],
    level: str,
) -> tuple[CheapLlmProvider, str, str, str, str]:
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
    if cheap_llm.get("api_key"):
        api_key = str(cheap_llm["api_key"]).strip()
        source = level

    return provider, url.rstrip("/"), model, api_key, source


def _resolve_cheap_llm(
    *,
    user_cfg: dict[str, Any],
    workspace_cfg: dict[str, Any],
    root: Path | None = None,
) -> CheapLlmSettings:
    provider: CheapLlmProvider = DEFAULT_CHEAP_LLM_PROVIDER
    url = DEFAULT_CHEAP_LLM_URL
    model = DEFAULT_CHEAP_LLM_MODEL
    api_key = ""
    source = "default"

    provider, url, model, api_key, source = _apply_level(
        provider=provider,
        url=url,
        model=model,
        api_key=api_key,
        source=source,
        cheap_llm=_section(user_cfg, "cheap_llm"),
        ollama=_section(user_cfg, "ollama"),
        level="user",
    )
    provider, url, model, api_key, source = _apply_level(
        provider=provider,
        url=url,
        model=model,
        api_key=api_key,
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
    if os.environ.get("CHEAP_LLM_API_KEY", "").strip():
        api_key = os.environ["CHEAP_LLM_API_KEY"].strip()
        source = "env"

    return CheapLlmSettings(
        provider=provider, url=url, model=model, source=source, api_key=api_key
    )


def _resolve_footer_style(
    *,
    user_cfg: dict[str, Any],
    workspace_cfg: dict[str, Any],
) -> FooterSettings:
    style: FooterStyle = DEFAULT_FOOTER_STYLE
    source = "default"

    for level, cfg in (("user", user_cfg), ("workspace", workspace_cfg)):
        footer = _section(cfg, "footer")
        next_style = _normalize_footer_style(footer.get("style"))
        if next_style:
            style = next_style
            source = level

    env_style = _normalize_footer_style(os.environ.get("GREEDY_TOKEN_FOOTER_STYLE", ""))
    if env_style:
        style = env_style
        source = "env"

    return FooterSettings(style=style, source=source)


def get_footer_settings(root: Path | None = None) -> FooterSettings:
    user_cfg = _read_yaml(user_config_path())
    workspace_cfg: dict[str, Any] = {}
    if root is not None:
        workspace_cfg = _read_yaml(workspace_config_path(root))
    else:
        try:
            from greedy_token.paths import find_workspace_root

            workspace_cfg = _read_yaml(workspace_config_path(find_workspace_root()))
        except SystemExit:
            pass
    return _resolve_footer_style(user_cfg=user_cfg, workspace_cfg=workspace_cfg)


def _normalize_search_context(value: str | None) -> SearchContextMode | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized in ("none", "snippet", "file"):
        return normalized  # type: ignore[return-value]
    return None


def _resolve_search_settings(
    *,
    user_cfg: dict[str, Any],
    workspace_cfg: dict[str, Any],
) -> SearchSettings:
    context: SearchContextMode = DEFAULT_SEARCH_CONTEXT
    max_tokens = DEFAULT_MAX_CONTEXT_TOKENS
    max_files = DEFAULT_MAX_SNIPPET_FILES
    context_lines = DEFAULT_CONTEXT_LINES
    source = "default"

    for level, cfg in (("user", user_cfg), ("workspace", workspace_cfg)):
        section = _section(cfg, "search")
        next_ctx = _normalize_search_context(
            str(section["context"]) if section.get("context") is not None else None
        )
        if next_ctx:
            context = next_ctx
            source = level
        if section.get("max_context_tokens") is not None:
            try:
                max_tokens = max(200, int(section["max_context_tokens"]))
                source = level
            except (TypeError, ValueError):
                pass
        if section.get("max_snippet_files") is not None:
            try:
                max_files = max(1, min(10, int(section["max_snippet_files"])))
                source = level
            except (TypeError, ValueError):
                pass
        if section.get("context_lines") is not None:
            try:
                context_lines = max(3, min(80, int(section["context_lines"])))
                source = level
            except (TypeError, ValueError):
                pass

    env_ctx = _normalize_search_context(os.environ.get("GREEDY_TOKEN_SEARCH_CONTEXT", ""))
    if env_ctx:
        context = env_ctx
        source = "env"
    if os.environ.get("GREEDY_TOKEN_MAX_CONTEXT_TOKENS", "").strip():
        try:
            max_tokens = max(200, int(os.environ["GREEDY_TOKEN_MAX_CONTEXT_TOKENS"]))
            source = "env"
        except ValueError:
            pass

    return SearchSettings(
        context=context,
        max_context_tokens=max_tokens,
        max_snippet_files=max_files,
        context_lines=context_lines,
        source=source,
    )


def get_search_settings(root: Path | None = None) -> SearchSettings:
    user_cfg = _read_yaml(user_config_path())
    workspace_cfg: dict[str, Any] = {}
    if root is not None:
        workspace_cfg = _read_yaml(workspace_config_path(root))
    else:
        try:
            from greedy_token.paths import find_workspace_root

            workspace_cfg = _read_yaml(workspace_config_path(find_workspace_root()))
        except SystemExit:
            pass
    return _resolve_search_settings(user_cfg=user_cfg, workspace_cfg=workspace_cfg)


def get_cheap_llm_settings(root: Path | None = None) -> CheapLlmSettings:
    """Resolved default cheap model (legacy API — delegates to model registry when configured)."""
    user_cfg = _read_yaml(user_config_path())
    workspace_cfg: dict[str, Any] = {}
    resolved_root = root
    if resolved_root is None:
        try:
            from greedy_token.paths import find_workspace_root

            resolved_root = find_workspace_root()
        except SystemExit:
            resolved_root = None
    if resolved_root is not None:
        workspace_cfg = _read_yaml(workspace_config_path(resolved_root))

    if not (_section(user_cfg, "llm") or _section(workspace_cfg, "llm")):
        return _resolve_cheap_llm(
            user_cfg=user_cfg, workspace_cfg=workspace_cfg, root=resolved_root
        )

    from greedy_token.model_select import resolve_model

    try:
        return resolve_model("", root=resolved_root, tier_hint="cheap").settings
    except ValueError:
        return _resolve_cheap_llm(
            user_cfg=user_cfg, workspace_cfg=workspace_cfg, root=resolved_root
        )


def get_ollama_settings(root: Path | None = None) -> OllamaSettings:
    """Backward-compat alias — url/model from cheap_llm settings."""
    settings = get_cheap_llm_settings(root)
    return OllamaSettings(url=settings.url, model=settings.model, source=settings.source)


# Backward-compat alias for tests and internal callers
_resolve_ollama = _resolve_cheap_llm


def apply_cheap_llm_env(root: Path | None = None, *, profile: str = "") -> CheapLlmSettings:
    """Export resolved settings into os.environ for shell wrappers."""
    from greedy_token.model_select import apply_model_env, resolve_model

    resolved_root = root
    if resolved_root is None:
        try:
            from greedy_token.paths import find_workspace_root

            resolved_root = find_workspace_root()
        except SystemExit:
            resolved_root = None

    user_cfg = _read_yaml(user_config_path())
    workspace_cfg: dict[str, Any] = {}
    if resolved_root is not None:
        workspace_cfg = _read_yaml(workspace_config_path(resolved_root))

    use_registry = bool(
        profile.strip()
        or _section(user_cfg, "llm")
        or _section(workspace_cfg, "llm")
    )
    if use_registry:
        try:
            resolved = resolve_model(profile, root=resolved_root)
            apply_model_env(resolved)
            return resolved.settings
        except ValueError:
            pass

    settings = get_cheap_llm_settings(resolved_root)
    os.environ.setdefault("CHEAP_LLM_PROVIDER", settings.provider)
    os.environ.setdefault("CHEAP_LLM_URL", settings.url)
    os.environ.setdefault("CHEAP_LLM_MODEL", settings.model)
    if settings.api_key:
        os.environ.setdefault("CHEAP_LLM_API_KEY", settings.api_key)
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
    footer = get_footer_settings(root)
    lines = [
        "greedy-token cheap LLM settings",
        "",
        f"  provider: {_provider_label(settings.provider)} ({settings.provider})",
        f"  url:      {settings.url}",
        f"  model:    {settings.model}",
        f"  source:   {settings.source}",
        "",
        "Footer (MCP tool responses):",
        f"  style:    {footer.style}  (compact | markdown | full)",
        f"  source:   {footer.source}",
        "",
        "Config files (low → high priority):",
        "  1. defaults",
        f"  2. {user_path}",
    ]
    if workspace_path is not None:
        lines.append(f"  3. {workspace_path}")
    lines.extend(
        [
            "  4. CHEAP_LLM_* / OLLAMA_* env (OLLAMA_* = url/model aliases;",
            "     CHEAP_LLM_API_KEY optional for openai_compat)",
            "",
            "Create user config:",
            "  greedy-token config --init",
            "  greedy-token config --init --preset local-ollama",
            "  greedy-token config --list-presets",
            "",
            "Multi-model: use llm.cheap.models[] + profiles (see docs/ROADMAP-RU.md).",
            "Prefer: greedy-token llm invoke --profile tms-classify over CHEAP_LLM_MODEL env.",
        ]
    )
    return "\n".join(lines)


MASKED_SECRET = "***"


def format_shell_export(
    settings: CheapLlmSettings | OllamaSettings | None = None,
    *,
    root: Path | None = None,
    reveal: bool = False,
) -> str:
    """Render shell `export` lines for the cheap-LLM settings.

    ``CHEAP_LLM_API_KEY`` is masked as ``***`` by default so piping into a
    terminal (and shell history) never leaks the secret. Pass ``reveal=True``
    to print the real key value.
    """
    if settings is None:
        settings = get_cheap_llm_settings(root)
    elif isinstance(settings, OllamaSettings):
        settings = CheapLlmSettings(
            provider=DEFAULT_CHEAP_LLM_PROVIDER,
            url=settings.url,
            model=settings.model,
            source=settings.source,
        )
    lines = [
        f'export CHEAP_LLM_PROVIDER="{settings.provider}"',
        f'export CHEAP_LLM_URL="{settings.url}"',
        f'export CHEAP_LLM_MODEL="{settings.model}"',
        f'export OLLAMA_URL="{settings.url}"',
        f'export OLLAMA_MODEL="{settings.model}"',
    ]
    if settings.api_key:
        value = settings.api_key if reveal else MASKED_SECRET
        lines.append(f'export CHEAP_LLM_API_KEY="{value}"')
    return "\n".join(lines)


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


def presets_dir() -> Path:
    from greedy_token.version import repo_root

    for candidate in (
        repo_root() / "examples" / "presets",
        Path(__file__).resolve().parent / "presets",
    ):
        if candidate.is_dir() and any(candidate.glob("*.yaml")):
            return candidate
    return Path(__file__).resolve().parent / "presets"  # pragma: no cover - packaged presets always present


def list_preset_names() -> list[str]:
    directory = presets_dir()
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.yaml"))


def preset_path(name: str) -> Path:
    safe = name.strip().removesuffix(".yaml")
    if not safe:
        raise FileNotFoundError("Preset name is required")
    path = presets_dir() / f"{safe}.yaml"
    if not path.is_file():
        available = ", ".join(list_preset_names()) or "(none)"
        raise FileNotFoundError(f"Unknown preset {name!r}. Available: {available}")
    return path


def load_preset_yaml(name: str) -> dict[str, Any]:
    raw = yaml.safe_load(preset_path(name).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Preset {name!r} is not a YAML mapping")
    return raw


def init_user_config_from_preset(*, preset: str, force: bool = False) -> Path:
    path = user_config_path()
    if path.is_file() and not force:
        raise FileExistsError(f"Config already exists: {path} (use --force to overwrite)")

    payload = load_preset_yaml(preset)
    path.parent.mkdir(parents=True, exist_ok=True)
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
        "footer:\n"
        f"  style: {DEFAULT_FOOTER_STYLE}  # compact | markdown | full\n"
        "search:\n"
        f"  context: {DEFAULT_SEARCH_CONTEXT}  # none | snippet | file\n"
        f"  max_context_tokens: {DEFAULT_MAX_CONTEXT_TOKENS}\n"
        f"  max_snippet_files: {DEFAULT_MAX_SNIPPET_FILES}\n"
        f"  context_lines: {DEFAULT_CONTEXT_LINES}\n"
    )
