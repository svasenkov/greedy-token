"""Unit tests for code_search parse/enrich edge branches (fail_under=100)."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import allure
import greedy_token.code_search as cs

pytestmark = [
    allure.epic("Code search"),
    allure.parent_suite("Code search"),
    allure.feature("Parse and enrich"),
    allure.suite("Code search gaps"),
]


@allure.title("normalize_hit_body prefixes bare rows and passes through the rest")
def test_normalize_hit_body() -> None:
    body = "a.js:1:hit\n45:bare\nplain narrative line"
    out = cs.normalize_hit_body(body, default_path="a.js").splitlines()
    assert out[0] == "a.js:1:hit"
    assert out[1] == "a.js:45:bare"
    assert out[2] == "plain narrative line"
    assert cs.normalize_hit_body(body) == body


@allure.title("parse_hit_lines skips error paths and reparses numeric mis-splits")
def test_parse_hit_lines_variants() -> None:
    hits = cs.parse_hit_lines("error:1:oops\n12:34:content", default_path="d.js")
    assert ("d.js", 12, "34:content") in hits
    assert all(h[0] != "error" for h in hits)

    # bare row without default_path → not promoted to a hit
    assert cs.parse_hit_lines("50:xyz") == []

    # numeric mis-split without default_path → skipped, not appended
    assert cs.parse_hit_lines("12:34:content") == []

    # unicode-digit "path" (isdigit() True but int() raises) → ValueError swallowed
    assert "\u00b2".isdigit() and cs.parse_hit_lines("\u00b2:5:content", default_path="d.js") == []


@allure.title("enrich_search_hits returns empty on none mode or no hits")
def test_enrich_empty(tmp_path: Path) -> None:
    assert cs.enrich_search_hits(tmp_path, [], mode="snippet") == ("", 0, 0)
    assert cs.enrich_search_hits(tmp_path, [("a.js", 1, "x")], mode="none") == ("", 0, 0)


@allure.title("enrich_search_hits skips missing/unreadable files, supports full-file mode")
def test_enrich_file_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # missing file → skipped → no blocks
    assert cs.enrich_search_hits(tmp_path, [("nope.js", 1, "x")], mode="snippet") == ("", 0, 0)

    real = tmp_path / "real.js"
    real.write_text("line1\nline2\nline3\n", encoding="utf-8")
    block, files, toks = cs.enrich_search_hits(tmp_path, [("real.js", 1, "x")], mode="file")
    assert files == 1 and "full file" in block and toks > 0

    # unreadable file → OSError branch skipped
    orig_read = Path.read_text

    def boom_read(self, *a, **k):
        if self.name == "real.js":
            raise OSError("permission denied")
        return orig_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", boom_read)
    assert cs.enrich_search_hits(tmp_path, [("real.js", 1, "x")], mode="snippet") == ("", 0, 0)


@allure.title("enrich_search_hits stops at token budget across multiple files")
def test_enrich_token_budget(tmp_path: Path) -> None:
    for name in ("a.js", "b.js"):
        (tmp_path / name).write_text("\n".join(f"row{i}" for i in range(40)), encoding="utf-8")
    block, files, toks = cs.enrich_search_hits(
        tmp_path,
        [("a.js", 5, "x"), ("b.js", 5, "y")],
        mode="snippet",
        max_tokens=1,
        max_files=3,
    )
    assert files == 0 and "stopped at token budget" in block


@allure.title(".ignore specs: *, **, ?, char classes, comments, dir-only, negation")
def test_ignore_specs_glob_variants(tmp_path: Path) -> None:
    _write_ignore(
        tmp_path / ".ignore",
        "# a comment line\n"
        "\n"
        "*.log\n"  # `*` never crosses "/"
        "**/cache/**\n"  # `**` crosses "/"
        "a?.txt\n"  # `?`
        "c[ab].txt\n"  # closed char class
        "d[unclosed\n"  # unclosed `[` — gitignore keeps it a non-matching line
        "build/\n"  # dir-only: matches proper ancestors, never the file itself
        "!keep.log\n",  # negation re-includes
    )
    specs = cs._load_ignore_specs(tmp_path, [tmp_path])
    assert cs._is_ignored("x/app.log", specs) is True
    assert cs._is_ignored("keep.log", specs) is False
    assert cs._is_ignored("deep/a/cache/file", specs) is True
    assert cs._is_ignored("a1.txt", specs) is True
    assert cs._is_ignored("a12.txt", specs) is False
    assert cs._is_ignored("ca.txt", specs) is True
    assert cs._is_ignored("cc.txt", specs) is False
    assert cs._is_ignored("d[unclosed", specs) is True
    assert cs._is_ignored("dunclosed", specs) is False
    assert cs._is_ignored("build/out.txt", specs) is True
    # A file literally named "build" is not a directory → the dir-only rule
    # must not match it.
    assert cs._is_ignored("build", specs) is False


@allure.title("_escape_literal_brackets escapes only unescaped unclosed `[`")
def test_escape_literal_brackets() -> None:
    # An unclosed `[` is a literal in globset; pathspec would drop the line, so
    # it is rewritten as an escaped `[` — unless already `\`-escaped.
    assert cs._escape_literal_brackets("d[unclosed") == "d\\[unclosed"
    assert cs._escape_literal_brackets("a[b]c[d") == "a[b]c\\[d"
    assert cs._escape_literal_brackets("a\\[b") == "a\\[b"
    assert cs._escape_literal_brackets("a\\\\[b") == "a\\\\\\[b"
    assert cs._escape_literal_brackets("plain") == "plain"
    assert cs._escape_literal_brackets("c[ab].txt") == "c[ab].txt"


@allure.title("an unreadable .ignore is skipped, not fatal")
def test_load_ignore_specs_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_ignore(ws / ".ignore", "vendor/\n")
    real_read_text = Path.read_text

    def guarded(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == ".ignore":
            raise OSError("denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    assert cs._load_ignore_specs(ws, [ws]) == []
    assert cs._is_ignored("vendor/x.txt", []) is False


@allure.title("nested .ignore anchors to its dir; vendor/hidden .ignore files are skipped")
def test_ignore_specs_nested_and_vendor_skipped(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    base = ws / "proj"
    (base / "sub").mkdir(parents=True)
    (base / "node_modules").mkdir()
    (base / ".hidden").mkdir()
    _write_ignore(base / "sub" / ".ignore", "ignored.txt\ndocs/private.md\n")
    # Nested inside a skipped dir / a dot-dir: discovered by rglob but dropped.
    _write_ignore(base / "node_modules" / ".ignore", "*\n")
    _write_ignore(base / ".hidden" / ".ignore", "*\n")

    specs = cs._load_ignore_specs(ws, [base])
    assert [base_dir for base_dir, _ in specs] == ["proj/sub"]
    # A path outside the spec's base_dir can never match it.
    assert cs._is_ignored("proj/other/ignored.txt", specs) is False
    assert cs._is_ignored("proj/sub/ignored.txt", specs) is True
    # Anchored to the nested base_dir: only proj/sub/docs/private.md matches.
    assert cs._is_ignored("proj/sub/docs/private.md", specs) is True
    assert cs._is_ignored("proj/sub/x/docs/private.md", specs) is False
    # A file named exactly like the spec's base dir is not under its scope.
    assert cs._is_ignored("proj/sub", specs) is False

    # The same spec set drives the python tree scan: ignored hits disappear.
    (base / "sub" / "ignored.txt").write_text("NEEDLE\n", encoding="utf-8")
    (base / "sub" / "keep.txt").write_text("NEEDLE\n", encoding="utf-8")
    hits = cs._python_search_tree(ws, "NEEDLE", scope_dirs=[base], name_glob=None, limit=50)
    assert any("keep.txt" in h for h in hits)
    assert not any("ignored.txt" in h for h in hits)


@allure.title("an .ignore outside the workspace root still loads with a root anchor")
def test_load_ignore_specs_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    _write_ignore(outside / "sub" / ".ignore", "*.log\n")
    specs = cs._load_ignore_specs(root, [outside])
    # `ignore.parent.relative_to(root)` raised ValueError → base_dir "".
    assert [base_dir for base_dir, _ in specs] == [""]
    assert cs._is_ignored("anywhere/x.log", specs) is True


@allure.title("scope ancestors' .ignore files apply, like rg parent-dir handling")
def test_ignore_specs_scope_ancestors(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    deep = ws / "src" / "sub"
    deep.mkdir(parents=True)
    _write_ignore(ws / ".ignore", "*.gen\n")
    _write_ignore(ws / "src" / ".ignore", "draft.txt\n")
    specs = cs._load_ignore_specs(ws, [deep])
    assert cs._is_ignored("src/sub/a.gen", specs) is True
    assert cs._is_ignored("src/sub/draft.txt", specs) is True
    assert cs._is_ignored("src/sub/keep.txt", specs) is False


@allure.title("an excluded parent dir wins over a deeper negation (git pruning)")
def test_is_ignored_parent_exclusion_prunes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_ignore(ws / ".ignore", "src/\n!src/keep/\n")
    specs = cs._load_ignore_specs(ws, [ws])
    # rg/git never descend into an excluded dir — the negation cannot
    # re-include a path whose ancestor is excluded.
    assert cs._is_ignored("src/keep/x.txt", specs) is True
    assert cs._is_ignored("src/a.txt", specs) is True
    assert cs._is_ignored("other/a.txt", specs) is False


@allure.title("deeper .ignore rules win over shallower ones on conflict")
def test_ignore_specs_deeper_wins(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "a" / "b").mkdir(parents=True)
    _write_ignore(ws / ".ignore", "x.txt\n")
    _write_ignore(ws / "a" / ".ignore", "!x.txt\n")
    specs = cs._load_ignore_specs(ws, [ws])
    assert cs._is_ignored("a/x.txt", specs) is False
    assert cs._is_ignored("a/b/x.txt", specs) is False
    assert cs._is_ignored("x.txt", specs) is True


@allure.title(".ignore rg parity: leading anchor, zero-dir globstar, negated class")
def test_ignore_specs_rg_parity_edges(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "src" / "sub").mkdir(parents=True)
    _write_ignore(ws / ".ignore", "/root.txt\n**/gen.txt\n[!a].txt\n")
    specs = cs._load_ignore_specs(ws, [ws])
    assert cs._is_ignored("root.txt", specs) is True
    assert cs._is_ignored("src/root.txt", specs) is False
    assert cs._is_ignored("gen.txt", specs) is True
    assert cs._is_ignored("src/sub/gen.txt", specs) is True
    assert cs._is_ignored("src/sub/b.txt", specs) is True
    assert cs._is_ignored("src/sub/a.txt", specs) is False


@allure.title("_cap_hit_lines: limit<=0 empties the body; non-hit lines pass through")
def test_cap_hit_lines_edges() -> None:
    assert cs._cap_hit_lines("a.js:1:x", 0) == ""
    # rg diagnostics/context rows are not hit lines — they survive the cap.
    out = cs._cap_hit_lines("rg: warning line\na.js:1:x\na.js:2:y", 1)
    assert "rg: warning line" in out
    assert "a.js:1:x" in out
    assert "a.js:2:y" not in out


@allure.title("_fit_rows_to_budget returns the whole slice when everything fits")
def test_fit_rows_to_budget_all_fit() -> None:
    assert cs._fit_rows_to_budget(["tiny row", "another"], 1000) == ["tiny row", "another"]
    assert cs._fit_rows_to_budget(["x"], 0) == []


@allure.title("enrich trims the fitted first-file slice until the note also fits")
def test_enrich_first_file_trim_loop(tmp_path: Path) -> None:
    # The fitted slice plus the truncation note still exceeds the budget, so
    # the loop must drop rows one at a time (line 714) until the note fits.
    f = tmp_path / "f.py"
    f.write_text("lorem ipsum dolor sit amet\n" * 6, encoding="utf-8")
    block, files, toks = cs.enrich_search_hits(
        tmp_path, [("f.py", 1, "x")], mode="file", max_tokens=30, max_files=3
    )
    assert files == 1
    assert 0 < toks <= 30
    assert "truncated to token budget" in block


@allure.title("enrich_search_hits truncates a first file that exceeds the token budget")
def test_enrich_first_file_over_budget(tmp_path: Path) -> None:
    big = tmp_path / "big.md"
    big.write_text(
        "\n".join(f"row {i} " + "payload " * 20 for i in range(400)),
        encoding="utf-8",
    )

    with allure.step("file mode: leading slice fits, rest is marked truncated"):
        block, files, toks = cs.enrich_search_hits(
            tmp_path,
            [("big.md", 1, "x")],
            mode="file",
            max_tokens=500,
            max_files=3,
        )
        assert files == 1
        assert 0 < toks <= 500
        assert "truncated to token budget" in block
        assert "### big.md (full file, 400 lines)" in block

    with allure.step("snippet mode: header range reflects the emitted slice"):
        block2, files2, toks2 = cs.enrich_search_hits(
            tmp_path,
            [("big.md", 200, "x")],
            mode="snippet",
            max_tokens=300,
            context_lines=15,
        )
        assert files2 == 1
        assert 0 < toks2 <= 300
        assert "truncated to token budget" in block2
        assert "### big.md:200 (±15 lines, 185-" in block2


@allure.title("enrich_search_hits emits the budget marker when even a row cannot fit")
def test_enrich_first_file_unfittable(tmp_path: Path) -> None:
    (tmp_path / "a.js").write_text("row\n" * 10, encoding="utf-8")
    block, files, toks = cs.enrich_search_hits(
        tmp_path, [("a.js", 1, "x")], mode="snippet", max_tokens=1
    )
    assert files == 0
    assert toks == 0
    assert "stopped at token budget" in block


# --- Mutation kill-tests: resolve_search_path_detail / _path_resolve_error ---


@allure.title("resolve_search_path_detail: exact reason strings across every return site")
def test_resolve_detail_reason_strings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "ws"
    root.mkdir()

    with allure.step("empty hint → reason 'empty'"):
        assert cs.resolve_search_path_detail("   ", root).reason == "empty"
    with allure.step("absolute non-existent → reason 'not_found'"):
        assert cs.resolve_search_path_detail("/no/such/abs/zzz", root).reason == "not_found"
    with allure.step("bare name with no glob match → reason 'not_found'"):
        assert cs.resolve_search_path_detail("zzz-nomatch-bare", root).reason == "not_found"
    with allure.step("relative '..' has empty Path.name → reason 'not_found'"):
        assert cs.resolve_search_path_detail("..", root).reason == "not_found"
    with allure.step("absolute is_file() raising OSError → reason 'not_found'"):
        orig_is_file = Path.is_file

        def boom_is_file(self):  # type: ignore[no-untyped-def]
            if str(self) == "/abs/boom-isfile":
                raise OSError("boom")
            return orig_is_file(self)

        with monkeypatch.context() as m:
            m.setattr(Path, "is_file", boom_is_file)
            assert cs.resolve_search_path_detail("/abs/boom-isfile", root).reason == "not_found"

    with allure.step("absolute resolve() raising OSError → reason 'not_found'"):
        real = root / "real-abs.txt"
        real.write_text("x", encoding="utf-8")
        abs_real = str(real)
        orig_resolve = Path.resolve

        def boom_resolve(self, *a, **k):  # type: ignore[no-untyped-def]
            if str(self) == abs_real:
                raise OSError("boom")
            return orig_resolve(self, *a, **k)

        monkeypatch.setattr(Path, "resolve", boom_resolve)
        assert cs.resolve_search_path_detail(abs_real, root).reason == "not_found"


@allure.title("resolve_search_path_detail: ambiguous reason/candidates for dirs and files")
def test_resolve_detail_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()

    with allure.step("two non-unique dir matches → ambiguous with 2 candidates"):
        (root / "projects" / "amb").mkdir(parents=True)
        (root / "docs" / "amb").mkdir(parents=True)
        res = cs.resolve_search_path_detail("amb", root)
        assert res.path is None
        assert res.reason == "ambiguous"
        assert len(res.candidates) == 2

    with allure.step("exactly two file matches → ambiguous (kills > 1 → > 2)"):
        (root / "projects" / "ambf.txt").write_text("x", encoding="utf-8")
        (root / "docs" / "ambf.txt").write_text("x", encoding="utf-8")
        res_f = cs.resolve_search_path_detail("ambf.txt", root)
        assert res_f.reason == "ambiguous"
        assert len(res_f.candidates) == 2


@allure.title("resolve_search_path_detail prefers one default-scope match")
def test_resolve_detail_prefers_default_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ws"
    preferred = root / "projects" / "app" / "unique.txt"
    secondary = root / "misc" / "app" / "unique.txt"
    preferred.parent.mkdir(parents=True)
    secondary.parent.mkdir(parents=True)
    preferred.write_text("preferred\n", encoding="utf-8")
    secondary.write_text("secondary\n", encoding="utf-8")
    monkeypatch.setattr(cs, "search_scope_paths", lambda _root: ["projects"])
    result = cs.resolve_search_path_detail("unique.txt", root)
    assert result.path == preferred.resolve()


@allure.title("resolve_search_path_detail: candidate lists are capped at 8 (dirs and files)")
def test_resolve_detail_candidate_cap(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()

    with allure.step("9 non-unique dir matches → candidates capped at 8 (kills [:9])"):
        for i in range(9):
            (root / "projects" / f"d{i}" / "many").mkdir(parents=True)
        res = cs.resolve_search_path_detail("many", root)
        assert res.reason == "ambiguous"
        assert len(res.candidates) == 8

    with allure.step("9 non-unique file matches → candidates capped at 8 (kills [:9])"):
        for i in range(9):
            d = root / "docs" / f"f{i}"
            d.mkdir(parents=True)
            (d / "manyf.txt").write_text("x", encoding="utf-8")
        res_f = cs.resolve_search_path_detail("manyf.txt", root)
        assert res_f.reason == "ambiguous"
        assert len(res_f.candidates) == 8


@allure.title("_path_resolve_error: exact ambiguous message; '…' marker only at 8 candidates")
def test_path_resolve_error_more_marker(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    cands = tuple((root / "projects" / f"c{i}").resolve() for i in range(2))
    detail = cs.PathResolveResult(candidates=cands, reason="ambiguous")
    msg = cs._path_resolve_error("h", detail, root)
    listed = "\n".join(f"  - {cs._format_rel(c, root)}" for c in cands)
    expected = (
        f"Error: path 'h' is ambiguous under {root.name}. "
        f"Pass a path relative to the workspace root.\n"
        f"Candidates:\n{listed}"
    )
    with allure.step("with < 8 candidates the more-marker is empty (kills '' → 'XXXX')"):
        assert msg == expected
        assert "…" not in msg


# --- Mutation kill-tests: _python_search_tree ---


def _numbered_file(path: Path, n: int) -> None:
    path.write_text("\n".join(f"L{i}" for i in range(1, n + 1)) + "\n", encoding="utf-8")


@allure.title("_python_search_tree: skipped dir uses continue (not break) and 200-char boundary")
def test_python_search_tree_edges(tmp_path: Path) -> None:
    with allure.step("a skipped __pycache__ entry does not abort the whole scan"):
        base = tmp_path / "base"
        (base / "__pycache__").mkdir(parents=True)
        (base / "__pycache__" / "a.py").write_text("NEEDLE here\n", encoding="utf-8")
        (base / "zzz.py").write_text("NEEDLE here\n", encoding="utf-8")
        hits = cs._python_search_tree(
            tmp_path, "NEEDLE", scope_dirs=[base], name_glob=None, limit=50
        )
        assert any("zzz.py" in h for h in hits)  # break would skip zzz.py
        assert all("__pycache__" not in h for h in hits)

    with allure.step("a 200-char line is shown in full (kills <= 200 → < 200)"):
        base2 = tmp_path / "base2"
        base2.mkdir()
        line = "N" + "x" * 199  # exactly 200 chars, contains the query
        (base2 / "f.py").write_text(line + "\n", encoding="utf-8")
        hits2 = cs._python_search_tree(
            tmp_path, "N", scope_dirs=[base2], name_glob=None, limit=50
        )
        assert hits2 and hits2[0].endswith(line)
        assert "…" not in hits2[0]

    with allure.step("name_glob filters files by pattern"):
        base3 = tmp_path / "base3"
        base3.mkdir()
        (base3 / "keep.py").write_text("NEEDLE\n", encoding="utf-8")
        (base3 / "skip.txt").write_text("NEEDLE\n", encoding="utf-8")
        hits3 = cs._python_search_tree(
            tmp_path, "NEEDLE", scope_dirs=[base3], name_glob="*.py", limit=50
        )
        assert [h for h in hits3 if "keep.py" in h]
        assert not [h for h in hits3 if "skip.txt" in h]

    with allure.step("name_glob mismatch uses continue not break (a later match is found)"):
        base4 = tmp_path / "base4"
        base4.mkdir()
        # a_skip.txt sorts first and does NOT match; b_keep.py sorts later and matches.
        (base4 / "a_skip.txt").write_text("NEEDLE\n", encoding="utf-8")
        (base4 / "b_keep.py").write_text("NEEDLE\n", encoding="utf-8")
        hits4 = cs._python_search_tree(
            tmp_path, "NEEDLE", scope_dirs=[base4], name_glob="*.py", limit=50
        )
        # break on the first (non-matching) file would skip b_keep.py entirely
        assert any("b_keep.py" in h for h in hits4)
        assert not any("a_skip.txt" in h for h in hits4)


@allure.title("_python_search_file still matches inside a non-UTF-8 file")
def test_python_search_file_invalid_utf8(tmp_path: Path) -> None:
    """Kills the errors="replace" -> "strict" mutant: real trees contain
    non-UTF-8 files and the fallback must degrade, not crash."""
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"needle \xff\nother line\n")
    hits = cs._python_search_file(bad, "needle", limit=5)
    assert len(hits) == 1
    assert "needle" in hits[0]


@allure.title("_python_search_tree scans a tree containing a non-UTF-8 file")
def test_python_search_tree_invalid_utf8(tmp_path: Path) -> None:
    """Same strict-decode mutant for the tree scan: a binary file in the tree
    must not abort the scan or lose the sibling's hit."""
    base = tmp_path / "tree"
    base.mkdir()
    (base / "a_bad.bin").write_bytes(b"junk \xff\xfe\n")
    (base / "z_good.py").write_text("NEEDLE here\n", encoding="utf-8")
    hits = cs._python_search_tree(
        tmp_path, "NEEDLE", scope_dirs=[base], name_glob=None, limit=50
    )
    assert any("z_good.py" in h for h in hits)


