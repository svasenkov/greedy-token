from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from greedy_token.settings import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    _resolve_ollama,
    format_shell_export,
    get_ollama_settings,
    init_user_config,
    user_config_path,
    workspace_config_path,
)


def test_defaults_without_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setattr(
        "greedy_token.settings.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    settings = get_ollama_settings(tmp_path)
    assert settings.url == DEFAULT_OLLAMA_URL
    assert settings.model == DEFAULT_OLLAMA_MODEL
    assert settings.source == "default"


def test_workspace_config_overrides_user(tmp_path: Path) -> None:
    user_cfg = {"ollama": {"url": "http://user:11434", "model": "user-model"}}
    workspace_cfg = {"ollama": {"url": "http://workspace:11434", "model": "workspace-model"}}
    settings = _resolve_ollama(user_cfg=user_cfg, workspace_cfg=workspace_cfg, root=tmp_path)
    assert settings.url == "http://workspace:11434"
    assert settings.model == "workspace-model"
    assert settings.source == "workspace"


def test_env_overrides_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_URL", "http://env:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "env-model")
    settings = _resolve_ollama(
        user_cfg={"ollama": {"url": "http://user:11434", "model": "user-model"}},
        workspace_cfg={"ollama": {"url": "http://workspace:11434", "model": "workspace-model"}},
        root=tmp_path,
    )
    assert settings.url == "http://env:11434"
    assert settings.model == "env-model"
    assert settings.source == "env"


def test_init_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    created = init_user_config(url="http://custom:11434", model="custom-model")
    assert created == cfg_path
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["ollama"]["url"] == "http://custom:11434"
    assert data["ollama"]["model"] == "custom-model"


def test_format_shell_export() -> None:
    from greedy_token.settings import OllamaSettings

    out = format_shell_export(
        OllamaSettings(url="http://localhost:11434", model="llama3", source="default")
    )
    assert 'export OLLAMA_URL="http://localhost:11434"' in out
    assert 'export OLLAMA_MODEL="llama3"' in out


def test_workspace_config_path(tmp_path: Path) -> None:
    assert workspace_config_path(tmp_path) == tmp_path / ".greedy-token.yaml"
