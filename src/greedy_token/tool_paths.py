"""Resolve rg/jq binaries when MCP/IDE runs with minimal PATH."""

from __future__ import annotations

import os
import shlex
import shutil
from collections.abc import Iterator
from pathlib import Path


def _rg_candidates() -> Iterator[Path]:
    override = os.environ.get("GREEDY_TOKEN_RG", "").strip()
    if override:
        yield Path(override).expanduser()
    which = shutil.which("rg")
    if which:
        yield Path(which)

    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory:
            yield Path(directory) / "rg"

    yield from (
        Path("/opt/homebrew/bin/rg"),
        Path("/usr/local/bin/rg"),
        Path("/Applications/Cursor.app/Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg"),
        Path("/Applications/Visual Studio Code.app/Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg"),
    )

    home = Path.home()
    for app in ("Cursor.app", "Visual Studio Code.app"):
        bundled = (
            home
            / "Applications"
            / app
            / "Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg"
        )
        yield bundled


def resolve_rg() -> Path | None:
    seen: set[Path] = set()
    for candidate in _rg_candidates():
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    return None


def rg_path_for_shell() -> str:
    found = resolve_rg()
    if found:
        return sh_quote(str(found))
    return "rg"


def sh_quote(value: str) -> str:
    """Quote a string as a single POSIX shell token.

    Delegates to :func:`shlex.quote` (the stdlib reference implementation) so
    the output is provably shell-safe. See ``tests/test_security.py`` for the
    hypothesis round-trip proof that ``shlex.split`` recovers the original.
    """
    return shlex.quote(value)


def root_cd_prefix(root: Path) -> str:
    """Shell-safe `cd <root> &&` for subprocess commands."""
    return f"cd {sh_quote(str(root))} &&"


def shell_args(extra_args: str) -> str:
    """Quote extra CLI args as one shell token (safe suffix for script invocations)."""
    text = extra_args.strip()
    if not text:
        return ""
    return sh_quote(text)


# Subprocess timeouts (seconds)
RG_TIMEOUT = 30
SCRIPT_TIMEOUT = 120