@allure.title("enrich_search_hits survives a non-UTF-8 file in the hit list")
def test_enrich_invalid_utf8_hit(tmp_path: Path) -> None:
    """rg can hand enrich a binary-file hit; errors="replace" re-reads it
    instead of the strict mutant raising UnicodeDecodeError."""
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"needle \xff\nsecond line\n")
    block, files_done, _tokens = cs.enrich_search_hits(
        tmp_path, [(str(bad), 1, "needle")], mode="snippet"
    )
    assert files_done == 1
    assert "needle" in block


# --- Mutation kill-tests: _run_rg timeout kwarg ---


@allure.title("_run_rg passes the RG_TIMEOUT keyword to subprocess.run")
def test_run_rg_timeout_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    class _Proc:
        returncode = 0
        stdout = "out"
        stderr = ""

    def fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        seen.update(kw)
        return _Proc()

    monkeypatch.setattr(cs.subprocess, "run", fake_run)
    cs._run_rg(("echo", "x"), cwd=Path.cwd())
    assert seen.get("timeout") == cs.RG_TIMEOUT


@allure.title("_run_rg converts process launch failures to result codes")
def test_run_rg_launch_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    for error, expected in (
        (FileNotFoundError("missing"), 127),
        (OSError("exec"), 126),
    ):
        monkeypatch.setattr(
            cs.subprocess,
            "run",
            lambda *_args, _error=error, **_kwargs: (_ for _ in ()).throw(_error),
        )
        code, text = cs._run_rg(("rg", "x"), cwd=Path.cwd())
        assert code == expected
        assert "Error:" in text


