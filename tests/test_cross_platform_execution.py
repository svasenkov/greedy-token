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
