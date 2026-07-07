"""Resolve rg/jq binaries when MCP/IDE runs with minimal PATH."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def _rg_candidates() -> list[Path]:
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
    import re

    if re.fullmatch(r"[\w@./:-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"
