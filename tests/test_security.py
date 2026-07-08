from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.router import _build_tool_command
from greedy_token.tool_paths import root_cd_prefix, shell_args
from greedy_token.wrappers import resolve_wrapper_command
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Security"),
    allure.parent_suite("Security"),
    allure.feature("Shell quoting"),
    allure.suite("Shell quoting"),
]


@allure.story("Workspace path")
@allure.title("Workspace cd prefix quotes paths with spaces")
def test_root_cd_prefix_quotes_spaces(tmp_path: Path) -> None:
    root = tmp_path / "my workspace"
    root.mkdir()
    with allure.step("Build cd prefix for path with spaces"):
        prefix = root_cd_prefix(root)
        attach_text("cd prefix", prefix)
    with allure.step("Verify path is single-quoted"):
        assert prefix.startswith("cd '")
        assert "my workspace" in prefix
        assert prefix.endswith(" &&")


@allure.story("Shell args")
@allure.title("Shell argument quoter escapes metacharacters")
def test_shell_args_quotes_metacharacters() -> None:
    with allure.step("Quote shell arguments with metacharacters"):
        dangerous = shell_args("foo; rm -rf /")
        safe = shell_args("safe-name")
        spaced = shell_args("two words")
        attach_text("dangerous arg", dangerous)
        attach_text("safe arg", safe)
        attach_text("spaced arg", spaced)
    with allure.step("Verify quoting rules"):
        assert dangerous == "'foo; rm -rf /'"
        assert safe == "safe-name"
        assert spaced == "'two words'"


@allure.story("Ripgrep command")
@allure.title("Tool command builder quotes workspace root with spaces")
def test_build_tool_command_quotes_root(tmp_path: Path) -> None:
    root = tmp_path / "repo with spaces"
    root.mkdir()
    route = {"tool": "rg", "globs": ["!node_modules/**"], "search_paths": ["docs"]}
    with allure.step("Build ripgrep command for spaced root"):
        cmd = _build_tool_command(route, "find baseUrl", root)
        attach_text("tool command", cmd)
    with allure.step("Verify workspace root is quoted"):
        assert "repo with spaces" in cmd
        assert f"cd '{root}'" in cmd or "cd '" in cmd


@allure.story("Wrapper scripts")
@allure.title("Wrapper command resolver quotes root and extra args")
def test_resolve_wrapper_command_quotes_root_and_args(tmp_path: Path) -> None:
    root = tmp_path / "space root"
    script_dir = root / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "check-meta-sync.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

    with allure.step("Resolve wrapper command with spaced root and args"):
        cmd = resolve_wrapper_command("check-meta-sync", root, extra_args="x; id")
        attach_text("wrapper command", cmd)
    with allure.step("Verify root and args are quoted"):
        assert "space root" in cmd
        assert "'x; id'" in cmd


@patch("greedy_token.mcp.run_pipeline")
@allure.story("MCP safety")
@allure.title("MCP pipeline tool is dry-run by default")
def test_mcp_pipeline_dry_run_by_default(
    mock_run,
    minimal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from greedy_token.mcp import greedy_token_pipeline
    from greedy_token.pipeline import PipelineResult

    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(minimal_workspace))
    mock_run.return_value = PipelineResult(task="t", steps=[])

    with allure.step("Call greedy_token_pipeline without execute flag"):
        greedy_token_pipeline("pipeline: check-meta-sync then rag baseUrl")
        attach_text("execute kwarg", str(mock_run.call_args.kwargs.get("execute")))
    with allure.step("Verify dry-run by default"):
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs.get("execute") is False


@patch("greedy_token.wrappers.json.load", return_value={"models": []})
@patch("urllib.request.urlopen")
@allure.story("Ollama probe")
@allure.title("Ollama availability probe caches successful result")
def test_ollama_available_uses_cache(mock_urlopen, mock_json_load) -> None:
    from greedy_token.wrappers import _ollama_probe_cache, ollama_available

    _ollama_probe_cache.clear()
    mock_resp = mock_urlopen.return_value.__enter__.return_value
    with allure.step("Probe Ollama twice with same URL"):
        ollama_available("http://localhost:11434")
        ollama_available("http://localhost:11434")
        attach_text("urlopen call count", str(mock_urlopen.call_count))
    with allure.step("Verify probe result is cached"):
        assert mock_urlopen.call_count == 1
