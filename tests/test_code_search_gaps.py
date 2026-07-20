"""Unit tests for code_search parse/enrich edge branches (fail_under=100)."""

from __future__ import annotations

from pathlib import Path

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
