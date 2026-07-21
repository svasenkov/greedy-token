from __future__ import annotations

import shlex
from pathlib import Path
from unittest.mock import patch

import allure
import pytest
from hypothesis import given
from hypothesis import strategies as st

from greedy_token.router import _build_tool_command
from greedy_token.tool_paths import root_cd_prefix, sh_quote, shell_args
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


# Chars that are dangerous in a shell if left unquoted, plus unicode.
_QUOTE_ALPHABET = st.characters(
    blacklist_categories=("Cs",),  # exclude lone surrogates (not valid in argv)
) | st.sampled_from(list(" \t\n'\"\\;|&$`(){}[]<>*?#~!%+=,:.-"))


@allure.story("Shell quoting")
@allure.title("sh_quote output round-trips through shlex.split for arbitrary strings")
@given(value=st.text(alphabet=_QUOTE_ALPHABET, max_size=64))
def test_sh_quote_roundtrips_through_shell(value: str) -> None:
    # A quoted token must parse back to exactly the original single argument,
    # proving it is a shell-safe single token (equivalent to shlex.quote).
    quoted = sh_quote(value)
    assert shlex.split("cmd " + quoted)[1:] == [value]
    # And it stays consistent with the stdlib reference implementation.
    assert quoted == shlex.quote(value)


@allure.story("Shell quoting")
@allure.title("sh_quote neutralizes injection metacharacters as one token")
def test_sh_quote_blocks_injection() -> None:
    payload = "foo; rm -rf / && echo $(whoami) | cat `id`"
    with allure.step("Quote an injection payload"):
        quoted = sh_quote(payload)
        attach_text("quoted payload", quoted)
    with allure.step("Verify it parses back to a single, inert argument"):
        assert shlex.split("cmd " + quoted)[1:] == [payload]


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
    (script_dir / "meta-sync-check.py").write_text("#!/usr/bin/env python\nprint('ok')\n", encoding="utf-8")

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


@allure.story("Pipeline path confinement")
@allure.title("execute=True rejects audit-skill / classify-file paths outside workspace")
def test_pipeline_execute_rejects_outside_path(
    minimal_workspace: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from greedy_token.pipeline import parse_pipeline, run_pipeline

    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(minimal_workspace))
    outside_dir = tmp_path_factory.mktemp("outside-pipeline")
    outside_skill = outside_dir / "SKILL.md"
    outside_skill.write_text("# leaked\nsecret\n", encoding="utf-8")
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("classify-me\n", encoding="utf-8")

    with allure.step("Reject absolute audit-skill path outside root at parse"):
        with pytest.raises(ValueError, match="outside workspace root"):
            parse_pipeline(f"audit-skill {outside_skill}")
        attach_text("outside skill", str(outside_skill))

    with allure.step("Reject absolute classify-file path outside root at parse"):
        with pytest.raises(ValueError, match="outside workspace root"):
            parse_pipeline(f"classify-file {outside_file}")
        attach_text("outside file", str(outside_file))

    with allure.step("execute=True does not run subprocess for outside path"):
        with patch("greedy_token.pipeline.subprocess.run") as mock_run:
            with patch("greedy_token.pipeline.ollama_available", return_value=True):
                with pytest.raises(ValueError, match="outside workspace root"):
                    run_pipeline(f"audit-skill {outside_skill}", minimal_workspace, execute=True)
                with pytest.raises(ValueError, match="outside workspace root"):
                    run_pipeline(f"classify-file {outside_file}", minimal_workspace, execute=True)
            mock_run.assert_not_called()
            attach_text("subprocess calls", str(mock_run.call_count))

    with allure.step("Reject ../ escape, empty classify-file, missing bare name"):
        with pytest.raises(ValueError, match="outside workspace root"):
            parse_pipeline(f"classify-file ../{outside_dir.name}/{outside_file.name}")
        with pytest.raises(ValueError, match="classify-file needs"):
            parse_pipeline("classify-file")
        with pytest.raises(FileNotFoundError, match="File not found"):
            parse_pipeline("classify-file missing-bare-name.txt")
        attach_text("outside dir", str(outside_dir))

    with allure.step("Accept classify-file under root; reject absolute dir / missing abs"):
        inside = minimal_workspace / "docs" / "phase-manifest.json"
        steps = parse_pipeline(f"classify-file {inside}")
        assert steps[0].args == "docs/phase-manifest.json"
        with pytest.raises(FileNotFoundError, match="File not found"):
            parse_pipeline(f"audit-skill {minimal_workspace / 'docs'}")
        with pytest.raises(FileNotFoundError, match="File not found"):
            parse_pipeline(f"classify-file {minimal_workspace / 'docs' / 'nope.md'}")


@patch("greedy_token.cheap_llm.json.load", return_value={"models": []})
@patch("urllib.request.urlopen")
@allure.story("Ollama probe")
@allure.title("Ollama availability probe caches successful result")
def test_ollama_available_uses_cache(mock_urlopen, mock_json_load) -> None:
    from greedy_token.cheap_llm import _cheap_llm_probe_cache, clear_cheap_llm_probe_cache
    from greedy_token.wrappers import ollama_available

    clear_cheap_llm_probe_cache()
    mock_resp = mock_urlopen.return_value.__enter__.return_value
    with allure.step("Probe Ollama twice with same URL"):
        ollama_available("http://localhost:11434")
        ollama_available("http://localhost:11434")
        attach_text("urlopen call count", str(mock_urlopen.call_count))
    with allure.step("Verify probe result is cached"):
        assert mock_urlopen.call_count == 1
