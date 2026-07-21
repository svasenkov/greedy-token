"""Tests for multi-model registry and profile resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from greedy_token.model_select import get_llm_registry, list_models, resolve_model

pytestmark = pytest.mark.unit


def test_legacy_cheap_llm_without_llm_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "greedy_token.model_select.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    ws = tmp_path / ".greedy-token.yaml"
    ws.write_text(
        yaml.safe_dump(
            {
                "cheap_llm": {
                    "provider": "ollama",
                    "url": "http://localhost:11434",
                    "model": "legacy-model",
                }
            }
        ),
        encoding="utf-8",
    )
    resolved = resolve_model("", root=tmp_path)
    assert resolved.model_id == "default"
    assert resolved.settings.model == "legacy-model"
    assert resolved.billing_tier == "cheap"


def test_profile_selects_fast_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_LLM_MODEL_ID", raising=False)
    monkeypatch.setattr(
        "greedy_token.model_select.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    cfg = {
        "llm": {
            "policy": "cheap_only",
            "cheap": {
                "default_id": "fast",
                "models": [
                    {
                        "id": "fast",
                        "enabled": True,
                        "provider": "ollama",
                        "url": "http://localhost:11434",
                        "model": "qwen:7b",
                        "profiles": ["tms-classify", "classify"],
                    },
                    {
                        "id": "smart",
                        "enabled": True,
                        "provider": "ollama",
                        "url": "http://localhost:11434",
                        "model": "qwen:14b",
                        "profiles": ["tms-generate", "generate"],
                    },
                ],
            },
        }
    }
    (tmp_path / ".greedy-token.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    fast = resolve_model("tms-classify", root=tmp_path)
    smart = resolve_model("tms-generate", root=tmp_path)
    assert fast.model_id == "fast"
    assert fast.settings.model == "qwen:7b"
    assert smart.model_id == "smart"
    assert smart.settings.model == "qwen:14b"


def test_greedy_llm_model_id_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "greedy_token.model_select.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    cfg = {
        "llm": {
            "cheap": {
                "models": [
                    {"id": "fast", "enabled": True, "model": "m7", "profiles": ["*"]},
                    {"id": "smart", "enabled": True, "model": "m14", "profiles": ["generate"]},
                ]
            }
        }
    }
    (tmp_path / ".greedy-token.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("GREEDY_LLM_MODEL_ID", "smart")
    resolved = resolve_model("", root=tmp_path)
    assert resolved.model_id == "smart"
    assert resolved.settings.model == "m14"

    # Explicit profile wins over GREEDY_LLM_MODEL_ID (profile routing is SSOT).
    profile_resolved = resolve_model("tms-classify", root=tmp_path)
    assert profile_resolved.model_id == "fast"


def test_expensive_only_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "greedy_token.model_select.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    cfg = {
        "llm": {
            "policy": "expensive_only",
            "expensive": {
                "opt_in": True,
                "models": [
                    {
                        "id": "yandex-lite",
                        "enabled": True,
                        "provider": "yandex_gpt",
                        "model": "yandexgpt-lite",
                        "profiles": ["*"],
                    }
                ],
            },
        }
    }
    (tmp_path / ".greedy-token.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    resolved = resolve_model("", root=tmp_path)
    assert resolved.model_id == "yandex-lite"
    assert resolved.billing_tier == "expensive"


def test_safe_policy_aliases_cheap_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "greedy_token.model_select.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    cfg = {
        "llm": {
            "policy": "safe",
            "expensive": {
                "opt_in": True,
                "models": [
                    {"id": "yandex-lite", "enabled": True, "model": "yandexgpt-lite", "profiles": ["*"]}
                ],
            },
            "cheap": {
                "models": [
                    {"id": "fast", "enabled": True, "model": "qwen:7b", "profiles": ["*"]}
                ]
            },
        }
    }
    (tmp_path / ".greedy-token.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    reg = get_llm_registry(tmp_path)
    assert reg.policy == "cheap_only"
    # safe mode keeps the cheap model even though an expensive one is opted in
    resolved = resolve_model("", root=tmp_path)
    assert resolved.billing_tier == "cheap"


def test_list_models_and_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "greedy_token.model_select.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    reg = get_llm_registry(tmp_path)
    models = list_models(tmp_path)
    assert reg.cheap_default_id == "default"
    assert len(models) >= 1
    assert models[0].id == "default"
