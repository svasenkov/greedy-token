"""Mutation kill-tests for subprocess_safe: exact messages, codes, argv math."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import allure
from greedy_token.subprocess_safe import (
    UnsafeCommandError,
    _validate_script_args,
    _workspace_script_path,
    command_to_argv,
    executable_name,
    format_invocation,
    is_absolute_path,
    run_command,
    trusted_script_argv,
    trusted_script_invocation,
    trusted_tool_invocation,
)

pytestmark = [
    allure.epic("Security"),
    allure.parent_suite("Security"),
    allure.feature("Subprocess safety"),
    allure.suite("Subprocess safety mutation gaps"),
]


# --- executable_name / is_absolute_path / format_invocation ---


@allure.story("Executable names")
@allure.title(".bat suffix is stripped case-insensitively")
def test_executable_name_bat_suffix() -> None:
    # kills "XX.batXX" and ".BAT" tuple mutants — name is lowercased first.
    assert executable_name(r"C:\Tools\runnel.bat") == "runnel"
    assert executable_name(r"C:\Tools\runnel.BAT") == "runnel"


@allure.story("Path shapes")
@allure.title("absolute detection covers posix, drive and UNC; rejects drive-relative")
def test_is_absolute_path_all_engines() -> None:
    assert is_absolute_path("/posix/abs")
    assert is_absolute_path(r"C:\win\abs")
    assert is_absolute_path("C:/win/abs")
    assert is_absolute_path(r"\\server\share\x")
    assert not is_absolute_path("rel/path")
    assert not is_absolute_path("C:rel")
    assert not is_absolute_path("")


@allure.story("Dry-run format")
@allure.title("unicode cwd/argv render verbatim (ensure_ascii=False)")
def test_format_invocation_unicode_exact() -> None:
    out = format_invocation(["ток"], Path("/дир"))
    # ensure_ascii=None/True/dropped escapes to \uXXXX.
    assert out == 'cwd="/дир" argv=["ток"]'


# --- command_to_argv ---


@allure.story("Command parsing")
@allure.title("refusal messages are exact, not XX-padded")
def test_command_to_argv_exact_messages(tmp_path: Path) -> None:
    with pytest.raises(UnsafeCommandError) as empty:
        command_to_argv("")
    assert str(empty.value) == "empty command"

    with pytest.raises(UnsafeCommandError) as after_cd:
        command_to_argv(f"cd {tmp_path} &&")
    assert str(after_cd.value) == "empty command after cd prefix"

    with pytest.raises(UnsafeCommandError) as cd_drive:
        command_to_argv("cd C:rel && rg x")
    # parts[2]-mutant would print '&&' instead of the drive-relative target.
    assert "C:rel" in str(cd_drive.value)

    with pytest.raises(UnsafeCommandError) as exe_drive:
        command_to_argv("C:tool arg")
    # parts[1]-mutant would print 'arg' instead of the executable token.
    assert "C:tool" in str(exe_drive.value)


@allure.story("Command parsing")
@allure.title("X is a plain character — commenters and escape stay empty")
def test_command_to_argv_lexer_no_comment_no_escape() -> None:
    _, argv = command_to_argv("echo X-ray aXbXc")
    # commenters="XXXX" would truncate at 'X'; escape="XXXX" would eat it.
    assert argv == ["echo", "X-ray", "aXbXc"]


@allure.story("Command parsing")
@allure.title("single-token backtick substitution is refused")
def test_command_to_argv_backtick_token() -> None:
    with pytest.raises(UnsafeCommandError, match="substitution"):
        command_to_argv("ls `id`")


@allure.story("Command parsing")
@allure.title("unregistered absolute executable names itself in the error")
def test_command_to_argv_unregistered_exec_message(tmp_path: Path) -> None:
    with pytest.raises(UnsafeCommandError) as exc:
        command_to_argv("/bin/definitely-not-registered-xyz arg")
    # parts[1]-mutant prints 'arg'; XX-mutant pads the message.
    assert "definitely-not-registered-xyz" in str(exc.value)
    assert "not registered" in str(exc.value)


@allure.story("Command parsing")
@allure.title("unresolvable absolute executable reports the resolve failure")
def test_command_to_argv_resolve_error_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(self: Path, *args: object, **kwargs: object) -> Path:
        raise OSError("cannot resolve")

    monkeypatch.setattr(Path, "resolve", _boom)
    with pytest.raises(UnsafeCommandError) as exc:
        command_to_argv("/abs/tool arg")
    assert str(exc.value) == "cannot resolve absolute executable"


# --- _workspace_script_path ---


@allure.story("Script path")
@allure.title("drive-relative script path names the drive token")
def test_workspace_script_path_drive_relative(tmp_path: Path) -> None:
    with pytest.raises(UnsafeCommandError, match="drive-relative"):
        _workspace_script_path("C:x", tmp_path)


@allure.story("Script path")
@allure.title("non-regular suffixed path raises code=untrusted_type")
def test_workspace_script_path_untrusted_type_code(tmp_path: Path) -> None:
    (tmp_path / "dir.py").mkdir()
    with pytest.raises(UnsafeCommandError) as exc:
        _workspace_script_path("dir.py", tmp_path)
    # kills code=None/dropped/"XXuntrusted_typeXX"/"UNTRUSTED_TYPE".
    assert exc.value.code == "untrusted_type"


# --- _validate_script_args ---


@allure.story("Argument confinement")
@allure.title("value is taken after the FIRST '=' only")
def test_validate_script_args_first_equals_split(tmp_path: Path) -> None:
    # "..=/x" and "v=/etc" contain no absolute/.. segments — no raise.
    # rsplit / split-2 mutants see "/x", ".." or "/etc" and refuse.
    _validate_script_args(["k=..=/x"], tmp_path)
    _validate_script_args(["k=v=/etc/passwd"], tmp_path)


@allure.story("Argument confinement")
@allure.title("skipped args do not stop the scan — continue, not break")
def test_validate_script_args_continue_not_break(tmp_path: Path) -> None:
    with pytest.raises(UnsafeCommandError, match="escapes workspace root"):
        # break-mutant exits after skipping "-" and never sees "../escape".
        _validate_script_args(["-", "../escape"], tmp_path)


@allure.story("Argument confinement")
@allure.title("tilde paths are refused like absolute paths")
def test_validate_script_args_tilde(tmp_path: Path) -> None:
    with pytest.raises(UnsafeCommandError, match="absolute argument"):
        _validate_script_args(["~/somewhere"], tmp_path)


# --- trusted_script_argv: refusal codes ---


@allure.story("Trust codes")
@allure.title("deprecated and unregistered scripts both carry code=not_approved")
def test_trusted_script_argv_not_approved_codes(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "x.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("print('ok')\n", encoding="utf-8")

    with pytest.raises(UnsafeCommandError) as deprecated:
        trusted_script_argv(
            (sys.executable, "scripts/x.py"),
            cwd=tmp_path,
            root=tmp_path,
            trusted_script_paths=("scripts/x.py",),
        )
    assert deprecated.value.code == "not_approved"

    with pytest.raises(UnsafeCommandError) as unregistered:
        trusted_script_argv(
            (sys.executable, "scripts/x.py"), cwd=tmp_path, root=tmp_path
        )
    assert unregistered.value.code == "not_approved"


# --- trusted_script_invocation: command_to_argv kwargs ---


@allure.story("Legacy invocation")
@allure.title("no cd prefix falls back to the workspace root cwd")
def test_trusted_script_invocation_default_cwd(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "ok.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    inv = trusted_script_invocation(
        "python scripts/ok.py",
        root=tmp_path,
        manifest_script_paths=("scripts/ok.py",),
    )
    # default_cwd=None mutant hits `assert cwd is not None`.
    assert inv.cwd == tmp_path.resolve()
    assert inv.script_type == "python"


@allure.story("Legacy invocation")
@allure.title("workspace_root confinement reports 'outside workspace root'")
def test_trusted_script_invocation_workspace_confinement(tmp_path: Path) -> None:
    # workspace_root=None mutant skips confinement and reaches a different
    # refusal ("script cwd must equal the workspace root").
    with pytest.raises(UnsafeCommandError, match="outside workspace root"):
        trusted_script_invocation("cd /tmp && python x.py", root=tmp_path)


# --- trusted_tool_invocation ---


@allure.story("Tool invocation")
@allure.title("empty argv / cwd mismatch messages are exact")
def test_trusted_tool_invocation_exact_messages(tmp_path: Path) -> None:
    with pytest.raises(UnsafeCommandError) as empty:
        trusted_tool_invocation((), cwd=tmp_path, root=tmp_path, tool="rg")
    assert str(empty.value) == "empty tool argv"

    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(UnsafeCommandError) as mismatch:
        trusted_tool_invocation(
            ("rg", "-n", "x", "d"), cwd=tmp_path, root=root, tool="rg"
        )
    assert str(mismatch.value) == "tool cwd must equal the workspace root"


@allure.story("Tool invocation")
@allure.title("executable mismatch error names argv[0]")
def test_trusted_tool_invocation_mismatch_names_executable(tmp_path: Path) -> None:
    with pytest.raises(UnsafeCommandError) as exc:
        trusted_tool_invocation(
            ("not-rg", "operand"), cwd=tmp_path, root=tmp_path, tool="rg"
        )
    # args[1]-mutant prints 'operand' instead of 'not-rg'.
    assert "not-rg" in str(exc.value)


@allure.story("Tool invocation")
@allure.title("jq checks only the LAST argv operand as a workspace path")
def test_trusted_tool_invocation_jq_last_operand_only(tmp_path: Path) -> None:
    # argv[+1:]/argv[-2:] mutants would confinement-check "/etc/passwd" too.
    inv = trusted_tool_invocation(
        ("jq", "/etc/passwd", "out.json"), cwd=tmp_path, root=tmp_path, tool="jq"
    )
    assert inv.authorization == "internal-tool:jq"


@allure.story("Tool invocation")
@allure.title("rg '--' separator strips the pattern operand before confinement")
def test_trusted_tool_invocation_rg_separator_strips_pattern(tmp_path: Path) -> None:
    # keeping tail unsliced (and-False/tail[:2]/"XX--XX" mutants) would check
    # the absolute "/abs-pattern" token and refuse.
    inv = trusted_tool_invocation(
        ("rg", "-n", "--max-count", "1", "--", "/abs-pattern", "sub/dir"),
        cwd=tmp_path,
        root=tmp_path,
        tool="rg",
    )
    assert inv.authorization == "internal-tool:rg"


@allure.story("Tool invocation")
@allure.title("malformed rg argv and path refusals carry exact messages")
def test_trusted_tool_invocation_rg_refusal_messages(tmp_path: Path) -> None:
    with pytest.raises(UnsafeCommandError) as malformed:
        trusted_tool_invocation(
            ("rg", "-n", "pat", "dir"), cwd=tmp_path, root=tmp_path, tool="rg"
        )
    assert str(malformed.value) == "malformed ripgrep argv"

    with pytest.raises(UnsafeCommandError) as absolute:
        trusted_tool_invocation(
            ("rg", "-n", "--max-count", "1", "--", "p", "/abs"),
            cwd=tmp_path,
            root=tmp_path,
            tool="rg",
        )
    assert str(absolute.value) == "absolute tool path is not allowed"

    with pytest.raises(UnsafeCommandError, match="drive-relative"):
        trusted_tool_invocation(
            ("rg", "-n", "--max-count", "1", "--", "p", "C:x"),
            cwd=tmp_path,
            root=tmp_path,
            tool="rg",
        )


# --- run_command: subprocess.run kwarg forwarding ---


@allure.story("Run command")
@allure.title("subprocess.run receives exact kwargs for defaults and overrides")
def test_run_command_forwards_kwargs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple, dict]] = []

    def fake_run(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_command("echo hi", timeout=5, cwd=tmp_path)
    args, kw = calls[0]
    assert args == (["echo", "hi"],)
    # kills capture_output/text/check default flips and every
    # kwarg→None/dropped forwarder.
    assert kw == {
        "shell": False,
        "capture_output": True,
        "text": True,
        "cwd": str(tmp_path),
        "timeout": 5,
        "check": False,
    }

    run_command(
        "echo hi",
        timeout=1,
        cwd=tmp_path,
        capture_output=False,
        text=False,
        check=True,
    )
    assert calls[1][1]["capture_output"] is False
    assert calls[1][1]["text"] is False
    assert calls[1][1]["check"] is True
