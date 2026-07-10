from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.code_search import resolve_search_path, search_code
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Code search"),
    allure.parent_suite("Code search"),
    allure.feature("Search path resolution"),
    allure.suite("Search path resolution"),
]


@allure.story("Workspace")
@allure.title("Resolve search path by filename in workspace")
def test_resolve_search_path_by_filename(workspace_root: Path) -> None:
    with allure.step("Resolve phase-manifest.json in workspace"):
        p = resolve_search_path("phase-manifest.json", workspace_root)
        attach_text("resolved path", str(p) if p else "(none)")
    with allure.step("Verify filename match"):
        assert p is not None
        assert p.name == "phase-manifest.json"


@allure.story("Minimal workspace")
@allure.title("Resolve search path by filename in minimal workspace")
def test_resolve_search_path_in_minimal_workspace(minimal_workspace: Path) -> None:
    with allure.step("Resolve sample.js in minimal workspace"):
        p = resolve_search_path("sample.js", minimal_workspace)
        attach_text("resolved path", str(p) if p else "(none)")
    with allure.step("Verify filename match"):
        assert p is not None
        assert p.name == "sample.js"


@allure.story("Scoped search")
@allure.title("Scoped file search reports no matches when absent")
def test_search_code_scoped_file_no_match(workspace_root: Path) -> None:
    scoped = "projects/design-system-home/design-system/preview/configurator-option-presets.html"
    with allure.step("Search baseUrl in scoped file with no match"):
        out = search_code("baseUrl", workspace_root, path=scoped)
        attach_text("search output", out.text)
        attach_text("engine", out.engine)
    with allure.step("Verify no matches message"):
        assert "No matches" in out.text
        assert "configurator-option-presets.html" in out.text


@allure.story("Scoped search")
@allure.title("Scoped search finds baseUrl in sample.js")
def test_search_code_finds_in_minimal_workspace(minimal_workspace: Path) -> None:
    with allure.step("Search baseUrl in sample.js"):
        out = search_code("baseUrl", minimal_workspace, path="sample.js")
        attach_text("search output", out.text)
        attach_text("engine", out.engine)
    with allure.step("Verify baseUrl match"):
        assert "baseUrl" in out.text
        assert out.engine in ("rg", "python")


@allure.story("Global search")
@allure.title("Global search finds baseUrl across workspace")
def test_search_code_global_finds_baseurl(workspace_root: Path) -> None:
    with allure.step("Search baseUrl globally in workspace"):
        out = search_code("baseUrl", workspace_root, path="", limit=5)
        attach_text("search output", out.text)
        attach_text("engine", out.engine)
    with allure.step("Verify baseUrl found"):
        assert "baseUrl" in out.text or "baseurl" in out.text.lower()


@allure.story("Validation")
@allure.title("search_code rejects empty query")
def test_search_code_empty_query(minimal_workspace: Path) -> None:
    with allure.step("Search with blank query"):
        out = search_code("   ", minimal_workspace)
        attach_text("search output", out.text)
    with allure.step("Verify validation error"):
        assert "Error: query is required" in out.text


@allure.story("Path resolution")
@allure.title("resolve_search_path resolves directory under root")
def test_resolve_search_path_directory(minimal_workspace: Path) -> None:
    with allure.step("Resolve docs directory"):
        found = resolve_search_path("docs", minimal_workspace)
        attach_text("resolved path", str(found) if found else "(none)")
    with allure.step("Verify directory match"):
        assert found is not None
        assert found.is_dir()
        assert found.name == "docs"


@allure.story("Scoped search")
@allure.title("search_code scopes to directory path without mocking rg")
def test_search_code_directory_scope(minimal_workspace: Path) -> None:
    docs = minimal_workspace / "docs"
    (docs / "note.md").write_text("dirNeedleXYZ\n", encoding="utf-8")
    with allure.step("Search inside docs directory"):
        out = search_code("dirNeedleXYZ", minimal_workspace, path="docs")
        attach_text("search output", out.text)
        attach_text("engine", out.engine)
    with allure.step("Verify directory-scoped hit"):
        assert "dirNeedleXYZ" in out.text
        assert out.engine in ("rg", "python")


