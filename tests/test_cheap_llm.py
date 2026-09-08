from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import allure
import pytest

from greedy_token.cheap_llm import (
    cheap_llm_available,
    cheap_llm_chat,
    cheap_llm_status_line,
    clear_cheap_llm_probe_cache,
    model_is_served,
    openai_compat_base,
    probe_cheap_llm,
    request_target,
    served_models,
    split_userinfo,
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
@allure.title("cheap_llm_available probes Ollama /api/tags and requires the configured model")
@patch("greedy_token.cheap_llm.json.load", return_value={"models": [{"name": "m"}]})
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


@allure.story("Auth")
@allure.title("CHEAP_LLM_API_KEY is sent as Bearer for openai_compat")
def test_openai_compat_sends_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_cheap_llm_probe_cache()
    captured: dict[str, str] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"data":[]}'

    def fake_urlopen(req, timeout=2.0):
        auth = req.get_header("Authorization") or ""
        captured["Authorization"] = auth
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(
        "greedy_token.cheap_llm.json.load", lambda _fh: {"data": [{"id": "m"}]}
    )
    settings = CheapLlmSettings(
        provider="openai_compat",
        url="http://localhost:1234",
        model="m",
        source="env",
        api_key="sk-test",
    )
    assert cheap_llm_available(settings) is True
    assert captured["Authorization"] == "Bearer sk-test"

    # Chat path also attaches Bearer for openai_compat.
    clear_cheap_llm_probe_cache()
    chat_auth: dict[str, str] = {}

    def fake_chat_urlopen(req, timeout=120.0):
        chat_auth["Authorization"] = req.get_header("Authorization") or ""

        class _ChatResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return _ChatResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_chat_urlopen)
    monkeypatch.setattr(
        "greedy_token.cheap_llm.json.load",
        lambda _fh: {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"completion_tokens": 3},
        },
    )
    content, eval_tokens = cheap_llm_chat(settings, system="s", user="u")
    assert content == "hi"
    assert eval_tokens == 3
    assert chat_auth["Authorization"] == "Bearer sk-test"


@allure.story("Settings")
@allure.title("cheap_llm config section sets provider")
def test_cheap_llm_config_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("CHEAP_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CHEAP_LLM_API_KEY", raising=False)
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


