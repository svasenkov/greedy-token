"""Run shell-looking command strings without ``shell=True``.

Workspace YAML / route overlays can influence command strings. Passing them to
``subprocess.run(..., shell=True)`` is a supply-chain boundary: unquoted
metacharacters become real shell syntax. This module peels a leading
``cd <root> &&`` into ``cwd``, splits the remainder with :func:`shlex.split`,
rejects leftover shell operators as separate tokens, and runs ``shell=False``.
"""

from __future__ import annotations

import shlex
import subprocess
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


def _resolved_allowed_executables(values: Iterable[Path | str]) -> frozenset[Path]:
    resolved: set[Path] = set()
    for value in values:
        try:
            resolved.add(Path(value).expanduser().resolve())
        except OSError:
            continue
    return frozenset(resolved)


def _reject_code_string_launch(argv: list[str]) -> None:
    executable = Path(argv[0]).name.lower()
    if executable in _PYTHON_NAMES:
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
        parts = shlex.split(text)
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
    if executable.is_absolute():
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
    if candidate.is_absolute():
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
        if candidate.is_absolute() or value.startswith("~"):
            raise UnsafeCommandError(
                f"absolute argument path is not allowed: {value!r}"
            )
        if ".." in candidate.parts:
            raise UnsafeCommandError(
                f"argument path escapes workspace root: {value!r}"
            )
        if "/" in value or value.startswith("."):
            try:
                (root / candidate).resolve().relative_to(root.resolve())
            except (OSError, ValueError) as exc:
                raise UnsafeCommandError(
                    f"argument path escapes workspace root: {value!r}"
                ) from exc


def trusted_script_invocation(
    command: str,
    *,
    root: Path,
    registered_script_paths: Iterable[str] = (),
    trusted_script_paths: Iterable[str] = (),
) -> CommandInvocation:
    """Validate a route command against wrapper and local script allowlists."""
    resolved_root = root.resolve()
    cwd, argv = command_to_argv(
        command,
        default_cwd=resolved_root,
        workspace_root=resolved_root,
    )
    assert cwd is not None
    if cwd != resolved_root:
        raise UnsafeCommandError("script cwd must equal the workspace root")

    executable = Path(argv[0]).name.lower()
    if executable in _PYTHON_NAMES:
        if executable != "python" or len(argv) < 2 or argv[1].startswith("-"):
            raise UnsafeCommandError(
                "python commands must be 'python <trusted-script.py> [args...]'"
            )
        _script, rel = _workspace_script_path(argv[1], resolved_root)
        script_args = argv[2:]
    else:
        if executable in _SHELL_NAMES:
            raise UnsafeCommandError(
                "shell interpreters are not route executors; register the script path"
            )
        _script, rel = _workspace_script_path(argv[0].removeprefix("./"), resolved_root)
        script_args = argv[1:]

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
        argv=tuple(argv),
        authorization=authorization,
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
    if executable.name != expected:
        raise UnsafeCommandError(
            f"tool executable mismatch: expected {expected!r}, got {args[0]!r}"
        )
    if executable.is_absolute():
        if expected != "rg":
            raise UnsafeCommandError("absolute tool executable is not registered")
        from greedy_token.tool_paths import resolve_rg

        registered_rg = resolve_rg()
        if registered_rg is None or executable.resolve() != registered_rg.resolve():
            raise UnsafeCommandError("absolute ripgrep executable is not registered")

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
        if candidate.is_absolute():
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
