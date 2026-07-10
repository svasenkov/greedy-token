from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from greedy_token.tool_output import filter_tool_output
from greedy_token.paths import find_workspace_root
from greedy_token.tool_paths import RG_TIMEOUT, resolve_rg, rg_path_for_shell, root_cd_prefix, sh_quote

DEFAULT_GLOBS = [
    "!.git/**",
    "!node_modules/**",
    "!build/**",
    "!.venv/**",
    "!.cursor/hooks/**",
]

DEFAULT_PATHS = [
    "projects",
    "docs",
    "stacks",
    "scripts",
    "generators",
]

SKIP_DIR_NAMES = {".git", "node_modules", "build", ".venv", "__pycache__"}


@dataclass
class SearchResult:
    text: str
    engine: str  # rg | python
    spent_tokens: int = 0


def _under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_search_path(path_hint: str, root: Path) -> Path | None:
    """Resolve a file or directory under *root*. Paths outside the workspace are rejected."""
    hint = path_hint.strip()
    if not hint:
        return None

    root = root.resolve()
    direct = Path(hint)

    # Absolute paths: accept only when under root.
    if direct.is_absolute():
        if direct.is_file() or direct.is_dir():
            resolved = direct.resolve()
            return resolved if _under_root(resolved, root) else None
        return None

    # Relative to workspace root (preferred over cwd).
    rooted = (root / hint).resolve()
    if (rooted.is_file() or rooted.is_dir()) and _under_root(rooted, root):
        return rooted

    name = Path(hint).name
    if not name:
        return None

    # Prefer a unique directory match for bare names like "docs".
    dir_matches = sorted(
        p.resolve() for p in root.glob(f"**/{name}") if p.is_dir()
    )
    dir_matches = [m for m in dir_matches if _under_root(m, root)]
    if len(dir_matches) == 1:
        return dir_matches[0]

    matches = sorted(p.resolve() for p in root.glob(f"**/{name}") if p.is_file())
    matches = [m for m in matches if _under_root(m, root)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        exact = [m for m in matches if m.name == name]
        if len(exact) == 1:
            return exact[0]

    return None


def _python_search_file(path: Path, query: str, *, limit: int) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"Error reading {path}: {exc}"]

    hits: list[str] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if query not in line:
            continue
        display = line if len(line) <= 200 else line[:200] + "…"
        hits.append(f"{path}:{line_no}:{display}")
        if len(hits) >= limit:
            break
    return hits


def _python_search_tree(
    root: Path,
    query: str,
    *,
    scope_dirs: list[Path],
    name_glob: str | None = None,
    limit: int,
) -> list[str]:
    hits: list[str] = []
    for base in scope_dirs:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if name_glob and not path.match(name_glob):
                continue
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = path
            for line_no, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if query not in line:
                    continue
                display = line if len(line) <= 200 else line[:200] + "…"
                hits.append(f"{rel}:{line_no}:{display}")
                if len(hits) >= limit:
                    return hits
    return hits


def _run_rg(cmd: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=RG_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return 124, f"Error: ripgrep timed out after {RG_TIMEOUT}s"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def search_code(
    query: str,
    root: Path | None = None,
    *,
    path: str | None = None,
    limit: int = 50,
) -> SearchResult:
    root = (root or find_workspace_root()).resolve()
    query = query.strip()
    if not query:
        return SearchResult(text="Error: query is required.", engine="rg")

    if path:
        hint = path.strip()
        candidate = Path(hint)
        try:
            abs_candidate = (
                candidate.resolve()
                if candidate.is_absolute()
                else (root / hint).resolve()
            )
        except OSError:
            abs_candidate = None
        if (
            abs_candidate is not None
            and abs_candidate.exists()
            and not _under_root(abs_candidate, root)
        ):
            return SearchResult(
                text=(
                    f"Error: path {hint!r} is outside workspace root "
                    f"({root}). Search is confined to the workspace."
                ),
                engine="rg",
            )

    resolved = resolve_search_path(path, root) if path else None
    rg_bin = resolve_rg()

    if resolved and resolved.is_file():
        scope = str(resolved.relative_to(root))
        if rg_bin:
            rel = sh_quote(scope)
            cmd = (
                f"{root_cd_prefix(root)} {rg_path_for_shell()} -n --max-columns 200 -F "
                f"{sh_quote(query)} --max-count {limit} {rel}"
            )
            _, out = _run_rg(cmd)
            filtered = filter_tool_output(out)
            if filtered and "command not found" not in filtered.lower():
                return SearchResult(
                    text=f"Search: {query!r} in {scope}\n\n{filtered}",
                    engine="rg",
                )
        lines = _python_search_file(resolved, query, limit=limit)
        if lines:
            body = "\n".join(lines)
            return SearchResult(
                text=(
                    f"Search: {query!r} in {scope} [python]\n"
                    f"(rg not in PATH — python file scan)\n\n{body}"
                ),
                engine="python",
            )
        return SearchResult(
            text=(
                f"No matches for {query!r} in {scope}.\n"
                f"Try greedy_token_rag for docs/rag lookup, or search without path."
            ),
            engine="rg" if rg_bin else "python",
        )

    glob_flags = " ".join(f"-g {sh_quote(g)}" for g in DEFAULT_GLOBS)

    if rg_bin:
        if resolved and resolved.is_dir():
            rel = resolved.relative_to(root) if resolved.is_relative_to(root) else resolved
            scope = str(rel)
            cmd = (
                f"{root_cd_prefix(root)} {rg_path_for_shell()} -n --max-columns 200 -F "
                f"{sh_quote(query)} {glob_flags} --max-count {limit} {sh_quote(str(rel))}"
            )
        elif path:
            name = Path(path.strip()).name
            scope = f"*{name}*"
            cmd = (
                f"{root_cd_prefix(root)} {rg_path_for_shell()} -n --max-columns 200 -F "
                f"{sh_quote(query)} {glob_flags} --max-count {limit} "
                f"-g {sh_quote(f'*{name}*')} {' '.join(DEFAULT_PATHS)}"
            )
        else:
            scope = "workspace"
            cmd = (
                f"{root_cd_prefix(root)} {rg_path_for_shell()} -n --max-columns 200 -F "
                f"{sh_quote(query)} {glob_flags} --max-count {limit} "
                f"{' '.join(DEFAULT_PATHS)}"
            )
        _, out = _run_rg(cmd)
        filtered = filter_tool_output(out)
        if filtered and "command not found" not in filtered.lower():
            return SearchResult(
                text=f"Search: {query!r} in {scope}\n\n{filtered}",
                engine="rg",
            )

    if resolved and resolved.is_dir():
        scope_dirs = [resolved]
        name_glob = None
        scope = str(resolved.relative_to(root) if resolved.is_relative_to(root) else resolved)
    else:
        scope_dirs = [root / p for p in DEFAULT_PATHS]
        name_glob = f"*{Path(path.strip()).name}*" if path else None
        scope = f"*{Path(path.strip()).name}*" if path else "workspace"
    lines = _python_search_tree(
        root,
        query,
        scope_dirs=scope_dirs,
        name_glob=name_glob,
        limit=limit,
    )
    if lines:
        note = "\n(rg not in PATH — python tree scan)\n\n" if not rg_bin else "\n"
        return SearchResult(
            text=f"Search: {query!r} in {scope} [python]{note}" + "\n".join(lines),
            engine="python",
        )

    return SearchResult(
        text=f"No matches for {query!r} in {scope}.",
        engine="rg" if rg_bin else "python",
    )
