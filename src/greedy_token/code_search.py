from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from greedy_token.tool_output import filter_tool_output
from greedy_token.paths import find_workspace_root
from greedy_token.tool_paths import RG_TIMEOUT, resolve_rg, rg_path_for_shell, root_cd_prefix, sh_quote

SearchContextMode = Literal["none", "snippet", "file"]

_HIT_LINE_RE = re.compile(
    r"^((?:[A-Za-z]:)?[^:\n]+|\.?/?[\w./+-]+):(\d+):(.*)$"
)

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

SKIP_DIR_NAMES = {".git", "node_modules", "build", ".venv", "__pycache__", "dist", ".tox"}


@dataclass
class SearchResult:
    text: str
    engine: str  # rg | python
    hit_count: int = 0
    enriched_files: int = 0
    context_tokens: int = 0
    hit_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PathResolveResult:
    """Outcome of resolving a search path hint under the workspace root."""

    path: Path | None = None
    candidates: tuple[Path, ...] = ()
    reason: str = ""  # "", "not_found", "ambiguous", "outside", "empty"


def _under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _rel_parts(path: Path, root: Path) -> tuple[str, ...] | None:
    try:
        return path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return None


def _is_skipped_path(path: Path, root: Path) -> bool:
    parts = _rel_parts(path, root)
    if parts is None:
        return True
    return any(part in SKIP_DIR_NAMES for part in parts)


def _under_default_paths(path: Path, root: Path) -> bool:
    parts = _rel_parts(path, root)
    return bool(parts) and parts[0] in DEFAULT_PATHS


def _format_rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _pick_unique(matches: list[Path], root: Path) -> Path | None:
    """Pick one match: unique overall, else unique under DEFAULT_PATHS."""
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    preferred = [m for m in matches if _under_default_paths(m, root)]
    if len(preferred) == 1:
        return preferred[0]
    return None


def _glob_name_matches(root: Path, name: str, *, want_dir: bool) -> list[Path]:
    found: list[Path] = []
    for p in root.glob(f"**/{name}"):
        if want_dir and not p.is_dir():
            continue
        if not want_dir and not p.is_file():
            continue
        resolved = p.resolve()
        if not _under_root(resolved, root):
            continue
        if _is_skipped_path(resolved, root):
            continue
        found.append(resolved)
    return sorted(found, key=lambda m: (not _under_default_paths(m, root), len(_rel_parts(m, root) or ()), str(m)))


def resolve_search_path_detail(path_hint: str, root: Path) -> PathResolveResult:
    """Resolve a file or directory under *root* with skip/prefer rules.

    Bare names skip vendor trees (node_modules, .venv, …). When several
    non-vendor matches exist, prefer a unique hit under DEFAULT_PATHS.
    Ambiguous or missing hints return ``path=None`` with ``reason`` set.
    """
    hint = path_hint.strip()
    if not hint:
        return PathResolveResult(reason="empty")

    root = root.resolve()
    direct = Path(hint)

    # Absolute paths: accept only when under root.
    if direct.is_absolute():
        try:
            exists = direct.is_file() or direct.is_dir()
        except OSError:
            return PathResolveResult(reason="not_found")
        if exists:
            try:
                resolved = direct.resolve()
            except OSError:
                return PathResolveResult(reason="not_found")
            if not _under_root(resolved, root):
                return PathResolveResult(reason="outside")
            return PathResolveResult(path=resolved)
        return PathResolveResult(reason="not_found")

    # Relative to workspace root (preferred over cwd). Explicit paths win even
    # under vendor trees — skip rules apply only to bare-name glob discovery.
    try:
        rooted = (root / hint).resolve()
    except OSError:
        rooted = None
    if (
        rooted is not None
        and (rooted.is_file() or rooted.is_dir())
        and _under_root(rooted, root)
    ):
        return PathResolveResult(path=rooted)

    name = Path(hint).name
    if not name:
        return PathResolveResult(reason="not_found")

    # Prefer a unique directory match for bare names like "docs".
    dir_matches = _glob_name_matches(root, name, want_dir=True)
    picked = _pick_unique(dir_matches, root)
    if picked is not None:
        return PathResolveResult(path=picked)
    if len(dir_matches) > 1:
        return PathResolveResult(
            candidates=tuple(dir_matches[:8]),
            reason="ambiguous",
        )

    file_matches = _glob_name_matches(root, name, want_dir=False)
    picked = _pick_unique(file_matches, root)
    if picked is not None:
        return PathResolveResult(path=picked)
    if len(file_matches) > 1:
        return PathResolveResult(
            candidates=tuple(file_matches[:8]),
            reason="ambiguous",
        )

    return PathResolveResult(reason="not_found")


