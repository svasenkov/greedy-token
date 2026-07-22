from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from greedy_token.model_select import get_llm_registry, list_models
from greedy_token.settings import (
    init_user_config_from_preset,
    list_preset_names,
    load_preset_yaml,
    preset_path,
)


@pytest.mark.unit
def test_list_preset_names_includes_catalog() -> None:
    names = list_preset_names()
    assert "local-ollama" in names
    assert "cursor-like-catalog" in names
    assert "selectel-cl21r" in names
    assert "tms-automator" in names
    assert "local-ollama-3" in names
    assert "prod-ollama-2" in names
    assert "prod-ollama-3" in names


@pytest.mark.unit
def test_preset_path_resolves_yaml() -> None:
    path = preset_path("local-ollama")
    assert path.name == "local-ollama.yaml"
    assert path.is_file()


@pytest.mark.unit
def test_unknown_preset_raises() -> None:
    with pytest.raises(FileNotFoundError, match="Unknown preset"):
        preset_path("does-not-exist")


@pytest.mark.unit
def test_load_preset_yaml_has_llm_section() -> None:
    payload = load_preset_yaml("cursor-like-catalog")
    assert "llm" in payload
    cheap = payload["llm"]["cheap"]
    assert cheap["default_id"] == "ollama-fast"


@pytest.mark.unit
def test_local_ollama_preset_models_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    monkeypatch.setattr("greedy_token.model_select.user_config_path", lambda: cfg_path)

    init_user_config_from_preset(preset="local-ollama", force=True)
    models = list_models(tmp_path)
    by_id = {m.id: m for m in models}
    assert by_id["ollama-fast"].enabled is True
    assert by_id["ollama-smart"].enabled is True


@pytest.mark.unit
def test_cursor_catalog_paid_models_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    monkeypatch.setattr("greedy_token.model_select.user_config_path", lambda: cfg_path)

    init_user_config_from_preset(preset="cursor-like-catalog", force=True)
    models = list_models(tmp_path)
    by_id = {m.id: m for m in models}

    assert by_id["ollama-fast"].enabled is True
    assert by_id["ollama-smart"].enabled is True
    assert by_id["openai-mini"].enabled is False
    assert by_id["openai-gpt4o"].enabled is False
    assert by_id["yandex-lite"].enabled is False

    reg = get_llm_registry(tmp_path)
    assert reg.expensive_opt_in is False


@pytest.mark.unit
def test_cursor_catalog_has_no_anthropic_stub_in_yaml() -> None:
    payload = load_preset_yaml("cursor-like-catalog")
    ids = {m["id"] for m in payload["llm"]["models"]}
    assert "anthropic-sonnet" not in ids
    assert "gemini-pro" not in ids


@pytest.mark.unit
def test_cursor_catalog_unified_models_derived_tiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0001 phase 3: single llm.models[] list; tier derives from billing/cost.
    The old groq-llama/groq-70b duplicate is merged into one sub-threshold entry."""
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    monkeypatch.setattr("greedy_token.model_select.user_config_path", lambda: cfg_path)

    init_user_config_from_preset(preset="cursor-like-catalog", force=True)
    reg = get_llm_registry(tmp_path)

    ids = [m.id for m in reg.models]
    assert "groq-70b" not in ids
    assert ids.count("groq-llama") == 1

    tiers = {m.id: reg.tier_of(m) for m in reg.models}
    assert tiers["ollama-fast"] == "cheap"        # free
    assert tiers["groq-llama"] == "cheap"         # metered 0.05 <= 0.2
    assert tiers["openai-mini"] == "cheap"        # metered 0.15 <= 0.2
    assert tiers["deepseek-chat"] == "expensive"  # metered 0.27 > 0.2
    assert tiers["openai-gpt4o"] == "expensive"
    assert tiers["yandex-pro"] == "expensive"


@pytest.mark.unit
def test_init_user_config_from_preset_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "greedy-token" / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)

    out = init_user_config_from_preset(preset="tms-automator", force=True)
    assert out == cfg_path
    assert cfg_path.is_file()
    written = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert written["llm"]["models"][0]["id"] == "fast"


@pytest.mark.unit
def test_init_user_config_from_preset_refuses_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("cheap_llm: {}\n", encoding="utf-8")
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)

    with pytest.raises(FileExistsError):
        init_user_config_from_preset(preset="local-ollama")


@pytest.mark.unit
def test_local_ollama_3_preset_models_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    monkeypatch.setattr("greedy_token.model_select.user_config_path", lambda: cfg_path)

    init_user_config_from_preset(preset="local-ollama-3", force=True)
    models = list_models(tmp_path)
    by_id = {m.id: m for m in models}
    assert by_id["ollama-fast"].enabled is True
    assert by_id["ollama-smart"].enabled is True
    assert by_id["ollama-heavy"].enabled is True


@pytest.mark.unit
def test_prod_ollama_3_heavy_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    monkeypatch.setattr("greedy_token.model_select.user_config_path", lambda: cfg_path)

    init_user_config_from_preset(preset="prod-ollama-3", force=True)
    models = list_models(tmp_path)
    by_id = {m.id: m for m in models}
    assert by_id["fast"].enabled is True
    assert by_id["smart"].enabled is True
    assert by_id["ollama-heavy"].enabled is False
