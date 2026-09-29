from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pathspec import GitIgnoreSpec

from greedy_token.paths import find_workspace_root
from greedy_token.tool_output import filter_tool_output
from greedy_token.tool_paths import RG_TIMEOUT, resolve_rg

SearchContextMode = Literal["none", "snippet", "file"]

# ``path:line:content`` — the path may itself contain ":" (POSIX filenames),
# so the line-number anchor (a digit run between the last two colons of the
# path:line pair) is what identifies the split, not the first colon.
_HIT_LINE_RE = re.compile(r"^(.+?):(\d+):(.*)$")

DEFAULT_GLOBS = [
    "!.git/**",
    "!node_modules/**",
    "!build/**",
    "!.venv/**",
    # Agent-host internals — machinery, not content (all known hosts, not just
    # the configured one: devin hooks reuse the .cursor/hooks script store).
    "!.cursor/hooks/**",
    "!.claude/hooks/**",
    "!.devin/hooks/**",
]

DEFAULT_PATHS = ["."]


def search_scope_paths(root: Path) -> list[str]:
    """Visible top-level folders plus visible root-level files.

    Folder detection stays delegated to ``detect_search_paths`` (the scaffold
    contract is folders-only); the search scope additionally covers files
    sitting at the workspace root — ``README.md``, ``package.json`` — which a
    folders-only scope silently drops.  When no folders exist the ``["."]``
    fallback already covers root files, so nothing is appended.
    """
    from greedy_token.paths import detect_search_paths

    dirs = detect_search_paths(root)
    if dirs == ["."]:
        return dirs
    files = sorted(
        p.name
        for p in root.iterdir()
        if p.is_file() and not p.name.startswith(".") and _under_root(p, root)
    )
    return [d for d in dirs if _under_root(root / d, root)] + files

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
    return bool(parts) and parts[0] in search_scope_paths(root)


def _format_rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
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
    # _pick_unique already returned for a single match, so reaching here with a
    # non-empty list means len > 1 — ``if dir_matches`` is equivalent to ``> 1``.
    if dir_matches:
        return PathResolveResult(
            candidates=tuple(dir_matches[:8]),
            reason="ambiguous",
        )

    file_matches = _glob_name_matches(root, name, want_dir=False)
    picked = _pick_unique(file_matches, root)
    if picked is not None:
        return PathResolveResult(path=picked)
    if file_matches:
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


def _python_search_file(
    path: Path,
    query: str,
    *,
    limit: int,
    display_path: str | None = None,
) -> list[str]:
    try:
        # errors="replace" keeps non-UTF-8 files searchable instead of raising.
        # equivalent: encoding=None, a dropped encoding arg, and "UTF-8" all
        # decode with the same codec on supported dev hosts — the platform
        # default is UTF-8 and codec names are case-insensitive.
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"Error reading {path}: {exc}"]

    shown_path = display_path or str(path)
    hits: list[str] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if query not in line:
            continue
        display = line if len(line) <= 200 else line[:200] + "…"
        hits.append(f"{shown_path}:{line_no}:{display}")
        if len(hits) >= limit:
            break
    return hits