def resolve_search_path(path_hint: str, root: Path) -> Path | None:
    """Resolve a file or directory under *root*. Paths outside the workspace are rejected."""
    return resolve_search_path_detail(path_hint, root).path


def _path_resolve_error(hint: str, detail: PathResolveResult, root: Path) -> str:
    if detail.reason == "outside":
        return (
            f"Error: path {hint!r} is outside workspace root "
            f"({root}). Search is confined to the workspace."
        )
    if detail.reason == "ambiguous":
        listed = "\n".join(f"  - {_format_rel(c, root)}" for c in detail.candidates)
        more = "" if len(detail.candidates) < 8 else "\n  - …"
        return (
            f"Error: path {hint!r} is ambiguous under {root.name}. "
            f"Pass a path relative to the workspace root.\n"
            f"Candidates:\n{listed}{more}"
        )
    return (
        f"Error: path {hint!r} not found under workspace root ({root}). "
        f"Use a relative path (e.g. projects/…/file.py) or a unique filename."
    )


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


_BARE_LINE_RE = re.compile(r"^(\d+):(.*)$")


def normalize_hit_body(body: str, *, default_path: str | None = None) -> str:
    """Prefix bare ``line:content`` rows (rg single-file mode) with *default_path*."""
    if not default_path:
        return body
    out: list[str] = []
    for raw in body.splitlines():
        line = raw.rstrip("\n")
        if _HIT_LINE_RE.match(line.strip()):
            out.append(line)
            continue
        m = _BARE_LINE_RE.match(line.strip())
        if m:
            out.append(f"{default_path}:{m.group(1)}:{m.group(2)}")
        else:
            out.append(line)
    return "\n".join(out)


