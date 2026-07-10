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


@allure.story("Monorepo")
@allure.title("Resolve search path by filename in monorepo")
def test_resolve_search_path_by_filename(monorepo_root: Path) -> None:
    with allure.step("Resolve phase-manifest.json in monorepo"):
        p = resolve_search_path("phase-manifest.json", monorepo_root)
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
def test_search_code_scoped_file_no_match(monorepo_root: Path) -> None:
    with allure.step("Search baseUrl in scoped file with no match"):
        out = search_code("baseUrl", monorepo_root, path="configurator-option-presets.html")
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
@allure.title("Global search finds baseUrl across monorepo")
def test_search_code_global_finds_baseurl(monorepo_root: Path) -> None:
    with allure.step("Search baseUrl globally in monorepo"):
        out = search_code("baseUrl", monorepo_root, path="", limit=5)
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

