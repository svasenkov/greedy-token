"""Resolve external tools when MCP/IDE runs with a minimal PATH."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path


def _tool_candidates(tool: str, *, override_var: str) -> Iterator[Path]:
    override = os.environ.get(override_var, "").strip()
    if override:
        yield Path(override).expanduser()
    which = shutil.which(tool)
    if which:
        yield Path(which)

    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory:
            yield Path(directory) / tool

    if tool == "rg":
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


def _rg_candidates() -> Iterator[Path]:
    """Backward-compatible ripgrep candidate iterator."""
    yield from _tool_candidates("rg", override_var="GREEDY_TOKEN_RG")


def resolve_tool(tool: str) -> Path | None:
    """Resolve an executable by override, PATH, and known bundled locations."""
    if tool not in {"rg", "jq"}:
        raise ValueError(f"unsupported tool: {tool}")
    if os.environ.get("GREEDY_TOKEN_DISABLE_EXTERNAL_TOOLS") == "1":
        return None
    override_var = f"GREEDY_TOKEN_{tool.upper()}"
    seen: set[Path] = set()
    candidates = _rg_candidates() if tool == "rg" else _tool_candidates(
        tool, override_var=override_var
    )
    for candidate in candidates:
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


def resolve_rg() -> Path | None:
    return resolve_tool("rg")


def resolve_jq() -> Path | None:
    return resolve_tool("jq")


def resolve_python() -> Path:
    """Return the interpreter running greedy-token on every supported OS."""
    return Path(sys.executable).resolve()


# Legacy command-string formatting. Core execution must not call these helpers.
def sh_quote(value: str) -> str:
    return shlex.quote(value)


def root_cd_prefix(root: Path) -> str:
    return f"cd {sh_quote(str(root))} &&"


def shell_args(extra_args: str) -> str:
    text = extra_args.strip()
    return sh_quote(text) if text else ""


def rg_path_for_shell() -> str:
    found = resolve_rg()
    return sh_quote(str(found)) if found else "rg"


# Subprocess timeouts (seconds)
RG_TIMEOUT = 30
SCRIPT_TIMEOUT = 120
