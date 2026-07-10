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


@allure.story("Registry")
@allure.title("Stdout-only Ollama wrappers are read_only for --execute")
def test_wrappers_stdout_only_are_read_only() -> None:
    with allure.step("Inspect audit-skill and classify-file"):
        attach_json(
            "stdout-only wrappers",
            {
                "audit-skill": WRAPPERS["audit-skill"].read_only,
                "classify-file": WRAPPERS["classify-file"].read_only,
            },
        )
    with allure.step("Verify PIPELINE_AUTO_RUN wrappers are read_only"):
        assert WRAPPERS["audit-skill"].read_only is True
        assert WRAPPERS["classify-file"].read_only is True


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


@patch("greedy_token.cheap_llm.json.load", return_value={"models": []})
@patch("urllib.request.urlopen")
@allure.story("Ollama probe")
@allure.title("Ollama availability returns true when /api/tags responds")
def test_ollama_available_true(mock_urlopen, mock_json_load) -> None:
    from greedy_token.cheap_llm import clear_cheap_llm_probe_cache

    clear_cheap_llm_probe_cache()
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
    from greedy_token.cheap_llm import clear_cheap_llm_probe_cache

    clear_cheap_llm_probe_cache()
    with allure.step("Probe unreachable Ollama server"):
        attach_text("ollama url", "http://localhost:11434")
        available = ollama_available("http://localhost:11434")
        attach_text("available", str(available))
    with allure.step("Verify Ollama is unavailable"):
        assert available is False


@allure.story("Registry")
@allure.title("wrapper_for_command resolves wrapper from command string")
def test_wrapper_for_command() -> None:
    from greedy_token.wrappers import wrapper_for_command

    assert wrapper_for_command(None) is None
    assert wrapper_for_command("echo noop") is None
    w = wrapper_for_command("./scripts/check-meta-sync.sh")
    assert w is not None
    assert w.id == "check-meta-sync"


@allure.story("Command resolution")
@allure.title("resolve_wrapper_command builds python invocation for .py scripts")
def test_resolve_wrapper_command_python_script(minimal_workspace: Path) -> None:
    script = minimal_workspace / "scripts" / "demo.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    from greedy_token.wrappers import ScriptWrapper, WRAPPERS

    WRAPPERS["demo-py"] = ScriptWrapper(
        id="demo-py",
        path="scripts/demo.py",
        category="demo",
        read_only=True,
        requires_ollama=False,
        note="",
    )
    cmd = resolve_wrapper_command("demo-py", minimal_workspace, extra_args="--flag")
    assert "python scripts/demo.py" in cmd
    assert "--flag" in cmd
    del WRAPPERS["demo-py"]


@allure.story("Status")
@allure.title("ollama_status_line reports unavailable when server is down")
def test_ollama_status_line_unavailable() -> None:
    from greedy_token.wrappers import ollama_status_line

    with patch("greedy_token.wrappers.ollama_available", return_value=False):
        line = ollama_status_line()
    assert "unavailable" in line