# --- Mutation kill-tests: normalize_hit_body pass-through fidelity ---


@allure.title("normalize_hit_body preserves trailing content verbatim on pass-through lines")
def test_normalize_hit_body_passthrough() -> None:
    body = "keep spaces here  \nendsX"
    out = cs.normalize_hit_body(body, default_path="d.js").splitlines()
    assert out[0] == "keep spaces here  "  # trailing spaces preserved
    assert out[1] == "endsX"  # trailing 'X' preserved


# --- Mutation kill-tests: parse_hit_lines prefix skips and continue-not-break ---


@allure.title("parse_hit_lines: 'Search:'/'(' prefixed lines are skipped; real hits parse")
def test_parse_hit_lines_prefix_skips() -> None:
    with allure.step("'Search:'-prefixed hit-shaped line is skipped (kills case/verbatim/or→and)"):
        assert cs.parse_hit_lines("Search:9:zzz") == []
    with allure.step("'('-prefixed hit-shaped line is skipped (kills '(' verbatim)"):
        assert cs.parse_hit_lines("(a.js:5:hit") == []
    with allure.step("a genuine hit line still parses"):
        assert cs.parse_hit_lines("a.js:5:hit") == [("a.js", 5, "hit")]


@allure.title("parse_hit_lines: non-hit and numeric-mis-split lines use continue (not break)")
def test_parse_hit_lines_continue() -> None:
    with allure.step("a plain non-matching line does not abort parsing (kills continue → break)"):
        assert cs.parse_hit_lines("plain line no colon\na.js:5:hit") == [("a.js", 5, "hit")]
    with allure.step("numeric mis-split w/o default_path is skipped, later hit still found"):
        assert cs.parse_hit_lines("12:34:content\na.js:5:hit") == [("a.js", 5, "hit")]


# --- Mutation kill-tests: unique_hit_paths default limit ---


@allure.title("unique_hit_paths default limit is 3")
def test_unique_hit_paths_default_limit() -> None:
    hits = [("a", 1, ""), ("b", 1, ""), ("c", 1, ""), ("d", 1, "")]
    assert cs.unique_hit_paths(hits) == ["a", "b", "c"]


# --- Mutation kill-tests: enrich_search_hits (defaults, centering, math, joins, budget) ---


@allure.title("enrich_search_hits: duplicate paths keep the first hit line as center")
def test_enrich_duplicate_path_keeps_first_line(tmp_path: Path) -> None:
    _numbered_file(tmp_path / "r.js", 50)
    block, files, _ = cs.enrich_search_hits(
        tmp_path,
        [("r.js", 20, "first"), ("r.js", 40, "second")],
        mode="snippet",
        max_tokens=99999,
    )
    assert files == 1
    assert "### r.js:20 (±15 lines, 5-35)" in block
    assert "   20|L20" in block
    assert "   40|L40" not in block