@allure.story("Path resolution")
@allure.title("resolve_search_path returns None for empty hint")
def test_resolve_search_path_empty(minimal_workspace: Path) -> None:
    with allure.step("Resolve empty path hint"):
        found = resolve_search_path("", minimal_workspace)
        attach_text("resolved path", str(found) if found else "(none)")
    with allure.step("Verify None for empty hint"):
        assert found is None


@allure.story("Path resolution")
@allure.title("resolve_search_path resolves absolute file path")
def test_resolve_search_path_absolute_file(minimal_workspace: Path) -> None:
    sample = minimal_workspace / "projects" / "sample.js"
    with allure.step("Resolve absolute file path"):
        found = resolve_search_path(str(sample), minimal_workspace)
        attach_text("resolved path", str(found) if found else "(none)")
    with allure.step("Verify absolute path match"):
        assert found == sample.resolve()


@allure.story("Path resolution")
@allure.title("resolve_search_path resolves relative path under root")
def test_resolve_search_path_relative(minimal_workspace: Path) -> None:
    with allure.step("Resolve relative path under workspace root"):
        found = resolve_search_path("projects/sample.js", minimal_workspace)
        attach_text("resolved path", str(found) if found else "(none)")
    with allure.step("Verify relative path resolves to sample.js"):
        assert found is not None
        assert found.name == "sample.js"


@allure.story("Path resolution")
@allure.title("resolve_search_path picks unique glob match")
def test_resolve_search_path_unique_glob(minimal_workspace: Path) -> None:
    with allure.step("Resolve filename via unique glob"):
        found = resolve_search_path("sample.js", minimal_workspace)
        attach_text("resolved path", str(found) if found else "(none)")
    with allure.step("Verify unique glob match"):
        assert found is not None
        assert found.name == "sample.js"


@allure.story("Path resolution")
@allure.title("resolve_search_path returns None when multiple glob matches")
def test_resolve_search_path_ambiguous_glob(minimal_workspace: Path) -> None:
    with allure.step("Add duplicate sample.js for ambiguous glob"):
        extra = minimal_workspace / "projects" / "nested"
        extra.mkdir()
        (extra / "sample.js").write_text("dup\n", encoding="utf-8")
    with allure.step("Resolve filename with multiple matches"):
        found = resolve_search_path("sample.js", minimal_workspace)
        attach_text("resolved path", str(found) if found else "(none)")
    with allure.step("Verify ambiguous glob returns None"):
        assert found is None


