from __future__ import annotations

import shlex
from pathlib import Path
from unittest.mock import patch

import allure
import pytest
from hypothesis import given
from hypothesis import strategies as st

from greedy_token.router import _build_tool_argv, _build_tool_command
from greedy_token.subprocess_safe import (
    UnsafeCommandError,
    command_to_argv,
    trusted_script_invocation,
)
from greedy_token.tool_paths import resolve_rg, root_cd_prefix, sh_quote, shell_args
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
        assert cmd.startswith("cwd=")
        assert "argv=" in cmd


@allure.story("Shell harden")
@allure.title("search_paths from workspace YAML are quoted (no bare metacharacters)")
def test_build_tool_command_quotes_search_paths(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    route = {
        "tool": "rg",
        "globs": ["!node_modules/**"],
        "search_paths": ["docs; rm -rf /", "src && id"],
    }
    cmd = _build_tool_command(route, "find baseUrl", root)
    attach_text("tool command", cmd)
    argv = list(_build_tool_argv(route, "find baseUrl", root))
    assert f'cwd="{root}"' in cmd
    # Injection strings must be single inert argv tokens, not shell syntax.
    assert "docs; rm -rf /" in argv
    assert "src && id" in argv
    assert ";" not in argv
    assert "&&" not in argv


@allure.story("Shell harden")
@allure.title("command_to_argv peels cd && and rejects bare shell operators")
def test_command_to_argv_fail_closed_on_operators(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    safe = f"cd {sh_quote(str(root))} && rg -n foo ."
    cwd, argv = command_to_argv(safe)
    assert cwd == root
    assert argv[0].endswith("rg") or argv[0] == "rg"
    with pytest.raises(UnsafeCommandError, match="shell operator"):
        command_to_argv("rg foo && rm -rf /")
    with pytest.raises(UnsafeCommandError, match="substitution"):
        command_to_argv("rg $(whoami)")


@allure.story("Command trust boundary")
@allure.title("Code-string launchers and arbitrary absolute executables are rejected")
@pytest.mark.parametrize(
    "command",
    [
        "python -c 'print(1)'",
        "python -I -c 'print(1)'",
        "sh -c 'echo unsafe'",
        "bash -eu -c 'echo unsafe'",
        "/bin/echo unsafe",
    ],
)
def test_command_to_argv_rejects_code_strings_and_absolute_executables(
    command: str,
) -> None:
    with pytest.raises(UnsafeCommandError):
        command_to_argv(command)


@allure.story("Command trust boundary")
@allure.title("Command cwd cannot leave the explicit workspace root")
def test_command_to_argv_rejects_outside_workspace_cwd(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    with pytest.raises(UnsafeCommandError, match="cwd is outside workspace"):
        command_to_argv(
            f"cd {sh_quote(str(outside))} && python scripts/check.py",
            workspace_root=root,
        )


@allure.story("Command trust boundary")
@allure.title("Command parser covers malformed, empty, and non-code interpreter argv")
def test_command_to_argv_parser_edges(tmp_path: Path) -> None:
    with pytest.raises(UnsafeCommandError, match="empty command"):
        command_to_argv("")
    with pytest.raises(UnsafeCommandError, match="cannot parse"):
        command_to_argv("rg 'unterminated")
    with pytest.raises(UnsafeCommandError, match="empty command after cd"):
        command_to_argv(f"cd {sh_quote(str(tmp_path))} &&")
    assert command_to_argv("python scripts/check.py")[1] == [
        "python",
        "scripts/check.py",
    ]
    assert command_to_argv("sh scripts/check.sh")[1] == [
        "sh",
        "scripts/check.sh",
    ]
    assert command_to_argv("python")[1] == ["python"]
    assert command_to_argv("sh")[1] == ["sh"]


@allure.story("Command trust boundary")
@allure.title("Executable resolution failures fail closed")
def test_command_to_argv_resolution_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.subprocess_safe import _resolved_allowed_executables

    boom = tmp_path / "boom-executable"
    good = tmp_path / "good-executable"
    real_resolve = Path.resolve

    def fail_boom(self, *args, **kwargs):
        if self == boom:
            raise OSError("cannot resolve")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_boom)
    assert _resolved_allowed_executables((boom, good)) == frozenset(
        {real_resolve(good)}
    )
    with pytest.raises(UnsafeCommandError, match="cannot resolve absolute executable"):
        command_to_argv(str(boom), allowed_absolute_executables=(boom,))


@allure.story("Command trust boundary")
@allure.title("Only registered or explicitly trusted workspace scripts get argv")
def test_trusted_script_invocation_requires_allowlist(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "check.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    command = f"{root_cd_prefix(root)} python scripts/check.py"

    with pytest.raises(UnsafeCommandError, match="not registered"):
        trusted_script_invocation(command, root=root)

    registered = trusted_script_invocation(
        command,
        root=root,
        registered_script_paths=("scripts/check.py",),
    )
    assert registered.argv == ("python", "scripts/check.py")
    assert registered.cwd == root
    assert registered.authorization == "wrapper:scripts/check.py"

    trusted = trusted_script_invocation(
        command,
        root=root,
        trusted_script_paths=("scripts/check.py",),
    )
    assert trusted.authorization == "trusted-script:scripts/check.py"


@allure.story("Command trust boundary")
@allure.title("Trusted script validation confines interpreter, script, cwd, and args")
def test_trusted_script_invocation_rejects_boundary_breaks(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    py_script = scripts / "check.py"
    sh_script = scripts / "check.sh"
    txt_script = scripts / "check.txt"
    py_script.write_text("print('ok')\n", encoding="utf-8")
    sh_script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    txt_script.write_text("not a script\n", encoding="utf-8")
    registered = ("scripts/check.py", "scripts/check.sh")

    direct = trusted_script_invocation(
        f"{root_cd_prefix(root)} ./scripts/check.sh",
        root=root,
        registered_script_paths=registered,
    )
    assert direct.authorization == "wrapper:scripts/check.sh"

    subdir = root / "subdir"
    subdir.mkdir()
    with pytest.raises(UnsafeCommandError, match="cwd must equal"):
        trusted_script_invocation(
            f"cd {sh_quote(str(subdir))} && python scripts/check.py",
            root=root,
            registered_script_paths=registered,
        )
    py3 = trusted_script_invocation(
        f"{root_cd_prefix(root)} python3 scripts/check.py",
        root=root,
        registered_script_paths=registered,
    )
    assert py3.argv == ("python3", "scripts/check.py")
    with pytest.raises(UnsafeCommandError, match="shell interpreters"):
        trusted_script_invocation(
            f"{root_cd_prefix(root)} sh scripts/check.sh",
            root=root,
            registered_script_paths=registered,
        )
    with pytest.raises(UnsafeCommandError, match="absolute script paths"):
        trusted_script_invocation(
            f"{root_cd_prefix(root)} python {sh_quote(str(py_script))}",
            root=root,
            registered_script_paths=registered,
        )
    with pytest.raises(UnsafeCommandError, match="outside workspace"):
        trusted_script_invocation(
            f"{root_cd_prefix(root)} python ../outside.py",
            root=root,
            registered_script_paths=registered,
        )
    with pytest.raises(UnsafeCommandError, match="must end in"):
        trusted_script_invocation(
            f"{root_cd_prefix(root)} python scripts/check.txt",
            root=root,
            trusted_script_paths=("scripts/check.txt",),
        )

    for bad_arg, message in (
        ("/tmp/outside", "absolute argument"),
        ("--path=/tmp/outside", "absolute argument"),
        ("../outside", "escapes workspace"),
    ):
        with pytest.raises(UnsafeCommandError, match=message):
            trusted_script_invocation(
                f"{root_cd_prefix(root)} python scripts/check.py {sh_quote(bad_arg)}",
                root=root,
                registered_script_paths=registered,
            )

    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafeCommandError, match="escapes workspace"):
        trusted_script_invocation(
            f"{root_cd_prefix(root)} python scripts/check.py link/file.txt",
            root=root,
            registered_script_paths=registered,
        )

    valid_args = trusted_script_invocation(
        f"{root_cd_prefix(root)} python scripts/check.py '' - docs/file.txt",
        root=root,
        registered_script_paths=registered,
    )
    assert valid_args.argv[-3:] == ("", "-", "docs/file.txt")


@allure.story("Command trust boundary")
@allure.title("Internal tool argv validator rejects forged executables and paths")
def test_trusted_tool_invocation_boundary_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.subprocess_safe import trusted_tool_invocation

    root = tmp_path / "workspace"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    valid_rg = ("rg", "-n", "x", "--max-count", "50", ".")

    with pytest.raises(UnsafeCommandError, match="empty tool argv"):
        trusted_tool_invocation((), cwd=root, root=root, tool="rg")
    with pytest.raises(UnsafeCommandError, match="cwd must equal"):
        trusted_tool_invocation(valid_rg, cwd=other, root=root, tool="rg")
    with pytest.raises(UnsafeCommandError, match="executable mismatch"):
        trusted_tool_invocation(("rm", "--max-count", "50", "."), cwd=root, root=root, tool="rg")
    with pytest.raises(UnsafeCommandError, match="absolute jq executable"):
        trusted_tool_invocation(("/tmp/jq", "-r", ".", "data.json"), cwd=root, root=root, tool="jq")

    monkeypatch.setattr("greedy_token.tool_paths.resolve_rg", lambda: None)
    with pytest.raises(UnsafeCommandError, match="absolute rg executable"):
        trusted_tool_invocation(
            ("/tmp/rg", "--max-count", "50", "."),
            cwd=root,
            root=root,
            tool="rg",
        )
    with pytest.raises(UnsafeCommandError, match="malformed ripgrep"):
        trusted_tool_invocation(("rg", "-n", "x"), cwd=root, root=root, tool="rg")
    with pytest.raises(UnsafeCommandError, match="no workspace path"):
        trusted_tool_invocation(
            ("rg", "--max-count", "50"),
            cwd=root,
            root=root,
            tool="rg",
        )
    with pytest.raises(UnsafeCommandError, match="absolute tool path"):
        trusted_tool_invocation(
            ("rg", "--max-count", "50", "/tmp"),
            cwd=root,
            root=root,
            tool="rg",
        )
    with pytest.raises(UnsafeCommandError, match="escapes workspace"):
        trusted_tool_invocation(
            ("rg", "--max-count", "50", "../outside"),
            cwd=root,
            root=root,
            tool="rg",
        )

    jq = trusted_tool_invocation(
        ("jq", "-r", ".", "data.json"),
        cwd=root,
        root=root,
        tool="jq",
    )
    assert jq.authorization == "internal-tool:jq"


@allure.story("Command trust boundary")
@allure.title("run_command passes parsed argv and optional cwd to shell-false subprocess")
def test_run_command_structured_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.subprocess_safe import run_command

    calls: list[dict] = []

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return object()

    monkeypatch.setattr("greedy_token.subprocess_safe.subprocess.run", fake_run)
    run_command("echo ok", timeout=1)
    run_command("echo ok", timeout=2, cwd=tmp_path)
    assert calls[0]["argv"] == ["echo", "ok"]
    assert calls[0]["cwd"] is None
    assert calls[1]["cwd"] == str(tmp_path)
    assert all(call["shell"] is False for call in calls)


@allure.story("Command trust boundary")
@allure.title("Malicious read_only workspace route cannot create a side effect")
def test_malicious_read_only_workspace_route_not_executed(
    minimal_workspace: Path,
) -> None:
    from greedy_token.executors import execute_task

    side_effect = minimal_workspace / "SIDE_EFFECT"
    evil = minimal_workspace / "scripts" / "evil.py"
    evil.write_text(
        "from pathlib import Path\nPath('SIDE_EFFECT').write_text('owned')\n",
        encoding="utf-8",
    )
    (minimal_workspace / ".greedy-token.yaml").write_text(
        "routes:\n"
        "  - id: malicious\n"
        "    target: python\n"
        "    read_only: true\n"
        "    patterns: [malicious workspace command]\n"
        "    command: python scripts/evil.py\n",
        encoding="utf-8",
    )

    result = execute_task("malicious workspace command", minimal_workspace)
    assert result.exit_code == 1
    assert "not registered or explicitly trusted" in result.output
    assert not side_effect.exists()


@allure.story("Command trust boundary")
@allure.title("Explicit local trusted_script_paths authorises structured execution")
def test_explicit_trusted_script_path_executes(minimal_workspace: Path) -> None:
    from greedy_token.executors import execute_task

    safe = minimal_workspace / "scripts" / "safe.py"
    safe.write_text("print('trusted-ok')\n", encoding="utf-8")
    (minimal_workspace / ".greedy-token.yaml").write_text(
        "trusted_script_paths:\n"
        "  - scripts/safe.py\n"
        "routes:\n"
        "  - id: trusted-local\n"
        "    target: python\n"
        "    read_only: true\n"
        "    patterns: [trusted local script]\n"
        "    command: python scripts/safe.py\n",
        encoding="utf-8",
    )

    result = execute_task("trusted local script", minimal_workspace)
    assert result.exit_code == 0
    assert result.output.strip() == "trusted-ok"


@allure.story("Release integrity")
@allure.title("PyPI publish requires latest successful Test run for the exact tag commit")
def test_publish_workflow_is_gated_by_green_test_commit() -> None:
    workflow = Path(".github/_ethalon/publish.yml").read_text(encoding="utf-8")
    runnable = Path(".github/workflows/publish.yml").read_text(encoding="utf-8")
    assert "verify-tests:" in workflow
    assert "verify_release_matrix.py" in workflow
    assert "GITHUB_TOKEN: ${{ github.token }}" in workflow
    assert "needs: verify-tests" in workflow
    assert "id-token: write" in workflow
    assert workflow.splitlines()[1:] == runnable.splitlines()[1:]


@allure.story("Release integrity")
@allure.title("Mandatory CI covers supported OS, Python, dependency, and build matrices")
def test_required_cross_platform_matrices_are_release_gated() -> None:
    workflow = Path(".github/_ethalon/test.yml").read_text(encoding="utf-8")
    assert "os: [ubuntu-latest, macos-latest, windows-latest]" in workflow
    assert 'python: ["3.12", "3.14"]' in workflow
    assert "profile: [minimum, latest, mcp-lowest, mcp-latest]" in workflow
    assert "Unit tests without external tools" in workflow
    assert "Integration tests with real tools" in workflow
    assert "Build wheel and sdist, then smoke install" in workflow
    assert "name: required matrix gate" in workflow
    for job in (
        "test",
        "evidence",
        "tests",
        "portability",
        "integration",
        "dependencies",
        "distributions",
    ):
        assert f"      - {job}" in workflow


@allure.story("Release integrity")
@allure.title("Release gate runs the configured branch coverage threshold")
def test_release_gate_runs_coverage_report() -> None:
    gate = Path("scripts/release-gate.sh").read_text(encoding="utf-8")
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "python -m coverage run -m pytest tests/ -q" in gate
    assert "python -m coverage report --include='src/greedy_token/*'" in gate
    assert "branch = true" in pyproject
    assert "fail_under = 100" in pyproject


@allure.story("Release integrity")
@allure.title("CLI wrapper execution handles trust and process launch failures")
@pytest.mark.parametrize(
    ("error", "exit_code", "message"),
    [
        (UnsafeCommandError("unsafe"), 1, "Refusing unsafe command"),
        (FileNotFoundError("missing"), 127, "Executable not found"),
        (OSError("exec"), 126, "Cannot execute script"),
    ],
)
def test_cli_wrapper_execution_handles_launch_failures(
    minimal_workspace: Path,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    exit_code: int,
    message: str,
) -> None:
    from argparse import Namespace
    from unittest.mock import patch

    from greedy_token.cli import cmd_scripts

    args = Namespace(
        list=False,
        run="check-meta-sync",
        args="",
        execute=True,
        no_log=True,
    )
    with patch(
            "greedy_token.cli.resolve_wrapper_invocation",
        side_effect=error,
    ):
        assert cmd_scripts(args) == exit_code
    assert message in capsys.readouterr().err


@allure.story("Release integrity")
@allure.title("CLI structured subprocess maps launch failures after trust validation")
@pytest.mark.parametrize(
    ("error", "exit_code", "message"),
    [
        (FileNotFoundError("missing"), 127, "Executable not found"),
        (OSError("exec"), 126, "Cannot execute script"),
    ],
)
def test_cli_structured_subprocess_launch_failures(
    minimal_workspace: Path,
    capsys: pytest.CaptureFixture[str],
    error: OSError,
    exit_code: int,
    message: str,
) -> None:
    from argparse import Namespace

    from greedy_token.cli import cmd_scripts

    args = Namespace(
        list=False,
        run="check-meta-sync",
        args="",
        execute=True,
        no_log=True,
    )
    with patch("subprocess.run", side_effect=error):
        assert cmd_scripts(args) == exit_code
    assert message in capsys.readouterr().err


@allure.story("Command trust boundary")
@allure.title("Pipeline returns an explicit failure for unsafe registered step argv")
def test_pipeline_rejects_unsafe_step_command(
    minimal_workspace: Path,
) -> None:
    from unittest.mock import patch

    from greedy_token.pipeline import PipelineStep, run_pipeline

    step = PipelineStep(
        "check-meta-sync",
        "python",
        "unsafe",
        command="echo ok ; touch SIDE_EFFECT",
    )
    with patch("greedy_token.pipeline.parse_pipeline", return_value=[step]):
        result = run_pipeline("check-meta-sync", minimal_workspace, execute=True)
    assert result.steps[0].exit_code == 1
    assert "Refusing unsafe command" in result.steps[0].output
    assert result.steps[0].executed is False


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
        assert '"x; id"' in cmd
        assert cmd.startswith("cwd=")


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
