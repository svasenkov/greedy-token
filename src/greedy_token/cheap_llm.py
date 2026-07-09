"""Health and chat adapters for cheap LLM providers (Ollama native, OpenAI-compatible)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from greedy_token.settings import CheapLlmSettings

CHEAP_LLM_PROBE_TTL = 3.0
_cheap_llm_probe_cache: dict[str, tuple[float, bool]] = {}


def _cache_key(settings: CheapLlmSettings) -> str:
    return f"{settings.provider}:{settings.url.rstrip('/')}"


def openai_compat_base(url: str) -> str:
    base = url.rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def clear_cheap_llm_probe_cache() -> None:
    _cheap_llm_probe_cache.clear()


def cheap_llm_available(settings: CheapLlmSettings, timeout: float = 2.0) -> bool:
    key = _cache_key(settings)
    now = time.monotonic()
    cached = _cheap_llm_probe_cache.get(key)
    if cached is not None and now - cached[0] < CHEAP_LLM_PROBE_TTL:
        return cached[1]

    ok = _probe_health(settings, timeout=timeout)
    _cheap_llm_probe_cache[key] = (now, ok)
    return ok


def _probe_health(settings: CheapLlmSettings, *, timeout: float) -> bool:
    if settings.provider == "openai_compat":
        url = f"{openai_compat_base(settings.url)}/models"
    else:
        url = f"{settings.url.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            json.load(resp)
        return True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return False


def cheap_llm_chat(
    settings: CheapLlmSettings,
    *,
    system: str,
    user: str,
    timeout: float = 120.0,
) -> tuple[str, int | None]:
    if settings.provider == "openai_compat":
        return _chat_openai_compat(settings, system=system, user=user, timeout=timeout)
    return _chat_ollama(settings, system=system, user=user, timeout=timeout)


def _chat_ollama(
    settings: CheapLlmSettings,
    *,
    system: str,
    user: str,
    timeout: float,
) -> tuple[str, int | None]:
    url = settings.url.rstrip("/")
    body = json.dumps(
        {
            "model": settings.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        f"{url}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    content = data["message"]["content"].strip()
    eval_tokens = data.get("eval_count")
    return content, eval_tokens


def _chat_openai_compat(
    settings: CheapLlmSettings,
    *,
    system: str,
    user: str,
    timeout: float,
) -> tuple[str, int | None]:
    base = openai_compat_base(settings.url)
    body = json.dumps(
        {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    content = data["choices"][0]["message"]["content"].strip()
    usage = data.get("usage") or {}
    eval_tokens = usage.get("completion_tokens")
    return content, eval_tokens


def cheap_llm_status_line(settings: CheapLlmSettings) -> str:
    provider = settings.provider
    url = settings.url
    model = settings.model
    if cheap_llm_available(settings):
        return f"Cheap LLM: available ({provider}, {url}, model={model})"
    return (
        f"Cheap LLM: unavailable ({provider}, {url}) — "
        "start runtime or use expensive LLM (Cursor)"
    )
