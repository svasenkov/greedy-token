"""Health and chat adapters for cheap LLM providers (Ollama native, OpenAI-compatible)."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from greedy_token.settings import CheapLlmSettings

CHEAP_LLM_PROBE_TTL = 3.0
_LOOPBACK_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})
_cheap_llm_probe_cache: dict[str, tuple[float, CheapLlmProbe]] = {}


@dataclass(frozen=True)
class CheapLlmProbe:
    """Outcome of a cheap-LLM health probe.

    `reachable` answers "did the runtime respond"; `model_present` answers "is
    the configured model actually served". A runtime that is up with the wrong
    model configured used to look healthy here and fail later at call time.
    `model_present` is None when the answer could not be determined.
    """

    reachable: bool
    model_present: bool | None
    models: tuple[str, ...] = ()
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.reachable and self.model_present is not False


def _cache_key(settings: CheapLlmSettings) -> str:
    return f"{settings.provider}:{settings.url.rstrip('/')}:{settings.model}"


def openai_compat_base(url: str) -> str:
    base = url.rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def clear_cheap_llm_probe_cache() -> None:
    _cheap_llm_probe_cache.clear()


def split_userinfo(url: str) -> tuple[str, tuple[str, str] | None]:
    """Split `scheme://user:pass@host/path` into a clean URL and credentials.

    urllib will not send Basic credentials embedded in a URL on its own, and
    leaving the userinfo in place leaks it into the request line.
    """
    parts = urllib.parse.urlsplit(url)
    if not parts.username:
        return url, None
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    clean = urllib.parse.urlunsplit(
        (parts.scheme, host, parts.path, parts.query, parts.fragment)
    )
    return clean, (parts.username, parts.password or "")


def _is_loopback(url: str) -> bool:
    return (urllib.parse.urlsplit(url).hostname or "").lower() in _LOOPBACK_HOSTS


def _env_credentials() -> tuple[str, str] | None:
    for user_var, password_var in (
        ("CHEAP_LLM_USER", "CHEAP_LLM_PASSWORD"),
        ("OLLAMA_USER", "OLLAMA_PASSWORD"),
    ):
        user = os.environ.get(user_var, "").strip()
        if user:
            return user, os.environ.get(password_var, "")
    return None


def request_target(settings: CheapLlmSettings) -> tuple[str, dict[str, str]]:
    """Base URL with userinfo stripped, plus the auth headers it needs."""
    url, creds = split_userinfo(settings.url)
    return url.rstrip("/"), _auth_headers(settings, url=url, creds=creds)


def _auth_headers(
    settings: CheapLlmSettings,
    *,
    url: str | None = None,
    creds: tuple[str, str] | None = None,
) -> dict[str, str]:
    if settings.provider == "openai_compat" and settings.api_key:
        return {"Authorization": f"Bearer {settings.api_key}"}
    target = url if url is not None else split_userinfo(settings.url)[0]
    if _is_loopback(target):
        return {}
    if creds is None:
        creds = split_userinfo(settings.url)[1] or _env_credentials()
    if creds is None:
        return {}
    token = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def cheap_llm_available(settings: CheapLlmSettings, timeout: float = 2.0) -> bool:
    return probe_cheap_llm(settings, timeout=timeout).ok


def probe_cheap_llm(settings: CheapLlmSettings, timeout: float = 2.0) -> CheapLlmProbe:
    key = _cache_key(settings)
    now = time.monotonic()
    cached = _cheap_llm_probe_cache.get(key)
    if cached is not None and now - cached[0] < CHEAP_LLM_PROBE_TTL:
        return cached[1]

    probe = _probe_health(settings, timeout=timeout)
    _cheap_llm_probe_cache[key] = (now, probe)
    return probe


def _probe_health(settings: CheapLlmSettings, *, timeout: float) -> CheapLlmProbe:
    base, headers = request_target(settings)
    if settings.provider == "openai_compat":
        url = f"{openai_compat_base(base)}/models"
    else:
        url = f"{base}/api/tags"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        reason = f"HTTP {exc.code}"
        if exc.code in (401, 403):
            reason += (
                " — set CHEAP_LLM_USER / CHEAP_LLM_PASSWORD "
                "(or OLLAMA_USER / OLLAMA_PASSWORD)"
            )
        return CheapLlmProbe(reachable=False, model_present=None, reason=reason)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        return CheapLlmProbe(
            reachable=False, model_present=None, reason=str(exc) or "unreachable"
        )

    served = served_models(data)
    if not served:
        return CheapLlmProbe(reachable=True, model_present=False, reason="no models served")
    present = model_is_served(settings.model, served)
    reason = "" if present else f"model {settings.model!r} not served"
    return CheapLlmProbe(
        reachable=True, model_present=present, models=served, reason=reason
    )


def served_models(payload: object) -> tuple[str, ...]:
    """Model names from Ollama `/api/tags` or an OpenAI-compatible `/v1/models`."""
    if not isinstance(payload, dict):
        return ()
    entries = payload.get("models")
    if entries is None:
        entries = payload.get("data")
    names = [
        str(entry.get("name") or entry.get("id") or "").strip()
        for entry in (entries or [])
        if isinstance(entry, dict)
    ]
    return tuple(name for name in names if name)


def model_is_served(model: str, served: tuple[str, ...]) -> bool:
    """Ollama reports `name:tag`; an untagged config means the `latest` tag."""
    wanted = model.strip()
    if not wanted:
        return False
    candidates = {wanted, f"{wanted}:latest"}
    return any(
        name in candidates or name.removesuffix(":latest") == wanted for name in served
    )


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
    url, auth = request_target(settings)
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
        headers={"Content-Type": "application/json", **auth},
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
    target, auth = request_target(settings)
    base = openai_compat_base(target)
    body = json.dumps(
        {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode()
    headers = {"Content-Type": "application/json", **auth}
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers=headers,
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
    probe = probe_cheap_llm(settings)
    if probe.ok:
        return f"Cheap LLM: available ({provider}, {url}, model={model})"
    if probe.reachable:
        served = ", ".join(probe.models) or "none"
        return (
            f"Cheap LLM: model unavailable ({provider}, {url}, model={model}) — "
            f"{probe.reason}; served: {served}"
        )
    return (
        f"Cheap LLM: unavailable ({provider}, {url}) — "
        f"{probe.reason or 'start runtime'} — or use expensive LLM (Cursor)"
    )