def _escape_literal_brackets(line: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(line):
        if ch == "[" and "]" not in line[i + 1 :]:
            backslashes = 0
            j = i - 1
            while j >= 0 and line[j] == "\\":
                backslashes += 1
                j -= 1
            out.append("\\[" if backslashes % 2 == 0 else "[")
        else:
            out.append(ch)
    return "".join(out)


def _load_ignore_specs(
    root: Path, scope_dirs: list[Path]
) -> list[tuple[str, GitIgnoreSpec]]:
    """Collect ``.ignore`` specs the fallback must honor, mirroring rg.

    The root ``.ignore`` applies workspace-wide; ``.ignore`` files in scope
    ancestors load like rg's parent-dir handling, and nested ``.ignore``
    files anchor to their own directory — deeper specs sort last so their
    rules win on conflict.  Files passed explicitly as scope operands bypass
    ignore rules, like rg.
    """
    ignore_files: set[Path] = set()
    if (root / ".ignore").is_file():
        ignore_files.add(root / ".ignore")
    for base in scope_dirs:
        if not base.is_dir():
            continue
        try:
            ancestor = root
            for part in base.relative_to(root).parts:
                ancestor = ancestor / part
                if (ancestor / ".ignore").is_file():
                    ignore_files.add(ancestor / ".ignore")
        except ValueError:
            pass
        for ignore in base.rglob(".ignore"):
            if any(
                part in SKIP_DIR_NAMES or part.startswith(".")
                for part in ignore.relative_to(base).parts[:-1]
            ):
                continue
            ignore_files.add(ignore)
    specs: list[tuple[str, GitIgnoreSpec]] = []
    for ignore in sorted(ignore_files, key=lambda p: len(p.parts)):
        try:
            base_dir = ignore.parent.relative_to(root).as_posix()
        except ValueError:
            base_dir = ""
        if base_dir == ".":
            base_dir = ""
        try:
            lines = ignore.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        specs.append(
            (base_dir, GitIgnoreSpec.from_lines(map(_escape_literal_brackets, lines)))
        )
    return specs


def _specs_include(rel: str, specs: list[tuple[str, GitIgnoreSpec]]) -> bool:
    """Last matching rule wins; deeper ``.ignore`` files sort later."""
    ignored = False
    for base_dir, spec in specs:
        if base_dir:
            if not rel.startswith(base_dir + "/"):
                continue
            local = rel[len(base_dir) + 1 :]
        else:
            local = rel
        result = spec.check_file(local)
        if result.include is not None:
            ignored = result.include
    return ignored


def _is_ignored(rel: str, specs: list[tuple[str, GitIgnoreSpec]]) -> bool:
    """Decide whether workspace-relative *rel* is ignored under *specs*.

    Git prunes excluded directories: once a directory is ignored its whole
    subtree is out of reach, so a negation can only re-include a path whose
    ancestors are all un-excluded.
    """
    parts = rel.split("/")
    for depth in range(1, len(parts)):
        ancestor = "/".join(parts[:depth])
        if _specs_include(f"{ancestor}/", specs):
            return True
    return _specs_include(rel, specs)


def _python_search_tree(
    root: Path,
    query: str,
    *,
    scope_dirs: list[Path],
    name_glob: str | None = None,
    limit: int,
) -> list[str]:
    hits: list[str] = []
    specs = _load_ignore_specs(root, scope_dirs)
    for base in scope_dirs:
        # Explicit scope files are operands — like rg they bypass skip and
        # .ignore rules. Directory contents are traversed with the rules on.
        explicit = base.is_file()
        if explicit:
            entries = [base]
        elif base.is_dir():
            entries = sorted(base.rglob("*"))
        else:
            continue
        for path in entries:
            if not path.is_file():
                continue
            # rg never follows symlinks discovered during traversal; a
            # lexically-inside path that resolves outside root escaped via a
            # symlinked ancestor and stays confined like rg's --no-follow.
            if not explicit and path.is_symlink():
                continue
            try:
                lexical_rel = path.relative_to(root)
            except ValueError:
                lexical_rel = None
            if (
                not explicit
                and lexical_rel is not None
                and not _under_root(path, root)
            ):
                continue
            try:
                # equivalent: path==base makes relative_to(base).parts == ()
                # too — the unconditional branch yields the same empty tuple.
                local_parts = path.relative_to(base).parts if path != base else ()
            except ValueError:
                local_parts = path.parts
            if not explicit and any(
                part in SKIP_DIR_NAMES or part.startswith(".")
                for part in local_parts
            ):
                continue
            if name_glob and not path.match(name_glob):
                continue
            rel = lexical_rel.as_posix() if lexical_rel is not None else str(path)
            if not explicit and specs and _is_ignored(rel, specs):
                continue
            for line_no, line in enumerate(
                # errors="replace" keeps non-UTF-8 files in the scan instead of
                # raising UnicodeDecodeError on binary/badly-encoded sources.
                # equivalent: encoding=None / dropped / "UTF-8" — same codec
                # resolution as in _python_search_file above.
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if query not in line:
                    continue
                display = line if len(line) <= 200 else line[:200] + "…"
                hits.append(f"{rel}:{line_no}:{display}")
                if len(hits) >= limit:
                    return hits
    return hits


def _run_rg(argv: tuple[str, ...], *, cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            list(argv),
            # equivalent: subprocess treats shell=None and a missing shell
            # argument exactly like shell=False.
            shell=False,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=RG_TIMEOUT,
        )
    except FileNotFoundError as exc:
        return 127, f"Error: ripgrep executable not found: {exc}"
    except OSError as exc:
        return 126, f"Error: cannot execute ripgrep: {exc}"
    except subprocess.TimeoutExpired:
        return 124, f"Error: ripgrep timed out after {RG_TIMEOUT}s"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def _rg_completed(code: int, output: str) -> bool:
    """True when rg finished a search (hits or miss), not a launch/timeout failure."""
    if "command not found" in output.lower():
        return False
    return code in (0, 1)


def _rg_not_runnable(code: int, output: str) -> bool:
    """True when python fallback is allowed because rg could not execute."""
    if "command not found" in output.lower():
        return True
    return code in (126, 127)


def _cap_hit_lines(
    body: str, limit: int, *, default_path: str | None = None
) -> str:
    """Keep at most *limit* hit lines; diagnostic rows pass through.

    ``rg --max-count N`` caps matches per file — the python fallback caps
    globally.  Truncating the displayed hit rows brings rg back to the same
    contract: *limit* bounds the total number of reported matches.
    """
    if limit <= 0:
        return ""
    kept: list[str] = []
    hits = 0
    for line in body.splitlines():
        stripped = line.strip()
        is_hit = bool(_HIT_LINE_RE.match(stripped)) or (
            default_path is not None and bool(_BARE_LINE_RE.match(stripped))
        )
        if is_hit:
            hits += 1
            if hits > limit:
                continue
        kept.append(line)
    return "\n".join(kept)


def _search_from_rg(
    *,
    query: str,
    scope: str,
    code: int,
    out: str,
    root: Path,
    context: SearchContextMode | None,
    default_path: str | None = None,
    limit: int | None = None,
    miss_suffix: str = "",
) -> SearchResult | None:
    """Interpret one rg invocation. ``None`` means the caller may python-fallback."""
    if _rg_completed(code, out):
        filtered = filter_tool_output(out)
        if limit is not None and filtered:
            filtered = _cap_hit_lines(filtered, limit, default_path=default_path)
        if filtered:
            return _finalize_search(
                header=f"Search: {query!r} in {scope}",
                body=filtered,
                engine="rg",
                root=root,
                context=context,
                default_path=default_path,
            )
        text = f"No matches for {query!r} in {scope}."
        if miss_suffix:
            text = f"{text}\n{miss_suffix}"
        return SearchResult(text=text, engine="rg")
    if _rg_not_runnable(code, out):
        return None
    filtered = filter_tool_output(out)
    text = (filtered or out).strip() or f"Error: ripgrep exited {code}."
    return SearchResult(text=text, engine="rg")


_BARE_LINE_RE = re.compile(r"^(\d+):(.*)$")


def normalize_hit_body(body: str, *, default_path: str | None = None) -> str:
    """Prefix bare ``line:content`` rows (rg single-file mode) with *default_path*."""
    if not default_path:
        return body
    out: list[str] = []
    for raw in body.splitlines():
        # splitlines() already dropped line terminators, so no further strip needed.
        line = raw
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
            # Bare ``12:content`` rows are already prefixed with default_path by
            # normalize_hit_body upstream, so any non-hit line here is narrative.
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


_TRUNCATION_NOTE = "… (truncated to token budget)"


def _fit_rows_to_budget(rows: list[str], token_budget: int) -> list[str]:
    """Largest leading slice of *rows* estimated to fit *token_budget*."""
    # equivalent: token_budget==0 also returns [] — the first row costs at
    # least one token plus a newline, so the loop below breaks with no rows.
    if token_budget <= 0:
        return []
    from greedy_token.tokens import count_tokens

    kept: list[str] = []
    spent = 0
    for row in rows:
        # +1 covers the newline joining the rows into the chunk body.
        spent += count_tokens(row).tokens + 1
        if spent > token_budget:
            break
        kept.append(row)
    return kept


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

    marker = (
        f"### … (stopped at token budget ~{max_tokens}; "
        f"skipped remaining files)"
    )
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
            # errors="replace" — hits may point at non-UTF-8 files (rg still
            # reports binary hits); strict decoding would raise here.
            # equivalent: encoding=None / dropped / "UTF-8" — same codec
            # resolution as in _python_search_tree above.
            all_lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        rel = _format_rel(file_path, root)
        # equivalent: both initializers are dead stores — non-file mode
        # reassigns anchor and start from the hit line below, and file mode
        # never reads them.
        snippet_anchor = 0
        snippet_start = 1
        if mode == "file":
            header = f"### {rel} (full file, {len(all_lines)} lines)"
            rows = all_lines
        else:
            # path_s always came from ``hits`` (via unique_hit_paths), so it is
            # guaranteed present in line_by_path — no default needed.
            center = line_by_path[path_s]
            start = max(1, center - context_lines)
            end = min(len(all_lines), center + context_lines)
            snippet_anchor = center
            snippet_start = start
            header = f"### {rel}:{center} (±{context_lines} lines, {start}-{end})"
            rows = [
                f"{start + i:>5}|{line}"
                for i, line in enumerate(all_lines[start - 1 : end])
            ]

        chunk = f"{header}\n" + "\n".join(rows)
        tok = count_tokens(chunk).tokens
        if used_tokens + tok > max_tokens:
            if used_tokens:
                blocks.append(marker)
                break
            # The very first file can exceed the whole budget: emit the largest
            # leading slice that fits instead of bypassing the limit.
            kept = _fit_rows_to_budget(
                rows, max_tokens - count_tokens(header + "\n").tokens
            )
            while kept:
                if mode != "file":
                    header = (
                        f"### {rel}:{snippet_anchor} (±{context_lines} lines, "
                        f"{snippet_start}-{snippet_start + len(kept) - 1})"
                    )
                chunk = f"{header}\n" + "\n".join([*kept, _TRUNCATION_NOTE])
                tok = count_tokens(chunk).tokens
                if tok <= max_tokens:
                    break
                kept = kept[:-1]
            if not kept:
                blocks.append(marker)
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

    # enrich_search_hits has its own ``mode == "none" or not hits`` guard, so a
    # bare ``if hits`` here is equivalent (mode=="none" yields an empty block).
    if hits:
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

    # equivalent: initial None sentinel is only truthiness-checked below and is
    # always reassigned before real use, so a falsy "" default routes identically.
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
        scope = resolved.relative_to(root).as_posix()
        if rg_bin:
            # "--" ends rg option parsing: the user query is a positional
            # pattern and must stay literal ("--version", "-l", "--files").
            argv = (
                str(rg_bin),
                "-n",
                "--max-columns",
                "200",
                "-F",
                "--max-count",
                str(limit),
                "--",
                query,
                scope,
            )
            code, out = _run_rg(argv, cwd=root)
            settled = _search_from_rg(
                query=query,
                scope=scope,
                code=code,
                out=out,
                root=root,
                context=context,
                default_path=scope,
                limit=limit,
                miss_suffix=(
                    "Try greedy_token_rag for docs/rag lookup, or search without path."
                ),
            )
            if settled is not None:
                return settled
        lines = _python_search_file(
            resolved,
            query,
            limit=limit,
            display_path=scope,
        )
        if lines:
            # Python file scan emits the same POSIX workspace-relative path as rg.
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
            )
        return SearchResult(
            text=(
                f"No matches for {query!r} in {scope}.\n"
                f"Try greedy_token_rag for docs/rag lookup, or search without path."
            ),
            engine="rg" if rg_bin else "python",
        )

    if rg_bin:
        argv = [
            str(rg_bin),
            "-n",
            "--max-columns",
            "200",
            "-F",
        ]
        for glob in DEFAULT_GLOBS:
            argv.extend(("-g", glob))
        # "--" ends rg option parsing: the user query is a positional pattern
        # and must stay literal even when it looks like a flag.
        argv.extend(("--max-count", str(limit), "--", query))
        if resolved and resolved.is_dir():
            # equivalent: resolve_search_path_detail only returns paths under
            # root, so is_relative_to(root) is always True here.
            rel = resolved.relative_to(root) if resolved.is_relative_to(root) else resolved
            scope = rel.as_posix()
            argv.append(scope)
        else:
            scope = "workspace"
            argv.extend(search_scope_paths(root))
        code, out = _run_rg(tuple(argv), cwd=root)
        settled = _search_from_rg(
            query=query,
            scope=scope,
            code=code,
            out=out,
            root=root,
            context=context,
            limit=limit,
        )
        if settled is not None:
            return settled

    if resolved and resolved.is_dir():
        scope_dirs = [resolved]
        # equivalent: same confinement — resolved always lies under root, so
        # the else branch is dead code.
        rel = resolved.relative_to(root) if resolved.is_relative_to(root) else resolved
        scope = rel.as_posix()
    else:
        scope_dirs = [root / p for p in search_scope_paths(root)]
        scope = "workspace"
    # search_code never filters by filename, so name_glob is always None here.
    lines = _python_search_tree(
        root,
        query,
        scope_dirs=scope_dirs,
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