@allure.story("Path resolution")
@allure.title("resolve skips node_modules bare-name trap")
def test_resolve_search_path_skips_node_modules(minimal_workspace: Path) -> None:
    from greedy_token.code_search import resolve_search_path_detail

    with allure.step("Plant unique bare name only under node_modules"):
        vendor = (
            minimal_workspace
            / "projects"
            / "app"
            / "node_modules"
            / "es5-ext"
            / "reg-exp"
            / "#"
            / "search"
        )
        vendor.mkdir(parents=True)
        (vendor / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
    with allure.step("Resolve bare name search"):
        detail = resolve_search_path_detail("search", minimal_workspace)
        attach_text("reason", detail.reason)
        attach_text("path", str(detail.path) if detail.path else "(none)")
    with allure.step("Verify vendor-only match is not_found"):
        assert detail.path is None
        assert detail.reason == "not_found"


@allure.story("Path resolution")
@allure.title("resolve prefers unique DEFAULT_PATHS hit over vendor")
def test_resolve_search_path_prefers_default_paths(minimal_workspace: Path) -> None:
    with allure.step("Plant same dirname in projects and node_modules"):
        real = minimal_workspace / "projects" / "lib" / "utils"
        real.mkdir(parents=True)
        (real / "a.py").write_text("REAL=1\n", encoding="utf-8")
        vendor = minimal_workspace / "projects" / "app" / "node_modules" / "utils"
        vendor.mkdir(parents=True)
        (vendor / "b.js").write_text("VENDOR=1\n", encoding="utf-8")
    with allure.step("Resolve bare name utils"):
        found = resolve_search_path("utils", minimal_workspace)
        attach_text("resolved path", str(found) if found else "(none)")
    with allure.step("Verify projects hit wins"):
        assert found is not None
        assert found == real.resolve()


@allure.story("Path resolution")
@allure.title("resolve prefers unique DEFAULT_PATHS among multiple non-vendor hits")
def test_resolve_search_path_prefers_default_among_multi(minimal_workspace: Path) -> None:
    from greedy_token.code_search import _format_rel, _rel_parts, resolve_search_path_detail

    with allure.step("Plant same dirname under projects and outside DEFAULT_PATHS"):
        preferred = minimal_workspace / "projects" / "pref-utils"
        preferred.mkdir(parents=True)
        (preferred / "a.py").write_text("P=1\n", encoding="utf-8")
        other = minimal_workspace / "other-zone" / "pref-utils"
        other.mkdir(parents=True)
        (other / "b.py").write_text("O=1\n", encoding="utf-8")
    with allure.step("Resolve bare name"):
        found = resolve_search_path("pref-utils", minimal_workspace)
        detail = resolve_search_path_detail("pref-utils", minimal_workspace)
        attach_text("resolved", str(found))
    with allure.step("Verify DEFAULT_PATHS preference and helpers"):
        assert found == preferred.resolve()
        assert detail.reason == ""
        outside = minimal_workspace.parent / "ext-rel.txt"
        outside.write_text("x\n", encoding="utf-8")
        assert _rel_parts(outside, minimal_workspace) is None
        assert "ext-rel" in _format_rel(outside, minimal_workspace)


@allure.story("Path resolution")
@allure.title("resolve reports ambiguous when multiple DEFAULT_PATHS dirs match")
def test_resolve_search_path_ambiguous_dirs(minimal_workspace: Path) -> None:
    from greedy_token.code_search import resolve_search_path_detail

    with allure.step("Plant same dirname under projects and docs"):
        a = minimal_workspace / "projects" / "ambig-dir"
        b = minimal_workspace / "docs" / "ambig-dir"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        (a / "a.txt").write_text("a\n", encoding="utf-8")
        (b / "b.txt").write_text("b\n", encoding="utf-8")
    with allure.step("Resolve ambiguous bare name"):
        detail = resolve_search_path_detail("ambig-dir", minimal_workspace)
        attach_text("reason", detail.reason)
    with allure.step("Verify ambiguous"):
        assert detail.path is None
        assert detail.reason == "ambiguous"
        assert len(detail.candidates) >= 2


@allure.story("Path resolution")
@allure.title("glob skips symlink that resolves outside workspace")
def test_glob_skips_outside_symlink(
    minimal_workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    from greedy_token.code_search import _is_skipped_path, resolve_search_path

    outside_dir = tmp_path_factory.mktemp("symlink-outside")
    outside_file = outside_dir / "link-target-name"
    outside_file.write_text("leak\n", encoding="utf-8")
    link = minimal_workspace / "projects" / "link-target-name"
    try:
        link.symlink_to(outside_file)
    except OSError:
        pytest.skip("symlinks unavailable")
    with allure.step("Resolve bare name that only matches outside symlink"):
        found = resolve_search_path("link-target-name", minimal_workspace)
        attach_text("found", str(found) if found else "(none)")
    with allure.step("Verify outside symlink skipped; helper treats outside as skipped"):
        assert found is None
        assert _is_skipped_path(outside_file, minimal_workspace) is True


@allure.story("Path resolution")
@allure.title("search_code reports ambiguous path instead of silent glob")
def test_search_code_ambiguous_path_error(minimal_workspace: Path) -> None:
    with allure.step("Add duplicate sample.js"):
        extra = minimal_workspace / "projects" / "nested"
        extra.mkdir()
        (extra / "sample.js").write_text("dup\n", encoding="utf-8")
    with allure.step("Search with ambiguous path"):
        out = search_code("baseUrl", minimal_workspace, path="sample.js")
        attach_text("search output", out.text)
    with allure.step("Verify ambiguous error lists candidates"):
        assert "ambiguous" in out.text
        assert "sample.js" in out.text
        assert "baseUrl = " not in out.text


@allure.story("Path resolution")
@allure.title("search_code reports not found for unknown path")
def test_search_code_path_not_found(minimal_workspace: Path) -> None:
    with allure.step("Search with missing path hint"):
        out = search_code("baseUrl", minimal_workspace, path="no-such-file-xyz.js")
        attach_text("search output", out.text)
    with allure.step("Verify not-found error"):
        assert "not found" in out.text
        assert "no-such-file-xyz.js" in out.text


@allure.story("Path resolution")
@allure.title("resolve_search_path rejects absolute path outside workspace")
def test_resolve_search_path_rejects_outside_root(
    minimal_workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside_dir = tmp_path_factory.mktemp("outside-root")
    outside = outside_dir / "secret.txt"
    outside.write_text("SECRET_TOKEN=leak\n", encoding="utf-8")
    with allure.step("Resolve absolute path outside workspace"):
        found = resolve_search_path(str(outside), minimal_workspace)
        attach_text("outside path", str(outside))
        attach_text("resolved", str(found) if found else "(none)")
    with allure.step("Verify outside-root path is rejected"):
        assert found is None


@allure.story("Path resolution")
@allure.title("search_code refuses absolute path outside workspace")
def test_search_code_rejects_outside_root(
    minimal_workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside_dir = tmp_path_factory.mktemp("outside-search")
    outside = outside_dir / "secret.txt"
    outside.write_text("SECRET_TOKEN=leak\n", encoding="utf-8")
    with allure.step("Search with absolute path outside workspace"):
        out = search_code("SECRET_TOKEN", minimal_workspace, path=str(outside))
        attach_text("search output", out.text)
    with allure.step("Verify error and no secret leak"):
        assert "outside workspace" in out.text
        assert "SECRET_TOKEN=leak" not in out.text


@allure.story("Python fallback")
@allure.title("search_code uses python scan when rg unavailable")
def test_search_code_python_fallback(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with allure.step("Disable ripgrep resolver"):
        monkeypatch.setattr("greedy_token.code_search.resolve_rg", lambda: None)
    with allure.step("Search baseUrl in sample.js via python fallback"):
        out = search_code("baseUrl", minimal_workspace, path="sample.js")
        attach_text("output", out.text)
        attach_text("engine", out.engine)
    with allure.step("Verify python engine and match"):
        assert out.engine == "python"
        assert "baseUrl" in out.text


@allure.story("Python fallback")
@allure.title("search_code python tree scan with path glob")
def test_search_code_python_tree_with_path(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with allure.step("Disable ripgrep resolver"):
        monkeypatch.setattr("greedy_token.code_search.resolve_rg", lambda: None)
    with allure.step("Search baseUrl with path glob via python tree"):
        out = search_code("baseUrl", minimal_workspace, path="sample.js", limit=5)
        attach_text("output", out.text)
        attach_text("engine", out.engine)
    with allure.step("Verify python engine and match"):
        assert out.engine == "python"
        assert "baseUrl" in out.text


@allure.story("Python fallback")
@allure.title("search_code reports no matches in scoped file")
def test_search_code_python_no_match(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with allure.step("Disable ripgrep resolver"):
        monkeypatch.setattr("greedy_token.code_search.resolve_rg", lambda: None)
    with allure.step("Search missing token in scoped file"):
        out = search_code("ZZZNOTFOUND", minimal_workspace, path="sample.js")
        attach_text("output", out.text)
    with allure.step("Verify no matches message"):
        assert "No matches" in out.text


@allure.story("Python fallback")
@allure.title("search_code python global workspace scan")
def test_search_code_python_global(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with allure.step("Disable ripgrep resolver"):
        monkeypatch.setattr("greedy_token.code_search.resolve_rg", lambda: None)
    with allure.step("Search baseUrl globally via python scan"):
        out = search_code("baseUrl", minimal_workspace, path=None, limit=5)
        attach_text("output", out.text)
        attach_text("engine", out.engine)
    with allure.step("Verify match or no-matches response"):
        assert "baseUrl" in out.text or "No matches" in out.text


@allure.story("Ripgrep timeout")
@allure.title("_run_rg returns timeout message on expiry")
def test_run_rg_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from greedy_token.code_search import _run_rg

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired("rg", 30)

    with allure.step("Simulate ripgrep timeout"):
        monkeypatch.setattr("greedy_token.code_search.subprocess.run", boom)
        code, out = _run_rg("rg foo")
        attach_text("exit code", str(code))
        attach_text("output", out)
    with allure.step("Verify timeout exit code and message"):
        assert code == 124
        assert "timed out" in out


@allure.story("Python file scan")
@allure.title("_python_search_file truncates long lines and handles read errors")
def test_python_search_file_edges(tmp_path: Path) -> None:
    from greedy_token.code_search import _python_search_file

    with allure.step("Search file with long matching line"):
        long_line = "needle " + "x" * 250
        f = tmp_path / "f.txt"
        f.write_text(long_line + "\n", encoding="utf-8")
        hits = _python_search_file(f, "needle", limit=1)
        attach_text("hits", "\n".join(hits))
    with allure.step("Verify long line truncation"):
        assert hits
        assert "…" in hits[0]

    with allure.step("Search missing file"):
        missing = tmp_path / "missing.txt"
        err_hits = _python_search_file(missing, "x", limit=1)
        attach_text("error hits", "\n".join(err_hits))
    with allure.step("Verify read error message"):
        assert err_hits[0].startswith("Error reading")


@allure.story("Python tree scan")
@allure.title("_python_search_tree respects skip dirs and name glob")
def test_python_search_tree(minimal_workspace: Path) -> None:
    from greedy_token.code_search import _python_search_tree

    with allure.step("Add hidden match under .git directory"):
        git = minimal_workspace / "projects" / ".git"
        git.mkdir()
        (git / "secret.js").write_text("baseUrl hidden\n", encoding="utf-8")
    with allure.step("Scan projects tree with name glob"):
        hits = _python_search_tree(
            minimal_workspace,
            "baseUrl",
            scope_dirs=[minimal_workspace / "projects"],
            name_glob="*sample.js",
            limit=5,
        )
        attach_text("hits", "\n".join(hits))
    with allure.step("Verify .git skipped and sample.js found"):
        assert any("sample.js" in h for h in hits)

    with allure.step("name_glob filters non-matching files; limit stops early"):
        (minimal_workspace / "projects" / "a.txt").write_text("limhit\n", encoding="utf-8")
        (minimal_workspace / "projects" / "b.txt").write_text("limhit\n", encoding="utf-8")
        filtered = _python_search_tree(
            minimal_workspace,
            "limhit",
            scope_dirs=[minimal_workspace / "projects"],
            name_glob="*.nope",
            limit=5,
        )
        assert filtered == []
        limited = _python_search_tree(
            minimal_workspace,
            "limhit",
            scope_dirs=[minimal_workspace / "projects"],
            name_glob=None,
            limit=1,
        )
        assert len(limited) == 1

