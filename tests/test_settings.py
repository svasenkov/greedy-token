from __future__ import annotations

import os
from pathlib import Path

import allure
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
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Configuration"),
    allure.parent_suite("Configuration"),
    allure.feature("Ollama settings"),
    allure.suite("Ollama settings"),
]


@allure.story("Defaults")
@allure.title("Ollama settings return defaults when no config files")
def test_defaults_without_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setattr(
        "greedy_token.settings.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    with allure.step("Load Ollama settings without config files"):
        settings = get_ollama_settings(tmp_path)
        attach_json("settings", {"url": settings.url, "model": settings.model, "source": settings.source})
    with allure.step("Verify default Ollama settings"):
        assert settings.url == DEFAULT_OLLAMA_URL
        assert settings.model == DEFAULT_OLLAMA_MODEL
        assert settings.source == "default"


@allure.story("Precedence")
@allure.title("Workspace config overrides user config")
def test_workspace_config_overrides_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    user_cfg = {"ollama": {"url": "http://user:11434", "model": "user-model"}}
    workspace_cfg = {"ollama": {"url": "http://workspace:11434", "model": "workspace-model"}}
    with allure.step("Resolve Ollama settings with user and workspace configs"):
        settings = _resolve_ollama(user_cfg=user_cfg, workspace_cfg=workspace_cfg, root=tmp_path)
        attach_json("settings", {"url": settings.url, "model": settings.model, "source": settings.source})
    with allure.step("Verify workspace config takes precedence"):
        assert settings.url == "http://workspace:11434"
        assert settings.model == "workspace-model"
        assert settings.source == "workspace"


@allure.story("Precedence")
@allure.title("OLLAMA_* env vars override config files")
def test_env_overrides_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_URL", "http://env:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "env-model")
    with allure.step("Resolve Ollama settings with env overrides"):
        settings = _resolve_ollama(
            user_cfg={"ollama": {"url": "http://user:11434", "model": "user-model"}},
            workspace_cfg={"ollama": {"url": "http://workspace:11434", "model": "workspace-model"}},
            root=tmp_path,
        )
        attach_json("settings", {"url": settings.url, "model": settings.model, "source": settings.source})
    with allure.step("Verify env vars take precedence"):
        assert settings.url == "http://env:11434"
        assert settings.model == "env-model"
        assert settings.source == "env"


@allure.story("User config")
@allure.title("User config init writes ~/.greedy-token/config.yaml")
def test_init_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    with allure.step("Initialize user config file"):
        created = init_user_config(url="http://custom:11434", model="custom-model")
        attach_text("config path", str(created))
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        attach_json("config data", data)
    with allure.step("Verify config file contents"):
        assert created == cfg_path
        assert data["ollama"]["url"] == "http://custom:11434"
        assert data["ollama"]["model"] == "custom-model"


@allure.story("Shell export")
@allure.title("Shell export emits OLLAMA_URL and OLLAMA_MODEL")
def test_format_shell_export() -> None:
    from greedy_token.settings import OllamaSettings

    with allure.step("Format shell export for Ollama settings"):
        out = format_shell_export(
            OllamaSettings(url="http://localhost:11434", model="llama3", source="default")
        )
        attach_text("shell export", out)
    with allure.step("Verify OLLAMA env exports"):
        assert 'export OLLAMA_URL="http://localhost:11434"' in out
        assert 'export OLLAMA_MODEL="llama3"' in out


@allure.story("Paths")
@allure.title("Workspace config path points to .greedy-token.yaml in root")
def test_workspace_config_path(tmp_path: Path) -> None:
    with allure.step("Resolve workspace config path"):
        path = workspace_config_path(tmp_path)
        attach_text("workspace config path", str(path))
    with allure.step("Verify .greedy-token.yaml location"):
        assert path == tmp_path / ".greedy-token.yaml"


@allure.story("YAML")
@allure.title("_read_yaml returns empty dict for missing file")
def test_read_yaml_missing(tmp_path: Path) -> None:
    from greedy_token.settings import _read_yaml

    assert _read_yaml(tmp_path / "missing.yaml") == {}


@allure.story("YAML")
@allure.title("_read_yaml returns empty dict for non-dict YAML")
def test_read_yaml_non_dict(tmp_path: Path) -> None:
    from greedy_token.settings import _read_yaml

    path = tmp_path / "bad.yaml"
    path.write_text("- list\n", encoding="utf-8")
    assert _read_yaml(path) == {}


@allure.story("Config display")
@allure.title("format_config lists config file paths")
def test_format_config(minimal_workspace: Path) -> None:
    from greedy_token.settings import OllamaSettings, format_config

    settings = OllamaSettings(url="http://localhost:11434", model="m", source="default")
    out = format_config(settings, root=minimal_workspace)
    assert "greedy-token Ollama settings" in out
    assert ".greedy-token.yaml" in out


@allure.story("Env export")
@allure.title("apply_ollama_env sets OLLAMA_* when unset")
def test_apply_ollama_env(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.settings import apply_ollama_env

    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    settings = apply_ollama_env(minimal_workspace)
    assert os.environ.get("OLLAMA_URL") == settings.url
    assert os.environ.get("OLLAMA_MODEL") == settings.model


@allure.story("Discovery")
@allure.title("get_ollama_settings tolerates missing monorepo root")
def test_get_ollama_settings_no_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setattr(
        "greedy_token.settings.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    monkeypatch.setattr(
        "greedy_token.paths.find_monorepo_root",
        lambda: (_ for _ in ()).throw(SystemExit("no root")),
    )
    settings = get_ollama_settings(None)
    assert settings.source == "default"


@allure.story("Example")
@allure.title("example_workspace_config returns YAML snippet")
def test_example_workspace_config() -> None:
    from greedy_token.settings import example_workspace_config

    text = example_workspace_config()
    assert "ollama:" in text
    assert DEFAULT_OLLAMA_URL in text

