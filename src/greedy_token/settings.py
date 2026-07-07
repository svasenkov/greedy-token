"""User/workspace Ollama settings — single source of truth."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:7b-instruct-q4_K_M"


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


def _ollama_section(cfg: dict[str, Any]) -> dict[str, Any]:
    section = cfg.get("ollama")
    return section if isinstance(section, dict) else {}


def _resolve_ollama(
    *,
    user_cfg: dict[str, Any],
    workspace_cfg: dict[str, Any],
    root: Path | None = None,
) -> OllamaSettings:
    url = DEFAULT_OLLAMA_URL
    model = DEFAULT_OLLAMA_MODEL
    source = "default"

    user_ollama = _ollama_section(user_cfg)
    if user_ollama.get("url"):
        url = str(user_ollama["url"]).strip()
        source = "user"
    if user_ollama.get("model"):
        model = str(user_ollama["model"]).strip()
        source = "user"

    workspace_ollama = _ollama_section(workspace_cfg)
    if workspace_ollama.get("url"):
        url = str(workspace_ollama["url"]).strip()
        source = "workspace"
    if workspace_ollama.get("model"):
        model = str(workspace_ollama["model"]).strip()
        source = "workspace"

    if os.environ.get("OLLAMA_URL", "").strip():
        url = os.environ["OLLAMA_URL"].strip()
        source = "env"
    if os.environ.get("OLLAMA_MODEL", "").strip():
        model = os.environ["OLLAMA_MODEL"].strip()
        source = "env"

    return OllamaSettings(url=url.rstrip("/"), model=model, source=source)


def get_ollama_settings(root: Path | None = None) -> OllamaSettings:
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
    return _resolve_ollama(user_cfg=user_cfg, workspace_cfg=workspace_cfg, root=root)


def apply_ollama_env(root: Path | None = None) -> OllamaSettings:
    """Export resolved settings into os.environ for shell wrappers."""
    settings = get_ollama_settings(root)
    os.environ.setdefault("OLLAMA_URL", settings.url)
    os.environ.setdefault("OLLAMA_MODEL", settings.model)
    return settings


def format_config(settings: OllamaSettings | None = None, *, root: Path | None = None) -> str:
    settings = settings or get_ollama_settings(root)
    user_path = user_config_path()
    workspace_path = workspace_config_path(root) if root else None
    lines = [
        "greedy-token Ollama settings",
        "",
        f"  url:    {settings.url}",
        f"  model:  {settings.model}",
        f"  source: {settings.source}",
        "",
        "Config files (low → high priority):",
        f"  1. defaults",
        f"  2. {user_path}",
    ]
    if workspace_path is not None:
        lines.append(f"  3. {workspace_path}")
    lines.extend(
        [
            "  4. OLLAMA_URL / OLLAMA_MODEL env",
            "",
            "Create user config:",
            "  greedy-token config init",
        ]
    )
    return "\n".join(lines)


def format_shell_export(settings: OllamaSettings | None = None, *, root: Path | None = None) -> str:
    settings = settings or get_ollama_settings(root)
    return "\n".join(
        [
            f'export OLLAMA_URL="{settings.url}"',
            f'export OLLAMA_MODEL="{settings.model}"',
        ]
    )


def init_user_config(
    *,
    url: str | None = None,
    model: str | None = None,
    force: bool = False,
) -> Path:
    path = user_config_path()
    if path.is_file() and not force:
        raise FileExistsError(f"Config already exists: {path} (use --force to overwrite)")

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ollama": {
            "url": url or DEFAULT_OLLAMA_URL,
            "model": model or DEFAULT_OLLAMA_MODEL,
        }
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return path


def example_workspace_config() -> str:
    return (
        "# Project-local greedy-token settings (optional)\n"
        "ollama:\n"
        f"  url: {DEFAULT_OLLAMA_URL}\n"
        f"  model: {DEFAULT_OLLAMA_MODEL}\n"
    )