@allure.story("Settings")
@allure.title("CHEAP_LLM_API_KEY env overrides config")
def test_api_key_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("CHEAP_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("CHEAP_LLM_API_KEY", "sk-from-env")
    monkeypatch.setattr(
        "greedy_token.settings.user_config_path",
        lambda: tmp_path / "missing.yaml",
    )
    workspace_cfg = tmp_path / ".greedy-token.yaml"
    workspace_cfg.write_text(
        "cheap_llm:\n  provider: openai_compat\n  url: http://lm:1234\n  model: lm-model\n  api_key: sk-file\n",
        encoding="utf-8",
    )
    settings = get_cheap_llm_settings(tmp_path)
    assert settings.api_key == "sk-from-env"
    assert settings.provider == "openai_compat"


def _ollama(url: str, model: str = "qwen2.5-coder:7b") -> CheapLlmSettings:
    return CheapLlmSettings(provider="ollama", url=url, model=model, source="test")


@allure.story("Auth")
@allure.title("split_userinfo strips credentials from the request URL")
def test_split_userinfo_strips_userinfo() -> None:
    clean, creds = split_userinfo("https://alice:s3cret@ollama.qa.guru")
    assert clean == "https://ollama.qa.guru"
    assert creds == ("alice", "s3cret")
    with_port, creds_port = split_userinfo("https://alice:s3cret@ollama.qa.guru:11434")
    assert with_port == "https://ollama.qa.guru:11434"
    assert creds_port == ("alice", "s3cret")
    untouched, none = split_userinfo("http://localhost:11434")
    assert untouched == "http://localhost:11434"
    assert none is None


@allure.story("Auth")
@allure.title("Loopback Ollama sends no Authorization even when env credentials are set")
def test_loopback_skips_basic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_USER", "alice")
    monkeypatch.setenv("OLLAMA_PASSWORD", "s3cret")
    _, headers = request_target(_ollama("http://127.0.0.1:11434"))
    assert headers == {}
    _, headers_local = request_target(_ollama("http://localhost:11434"))
    assert headers_local == {}


@allure.story("Auth")
@allure.title("Remote Ollama sends Basic from OLLAMA_USER / OLLAMA_PASSWORD")
def test_remote_ollama_basic_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_USER", "alice")
    monkeypatch.setenv("OLLAMA_PASSWORD", "s3cret")
    url, headers = request_target(_ollama("https://ollama.qa.guru"))
    assert url == "https://ollama.qa.guru"
    assert headers["Authorization"].startswith("Basic ")


@allure.story("Auth")
@allure.title("Remote Ollama prefers URL userinfo over env credentials")
def test_remote_ollama_basic_from_userinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_USER", "env-user")
    monkeypatch.setenv("OLLAMA_PASSWORD", "env-pass")
    url, headers = request_target(_ollama("https://alice:from-url@ollama.qa.guru"))
    assert url == "https://ollama.qa.guru"
    import base64

    expected = "Basic " + base64.b64encode(b"alice:from-url").decode()
    assert headers["Authorization"] == expected


@allure.story("Auth")
@allure.title("CHEAP_LLM_USER wins over OLLAMA_USER")
def test_cheap_llm_user_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEAP_LLM_USER", "cheap")
    monkeypatch.setenv("CHEAP_LLM_PASSWORD", "llm")
    monkeypatch.setenv("OLLAMA_USER", "ollama")
    monkeypatch.setenv("OLLAMA_PASSWORD", "ollama")
    _, headers = request_target(_ollama("https://ollama.qa.guru"))
    import base64

    expected = "Basic " + base64.b64encode(b"cheap:llm").decode()
    assert headers["Authorization"] == expected


@allure.story("Auth")
@allure.title("Remote Ollama probe and chat attach Basic auth")
def test_remote_ollama_probe_and_chat_use_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    import base64
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    expected = "Basic " + base64.b64encode(b"alice:s3cret").decode()
    seen: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def do_GET(self) -> None:
            seen.append(self.headers.get("Authorization") or "")
            if self.headers.get("Authorization") != expected:
                self.send_error(401)
                return
            body = json.dumps({"models": [{"name": "qwen2.5-coder:7b"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            seen.append(self.headers.get("Authorization") or "")
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            body = json.dumps({"message": {"content": "ok"}, "eval_count": 1}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _host, port = server.server_address
    try:
        from greedy_token import cheap_llm as mod

        # Bind is loopback; patch so this test exercises the remote Basic path.
        monkeypatch.setattr(mod, "_is_loopback", lambda _url: False)
        monkeypatch.setenv("OLLAMA_USER", "alice")
        monkeypatch.setenv("OLLAMA_PASSWORD", "s3cret")
        settings = _ollama(f"http://127.0.0.1:{port}")
        clear_cheap_llm_probe_cache()
        probe = probe_cheap_llm(settings, timeout=2.0)
        assert probe.ok is True
        content, tokens = cheap_llm_chat(settings, system="s", user="u", timeout=2.0)
        assert content == "ok"
        assert tokens == 1
        assert all(h == expected for h in seen)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@allure.story("Health")
@allure.title("Probe is not ok when the configured model is missing from /api/tags")
def test_probe_rejects_missing_model() -> None:
    from tests.ollama_stub import ollama_stub_server

    with ollama_stub_server() as url:
        clear_cheap_llm_probe_cache()
        probe = probe_cheap_llm(_ollama(url, model="m"), timeout=2.0)
        assert probe.reachable is True
        assert probe.model_present is False
        assert probe.ok is False
        assert "not served" in probe.reason
        assert cheap_llm_available(_ollama(url, model="m")) is False


@allure.story("Health")
@allure.title("Untagged model matches the :latest tag Ollama reports")
def test_model_is_served_latest_alias() -> None:
    assert model_is_served("qwen2.5-coder", ("qwen2.5-coder:latest",)) is True
    assert model_is_served("qwen2.5-coder:latest", ("qwen2.5-coder:latest",)) is True
    assert model_is_served("qwen2.5-coder:7b", ("qwen2.5-coder:7b",)) is True
    assert model_is_served("missing", ("qwen2.5-coder:latest",)) is False
    assert model_is_served("", ("qwen2.5-coder:latest",)) is False


@allure.story("Status")
@allure.title("cheap_llm_status_line names the missing model and lists what is served")
def test_status_line_missing_model() -> None:
    from tests.ollama_stub import ollama_stub_server

    with ollama_stub_server() as url:
        clear_cheap_llm_probe_cache()
        line = cheap_llm_status_line(_ollama(url, model="m"))
    assert "model unavailable" in line
    assert "model=m" in line
    assert "stub-model" in line


@allure.story("Health")
@allure.title("Probe reports HTTP 401, empty catalog, and non-dict payloads")
def test_probe_auth_and_empty_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import urllib.error
    from greedy_token.cheap_llm import _probe_health

    def unauthorized(*a, **k):
        raise urllib.error.HTTPError(
            "https://ollama.qa.guru/api/tags",
            401,
            "Unauthorized",
            hdrs={},
            fp=io.BytesIO(b""),
        )

    monkeypatch.setattr("urllib.request.urlopen", unauthorized)
    probe = _probe_health(_ollama("https://ollama.qa.guru"), timeout=1.0)
    assert probe.reachable is False
    assert "401" in probe.reason
    assert "CHEAP_LLM_USER" in probe.reason

    def server_error(*a, **k):
        raise urllib.error.HTTPError(
            "https://ollama.qa.guru/api/tags",
            500,
            "Internal Server Error",
            hdrs={},
            fp=io.BytesIO(b""),
        )

    monkeypatch.setattr("urllib.request.urlopen", server_error)
    five_hundred = _probe_health(_ollama("https://ollama.qa.guru"), timeout=1.0)
    assert five_hundred.reachable is False
    assert five_hundred.reason == "HTTP 500"

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: mock_resp)
    monkeypatch.setattr("greedy_token.cheap_llm.json.load", lambda _resp: {"models": []})
    empty = _probe_health(_ollama("https://ollama.qa.guru"), timeout=1.0)
    assert empty.reachable is True
    assert empty.model_present is False
    assert "no models served" in empty.reason

    assert served_models(None) == ()
    assert served_models({"models": []}) == ()
    assert served_models({"data": [{"id": "m"}]}) == ("m",)

