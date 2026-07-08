from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import allure
import pytest

from greedy_token.wrappers import WRAPPERS, ollama_available, resolve_wrapper_command
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Script wrappers"),
    allure.parent_suite("Script wrappers"),
    allure.feature("Workspace scripts"),
    allure.suite("Workspace scripts"),
]


@allure.story("Registry")
@allure.title("Script wrappers registry includes read-only check-meta-sync")
def test_wrappers_registry_has_check_meta_sync() -> None:
    with allure.step("Inspect wrappers registry"):
        entry = WRAPPERS.get("check-meta-sync")
        attach_json("check-meta-sync entry", {"present": entry is not None, "read_only": entry.read_only if entry else None})
    with allure.step("Verify check-meta-sync is read-only"):
        assert "check-meta-sync" in WRAPPERS
        assert WRAPPERS["check-meta-sync"].read_only is True


@allure.story("Command resolution")
@allure.title("Wrapper command resolver builds shell script invocation")
def test_resolve_wrapper_command_python(minimal_workspace) -> None:
    script = minimal_workspace / "scripts" / "check-meta-sync.sh"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    with allure.step("Resolve check-meta-sync wrapper command"):
        cmd = resolve_wrapper_command("check-meta-sync", minimal_workspace)
        attach_text("resolved command", cmd)
    with allure.step("Verify shell script invocation"):
        assert "check-meta-sync.sh" in cmd
        assert "python" not in cmd


@allure.story("Command resolution")
@allure.title("Wrapper command resolver raises for unknown wrapper id")
def test_resolve_wrapper_unknown_raises(minimal_workspace: Path) -> None:
    with allure.step("Resolve unknown wrapper id"):
        attach_text("wrapper id", "no-such-wrapper")
    with allure.step("Verify KeyError is raised"):
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
    with allure.step("Probe Ollama /api/tags endpoint"):
        available = ollama_available("http://localhost:11434")
        attach_text("ollama url", "http://localhost:11434")
        attach_text("available", str(available))
    with allure.step("Verify Ollama is available"):
        assert available is True


@allure.story("Ollama probe")
@allure.title("Ollama availability returns true when stub /api/tags responds")
def test_ollama_available_against_stub(ollama_stub: str) -> None:
    from greedy_token.wrappers import ollama_available

    with allure.step("Probe Ollama stub server"):
        attach_text("ollama stub url", ollama_stub)
        available = ollama_available(ollama_stub)
        attach_text("available", str(available))
    with allure.step("Verify stub responds as available"):
        assert available is True


@patch("urllib.request.urlopen", side_effect=OSError("connection refused"))
@allure.story("Ollama probe")
@allure.title("Ollama availability returns false when server is down")
def test_ollama_available_false(mock_urlopen) -> None:
    from greedy_token.wrappers import _ollama_probe_cache

    _ollama_probe_cache.clear()
    with allure.step("Probe unreachable Ollama server"):
        attach_text("ollama url", "http://localhost:11434")
        available = ollama_available("http://localhost:11434")
        attach_text("available", str(available))
    with allure.step("Verify Ollama is unavailable"):
        assert available is False
