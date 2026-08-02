"""Validate and run structured subprocess invocations.

Core execution accepts only ``argv`` plus ``cwd``. Legacy command strings are
parsed here solely for route-file compatibility and dry-run migration; they are
never passed to a shell.
"""

from __future__ import annotations

import shlex
import subprocess
import json
import ntpath
import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Operators that must not appear as their own argv tokens after split.
_FORBIDDEN_OPERATOR_TOKENS = frozenset(
    {"&&", "||", ";", "|", "`", ">", "<", ">>", "$(", "${"}
)


class UnsafeCommandError(ValueError):
    """Command string is empty or contains a disallowed shell operator token."""


@dataclass(frozen=True)
class CommandInvocation:
    """A validated, structured subprocess invocation."""

    cwd: Path
    argv: tuple[str, ...]
    authorization: str


_SHELL_NAMES = frozenset({"sh", "bash", "dash", "zsh", "ksh", "fish"})
_PYTHON_NAMES = frozenset({"python", "python3"})


def executable_name(value: str | Path) -> str:
    """Return a case-folded executable name on POSIX and Windows."""
    name = ntpath.basename(str(value)).lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def is_python_executable(value: str | Path) -> bool:
    """Accept python, python3, python3.12, and their Windows .exe forms."""
    name = executable_name(value)
    return bool(re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", name))


def is_absolute_path(value: str | Path) -> bool:
    """Recognise native, POSIX, and Windows drive/UNC absolute paths."""
    text = str(value)
    return Path(text).is_absolute() or posixpath.isabs(text) or ntpath.isabs(text)


def format_invocation(argv: Iterable[str], cwd: Path | str) -> str:
    """Portable, unambiguous dry-run representation."""
    args = [str(arg) for arg in argv]
    return (
        f"cwd={json.dumps(str(cwd), ensure_ascii=False)} "
        f"argv={json.dumps(args, ensure_ascii=False)}"
    )


def _resolved_allowed_executables(values: Iterable[Path | str]) -> frozenset[Path]:
    resolved: set[Path] = set()
    for value in values:
        try:
            resolved.add(Path(value).expanduser().resolve())
        except OSError:
            continue
    return frozenset(resolved)


def _reject_code_string_launch(argv: list[str]) -> None:
    executable = executable_name(argv[0])
    if is_python_executable(argv[0]):
        for arg in argv[1:]:
            if not arg.startswith("-"):
                break
            if "c" in arg[1:]:
                raise UnsafeCommandError("python -c is not allowed")
    if executable in _SHELL_NAMES:
        for arg in argv[1:]:
            if not arg.startswith("-"):
                break
            if "c" in arg[1:]:
                raise UnsafeCommandError("shell -c is not allowed")


def command_to_argv(
    command: str,
    *,
    default_cwd: Path | None = None,
    workspace_root: Path | None = None,
    allowed_absolute_executables: Iterable[Path | str] = (),
) -> tuple[Path | None, list[str]]:
    """Parse ``cd <dir> && rest…`` into ``(cwd, argv)`` without invoking a shell.

    Raises :class:`UnsafeCommandError` when the command is empty or contains a
    bare shell operator token (fail closed — do not pass operators through as
    inert argv that might confuse callers).
    """
    text = (command or "").strip()
    if not text:
        raise UnsafeCommandError("empty command")

    try:
        lexer = shlex.shlex(text, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        # Legacy route commands use quoted paths, so keep backslashes literal.
        # This preserves Windows paths without granting shell escape semantics.
        lexer.escape = ""
        parts = list(lexer)
    except ValueError as exc:
        raise UnsafeCommandError(f"cannot parse command: {exc}") from exc

    cwd = default_cwd
    if len(parts) >= 3 and parts[0] == "cd" and parts[2] == "&&":
        cwd = Path(parts[1])
        parts = parts[3:]

    if not parts:
        raise UnsafeCommandError("empty command after cd prefix")

    for token in parts:
        if token in _FORBIDDEN_OPERATOR_TOKENS:
            raise UnsafeCommandError(f"shell operator not allowed: {token!r}")
        # Fail closed on classic substitution forms left as a single token
        # only when they look like active shell forms (keep literal paths).
        if token.startswith("$(") or token.startswith("`"):
            raise UnsafeCommandError(f"shell substitution not allowed: {token!r}")

    _reject_code_string_launch(parts)

    executable = Path(parts[0]).expanduser()
    if is_absolute_path(parts[0]):
        try:
            resolved_executable = executable.resolve()
        except OSError as exc:
            raise UnsafeCommandError("cannot resolve absolute executable") from exc
        allowed = _resolved_allowed_executables(allowed_absolute_executables)
        if resolved_executable not in allowed:
            raise UnsafeCommandError(
                f"absolute executable is not registered: {parts[0]!r}"
            )

    if workspace_root is not None:
        root = workspace_root.resolve()
        effective_cwd = (cwd or root).resolve()
        try:
            effective_cwd.relative_to(root)
        except ValueError as exc:
            raise UnsafeCommandError(
                f"cwd is outside workspace root: {effective_cwd}"
            ) from exc
        cwd = effective_cwd

    return cwd, parts


def _workspace_script_path(token: str, root: Path) -> tuple[Path, str]:
    candidate = Path(token).expanduser()
    if is_absolute_path(token):
        raise UnsafeCommandError("absolute script paths are not allowed")
    try:
        resolved = (root / candidate).resolve()
        rel = resolved.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise UnsafeCommandError(
            f"script path is outside workspace root: {token!r}"
        ) from exc
    if resolved.suffix not in (".py", ".sh"):
        raise UnsafeCommandError("trusted script must end in .py or .sh")
    if not resolved.is_file():
        raise FileNotFoundError(f"Script not found: {resolved}")
    return resolved, rel.as_posix()


def _validate_script_args(args: Iterable[str], root: Path) -> None:
    for arg in args:
        value = arg.split("=", 1)[1] if "=" in arg else arg
        if not value or value == "-":
            continue
        candidate = Path(value).expanduser()
        if is_absolute_path(value) or value.startswith("~"):
            raise UnsafeCommandError(
                f"absolute argument path is not allowed: {value!r}"
            )
        if ".." in candidate.parts:
            raise UnsafeCommandError(
                f"argument path escapes workspace root: {value!r}"
            )
        if "/" in value or "\\" in value or value.startswith("."):
            try:
                (root / candidate).resolve().relative_to(root.resolve())
            except (OSError, ValueError) as exc:
                raise UnsafeCommandError(
                    f"argument path escapes workspace root: {value!r}"
                ) from exc


def trusted_script_argv(
    argv: Iterable[str],
    *,
    cwd: Path,
    root: Path,
    registered_script_paths: Iterable[str] = (),
    trusted_script_paths: Iterable[str] = (),
) -> CommandInvocation:
    """Validate structured argv against wrapper and local script allowlists."""
    resolved_root = root.resolve()
    resolved_cwd = cwd.resolve()
    if resolved_cwd != resolved_root:
        raise UnsafeCommandError("script cwd must equal the workspace root")
    args = [str(arg) for arg in argv]
    if not args:
        raise UnsafeCommandError("empty script argv")
    _reject_code_string_launch(args)

    executable = executable_name(args[0])
    if is_python_executable(args[0]):
        if is_absolute_path(args[0]):
            from greedy_token.tool_paths import resolve_python

            if Path(args[0]).resolve() != resolve_python():
                raise UnsafeCommandError("absolute Python executable is not registered")
        if len(args) < 2 or args[1].startswith("-"):
            raise UnsafeCommandError(
                "python commands must be 'python <trusted-script.py> [args...]'"
            )
        _script, rel = _workspace_script_path(args[1], resolved_root)
        script_args = args[2:]
    else:
        if executable in _SHELL_NAMES:
            raise UnsafeCommandError(
                "shell interpreters are not route executors; register the script path"
            )
        _script, rel = _workspace_script_path(args[0].removeprefix("./"), resolved_root)
        script_args = args[1:]

    registered = frozenset(Path(p).as_posix().removeprefix("./") for p in registered_script_paths)
    trusted = frozenset(Path(p).as_posix().removeprefix("./") for p in trusted_script_paths)
    if rel in registered:
        authorization = f"wrapper:{rel}"
    elif rel in trusted:
        authorization = f"trusted-script:{rel}"
    else:
        raise UnsafeCommandError(
            f"script is not registered or explicitly trusted: {rel!r}"
        )

    _validate_script_args(script_args, resolved_root)
    return CommandInvocation(
        cwd=resolved_root,
        argv=tuple(args),
        authorization=authorization,
    )


def trusted_script_invocation(
    command: str,
    *,
    root: Path,
    registered_script_paths: Iterable[str] = (),
    trusted_script_paths: Iterable[str] = (),
) -> CommandInvocation:
    """Parse and validate a legacy route command string.

    New execution code must call :func:`trusted_script_argv` directly.
    """
    resolved_root = root.resolve()
    cwd, argv = command_to_argv(
        command,
        default_cwd=resolved_root,
        workspace_root=resolved_root,
    )
    assert cwd is not None
    return trusted_script_argv(
        argv,
        cwd=cwd,
        root=resolved_root,
        registered_script_paths=registered_script_paths,
        trusted_script_paths=trusted_script_paths,
    )


def trusted_tool_invocation(
    argv: Iterable[str],
    *,
    cwd: Path,
    root: Path,
    tool: str | None,
) -> CommandInvocation:
    """Validate structured argv produced by the internal rg/jq builder."""
    args = tuple(str(arg) for arg in argv)
    if not args:
        raise UnsafeCommandError("empty tool argv")
    resolved_root = root.resolve()
    if cwd.resolve() != resolved_root:
        raise UnsafeCommandError("tool cwd must equal the workspace root")
    expected = "jq" if (tool or "rg").lower() == "jq" else "rg"
    executable = Path(args[0])
    if executable_name(args[0]) != expected:
        raise UnsafeCommandError(
            f"tool executable mismatch: expected {expected!r}, got {args[0]!r}"
        )
    if is_absolute_path(args[0]):
        from greedy_token.tool_paths import resolve_tool

        registered = resolve_tool(expected)
        if registered is None or executable.resolve() != registered.resolve():
            raise UnsafeCommandError(f"absolute {expected} executable is not registered")

    path_values: tuple[str, ...]
    if expected == "jq":
        path_values = args[-1:]
    else:
        try:
            marker = args.index("--max-count")
            path_values = args[marker + 2 :]
        except (ValueError, IndexError) as exc:
            raise UnsafeCommandError("malformed ripgrep argv") from exc
    if not path_values:
        raise UnsafeCommandError(f"{expected} invocation has no workspace path")
    for value in path_values:
        candidate = Path(value)
        if is_absolute_path(value):
            raise UnsafeCommandError("absolute tool path is not allowed")
        try:
            (resolved_root / candidate).resolve().relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise UnsafeCommandError(
                f"tool path escapes workspace root: {value!r}"
            ) from exc
    return CommandInvocation(
        cwd=resolved_root,
        argv=args,
        authorization=f"internal-tool:{expected}",
    )


def run_command(
    command: str,
    *,
    timeout: float,
    cwd: Path | str | None = None,
    capture_output: bool = True,
    text: bool = True,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run *command* with ``shell=False`` after :func:`command_to_argv`."""
    default_cwd = Path(cwd) if cwd is not None else None
    run_cwd, argv = command_to_argv(command, default_cwd=default_cwd)
    return subprocess.run(
        argv,
        shell=False,
        capture_output=capture_output,
        text=text,
        cwd=str(run_cwd) if run_cwd is not None else None,
        timeout=timeout,
        check=check,
    )
