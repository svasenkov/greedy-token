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
from pathlib import Path

# Operators that must not appear as their own argv tokens after split.
_FORBIDDEN_OPERATOR_TOKENS = frozenset(
    {"&&", "||", ";", "|", "`", ">", "<", ">>", "$(", "${"}
)


class UnsafeCommandError(ValueError):
    """Command string is empty or contains a disallowed shell operator token."""


def command_to_argv(
    command: str,
    *,
    default_cwd: Path | None = None,
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

    return cwd, parts


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
