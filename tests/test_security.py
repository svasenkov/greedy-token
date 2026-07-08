from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.router import _build_tool_command
from greedy_token.tool_paths import root_cd_prefix, shell_args
from greedy_token.wrappers import resolve_wrapper_command

pytestmark = [allure.epic("Security"), allure.feature("Shell quoting")]


@allure.story("Workspace path")
@allure.title("root_cd_prefix quotes paths with spaces")
def test_root_cd_prefix_quotes_spaces(tmp_path: Path) -> None:
    root = tmp_path / "my workspace"
    root.mkdir()
    prefix = root_cd_prefix(root)
    assert prefix.startswith("cd '")
    assert "my workspace" in prefix
    assert prefix.endswith(" &&")


@allure.story("Shell args")
@allure.title("shell_args quotes shell metacharacters")
def test_shell_args_quotes_metacharacters() -> None:
    assert shell_args("foo; rm -rf /") == "'foo; rm -rf /'"
    assert shell_args("safe-name") == "safe-name"
    assert shell_args("two words") == "'two words'"


@allure.story("Ripgrep command")
@allure.title("build_tool_command quotes workspace root with spaces")
def test_build_tool_command_quotes_root(tmp_path: Path) -> None:
    root = tmp_path / "repo with spaces"
    root.mkdir()
    route = {"tool": "rg", "globs": ["!node_modules/**"], "search_paths": ["docs"]}
    cmd = _build_tool_command(route, "find baseUrl", root)
    assert "repo with spaces" in cmd
    assert f"cd '{root}'" in cmd or "cd '" in cmd


@allure.story("Wrapper scripts")
@allure.title("resolve_wrapper_command quotes root and extra args")
def test_resolve_wrapper_command_quotes_root_and_args(tmp_path: Path) -> None:
    root = tmp_path / "space root"
    script_dir = root / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "check-meta-sync.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

    cmd = resolve_wrapper_command("check-meta-sync", root, extra_args="x; id")
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

    greedy_token_pipeline("pipeline: check-meta-sync then rag baseUrl")
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs.get("execute") is False


@patch("greedy_token.wrappers.json.load", return_value={"models": []})
@patch("urllib.request.urlopen")
@allure.story("Ollama probe")
@allure.title("ollama_available caches successful probe result")
def test_ollama_available_uses_cache(mock_urlopen, mock_json_load) -> None:
    from greedy_token.wrappers import _ollama_probe_cache, ollama_available

    _ollama_probe_cache.clear()
    mock_resp = mock_urlopen.return_value.__enter__.return_value
    ollama_available("http://localhost:11434")
    ollama_available("http://localhost:11434")
    assert mock_urlopen.call_count == 1