@allure.title("enrich_search_hits: default mode/context_lines and snippet centering are exact")
def test_enrich_defaults_and_centering(tmp_path: Path) -> None:
    _numbered_file(tmp_path / "r.js", 50)
    block, files, toks = cs.enrich_search_hits(tmp_path, [("r.js", 20, "x")], max_tokens=99999)
    with allure.step("default mode is 'snippet' (header shows the mode verbatim)"):
        assert "(snippet," in block
    with allure.step("default context_lines is 15 → window 5-35 for a hit at line 20"):
        assert "### r.js:20 (±15 lines, 5-35)" in block
    with allure.step("first-hit line is used as center (kills 'not in' → 'in' and get(None,1))"):
        assert "   20|L20" in block
    with allure.step("slice offset and numbering are exact (kills start-1/±i mutants)"):
        assert "    5|L5" in block
        assert "    6|L6" in block
        assert "   35|L35" in block
    with allure.step("numbered rows and header/body are '\\n'/'\\n\\n' joined (kills XX-joins)"):
        assert "    5|L5\n    6|L6" in block
        header = f"--- enriched context (snippet, 1 file(s), ~{toks} tokens) ---"
        assert block.startswith(header + "\n\n### r.js:20")
        assert files == 1


@allure.title("enrich_search_hits: default max_files is 3")
def test_enrich_default_max_files(tmp_path: Path) -> None:
    for name in ("a.js", "b.js", "c.js", "d.js"):
        _numbered_file(tmp_path / name, 5)
    hits = [(n, 1, "x") for n in ("a.js", "b.js", "c.js", "d.js")]
    _, files, _ = cs.enrich_search_hits(tmp_path, hits, mode="snippet", max_tokens=99999)
    assert files == 3  # default max_files=3 → only 3 enriched


@allure.title("enrich_search_hits: max_files is threaded into unique_hit_paths")
def test_enrich_max_files_threaded(tmp_path: Path) -> None:
    for name in ("a.js", "b.js"):
        _numbered_file(tmp_path / name, 5)
    _, files, _ = cs.enrich_search_hits(
        tmp_path, [("a.js", 1, "x"), ("b.js", 1, "y")], mode="snippet",
        max_files=1, max_tokens=99999,
    )
    assert files == 1  # dropping the limit= would default to 3 and enrich 2


@allure.title("enrich_search_hits: 'none' mode returns empty even with an existing file hit")
def test_enrich_none_mode_existing_file(tmp_path: Path) -> None:
    _numbered_file(tmp_path / "r.js", 5)
    assert cs.enrich_search_hits(tmp_path, [("r.js", 1, "x")], mode="none") == ("", 0, 0)


@allure.title("enrich_search_hits: full-file mode emits exact body and rel header")
def test_enrich_file_mode_exact(tmp_path: Path) -> None:
    (tmp_path / "r.js").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    block, files, _ = cs.enrich_search_hits(tmp_path, [("r.js", 1, "x")], mode="file")
    assert files == 1
    assert "### r.js (full file, 3 lines)\nalpha\nbeta\ngamma" in block


@allure.title("enrich_search_hits: start clamps to 1 for a top-of-file hit (kills max(2,..))")
def test_enrich_top_of_file_clamp(tmp_path: Path) -> None:
    _numbered_file(tmp_path / "r.js", 50)
    block, _, _ = cs.enrich_search_hits(tmp_path, [("r.js", 1, "x")], max_tokens=99999)
    assert "### r.js:1 (±15 lines, 1-16)" in block


@allure.title("enrich_search_hits: skip branches use continue, not break")
def test_enrich_skip_continue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _numbered_file(tmp_path / "real.js", 10)

    with allure.step("outside-root path is skipped; a later valid file is still enriched"):
        block, files, _ = cs.enrich_search_hits(
            tmp_path, [("../outside.js", 1, "x"), ("real.js", 1, "y")],
            mode="snippet", max_tokens=99999,
        )
        assert files == 1 and "### real.js" in block

    with allure.step("missing (non-file) path is skipped; later valid file still enriched"):
        block2, files2, _ = cs.enrich_search_hits(
            tmp_path, [("nope-dir", 1, "x"), ("real.js", 1, "y")],
            mode="snippet", max_tokens=99999,
        )
        assert files2 == 1 and "### real.js" in block2

    with allure.step("unreadable (OSError) file is skipped; later valid file still enriched"):
        _numbered_file(tmp_path / "bad.js", 10)
        orig_read = Path.read_text

        def boom(self, *a, **k):  # type: ignore[no-untyped-def]
            if self.name == "bad.js":
                raise OSError("nope")
            return orig_read(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", boom)
        block3, files3, _ = cs.enrich_search_hits(
            tmp_path, [("bad.js", 1, "x"), ("real.js", 1, "y")],
            mode="snippet", max_tokens=99999,
        )
        assert files3 == 1 and "### real.js" in block3


@allure.title("enrich_search_hits: token accounting, budget boundary, and multi-block join")
def test_enrich_token_accounting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("a.js", "b.js"):
        _numbered_file(tmp_path / name, 5)
    hits = [("a.js", 1, "x"), ("b.js", 1, "y")]

    import greedy_token.tokens as tokens

    calls = {"n": 0}

    def fake_count(text):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return SimpleNamespace(tokens=1500 if calls["n"] == 1 else 500)

    with allure.step("used+tok exactly == budget → NOT stopped (kills > → >=); sums accumulate"):
        calls["n"] = 0
        monkeypatch.setattr(tokens, "count_tokens", fake_count)
        block, files, toks = cs.enrich_search_hits(
            tmp_path, hits, mode="snippet", max_files=3, max_tokens=2000
        )
        assert files == 2  # 1500 then 1500+500==2000 (not > 2000) → second added
        assert toks == 2000  # kills used_tokens = tok and files_done = 1
        assert "\n\n### b.js" in block  # two blocks joined by '\n\n'

    with allure.step("used+tok just over budget → stopped early (kills default 2001)"):
        calls["n"] = 0

        def fake_count2(text):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return SimpleNamespace(tokens=1500 if calls["n"] == 1 else 501)

        monkeypatch.setattr(tokens, "count_tokens", fake_count2)
        block2, files2, _ = cs.enrich_search_hits(
            tmp_path, hits, mode="snippet", max_files=3
        )
        assert files2 == 1  # 1500+501==2001 > default 2000 → stop
        assert "stopped at token budget" in block2


# --- Mutation kill-tests: _finalize_search ---


def _settings(**kw):
    from greedy_token.settings import SearchSettings

    base = dict(
        context="snippet", max_context_tokens=99999, max_snippet_files=3,
        context_lines=15, source="test",
    )
    base.update(kw)
    return SearchSettings(**base)


@allure.title("_finalize_search: default_path threading, hit_paths cap 10, zero context tokens")
def test_finalize_default_path_and_paths(tmp_path: Path) -> None:
    with allure.step("numeric mis-split only becomes a hit when default_path is threaded"):
        res = cs._finalize_search(
            header="H", body="12:34:content", engine="python", root=tmp_path,
            context="none", default_path="d.js",
        )
        assert res.hit_count == 1
        assert res.hit_paths == ["d.js"]
        assert res.context_tokens == 0  # kills context_tokens = None / 1
        assert res.enriched_files == 0

    with allure.step("hit_paths are capped at 10 (kills limit removed / limit 11)"):
        body = "\n".join(f"f{i}.js:1:x" for i in range(11))
        res2 = cs._finalize_search(
            header="H", body=body, engine="rg", root=tmp_path, context="none"
        )
        assert res2.hit_count == 11
        assert len(res2.hit_paths) == 10


@allure.title("_finalize_search: settings root + mode/max_files/context_lines threading into enrich")
def test_finalize_settings_wiring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _numbered_file(tmp_path / "r.js", 50)
    _numbered_file(tmp_path / "r2.js", 5)
    seen: list = []

    def fake_gs(root):  # type: ignore[no-untyped-def]
        seen.append(root)
        return _settings(context="snippet", max_snippet_files=1, context_lines=3)

    monkeypatch.setattr("greedy_token.settings.get_search_settings", fake_gs)
    res = cs._finalize_search(
        header="H", body="r.js:20:x\nr2.js:1:y", engine="rg", root=tmp_path, context=None
    )
    with allure.step("mode resolved from settings ('snippet'), not None"):
        assert "(snippet," in res.text
    with allure.step("real root threaded into get_search_settings (kills None)"):
        assert seen and all(s == tmp_path for s in seen)
    with allure.step("context_lines from settings (3) used (kills default 15)"):
        assert "±3 lines" in res.text
    with allure.step("max_snippet_files from settings (1) used (kills default 3)"):
        assert res.enriched_files == 1
    with allure.step("context_tokens is reported nonzero (kills dropped kwarg default 0)"):
        assert res.context_tokens > 0


@allure.title("_finalize_search: 'file' context threads mode into enrich (kills mode=None/dropped)")
def test_finalize_file_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "r.js").write_text("alpha\nbeta\n", encoding="utf-8")
    monkeypatch.setattr(
        "greedy_token.settings.get_search_settings", lambda root: _settings(context="file")
    )
    res = cs._finalize_search(
        header="H", body="r.js:1:x", engine="rg", root=tmp_path, context=None
    )
    assert "full file" in res.text