def parse_hit_lines(
    text: str, *, default_path: str | None = None
) -> list[tuple[str, int, str]]:
    """Parse ``path:line:content`` hits from search output."""
    text = normalize_hit_body(text, default_path=default_path)
    hits: list[tuple[str, int, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("Search:") or line.startswith("("):
            continue
        m = _HIT_LINE_RE.match(line)
        if not m:
            # ripgrep single-file: ``12:content``
            bare = _BARE_LINE_RE.match(line)
            if bare and default_path:  # pragma: no cover - bare rows are normalized upstream
                try:
                    hits.append((default_path, int(bare.group(1)), bare.group(2)))
                except ValueError:
                    pass
            continue
        path_s, line_s, content = m.group(1), m.group(2), m.group(3)
        if path_s.lower().startswith("error"):
            continue
        # Reject pure-numeric "paths" unless no better match (line:content mis-parse)
        if path_s.isdigit():
            # ``str.isdigit()`` is broader than ``int()`` (e.g. superscripts),
            # so guard the conversion even though rg output is ASCII in practice.
            if default_path:
                try:
                    hits.append((default_path, int(path_s), f"{line_s}:{content}"))
                except ValueError:
                    pass
            continue
        line_no = int(line_s)
        hits.append((path_s, line_no, content))
    return hits


def unique_hit_paths(hits: list[tuple[str, int, str]], *, limit: int = 3) -> list[str]:
    seen: list[str] = []
    for path_s, _line, _content in hits:
        if path_s not in seen:
            seen.append(path_s)
        if len(seen) >= limit:
            break
    return seen


def enrich_search_hits(
    root: Path,
    hits: list[tuple[str, int, str]],
    *,
    mode: SearchContextMode = "snippet",
    max_files: int = 3,
    context_lines: int = 15,
    max_tokens: int = 2000,
) -> tuple[str, int, int]:
    """Return (snippet block, files_enriched, approx_tokens)."""
    if mode == "none" or not hits:
        return "", 0, 0

    from greedy_token.tokens import count_tokens

    paths = unique_hit_paths(hits, limit=max_files)
    # First hit line per file for snippet centering
    line_by_path: dict[str, int] = {}
    for path_s, line_no, _ in hits:
        if path_s not in line_by_path:
            line_by_path[path_s] = line_no

    blocks: list[str] = []
    used_tokens = 0
    files_done = 0
    for path_s in paths:
        file_path = Path(path_s)
        if not file_path.is_absolute():
            file_path = (root / path_s).resolve()
        else:
            file_path = file_path.resolve()
        try:
            file_path.relative_to(root.resolve())
        except ValueError:
            continue
        if not file_path.is_file():
            continue
        try:
            all_lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        rel = _format_rel(file_path, root)
        if mode == "file":
            body = "\n".join(all_lines)
            chunk = f"### {rel} (full file, {len(all_lines)} lines)\n{body}"
        else:
            center = line_by_path.get(path_s, 1)
            start = max(1, center - context_lines)
            end = min(len(all_lines), center + context_lines)
            slice_lines = all_lines[start - 1 : end]
            numbered = [f"{start + i:>5}|{line}" for i, line in enumerate(slice_lines)]
            chunk = (
                f"### {rel}:{center} (±{context_lines} lines, {start}-{end})\n"
                + "\n".join(numbered)
            )

        tok = count_tokens(chunk).tokens
        if used_tokens and used_tokens + tok > max_tokens:
            blocks.append(
                f"### … (stopped at token budget ~{max_tokens}; "
                f"skipped remaining files)"
            )
            break
        blocks.append(chunk)
        used_tokens += tok
        files_done += 1

    if not blocks:
        return "", 0, 0
    header = (
        f"--- enriched context ({mode}, {files_done} file(s), ~{used_tokens} tokens) ---"
    )
    return header + "\n\n" + "\n\n".join(blocks), files_done, used_tokens


def _finalize_search(
    *,
    header: str,
    body: str,
    engine: str,
    root: Path,
    context: SearchContextMode | None,
    default_path: str | None = None,
) -> SearchResult:
    body = normalize_hit_body(body, default_path=default_path)
    hits = parse_hit_lines(body, default_path=default_path)
    hit_paths = unique_hit_paths(hits, limit=10)
    text = f"{header}\n\n{body}" if body else header
    enriched_files = 0
    context_tokens = 0

    mode = context
    if mode is None:
        from greedy_token.settings import get_search_settings

        mode = get_search_settings(root).context

    if mode != "none" and hits:
        from greedy_token.settings import get_search_settings

        settings = get_search_settings(root)
        block, enriched_files, context_tokens = enrich_search_hits(
            root,
            hits,
            mode=mode,
            max_files=settings.max_snippet_files,
            context_lines=settings.context_lines,
            max_tokens=settings.max_context_tokens,
        )
        if block:
            text = f"{text}\n\n{block}"

    return SearchResult(
        text=text,
        engine=engine,
        hit_count=len(hits),
        enriched_files=enriched_files,
        context_tokens=context_tokens,
        hit_paths=hit_paths,
    )


def search_code(
    query: str,
    root: Path | None = None,
    *,
    path: str | None = None,
    limit: int = 50,
    context: SearchContextMode | None = None,
) -> SearchResult:
    root = (root or find_workspace_root()).resolve()
    query = query.strip()
    if not query:
        return SearchResult(text="Error: query is required.", engine="rg")

    path_detail: PathResolveResult | None = None
    resolved: Path | None = None
    if path:
        hint = path.strip()
        path_detail = resolve_search_path_detail(hint, root)
        if path_detail.path is None:
            return SearchResult(
                text=_path_resolve_error(hint, path_detail, root),
                engine="rg",
            )
        resolved = path_detail.path

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
                return _finalize_search(
                    header=f"Search: {query!r} in {scope}",
                    body=filtered,
                    engine="rg",
                    root=root,
                    context=context,
                    default_path=scope,
                )
        lines = _python_search_file(resolved, query, limit=limit)
        if lines:
            # python file scan returns ``path:line:content`` already
            body = "\n".join(lines)
            return _finalize_search(
                header=(
                    f"Search: {query!r} in {scope} [python]\n"
                    f"(rg not in PATH — python file scan)"
                ),
                body=body,
                engine="python",
                root=root,
                context=context,
                default_path=scope,
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
            return _finalize_search(
                header=f"Search: {query!r} in {scope}",
                body=filtered,
                engine="rg",
                root=root,
                context=context,
            )

    if resolved and resolved.is_dir():
        scope_dirs = [resolved]
        name_glob = None
        scope = str(resolved.relative_to(root) if resolved.is_relative_to(root) else resolved)
    else:
        scope_dirs = [root / p for p in DEFAULT_PATHS]
        name_glob = None
        scope = "workspace"
    lines = _python_search_tree(
        root,
        query,
        scope_dirs=scope_dirs,
        name_glob=name_glob,
        limit=limit,
    )
    if lines:
        note = "(rg not in PATH — python tree scan)" if not rg_bin else ""
        header = f"Search: {query!r} in {scope} [python]"
        if note:
            header = f"{header}\n{note}"
        return _finalize_search(
            header=header,
            body="\n".join(lines),
            engine="python",
            root=root,
            context=context,
        )

    return SearchResult(
        text=f"No matches for {query!r} in {scope}.",
        engine="rg" if rg_bin else "python",
    )
