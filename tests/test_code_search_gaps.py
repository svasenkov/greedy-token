"""Unit tests for code_search parse/enrich edge branches (fail_under=100)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import allure
import pytest

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
    assert files == 1 and "stopped at token budget" in block


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

        monkeypatch.setattr(Path, "is_file", boom_is_file)
        assert cs.resolve_search_path_detail("/abs/boom-isfile", root).reason == "not_found"
        monkeypatch.undo()

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
    cs._run_rg("echo x")
    assert seen.get("timeout") == cs.RG_TIMEOUT


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
        header = "--- enriched context (snippet, 1 file(s), ~%d tokens) ---" % toks
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
    monkeypatch.setattr(cs, "rg_path_for_shell", lambda: "RGBIN")

    def fake_run(cmd):  # type: ignore[no-untyped-def]
        seen["cmd"] = cmd
        return (0, canned)

    monkeypatch.setattr(cs, "_run_rg", fake_run)
    return seen


@allure.title("search_code: workspace rg command is exact; no enrichment for context 'none'")
def test_search_code_workspace_cmd(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.tool_paths import root_cd_prefix, sh_quote

    seen = _rg_present(monkeypatch, "projects/sample.js:1:const baseUrl = 'x';")
    r = cs.search_code("baseUrl", minimal_workspace, path=None, limit=7, context="none")
    prefix = root_cd_prefix(minimal_workspace)
    glob_flags = " ".join(f"-g {sh_quote(g)}" for g in cs.DEFAULT_GLOBS)
    expected = (
        f"{prefix} RGBIN -n --max-columns 200 -F {sh_quote('baseUrl')} "
        f"{glob_flags} --max-count 7 {' '.join(cs.DEFAULT_PATHS)}"
    )
    assert seen["cmd"] == expected
    assert r.engine == "rg"
    assert r.text.startswith("Search: 'baseUrl' in workspace")
    assert "enriched context" not in r.text  # kills context=None


@allure.title("search_code: directory-scoped rg command + scope header are exact")
def test_search_code_dir_cmd(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.tool_paths import root_cd_prefix, sh_quote

    seen = _rg_present(monkeypatch, "docs/x.md:1:hit here")
    r = cs.search_code("baseUrl", minimal_workspace, path="docs", context="none")
    prefix = root_cd_prefix(minimal_workspace)
    glob_flags = " ".join(f"-g {sh_quote(g)}" for g in cs.DEFAULT_GLOBS)
    expected = (
        f"{prefix} RGBIN -n --max-columns 200 -F {sh_quote('baseUrl')} "
        f"{glob_flags} --max-count 50 {sh_quote('docs')}"
    )
    assert seen["cmd"] == expected
    assert r.text.startswith("Search: 'baseUrl' in docs")


@allure.title("search_code: file-scoped rg header + default_path threading are exact")
def test_search_code_file_cmd(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _rg_present(monkeypatch, "1:const baseUrl = 'x';")  # bare row needs default_path
    r = cs.search_code("baseUrl", minimal_workspace, path="sample.js", context="none")
    assert r.text.startswith("Search: 'baseUrl' in projects/sample.js")
    assert r.hit_paths == ["projects/sample.js"]  # kills default_path=scope → None/dropped
    assert r.hit_count == 1
    assert "enriched context" not in r.text  # kills context=None


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


@allure.title("search_code: rg present but no rg matches → python tree, note suppressed")
def test_search_code_rg_present_tree_note(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: "rg")
    monkeypatch.setattr(cs, "rg_path_for_shell", lambda: "RGBIN")
    monkeypatch.setattr(cs, "_run_rg", lambda cmd: (1, ""))
    r = cs.search_code("baseUrl", minimal_workspace, path=None, context="none")
    assert "[python]" in r.text
    assert "(rg not in PATH" not in r.text  # note only when rg absent (kills if rg_bin flip)
    assert "XXXX" not in r.text  # kills else "" → "XXXX"


@allure.title("search_code: no-match final return engine is 'rg' when rg present, 'python' otherwise")
def test_search_code_no_match_engine(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with allure.step("rg present, nothing found anywhere → engine 'rg' + 'No matches' text"):
        monkeypatch.setattr(cs, "resolve_rg", lambda: "rg")
        monkeypatch.setattr(cs, "rg_path_for_shell", lambda: "RGBIN")
        monkeypatch.setattr(cs, "_run_rg", lambda cmd: (1, ""))
        r = cs.search_code("ZZZ-NOMATCH-QUERY", minimal_workspace, path=None, context="none")
        assert r.engine == "rg"
        assert r.text.startswith("No matches for")
        monkeypatch.undo()
    with allure.step("rg absent, nothing found → engine 'python'"):
        monkeypatch.setattr(cs, "resolve_rg", lambda: None)
        r2 = cs.search_code("ZZZ-NOMATCH-QUERY", minimal_workspace, path=None, context="none")
        assert r2.engine == "python"


@allure.title("search_code: scoped-file no-match return engine follows rg availability")
def test_search_code_file_no_match_engine(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "resolve_rg", lambda: "rg")
    monkeypatch.setattr(cs, "rg_path_for_shell", lambda: "RGBIN")
    monkeypatch.setattr(cs, "_run_rg", lambda cmd: (1, ""))
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
    monkeypatch.setattr(cs, "rg_path_for_shell", lambda: "RGBIN")
    monkeypatch.setattr(cs, "_run_rg", lambda cmd: (127, "zsh: command not found: rg"))
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
