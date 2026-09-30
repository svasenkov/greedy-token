from __future__ import annotations

import sys
from pathlib import Path

import pytest

from greedy_token.subprocess_safe import (
    UnsafeCommandError,
    command_to_argv,
    executable_name,
    format_invocation,
    is_absolute_path,
    trusted_script_argv,
    trusted_tool_invocation,
)


def test_executable_names_are_portable() -> None:
    assert executable_name(r"C:\Tools\rg.EXE") == "rg"
    assert executable_name(r"C:\Tools\jq.cmd") == "jq"
    assert executable_name(r"C:\Python312\python.exe") == "python"


@pytest.mark.parametrize(
    "value",
    [
        r"C:\Users\Тест User\repo",
        r"\\server\share\repo",
        "/workspace/repo",
    ],
)
def test_absolute_paths_are_recognised_on_every_host(value: str) -> None:
    assert is_absolute_path(value)


def test_structured_dry_run_preserves_spaces_backslashes_and_unicode() -> None:
    cwd = Path(r"C:\Users\Тест User\repo")
    rendered = format_invocation(
        (r"C:\Program Files\Ripgrep\rg.exe", "-F", "ключ", r"docs\space file.txt"),
        cwd,
    )
    assert "Тест User" in rendered
    assert "Program Files" in rendered
    assert "space file.txt" in rendered
    assert "argv=" in rendered


def test_trusted_python_uses_running_interpreter_in_unicode_workspace(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Проект with spaces"
    script = root / "scripts" / "проверка.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")

    invocation = trusted_script_argv(
        (sys.executable, "scripts/проверка.py", "--name=значение"),
        cwd=root,
        root=root,
        registered_script_paths=("scripts/проверка.py",),
    )
    assert invocation.cwd == root.resolve()
    assert invocation.argv[0] == sys.executable
    assert invocation.argv[-1] == "--name=значение"


@pytest.mark.parametrize("name", ["python", "python3", "python3.12", "python.exe"])
def test_bare_python_executable_pins_to_running_interpreter(
    tmp_path: Path, name: str
) -> None:
    # A bare interpreter name would float through PATH to a shadow install
    # without project deps — trusted scripts must run under sys.executable.
    root = tmp_path / "workspace"
    script = root / "scripts" / "check.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")

    invocation = trusted_script_argv(
        (name, "scripts/check.py"),
        cwd=root,
        root=root,
        registered_script_paths=("scripts/check.py",),
    )
    # sys.executable stays unresolved: the venv launcher path keeps
    # pyvenv.cfg discovery, its resolved base binary would not.
    assert invocation.argv == (sys.executable, "scripts/check.py")
    assert invocation.script_type == "python"


def test_bare_python_stays_bare_without_running_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Embedded hosts may report an empty sys.executable — keep the bare name
    # rather than substituting a resolved cwd as the executable.
    monkeypatch.setattr(sys, "executable", "")
    root = tmp_path / "workspace"
    script = root / "scripts" / "check.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")

    invocation = trusted_script_argv(
        ("python", "scripts/check.py"),
        cwd=root,
        root=root,
        registered_script_paths=("scripts/check.py",),
    )
    assert invocation.argv[0] == "python"


def test_windows_absolute_argument_fails_closed_on_non_windows_too(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    script = root / "scripts" / "check.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")

    with pytest.raises(UnsafeCommandError, match="absolute argument"):
        trusted_script_argv(
            (sys.executable, "scripts/check.py", r"C:\outside\secret.txt"),
            cwd=root,
            root=root,
            registered_script_paths=("scripts/check.py",),
        )


def test_unregistered_absolute_python_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    script = root / "scripts" / "check.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")

    with pytest.raises(UnsafeCommandError, match="Python executable"):
        trusted_script_argv(
            (str(tmp_path / "fake" / "python"), "scripts/check.py"),
            cwd=root,
            root=root,
            registered_script_paths=("scripts/check.py",),
        )


def test_absolute_legacy_executable_requires_exact_registration(tmp_path: Path) -> None:
    executable = tmp_path / "tool"
    executable.write_text("", encoding="utf-8")
    cwd, argv = command_to_argv(
        f"{executable} arg",
        allowed_absolute_executables=(executable,),
    )
    assert cwd is None
    assert argv == [str(executable), "arg"]


@pytest.mark.parametrize(
    "command",
    [
        "cd C:relative && rg x",
        "cd D: && rg x",
        'cd "E:dir with space" && rg x',
    ],
)
def test_command_to_argv_rejects_drive_relative_cd(command: str) -> None:
    # "C:x" resolves against the per-drive cwd on Windows — never a valid
    # cd target inside a parsed legacy command, on any host.
    with pytest.raises(UnsafeCommandError, match="drive-relative"):
        command_to_argv(command)


@pytest.mark.parametrize("command", ["C:tool arg", "z:script --flag"])
def test_command_to_argv_rejects_drive_relative_executable(command: str) -> None:
    with pytest.raises(UnsafeCommandError, match="drive-relative executable"):
        command_to_argv(command)


def test_command_to_argv_drive_absolute_is_not_drive_relative() -> None:
    # "C:\x" / "C:/x" are drive-anchored absolutes, not drive-relative — the
    # lookahead keeps them on the ordinary absolute-executable check, which
    # then refuses an unregistered executable.
    for command in (r"C:\tools\rg.exe --version", "C:/tools/rg.exe --version"):
        with pytest.raises(UnsafeCommandError, match="absolute executable is not registered"):
            command_to_argv(command)


def test_structured_script_rejects_empty_and_interpreter_only_argv(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(UnsafeCommandError, match="empty script argv"):
        trusted_script_argv((), cwd=root, root=root)
    with pytest.raises(UnsafeCommandError, match="python commands must"):
        trusted_script_argv(("python",), cwd=root, root=root)


def test_windows_tool_suffix_has_same_trust_policy(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    invocation = trusted_tool_invocation(
        ("rg.exe", "-n", "needle", "--max-count", "50", r"docs\space file.txt"),
        cwd=root,
        root=root,
        tool="rg",
    )
    assert invocation.authorization == "internal-tool:rg"
