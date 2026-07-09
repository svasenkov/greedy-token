from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import allure
import pytest

from greedy_token.cheap_llm import (
    clear_cheap_llm_probe_cache,
    cheap_llm_available,
    cheap_llm_chat,
    cheap_llm_status_line,
    openai_compat_base,
)
from greedy_token.settings import CheapLlmSettings, get_cheap_llm_settings
from tests.allure_reporting import attach_text
from tests.ollama_stub import clear_ollama_probe_cache, ollama_stub_server

pytestmark = [
    allure.epic("Configuration"),
    allure.parent_suite("Configuration"),
    allure.feature("Cheap LLM adapters"),
    allure.suite("Cheap LLM adapters"),
]


@allure.story("URL normalization")
@allure.title("openai_compat_base appends /v1 when missing")
def test_openai_compat_base() -> None:
    assert openai_compat_base("http://localhost:1234") == "http://localhost:1234/v1"
    assert openai_compat_base("http://localhost:1234/v1/") == "http://localhost:1234/v1"


@allure.story("Health")
@allure.title("cheap_llm_available probes Ollama /api/tags")
@patch("greedy_token.cheap_llm.json.load", return_value={"models": []})
@patch("urllib.request.urlopen")
def test_cheap_llm_available_ollama(mock_urlopen, mock_json_load) -> None:
    clear_cheap_llm_probe_cache()
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp
    settings = CheapLlmSettings(
        provider="ollama",
        url="http://localhost:11434",
        model="m",
        source="default",
    )
    assert cheap_llm_available(settings) is True


@allure.story("Health")
@allure.title("cheap_llm_available probes OpenAI-compatible /v1/models")
def test_cheap_llm_available_openai_compat(ollama_stub: str) -> None:
    clear_ollama_probe_cache()
    settings = CheapLlmSettings(
        provider="openai_compat",
        url=ollama_stub,
        model="stub-model",
        source="env",
    )
    assert cheap_llm_available(settings) is True


@allure.story("Chat")
@allure.title("cheap_llm_chat uses OpenAI-compatible /v1/chat/completions")
def test_cheap_llm_chat_openai_compat() -> None:
    with ollama_stub_server() as url:
        settings = CheapLlmSettings(
            provider="openai_compat",
            url=url,
            model="stub-model",
            source="env",
        )
        content, eval_tokens = cheap_llm_chat(settings, system="sys", user="hello")
    assert '"ok":true' in content
    assert eval_tokens == 12


@allure.story("Status")
@allure.title("cheap_llm_status_line includes provider and model")
def test_cheap_llm_status_line(ollama_stub: str) -> None:
    settings = CheapLlmSettings(
        provider="openai_compat",
        url=ollama_stub,
        model="stub-model",
        source="env",
    )
    line = cheap_llm_status_line(settings)
    attach_text("status line", line)
    assert "openai_compat" in line
    assert "stub-model" in line
    assert "Cheap LLM" in line


@allure.story("Settings")
@allure.title("cheap_llm config section sets provider")
def test_cheap_llm_config_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("CHEAP_LLM_PROVIDER", raising=False)
    monkeypatch.setattr(
        "greedy_token.settings.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    workspace_cfg = tmp_path / ".greedy-token.yaml"
    workspace_cfg.write_text(
        "cheap_llm:\n  provider: openai_compat\n  url: http://lm:1234\n  model: lm-model\n",
        encoding="utf-8",
    )
    settings = get_cheap_llm_settings(tmp_path)
    assert settings.provider == "openai_compat"
    assert settings.url == "http://lm:1234"
    assert settings.model == "lm-model"
