"""Paid LLM adapters — YandexGPT (native + openai_compat fallback)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from greedy_token.cheap_llm import _chat_openai_compat, openai_compat_base
from greedy_token.model_select import ModelSpec, ResolvedModel


def yandex_gpt_chat(
    resolved: ResolvedModel,
    *,
    system: str,
    user: str,
    timeout: float = 120.0,
) -> tuple[str, int | None]:
    spec = resolved.spec
    if spec.provider != "yandex_gpt":
        return _chat_openai_compat(resolved.settings, system=system, user=user, timeout=timeout)

    folder_id = _folder_from_env(spec)
    if folder_id and spec.api_key:
        try:
            return _chat_yandex_native(
                api_key=spec.api_key,
                folder_id=folder_id,
                model=spec.model,
                system=system,
                user=user,
                timeout=timeout,
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError, KeyError):
            pass

    if resolved.settings.url:
        return _chat_openai_compat(resolved.settings, system=system, user=user, timeout=timeout)

    raise RuntimeError(
        "YandexGPT: set api_key via api_key_env and YANDEX_FOLDER_ID, or configure openai_compat url"
    )


def _folder_from_env(spec: ModelSpec) -> str:
    import os

    return os.environ.get("YANDEX_FOLDER_ID", os.environ.get("YANDEX_GPT_FOLDER_ID", "")).strip()


def _chat_yandex_native(
    *,
    api_key: str,
    folder_id: str,
    model: str,
    system: str,
    user: str,
    timeout: float,
) -> tuple[str, int | None]:
    uri = f"gpt://{folder_id}/{model}/latest"
    body = json.dumps(
        {
            "modelUri": uri,
            "completionOptions": {"stream": False, "temperature": 0.2, "maxTokens": 4096},
            "messages": [
                {"role": "system", "text": system},
                {"role": "user", "text": user},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    result = data.get("result") or {}
    alts = result.get("alternatives") or []
    if not alts:
        raise ValueError("YandexGPT returned no alternatives")
    content = (alts[0].get("message") or {}).get("text", "").strip()
    usage = result.get("usage") or {}
    eval_tokens = usage.get("completionTokens") or usage.get("totalTokens")
    return content, eval_tokens


def llm_chat(
    resolved: ResolvedModel,
    *,
    system: str,
    user: str,
    timeout: float = 120.0,
) -> tuple[str, int | None]:
    """Unified chat entry — cheap providers or expensive YandexGPT."""
    if resolved.spec.provider == "yandex_gpt":
        return yandex_gpt_chat(resolved, system=system, user=user, timeout=timeout)
    from greedy_token.cheap_llm import cheap_llm_chat

    return cheap_llm_chat(resolved.settings, system=system, user=user, timeout=timeout)
