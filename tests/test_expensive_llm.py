"""Public-contract tests for expensive_llm YandexGPT adapters (fail_under=100)."""

from __future__ import annotations

import io
import json
import urllib.error
from contextlib import contextmanager

import allure
import pytest

from greedy_token import expensive_llm
from greedy_token.model_select import ModelSpec, ResolvedModel
from greedy_token.settings import CheapLlmSettings

pytestmark = [
    allure.epic("Expensive LLM"),
    allure.parent_suite("Expensive LLM"),
    allure.feature("YandexGPT adapter"),
    allure.suite("Expensive LLM"),
]


def _resolved(*, provider: str, url: str = "", api_key: str = "", model: str = "yandexgpt-lite") -> ResolvedModel:
    spec = ModelSpec(
        id="yandex-lite",
        enabled=True,
        provider=provider,  # type: ignore[arg-type]
        url=url,
        model=model,
        profiles=("*",),
        locality="remote",
        billing="metered",
        cost_per_1m_usd=1.0,
        api_key=api_key,
    )
    settings = CheapLlmSettings(provider="openai_compat", url=url, model=model, source="test", api_key=api_key)
    return ResolvedModel(spec=spec, settings=settings, profile="p", billing_tier="expensive")


@contextmanager
def _fake_urlopen(payload: dict):
    yield io.BytesIO(json.dumps(payload).encode("utf-8"))


@allure.title("non-yandex provider delegates to openai_compat")
def test_non_yandex_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(expensive_llm, "_chat_openai_compat", lambda *a, **k: ("compat", 7))
    text, tokens = expensive_llm.yandex_gpt_chat(_resolved(provider="openai_compat", url="http://x"), system="s", user="u")
    assert text == "compat"
    assert tokens == 7


@allure.title("_folder_from_env reads either env var")
def test_folder_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _resolved(provider="yandex_gpt").spec
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)
    monkeypatch.delenv("YANDEX_GPT_FOLDER_ID", raising=False)
    assert expensive_llm._folder_from_env(spec) == ""
    monkeypatch.setenv("YANDEX_GPT_FOLDER_ID", "fold-2")
    assert expensive_llm._folder_from_env(spec) == "fold-2"
    monkeypatch.setenv("YANDEX_FOLDER_ID", "fold-1")
    assert expensive_llm._folder_from_env(spec) == "fold-1"


@allure.title("yandex native success path returns content + tokens")
def test_yandex_native_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YANDEX_FOLDER_ID", "fold")
    payload = {
        "result": {
            "alternatives": [{"message": {"text": "  native answer  "}}],
            "usage": {"completionTokens": 42},
        }
    }
    monkeypatch.setattr(
        expensive_llm.urllib.request, "urlopen", lambda *a, **k: _fake_urlopen(payload)
    )
    text, tokens = expensive_llm.yandex_gpt_chat(
        _resolved(provider="yandex_gpt", api_key="key"), system="s", user="u"
    )
    assert text == "native answer"
    assert tokens == 42


@allure.title("_chat_yandex_native raises when no alternatives")
def test_yandex_native_no_alternatives(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        expensive_llm.urllib.request, "urlopen", lambda *a, **k: _fake_urlopen({"result": {}})
    )
    with pytest.raises(ValueError, match="no alternatives"):
        expensive_llm._chat_yandex_native(
            api_key="k", folder_id="f", model="m", system="s", user="u", timeout=1.0
        )


@allure.title("native failure falls back to openai_compat when url set")
def test_yandex_native_fallback_to_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YANDEX_FOLDER_ID", "fold")

    def boom(*a, **k):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(expensive_llm.urllib.request, "urlopen", boom)
    monkeypatch.setattr(expensive_llm, "_chat_openai_compat", lambda *a, **k: ("fallback", None))
    text, tokens = expensive_llm.yandex_gpt_chat(
        _resolved(provider="yandex_gpt", url="http://compat", api_key="key"), system="s", user="u"
    )
    assert text == "fallback"
    assert tokens is None


@allure.title("no folder/key and no url raises RuntimeError")
def test_yandex_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)
    monkeypatch.delenv("YANDEX_GPT_FOLDER_ID", raising=False)
    with pytest.raises(RuntimeError, match="YandexGPT"):
        expensive_llm.yandex_gpt_chat(_resolved(provider="yandex_gpt"), system="s", user="u")


@allure.title("llm_chat routes expensive vs cheap")
def test_llm_chat_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(expensive_llm, "yandex_gpt_chat", lambda *a, **k: ("exp", 1))
    assert expensive_llm.llm_chat(_resolved(provider="yandex_gpt"), system="s", user="u") == ("exp", 1)

    import greedy_token.cheap_llm as cheap
    monkeypatch.setattr(cheap, "cheap_llm_chat", lambda *a, **k: ("cheap", 2))
    resolved = _resolved(provider="openai_compat", url="http://x")
    assert expensive_llm.llm_chat(resolved, system="s", user="u") == ("cheap", 2)