@allure.title("_finalize_search: max_tokens from settings drives the budget stop (kills dropped kwarg)")
def test_finalize_budget_kwarg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _numbered_file(tmp_path / "a.js", 40)
    _numbered_file(tmp_path / "b.js", 40)
    monkeypatch.setattr(
        "greedy_token.settings.get_search_settings",
        lambda root: _settings(context="snippet", max_snippet_files=3, max_context_tokens=1),
    )
    res = cs._finalize_search(
        header="H", body="a.js:5:x\nb.js:5:y", engine="rg", root=tmp_path, context=None
    )
    assert "stopped at token budget" in res.text


# --- Mutation kill-tests: search_code end-to-end ---


@allure.title("search_code: empty query → exact error text and engine 'rg'")
def test_search_code_empty_query_exact(minimal_workspace: Path) -> None:
    r = cs.search_code("   ", minimal_workspace)
    assert r.text == "Error: query is required."
    assert r.engine == "rg"


@allure.title("search_code: passed root is used, find_workspace_root not consulted (kills or→and)")
def test_search_code_root_or(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom():  # type: ignore[no-untyped-def]
        raise AssertionError("find_workspace_root must not be called when root is given")

    monkeypatch.setattr(cs, "find_workspace_root", boom)
    r = cs.search_code("baseUrl", minimal_workspace, path="sample.js", context="none")
    assert r.engine in ("rg", "python")


@allure.title("search_code: unresolvable path → engine 'rg' and error text")
def test_search_code_path_error_engine(minimal_workspace: Path) -> None:
    r = cs.search_code("baseUrl", minimal_workspace, path="no-such-file-xyz.js")
    assert r.engine == "rg"
    assert r.text.startswith("Error: path")


def _rg_present(monkeypatch: pytest.MonkeyPatch, canned: str) -> dict:
    seen: dict = {}
    monkeypatch.setattr(cs, "resolve_rg", lambda: "rg")

    def fake_run(argv, *, cwd):  # type: ignore[no-untyped-def]
        seen["argv"] = argv
        seen["cwd"] = cwd
        return (0, canned)

    monkeypatch.setattr(cs, "_run_rg", fake_run)
    return seen


@allure.title("search_code: workspace rg command is exact; no enrichment for context 'none'")
def test_search_code_workspace_cmd(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _rg_present(monkeypatch, "projects/sample.js:1:const baseUrl = 'x';")
    r = cs.search_code("baseUrl", minimal_workspace, path=None, limit=7, context="none")
    expected = ["rg", "-n", "--max-columns", "200", "-F"]
    for glob in cs.DEFAULT_GLOBS:
        expected.extend(("-g", glob))
    expected.extend(("--max-count", "7", "--", "baseUrl", *cs.search_scope_paths(minimal_workspace)))
    assert seen["argv"] == tuple(expected)
    assert seen["cwd"] == minimal_workspace
    assert r.engine == "rg"
    assert r.text.startswith("Search: 'baseUrl' in workspace")
    assert "enriched context" not in r.text  # kills context=None


@allure.title("search_code: directory-scoped rg command + scope header are exact")
def test_search_code_dir_cmd(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _rg_present(monkeypatch, "docs/x.md:1:hit here")
    r = cs.search_code("baseUrl", minimal_workspace, path="docs", context="none")
    expected = ["rg", "-n", "--max-columns", "200", "-F"]
    for glob in cs.DEFAULT_GLOBS:
        expected.extend(("-g", glob))
    expected.extend(("--max-count", "50", "--", "baseUrl", "docs"))
    assert seen["argv"] == tuple(expected)
    assert seen["cwd"] == minimal_workspace
    assert r.text.startswith("Search: 'baseUrl' in docs")


@allure.title("search_code: file-scoped rg header + default_path threading are exact")
def test_search_code_file_cmd(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _rg_present(monkeypatch, "1:const baseUrl = 'x';")  # bare row needs default_path
    r = cs.search_code("baseUrl", minimal_workspace, path="sample.js", context="none")
    assert r.text.startswith("Search: 'baseUrl' in projects/sample.js")
    assert r.hit_paths == ["projects/sample.js"]  # kills default_path=scope → None/dropped
    assert r.hit_count == 1
    assert "enriched context" not in r.text  # kills context=None


@allure.title("search_code: rg multi-file output is capped to the global limit")
def test_search_code_rg_global_limit_capped(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --max-count is per-file in rg; search_code must enforce the global cap.
    _rg_present(
        monkeypatch,
        "projects/a.txt:1:capme\nprojects/a.txt:2:capme\nprojects/b.txt:1:capme",
    )
    r = cs.search_code("capme", minimal_workspace, limit=2, context="none")
    assert r.engine == "rg"
    assert r.hit_count == 2
    hit_lines = [ln for ln in r.text.splitlines() if ln.endswith(":capme")]
    assert len(hit_lines) == 2


@allure.title("search_code: file-scoped rg output is capped to the global limit")
def test_search_code_rg_file_limit_capped(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rg_present(monkeypatch, "1:capme\n2:capme\n3:capme\n4:capme")
    r = cs.search_code("capme", minimal_workspace, path="sample.js", limit=3, context="none")
    assert r.hit_count == 3
    assert "4:capme" not in r.text


@allure.title("search_code: python global tree scan — engine/note/header/body exact")
def test_search_code_python_global_exact(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: None)
    r = cs.search_code("baseUrl", minimal_workspace, path=None, limit=5, context="none")
    assert r.engine == "python"
    assert r.text.startswith("Search: 'baseUrl' in workspace [python]")
    assert "(rg not in PATH — python tree scan)" in r.text.split("\n")
    assert "sample.js" in r.text
    assert r.hit_count >= 1
    assert "enriched context" not in r.text  # kills context=None


@allure.title("search_code: rg present + empty miss → No matches, python tree not used")
def test_search_code_rg_empty_miss_no_python_tree(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: "rg")
    monkeypatch.setattr(cs, "_run_rg", lambda argv, *, cwd: (1, ""))
    tree_calls = {"n": 0}
    real_tree = cs._python_search_tree

    def spy_tree(*args, **kwargs):  # type: ignore[no-untyped-def]
        tree_calls["n"] += 1
        return real_tree(*args, **kwargs)

    monkeypatch.setattr(cs, "_python_search_tree", spy_tree)
    r = cs.search_code("baseUrl", minimal_workspace, path=None, context="none")
    assert r.engine == "rg"
    assert r.text == "No matches for 'baseUrl' in workspace."
    assert "[python]" not in r.text
    assert tree_calls["n"] == 0


# --- Regression: rg option injection via query (code_search surface) ---


@allure.title("search_code: '--' ends rg option parsing — query stays a literal pattern")
@pytest.mark.parametrize("query", ["--version", "-l", "--files"])
def test_search_code_workspace_query_is_not_an_option(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    seen = _rg_present(monkeypatch, "projects/sample.js:1:hit")
    cs.search_code(query, minimal_workspace, path=None, context="none")
    argv = list(seen["argv"])
    sep = argv.index("--")
    assert argv[sep + 1] == query  # the pattern, not an rg flag
    assert query not in argv[:sep]  # never lands in the option slot
    # every rg option (--max-count, -g globs, …) stays before the separator
    assert argv[sep + 2 :] == list(cs.search_scope_paths(minimal_workspace))


@allure.title("search_code: file-scoped rg argv keeps '--' before the literal pattern")
def test_search_code_file_query_is_not_an_option(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _rg_present(monkeypatch, "1:hit")
    cs.search_code("--files", minimal_workspace, path="sample.js", context="none")
    argv = list(seen["argv"])
    sep = argv.index("--")
    assert argv[sep + 1] == "--files"
    assert argv[sep + 2 :] == ["projects/sample.js"]
    assert "--max-count" in argv[:sep]  # option did not slip past the separator


@pytest.mark.parametrize(
    ("code", "output", "completed", "not_runnable"),
    [
        (0, "a.py:1:x", True, False),
        (1, "", True, False),
        (124, "Error: ripgrep timed out after 30s", False, False),
        (2, "rg: error", False, False),
        (126, "Error: cannot execute ripgrep: x", False, True),
        (127, "Error: ripgrep executable not found: x", False, True),
        (1, "command not found: rg", False, True),
        (0, "rg: command not found", False, True),
    ],
)
@allure.title("rg status predicates: completed vs not-runnable")
def test_rg_status_predicates(
    code: int, output: str, completed: bool, not_runnable: bool
) -> None:
    assert cs._rg_completed(code, output) is completed
    assert cs._rg_not_runnable(code, output) is not_runnable


@allure.title("search_code: rg timeout returns error and does not python-scan")
def test_search_code_rg_timeout_no_python_tree(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: "rg")
    monkeypatch.setattr(
        cs, "_run_rg", lambda argv, *, cwd: (124, "Error: ripgrep timed out after 30s")
    )
    tree_calls = {"n": 0}

    def spy_tree(*args, **kwargs):  # type: ignore[no-untyped-def]
        tree_calls["n"] += 1
        return []

    monkeypatch.setattr(cs, "_python_search_tree", spy_tree)
    r = cs.search_code("baseUrl", minimal_workspace, path=None, context="none")
    assert r.engine == "rg"
    assert "timed out" in r.text
    assert tree_calls["n"] == 0


@allure.title("search_code: rg exit 2 returns error and does not python-scan")
def test_search_code_rg_error_no_python_tree(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: "rg")
    monkeypatch.setattr(cs, "_run_rg", lambda argv, *, cwd: (2, "rg: regex error"))
    tree_calls = {"n": 0}

    def spy_tree(*args, **kwargs):  # type: ignore[no-untyped-def]
        tree_calls["n"] += 1
        return []

    monkeypatch.setattr(cs, "_python_search_tree", spy_tree)
    r = cs.search_code("baseUrl", minimal_workspace, path=None, context="none")
    assert r.engine == "rg"
    assert "regex error" in r.text
    assert tree_calls["n"] == 0


@allure.title("_search_from_rg: empty rg error output uses the exited-N fallback text")
def test_search_from_rg_empty_error_text(tmp_path: Path) -> None:
    r = cs._search_from_rg(
        query="q",
        scope="workspace",
        code=2,
        out="",
        root=tmp_path,
        context="none",
    )
    assert r is not None
    assert r.engine == "rg"
    assert r.text == "Error: ripgrep exited 2."


@allure.title("search_code: rg exit 126 still python-fallbacks (not-runnable)")
def test_search_code_rg_oserror_python_fallback(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: "rg")
    monkeypatch.setattr(
        cs, "_run_rg", lambda argv, *, cwd: (126, "Error: cannot execute ripgrep: x")
    )
    r = cs.search_code("baseUrl", minimal_workspace, path=None, context="none")
    assert r.engine == "python"
    assert "baseUrl" in r.text


@allure.title("search_code: file-scoped rg miss does not python-scan the file")
def test_search_code_file_rg_empty_miss_no_python_file(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: "rg")
    monkeypatch.setattr(cs, "_run_rg", lambda argv, *, cwd: (1, ""))
    file_calls = {"n": 0}
    real_file = cs._python_search_file

    def spy_file(*args, **kwargs):  # type: ignore[no-untyped-def]
        file_calls["n"] += 1
        return real_file(*args, **kwargs)

    monkeypatch.setattr(cs, "_python_search_file", spy_file)
    r = cs.search_code("baseUrl", minimal_workspace, path="sample.js", context="none")
    assert r.engine == "rg"
    assert r.text.startswith("No matches for 'baseUrl' in projects/sample.js.")
    assert "Try greedy_token_rag" in r.text
    assert file_calls["n"] == 0


@allure.title("search_code: no-match final return engine is 'rg' when rg present, 'python' otherwise")
def test_search_code_no_match_engine(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with allure.step("rg present, nothing found anywhere → engine 'rg' + 'No matches' text"):
        with monkeypatch.context() as m:
            m.setattr(cs, "resolve_rg", lambda: "rg")
            m.setattr(cs, "_run_rg", lambda argv, *, cwd: (1, ""))
            r = cs.search_code("ZZZ-NOMATCH-QUERY", minimal_workspace, path=None, context="none")
            assert r.engine == "rg"
            assert r.text.startswith("No matches for")
    with allure.step("rg absent, nothing found → engine 'python'"):
        monkeypatch.setattr(cs, "resolve_rg", lambda: None)
        r2 = cs.search_code("ZZZ-NOMATCH-QUERY", minimal_workspace, path=None, context="none")
        assert r2.engine == "python"


@allure.title("search_code: scoped-file no-match return engine follows rg availability")
def test_search_code_file_no_match_engine(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: "rg")
    monkeypatch.setattr(cs, "_run_rg", lambda argv, *, cwd: (1, ""))
    r = cs.search_code("ZZZNOMATCH", minimal_workspace, path="sample.js", context="none")
    assert r.engine == "rg"
    assert r.text.startswith("No matches for 'ZZZNOMATCH' in projects/sample.js")


@allure.title("search_code: default limit is 50 (python file scan)")
def test_search_code_default_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: None)
    (tmp_path / "projects").mkdir(exist_ok=True)
    (tmp_path / "projects" / "big.py").write_text(
        "\n".join("baseUrl line" for _ in range(60)), encoding="utf-8"
    )
    r = cs.search_code("baseUrl", tmp_path, path="big.py", context="none")
    assert r.hit_count == 50  # kills default 51 and the body '\n'-join
    assert "XX" not in r.text  # body rows are newline-joined, not 'XX'-joined


# --- Mutation kill-tests: additional code_search branch/join gaps ---


@allure.title("_finalize_search: body with no parseable hits keeps counters at 0")
def test_finalize_no_hits_zero_counters(tmp_path: Path) -> None:
    with allure.step("narrative body → 0 hits → enriched_files/context_tokens stay 0"):
        res = cs._finalize_search(
            header="H", body="just narrative, no hits here", engine="rg",
            root=tmp_path, context="snippet",
        )
        assert res.hit_count == 0
        assert res.enriched_files == 0  # kills init → 1 / None
        assert res.context_tokens == 0  # kills init → 1 / None
        assert res.text == "H\n\njust narrative, no hits here"


@allure.title("parse_hit_lines: bare 'line:content' row becomes a hit only via default_path")
def test_parse_hit_lines_bare_row_threaded() -> None:
    with allure.step("default_path threads into normalize_hit_body to prefix the bare row"):
        hits = cs.parse_hit_lines("12:hello world", default_path="d.js")
        assert hits == [("d.js", 12, "hello world")]
    with allure.step("without default_path the bare row is narrative → dropped"):
        assert cs.parse_hit_lines("12:hello world") == []


@allure.title("resolve_search_path_detail: hint '.' under a non-existent root → 'not_found'")
def test_resolve_detail_dot_ghost_root(tmp_path: Path) -> None:
    with allure.step("root does not exist and Path('.').name is empty → not_found return site"):
        ghost = tmp_path / "ghost"  # never created
        res = cs.resolve_search_path_detail(".", ghost)
        assert res.path is None
        assert res.reason == "not_found"


@allure.title("_python_search_tree: a missing scope dir is skipped via continue (not break)")
def test_python_search_tree_missing_scope_continue(tmp_path: Path) -> None:
    with allure.step("first scope dir is missing; a later valid dir is still scanned"):
        valid = tmp_path / "valid"
        valid.mkdir()
        (valid / "f.py").write_text("NEEDLE here\n", encoding="utf-8")
        missing = tmp_path / "does-not-exist"  # not a dir
        hits = cs._python_search_tree(
            tmp_path, "NEEDLE", scope_dirs=[missing, valid], name_glob=None, limit=50
        )
        # break on the missing dir would abort before reaching `valid`
        assert any("f.py" in h for h in hits)


@allure.title("search_code: file-scoped rg 'command not found' output falls back to python scan")
def test_search_code_file_command_not_found_fallback(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: "rg")
    monkeypatch.setattr(
        cs,
        "_run_rg",
        lambda argv, *, cwd: (127, "executable not found: rg"),
    )
    r = cs.search_code("baseUrl", minimal_workspace, path="sample.js", context="none")
    with allure.step("'command not found' in rg output → skip rg return, use python scan"):
        assert r.engine == "python"
        assert "command not found" not in r.text.lower()
        assert r.hit_count == 1
        assert r.text.startswith("Search: 'baseUrl' in projects/sample.js [python]")
        # context='none' is threaded through (kills context=None → default 'snippet')
        assert "enriched context" not in r.text


@allure.title("search_code: scoped-file no-match with rg absent → engine 'python'")
def test_search_code_file_no_match_engine_python(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: None)
    r = cs.search_code("ZZZNOMATCH", minimal_workspace, path="sample.js", context="none")
    with allure.step("rg absent, no python file hits → engine 'python' (kills rg-branch)"):
        assert r.engine == "python"
        assert r.text.startswith("No matches for 'ZZZNOMATCH' in projects/sample.js")


@allure.title("search_code: dir-scoped python tree scan reports the real dir scope, not 'None'")
def test_search_code_dir_python_scope(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: None)
    r = cs.search_code("baseUrl", minimal_workspace, path="docs", context="none")
    with allure.step("scope is the real dir 'docs' (kills scope=str(None))"):
        assert r.engine == "python"
        assert r.text.startswith("Search: 'baseUrl' in docs [python]")
        assert r.hit_count >= 1


@allure.title("search_code: python tree scan newline-joins hit rows (kills 'XX' join)")
def test_search_code_python_tree_join(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: None)
    (tmp_path / "projects").mkdir(exist_ok=True)
    (tmp_path / "projects" / "multi.py").write_text(
        "baseUrl one\nbaseUrl two\nbaseUrl three\n", encoding="utf-8"
    )
    r = cs.search_code("baseUrl", tmp_path, path=None, context="none")
    with allure.step("three distinct hit rows parse individually; no 'XX' separator"):
        assert r.engine == "python"
        assert "XX" not in r.text  # rows are newline-joined, not 'XX'-joined
        multi_rows = [ln for ln in r.text.splitlines() if "multi.py" in ln]
        assert len(multi_rows) == 3  # 'XX' join would collapse them onto one line


@allure.title("MCP search omits hit summary when search result is empty")
def test_mcp_search_empty_result(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import greedy_token.mcp as mcp_mod

    monkeypatch.setattr(mcp_mod, "find_workspace_root", lambda: minimal_workspace)
    monkeypatch.setattr(
        mcp_mod,
        "search_code",
        lambda *args, **kwargs: cs.SearchResult(
            text="No matches",
            engine="python",
        ),
    )
    monkeypatch.setattr(
        mcp_mod,
        "wrap_mcp_response",
        lambda body, **kwargs: body,
    )
    assert mcp_mod.greedy_token_search("missing") == "No matches"


# --- mutation kill-tests: glob/ignore plumbing, rg interpretation, budgets ---


def _bounded(fn, *args, seconds=2, **kwargs):
    """Call fn(); TimeoutError if it does not return (infinite-loop mutants)."""
    box: dict[str, object] = {}

    def _run() -> None:
        try:
            box["result"] = fn(*args, **kwargs)
        except BaseException as exc:
            box["error"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TimeoutError("call did not terminate")
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["result"]


def _write_ignore(path: Path, body: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, bytes):
        path.write_bytes(body)
    else:
        path.write_text(body, encoding="utf-8")
    return path


@allure.title("_load_ignore_specs: invalid UTF-8 bytes are replaced, never fatal")
def test_load_ignore_specs_bad_encoding(minimal_workspace: Path) -> None:
    _write_ignore(minimal_workspace / ".ignore", b"vendor/\n\xff\xfe\n")
    specs = cs._load_ignore_specs(minimal_workspace, [minimal_workspace])
    # errors=None / dropped errors raise UnicodeDecodeError; "XXreplaceXX" and
    # "REPLACE" raise LookupError — only "replace" survives the bad bytes.
    assert cs._is_ignored("vendor/x.txt", specs) is True
    assert cs._is_ignored("src/x.txt", specs) is False


@allure.title("_load_ignore_specs: comments and blanks never become patterns")
def test_load_ignore_specs_comment_lines(minimal_workspace: Path) -> None:
    _write_ignore(minimal_workspace / ".ignore", "# note\n\n*.py\n")
    specs = cs._load_ignore_specs(minimal_workspace, [minimal_workspace])
    assert cs._is_ignored("a.py", specs) is True
    assert cs._is_ignored("note", specs) is False
    assert cs._is_ignored("a.txt", specs) is False


@allure.title("_load_ignore_specs: a file scope operand does not stop the scan")
def test_load_ignore_specs_non_dir_continues(minimal_workspace: Path) -> None:
    base = minimal_workspace / "pkg"
    _write_ignore(base / ".ignore", "vendor/\n")
    operand = minimal_workspace / "README-op.md"
    operand.write_text("x\n", encoding="utf-8")
    specs = cs._load_ignore_specs(minimal_workspace, [operand, base])
    # continue→break would drop the real dir's spec entirely.
    assert [base_dir for base_dir, _ in specs] == ["pkg"]


@allure.title("_load_ignore_specs: .ignore under a hidden ancestor is skipped")
def test_load_ignore_specs_hidden_ancestor(minimal_workspace: Path) -> None:
    base = minimal_workspace / "pkg"
    _write_ignore(base / "a" / ".hidden" / ".ignore", "vendor/\n")
    specs = cs._load_ignore_specs(minimal_workspace, [base])
    # parts[:+1] only inspects the first component and misses '.hidden'.
    assert specs == []


@allure.title("_cap_hit_lines: limit/bare/overflow semantics are exact")
def test_cap_hit_lines_semantics() -> None:
    with allure.step("limit 0 → empty (kills < 0)"):
        assert cs._cap_hit_lines("a:1:x", 0) == ""
    with allure.step("bare lines count only with default_path (kills `or`)"):
        body = "1: a\n2: b\n3: c"
        assert cs._cap_hit_lines(body, 1) == body
        assert cs._cap_hit_lines(body, 1, default_path="f") == "1: a"
    with allure.step("over-limit hits are skipped, narrative kept (kills break)"):
        body = "a:1:x\na:2:y\ntail line"
        assert cs._cap_hit_lines(body, 1) == "a:1:x\ntail line"


@allure.title("_search_from_rg: cap only runs with a real limit")
def test_search_from_rg_no_limit(minimal_workspace: Path) -> None:
    res = cs._search_from_rg(
        query="q", scope="s", code=0, out="a/f:1:q",
        root=minimal_workspace, context="none", limit=None,
    )
    # `or filtered` would call _cap_hit_lines(filtered, None) → TypeError.
    assert res is not None
    assert res.engine == "rg"
    assert res.hit_count == 1


@allure.title("_search_from_rg: error path prefers filtered output over raw")
def test_search_from_rg_error_filtered(minimal_workspace: Path) -> None:
    out = ".cursor/hooks/x:1:n\nreal:2:z"
    res = cs._search_from_rg(
        query="q", scope="s", code=2, out=out,
        root=minimal_workspace, context="none",
    )
    assert res is not None
    assert res.engine == "rg"
    # filtered=None and `filtered and out` mutants leak the agent-internal line.
    assert res.text == "real:2:z"


@allure.title("_fit_rows_to_budget: exact cumulative accounting")
def test_fit_rows_to_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    import greedy_token.tokens as tk

    monkeypatch.setattr(tk, "count_tokens", lambda s: SimpleNamespace(tokens=len(s)))
    with allure.step("budget 1 keeps a zero-token row (kills <=1)"):
        assert cs._fit_rows_to_budget([""], 1) == [""]
    with allure.step("spent starts at 0 (kills =1)"):
        assert cs._fit_rows_to_budget(["aa"], 3) == ["aa"]
    with allure.step("cumulative spend (kills `spent =` and `-=`)"):
        assert cs._fit_rows_to_budget(["a", "b", "c"], 3) == ["a"]
    with allure.step("+1 per row for newlines (kills -1/+2)"):
        assert cs._fit_rows_to_budget(["a", "b"], 3) == ["a"]
        assert cs._fit_rows_to_budget(["a", "b"], 4) == ["a", "b"]
    with allure.step("boundary is `>` not `>=`"):
        assert cs._fit_rows_to_budget(["a"], 2) == ["a"]


@allure.title("_finalize_search: empty body keeps the bare header")
def test_finalize_search_empty_body(minimal_workspace: Path) -> None:
    res = cs._finalize_search(
        header="H", body="", engine="rg", root=minimal_workspace, context="none"
    )
    # `or True` would glue a dangling separator onto the header.
    assert res.text == "H"


@allure.title("enrich_search_hits: truncated snippet keeps exact header range and join")
def test_enrich_snippet_truncated(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import greedy_token.tokens as tk

    monkeypatch.setattr(tk, "count_tokens", lambda s: SimpleNamespace(tokens=len(s)))
    (minimal_workspace / "k.py").write_text(
        "".join(f"l{i}\n" for i in range(1, 31)), encoding="utf-8"
    )
    # Full 5-row chunk is 79 tokens; 78 forces the over-budget path. The
    # rebuilt header is 29 chars; chunk(1) = 29+1+9+1+29 = 69 — the only row
    # that fits. Kills the range-end arithmetic and the join-literal mutants.
    block, done, used = cs.enrich_search_hits(
        minimal_workspace, [("k.py", 15, "q")],
        mode="snippet", max_files=1, context_lines=2, max_tokens=78,
    )
    expected = (
        "--- enriched context (snippet, 1 file(s), ~69 tokens) ---\n\n"
        "### k.py:15 (±2 lines, 13-13)\n"
        "   13|l13\n"
        "… (truncated to token budget)"
    )
    assert done == 1
    assert used == 69
    assert block == expected


@allure.title("enrich_search_hits: a chunk exactly at max_tokens is kept")
def test_enrich_snippet_exact_boundary(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import greedy_token.tokens as tk

    monkeypatch.setattr(tk, "count_tokens", lambda s: SimpleNamespace(tokens=len(s)))
    (minimal_workspace / "k.py").write_text(
        "".join(f"l{i}\n" for i in range(1, 31)), encoding="utf-8"
    )
    # chunk(1) costs exactly 69 — `tok <= max_tokens` keeps it; `<` drops to
    # the marker block instead.
    block, done, _ = cs.enrich_search_hits(
        minimal_workspace, [("k.py", 15, "q")],
        mode="snippet", max_files=1, context_lines=2, max_tokens=69,
    )
    assert done == 1
    assert "13-13" in block
    assert "stopped at token budget" not in block


@allure.title("enrich_search_hits: over-budget trimming always empties kept")
def test_enrich_snippet_empty_budget_bounded(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import greedy_token.tokens as tk

    monkeypatch.setattr(tk, "count_tokens", lambda s: SimpleNamespace(tokens=len(s)))
    (minimal_workspace / "k.py").write_text(
        "".join(f"l{i}\n" for i in range(1, 31)), encoding="utf-8"
    )
    # Even a single row exceeds 60 tokens. kept[:-1] empties the list and the
    # loop exits; `kept[:+1]` pins it at one row forever.
    block, done, _ = _bounded(
        cs.enrich_search_hits,
        minimal_workspace, [("k.py", 15, "q")],
        mode="snippet", max_files=1, context_lines=2, max_tokens=60,
    )
    assert done == 0
    assert "stopped at token budget" in block


@allure.title("resolve_search_path_detail: rooted hints resolve before name globs")
def test_resolve_detail_rooted_preferred(minimal_workspace: Path) -> None:
    (minimal_workspace / "a").mkdir()
    (minimal_workspace / "b").mkdir()
    (minimal_workspace / "a" / "f.txt").write_text("x\n", encoding="utf-8")
    (minimal_workspace / "b" / "f.txt").write_text("x\n", encoding="utf-8")
    detail = cs.resolve_search_path_detail("a/f.txt", minimal_workspace)
    # rooted=None would fall to the name glob and report ambiguous;
    # is_file-and-is_dir would do the same.
    assert detail.path == (minimal_workspace / "a" / "f.txt").resolve()
    assert detail.reason == ""


@allure.title("search_scope_paths: ['.'] fallback and dotfile filtering")
def test_search_scope_paths_fallback_and_dotfiles(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import greedy_token.paths as pth

    monkeypatch.setattr(pth, "detect_search_paths", lambda r: ["."])
    (minimal_workspace / "top.txt").write_text("x\n", encoding="utf-8")
    assert cs.search_scope_paths(minimal_workspace) == ["."]
    # Now real dirs: root files join the scope, dotfiles stay out.
    monkeypatch.setattr(pth, "detect_search_paths", lambda r: ["docs"])
    (minimal_workspace / ".hidden-f").write_text("x\n", encoding="utf-8")
    scope = cs.search_scope_paths(minimal_workspace)
    assert "top.txt" in scope
    assert "workspace-routes.yaml" in scope
    assert ".hidden-f" not in scope
    assert ".greedy-token.yaml" not in scope


def _rg_stub(monkeypatch: pytest.MonkeyPatch, code: int, out: str):
    monkeypatch.setattr(cs, "resolve_rg", lambda: Path("/usr/bin/rg"))
    monkeypatch.setattr(cs, "_run_rg", lambda argv, *, cwd: (code, out))


@allure.title("search_code file scope: exact no-match text with RAG hint and engine")
def test_search_code_file_no_match(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rg_stub(monkeypatch, 1, "")
    f = minimal_workspace / "solo.py"
    f.write_text("nothing here\n", encoding="utf-8")
    res = cs.search_code("zzz", minimal_workspace, path="solo.py", context="none")
    assert res.engine == "rg"  # kills and-False / "XXrgXX" / "RG"
    assert res.text == (
        "No matches for 'zzz' in solo.py.\n"
        "Try greedy_token_rag for docs/rag lookup, or search without path."
    )


@allure.title("search_code file scope: python scan shows the relative scope path")
def test_search_code_file_python_display_path(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: None)
    f = minimal_workspace / "solo.py"
    f.write_text("find me\n", encoding="utf-8")
    res = cs.search_code("find", minimal_workspace, path="solo.py", context="none")
    assert res.engine == "python"
    # display_path=None/dropped leaks the absolute path into hit rows.
    assert "solo.py:1:find me" in res.text
    assert str(f) + ":1:" not in res.text


@allure.title("search_code file scope: unrunnable rg still reports engine rg")
def test_search_code_file_engine_rg_unrunnable(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rg_stub(monkeypatch, 126, "cannot execute")
    f = minimal_workspace / "solo.py"
    f.write_text("nothing here\n", encoding="utf-8")
    res = cs.search_code("zzz", minimal_workspace, path="solo.py", context="none")
    assert res.engine == "rg"


@allure.title("search_code python fallback: note appears only without rg")
def test_search_code_python_note_only_without_rg(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (minimal_workspace / "docs" / "t.txt").write_text("find me\n", encoding="utf-8")
    # rg present but refuses to run → python fallback must NOT print the note.
    _rg_stub(monkeypatch, 126, "cannot execute")
    res = cs.search_code("find", minimal_workspace, path="docs", context="none")
    assert res.engine == "python"
    first = res.text.splitlines()[0]
    assert first == "Search: 'find' in docs [python]"
    assert "rg not in PATH" not in res.text
    assert "XXXX" not in res.text


@allure.title("search_code workspace: no-match result reports engine rg when rg ran")
def test_search_code_workspace_no_match_engine(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rg_stub(monkeypatch, 126, "cannot execute")
    monkeypatch.setattr(cs, "_python_search_tree", lambda *a, **k: [])
    res = cs.search_code("zzz", minimal_workspace, context="none")
    assert res.engine == "rg"


@allure.title("_cap_hit_lines honours zero and positive limits exactly")
def test_cap_hit_lines_limits() -> None:
    body = "foo\nsrc/a.py:12:hit one\nsrc/b.py:34:hit two"
    assert cs._cap_hit_lines(body, 0) == ""
    assert cs._cap_hit_lines(body, 1) == "foo\nsrc/a.py:12:hit one"


# --- Regression: colon-containing filenames in hit parsing/capping ---------


@allure.title("_cap_hit_lines counts colon-named files as hits exactly")
def test_cap_hit_lines_colon_paths() -> None:
    body = "module:one.txt:5:capme\nmodule:two.txt:9:capme\nrg: warning here"
    assert cs._cap_hit_lines(body, 1) == "module:one.txt:5:capme\nrg: warning here"
    assert cs._cap_hit_lines(body, 2) == body
    assert cs._cap_hit_lines(body, 0) == ""


@allure.title("parse_hit_lines splits path:line:content on the digit anchor")
def test_parse_hit_lines_colon_path() -> None:
    hits = cs.parse_hit_lines("module:one.txt:12:hit")
    assert hits == [("module:one.txt", 12, "hit")]
    hits2 = cs.parse_hit_lines("a:b:c:d.txt:3:x")
    assert hits2 == [("a:b:c:d.txt", 3, "x")]


@allure.title("search_code: rg hits in colon-named files cap to the global limit")
def test_search_code_colon_filename_limit(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rg_present(
        monkeypatch,
        "projects/module:one.txt:1:capme\nprojects/module:two.txt:1:capme",
    )
    r = cs.search_code("capme", minimal_workspace, limit=1, context="none")
    assert r.hit_count == 1
    assert r.hit_paths == ["projects/module:one.txt"]


# --- Regression: symlink escape + traversal confinement --------------------


@allure.title("search_scope_paths drops root-level entries that escape the workspace")
def test_search_scope_paths_excludes_escaping_symlinks(
    minimal_workspace: Path, tmp_path: Path
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-escape-target"
    outside.mkdir(exist_ok=True)
    (outside / "o.txt").write_text("x\n", encoding="utf-8")
    outside_file = tmp_path.parent / f"{tmp_path.name}-escape.txt"
    outside_file.write_text("x\n", encoding="utf-8")
    (minimal_workspace / "escape-dir").symlink_to(outside, target_is_directory=True)
    (minimal_workspace / "escape.txt").symlink_to(outside_file)
    (minimal_workspace / "alias-sample.js").symlink_to(
        minimal_workspace / "projects" / "sample.js"
    )
    scope = cs.search_scope_paths(minimal_workspace)
    assert "escape-dir" not in scope
    assert "escape.txt" not in scope
    assert "alias-sample.js" in scope
    assert "projects" in scope


@allure.title("_python_search_tree skips symlinked files like rg --no-follow")
def test_python_search_tree_skips_symlinked_files(tmp_path: Path) -> None:
    root = tmp_path / "root"
    scope = root / "src"
    scope.mkdir(parents=True)
    (scope / "real.txt").write_text("needle r\n", encoding="utf-8")
    (scope / "alias.txt").symlink_to(tmp_path / "peer.txt")
    (tmp_path / "peer.txt").write_text("needle o\n", encoding="utf-8")
    hits = cs._python_search_tree(
        root, "needle", scope_dirs=[scope], name_glob=None, limit=10
    )
    assert hits == ["src/real.txt:1:needle r"]


@allure.title("_python_search_tree confines a symlinked scope dir to the root")
def test_python_search_tree_symlinked_scope_confined(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "o.txt").write_text("needle out\n", encoding="utf-8")
    link = root / "linkdir"
    link.symlink_to(outside, target_is_directory=True)
    hits = cs._python_search_tree(
        root, "needle", scope_dirs=[link], name_glob=None, limit=10
    )
    assert hits == []


# --- Mutation kill-test: enrich_search_hits first-file prefix fitting ------


@allure.title("enrich_search_hits: oversized first file emits the fitting prefix + note")
def test_enrich_first_file_fit_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import greedy_token.tokens as tk

    monkeypatch.setattr(tk, "count_tokens", lambda s: SimpleNamespace(tokens=len(s)))
    rows = ["HEAD_ROW", "MARKER_ROW", "", "", ""] + [
        f"payload_{i:02d} " + "x" * 30 for i in range(10)
    ]
    (tmp_path / "f.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    # The file chunk exceeds the budget, so _fit_rows_to_budget picks the
    # largest leading slice and the while-loop converges onto the fitting
    # prefix — a wrong initial `kept` either over-emits the tail or collapses
    # into the "stopped at token budget" marker.
    block, done, _ = cs.enrich_search_hits(
        tmp_path, [("f.txt", 2, "x")], mode="file", max_files=1, max_tokens=82,
    )
    assert done == 1
    assert "… (truncated to token budget)" in block
    assert "stopped at token budget" not in block
    assert "HEAD_ROW" in block and "MARKER_ROW" in block
    assert "payload_09" not in block
