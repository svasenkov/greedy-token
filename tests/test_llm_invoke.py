"""Tests for llm invoke and spend guard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from greedy_token.llm_invoke import invoke_profile
from greedy_token.spend_guard import check_expensive_allowed, expensive_opt_in

pytestmark = pytest.mark.unit


def test_expensive_blocked_without_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: missing)
    monkeypatch.setattr("greedy_token.model_select.user_config_path", lambda: missing)
    monkeypatch.delenv("GREEDY_LLM_MODEL_ID", raising=False)
    monkeypatch.delenv("GREEDY_EXPENSIVE_LLM", raising=False)
    cfg = {
        "llm": {
            "expensive": {
                "opt_in": False,
                "models": [
                    {
                        "id": "yandex-lite",
                        "enabled": True,
                        "provider": "yandex_gpt",
                        "model": "yandexgpt-lite",
                        "profiles": ["*"],
                    }
                ],
            }
        }
    }
    (tmp_path / ".greedy-token.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    from greedy_token.model_select import resolve_model

    spec = resolve_model("", root=tmp_path, tier_hint="expensive").spec
    decision = check_expensive_allowed(spec, root=tmp_path)
    assert not decision.allowed
    assert "opt_in" in decision.reason


def test_expensive_allowed_with_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "greedy_token.model_select.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    cfg = {"llm": {"expensive": {"opt_in": True, "models": []}}}
    (tmp_path / ".greedy-token.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("GREEDY_EXPENSIVE_LLM", "1")
    assert expensive_opt_in(root=tmp_path)


@patch("greedy_token.llm_invoke.llm_chat", return_value=("ok response", 12))
def test_invoke_profile_cheap(
    mock_chat: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", "0")
    monkeypatch.setattr(
        "greedy_token.model_select.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    cfg = {
        "llm": {
            "cheap": {
                "models": [
                    {
                        "id": "fast",
                        "enabled": True,
                        "model": "m7",
                        "profiles": ["tms-classify"],
                    }
                ]
            },
            "escalation": {"enabled": False},
        }
    }
    (tmp_path / ".greedy-token.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (tmp_path / "docs" / "phase-manifest.json").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "phase-manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(tmp_path))

    result = invoke_profile(
        "tms-classify",
        system="sys",
        user="classify this",
        root=tmp_path,
        log=False,
        allow_escalate=False,
    )
    assert result.text == "ok response"
    assert result.model_id == "fast"
    assert result.tier_billing == "cheap"
    mock_chat.assert_called_once()


def _remote_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: dict) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", "0")
    monkeypatch.setattr(
        "greedy_token.model_select.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    cfg = {
        "llm": {
            "cheap": {"models": [model]},
            "escalation": {"enabled": False},
        }
    }
    (tmp_path / ".greedy-token.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(tmp_path))


@patch("greedy_token.llm_invoke.llm_chat", return_value=("ok response", 12))
def test_invoke_profile_remote_timeout_clamped(
    mock_chat: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _remote_workspace(
        tmp_path,
        monkeypatch,
        {
            "id": "remote-7b",
            "enabled": True,
            "model": "m7",
            "url": "https://ollama.example.com",
            "profiles": ["classify"],
        },
    )
    invoke_profile(
        "classify", system="s", user="u", root=tmp_path, log=False, allow_escalate=False
    )
    from greedy_token.llm_invoke import REMOTE_CALL_TIMEOUT_S

    assert mock_chat.call_args.kwargs["timeout"] == REMOTE_CALL_TIMEOUT_S


@patch("greedy_token.llm_invoke.llm_chat", return_value=("ok response", 12))
def test_invoke_profile_timeout_s_override(
    mock_chat: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _remote_workspace(
        tmp_path,
        monkeypatch,
        {
            "id": "remote-7b",
            "enabled": True,
            "model": "m7",
            "url": "https://ollama.example.com",
            "profiles": ["classify"],
            "timeout_s": 7,
        },
    )
    invoke_profile(
        "classify", system="s", user="u", root=tmp_path, log=False, allow_escalate=False
    )
    assert mock_chat.call_args.kwargs["timeout"] == 7.0


@patch("greedy_token.llm_invoke.llm_chat", side_effect=OSError("timed out"))
def test_invoke_profile_remote_error_hint(
    mock_chat: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _remote_workspace(
        tmp_path,
        monkeypatch,
        {
            "id": "remote-7b",
            "enabled": True,
            "model": "m7",
            "url": "https://ollama.example.com",
            "profiles": ["classify"],
        },
    )
    with pytest.raises(RuntimeError, match="unreachable or unauthorized"):
        invoke_profile(
            "classify", system="s", user="u", root=tmp_path, log=False, allow_escalate=False
        )
