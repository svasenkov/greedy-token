from __future__ import annotations

from unittest.mock import MagicMock, patch

import allure
import pytest

from greedy_token.wrappers import WRAPPERS, ollama_available, resolve_wrapper_command

pytestmark = [
    allure.epic("Script wrappers"),
    allure.parent_suite("Script wrappers"),
    allure.feature("Workspace scripts"),
    allure.suite("Workspace scripts"),
]


@allure.story("Registry")
@allure.title("Script wrappers registry includes read-only check-meta-sync")
def test_wrappers_registry_has_check_meta_sync() -> None:
    assert "check-meta-sync" in WRAPPERS
    assert WRAPPERS["check-meta-sync"].read_only is True


@allure.story("Command resolution")
@allure.title("Wrapper command resolver builds shell script invocation")
def test_resolve_wrapper_command_python(minimal_workspace) -> None:
    script = minimal_workspace / "scripts" / "check-meta-sync.sh"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    cmd = resolve_wrapper_command("check-meta-sync", minimal_workspace)
    assert "check-meta-sync.sh" in cmd
    assert "python" not in cmd


@allure.story("Command resolution")
@allure.title("Wrapper command resolver raises for unknown wrapper id")
def test_resolve_wrapper_unknown_raises(minimal_workspace: Path) -> None:
    with pytest.raises(KeyError):
        resolve_wrapper_command("no-such-wrapper", minimal_workspace)


@patch("greedy_token.wrappers.json.load", return_value={"models": []})
@patch("urllib.request.urlopen")
@allure.story("Ollama probe")
@allure.title("Ollama availability returns true when /api/tags responds")
def test_ollama_available_true(mock_urlopen, mock_json_load) -> None:
    from greedy_token.wrappers import _ollama_probe_cache

    _ollama_probe_cache.clear()
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp
    assert ollama_available("http://localhost:11434") is True


@patch("urllib.request.urlopen", side_effect=OSError("connection refused"))
@allure.story("Ollama probe")
@allure.title("Ollama availability returns false when server is down")
def test_ollama_available_false(mock_urlopen) -> None:
    from greedy_token.wrappers import _ollama_probe_cache

    _ollama_probe_cache.clear()
    assert ollama_available("http://localhost:11434") is False
