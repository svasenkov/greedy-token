"""Unit tests for settings resolution edge branches (fail_under=100)."""

from __future__ import annotations

from pathlib import Path

import allure
import pytest

import greedy_token.settings as st

pytestmark = [
    allure.epic("Settings"),
    allure.parent_suite("Settings"),
    allure.feature("Config resolution"),
    allure.suite("Settings gaps"),
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "GREEDY_TOKEN_FOOTER_STYLE",
        "GREEDY_TOKEN_SEARCH_CONTEXT",
        "GREEDY_TOKEN_MAX_CONTEXT_TOKENS",
    ):
        monkeypatch.delenv(var, raising=False)


@allure.title("normalizers reject unknown values")
def test_normalizers() -> None:
    assert st._normalize_footer_style("wibble") is None
    assert st._normalize_footer_style("") is None
    assert st._normalize_search_context("wibble") is None
    assert st._normalize_search_context(None) is None


@allure.title("get_footer_settings tolerates missing workspace root")
def test_get_footer_settings_no_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(st, "user_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(
        "greedy_token.paths.find_workspace_root",
        lambda: (_ for _ in ()).throw(SystemExit(1)),
    )
    assert st.get_footer_settings(root=None).source == "default"


@allure.title("_resolve_search_settings ignores malformed ints and honours env")
def test_resolve_search_settings_bad_values(monkeypatch: pytest.MonkeyPatch) -> None:
    user_cfg = {
        "search": {
            "context": "snippet",
            "max_context_tokens": "abc",
            "max_snippet_files": "xyz",
            "context_lines": "nan",
        }
    }
    resolved = st._resolve_search_settings(user_cfg=user_cfg, workspace_cfg={})
    assert resolved.context == "snippet"
    assert resolved.max_context_tokens == st.DEFAULT_MAX_CONTEXT_TOKENS

    monkeypatch.setenv("GREEDY_TOKEN_SEARCH_CONTEXT", "file")
    monkeypatch.setenv("GREEDY_TOKEN_MAX_CONTEXT_TOKENS", "4321")
    env_resolved = st._resolve_search_settings(user_cfg={}, workspace_cfg={})
    assert env_resolved.context == "file"
    assert env_resolved.max_context_tokens == 4321
    assert env_resolved.source == "env"

    monkeypatch.setenv("GREEDY_TOKEN_MAX_CONTEXT_TOKENS", "not-a-number")
    bad_env = st._resolve_search_settings(user_cfg={}, workspace_cfg={})
    assert bad_env.max_context_tokens == st.DEFAULT_MAX_CONTEXT_TOKENS


@allure.title("get_search_settings tolerates missing workspace root")
def test_get_search_settings_no_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(st, "user_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(
        "greedy_token.paths.find_workspace_root",
        lambda: (_ for _ in ()).throw(SystemExit(1)),
    )
    assert st.get_search_settings(root=None).source == "default"


@allure.title("get_cheap_llm_settings falls back when registry resolve fails")
def test_get_cheap_llm_registry_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("llm:\n  policy: auto\n  models: []\n", encoding="utf-8")
    monkeypatch.setattr(st, "user_config_path", lambda: cfg)
    monkeypatch.setattr(
        "greedy_token.model_select.resolve_model",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("no model")),
    )
    settings = st.get_cheap_llm_settings(root=None)
    assert settings.provider


@allure.title("apply_cheap_llm_env: no-root path and registry resolve failure fall back")
def test_apply_cheap_llm_env_fallbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(st, "user_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(
        "greedy_token.paths.find_workspace_root",
        lambda: (_ for _ in ()).throw(SystemExit(1)),
    )
    for var in ("CHEAP_LLM_PROVIDER", "CHEAP_LLM_URL", "CHEAP_LLM_MODEL", "OLLAMA_URL", "OLLAMA_MODEL"):
        monkeypatch.delenv(var, raising=False)
    out = st.apply_cheap_llm_env(root=None)
    assert out.provider

    # profile set → registry path → resolve_model raises → falls through to defaults
    monkeypatch.setattr(
        "greedy_token.model_select.resolve_model",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("no model")),
    )
    out2 = st.apply_cheap_llm_env(root=None, profile="fast")
    assert out2.provider


@allure.title("presets_dir prefers repo examples, list/preset lookups validate names")
def test_presets_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # repo_root without examples/presets → first candidate skipped, packaged dir wins
    monkeypatch.setattr("greedy_token.version.repo_root", lambda: tmp_path)
    assert st.presets_dir().is_dir()

    monkeypatch.setattr(st, "presets_dir", lambda: tmp_path / "nope")
    assert st.list_preset_names() == []

    with pytest.raises(FileNotFoundError):
        st.preset_path("   ")


@allure.title("load_preset_yaml rejects non-mapping preset files")
def test_load_preset_yaml_non_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    monkeypatch.setattr(st, "preset_path", lambda name: bad)
    with pytest.raises(ValueError, match="not a YAML mapping"):
        st.load_preset_yaml("bad")
