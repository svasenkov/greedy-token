"""Doc-drift guard: fail loudly when README documentation diverges from code.

These tests keep the human-facing docs honest against the single source of
truth in code, so the CLI table, pipeline auto-run allowlist, and MCP tool
count can never silently rot.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

import allure
from greedy_token import cli
from greedy_token.calibration import BUCKET_BOUNDS, CALIBRATION_MIN_EVENTS, bucket_label
from greedy_token.pipeline import PIPELINE_AUTO_RUN

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"

# (readme path, CLI-table heading) — EN and RU use slightly different headings.
READMES = [
    pytest.param(REPO_ROOT / "README.md", "## CLI commands", id="en"),
    pytest.param(REPO_ROOT / "README-RU.md", "## CLI", id="ru"),
]

pytestmark = [
    allure.epic("Docs"),
    allure.parent_suite("Docs"),
    allure.feature("Doc-drift guard"),
    allure.suite("Doc-drift guard"),
]


def _inline_code(text: str) -> list[str]:
    return re.findall(r"`([^`]+)`", text)


def _section(markdown: str, heading: str) -> str:
    """Return the body of a `## heading` section up to the next `## `."""
    lines = markdown.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.strip() == heading:
            capturing = True
            continue
        if capturing and line.startswith("## "):
            break
        if capturing:
            out.append(line)
    return "\n".join(out)


def _argparse_command_paths(parser: argparse.ArgumentParser) -> set[str]:
    """Leaf subcommand paths, e.g. {'route', 'llm invoke', 'hub serve'}."""
    paths: set[str] = set()
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, subparser in action.choices.items():
            nested = [
                a for a in subparser._actions if isinstance(a, argparse._SubParsersAction)
            ]
            if nested:
                for nested_action in nested:
                    for nested_name in nested_action.choices:
                        paths.add(f"{name} {nested_name}")
            else:
                paths.add(name)
    return paths


def _readme_command_paths(markdown: str, cli_heading: str) -> set[str]:
    """Command paths documented in the CLI table."""
    section = _section(markdown, cli_heading)
    paths: set[str] = set()
    for code in _inline_code(section):
        if not code.startswith("greedy-token "):
            continue  # skips the `greedy-token-mcp` entry-point row
        rest = code[len("greedy-token ") :].strip()
        words: list[str] = []
        for token in rest.split():
            # subcommands are lowercase words; arg placeholders are UPPER/symbols
            if re.fullmatch(r"[a-z][a-z0-9-]*", token):
                words.append(token)
            else:
                break
        if words:
            paths.add(" ".join(words))
    return paths


@allure.story("CLI table")
@allure.title("README CLI table matches argparse subcommands exactly")
@pytest.mark.parametrize(("readme", "cli_heading"), READMES)
def test_readme_cli_table_matches_argparse(readme: Path, cli_heading: str) -> None:
    markdown = readme.read_text(encoding="utf-8")
    documented = _readme_command_paths(markdown, cli_heading)
    actual = _argparse_command_paths(cli.build_parser())
    allure.attach(
        "\n".join(sorted(documented)), "documented", allure.attachment_type.TEXT
    )
    allure.attach("\n".join(sorted(actual)), "argparse", allure.attachment_type.TEXT)
    missing = actual - documented
    stale = documented - actual
    assert not missing, f"CLI commands missing from {readme.name}: {sorted(missing)}"
    assert not stale, f"{readme.name} lists non-existent CLI commands: {sorted(stale)}"


@allure.story("Pipeline allowlist")
@allure.title("README auto-run list matches PIPELINE_AUTO_RUN")
@pytest.mark.parametrize(("readme", "cli_heading"), READMES)
def test_readme_auto_run_matches_pipeline(readme: Path, cli_heading: str) -> None:
    markdown = readme.read_text(encoding="utf-8")
    # Both EN and RU reference PIPELINE_AUTO_RUN on exactly one line.
    auto_line = next(
        line for line in markdown.splitlines() if "PIPELINE_AUTO_RUN" in line
    )
    # The allowlisted step ids are the backticked tokens after the em-dash.
    tail = auto_line.split("—", 1)[1]
    documented = set(_inline_code(tail))
    allure.attach(
        "\n".join(sorted(documented)), "documented", allure.attachment_type.TEXT
    )
    allure.attach(
        "\n".join(sorted(PIPELINE_AUTO_RUN)), "code", allure.attachment_type.TEXT
    )
    assert documented == set(PIPELINE_AUTO_RUN)


@allure.story("MCP tools")
@allure.title("README MCP tool count matches @mcp.tool() registrations")
@pytest.mark.parametrize(("readme", "cli_heading"), READMES)
def test_readme_mcp_tool_count_matches_code(readme: Path, cli_heading: str) -> None:
    markdown = readme.read_text(encoding="utf-8")
    mcp_source = (REPO_ROOT / "src" / "greedy_token" / "mcp.py").read_text(encoding="utf-8")
    registered = len(re.findall(r"^@mcp\.tool\(\)", mcp_source, flags=re.MULTILINE))
    assert registered > 0

    match = re.search(r"\*\*(\d+) MCP tools\*\*", markdown)
    assert match, f"{readme.name} must state the MCP tool count as **N MCP tools**"
    documented_count = int(match.group(1))
    allure.attach(
        f"documented={documented_count} registered={registered}",
        "mcp tool count",
        allure.attachment_type.TEXT,
    )
    assert documented_count == registered

    # The MCP tools table should list exactly that many tool rows.
    section = _section(markdown, "## MCP tools")
    tool_rows = re.findall(r"^\|\s*`greedy_token_\w+`\s*\|", section, flags=re.MULTILINE)
    assert len(tool_rows) == registered


@allure.story("Confidence calibration")
@allure.title("README calibration section matches code constants (threshold + buckets)")
@pytest.mark.parametrize(("readme", "cli_heading"), READMES)
def test_readme_calibration_matches_code(readme: Path, cli_heading: str) -> None:
    markdown = readme.read_text(encoding="utf-8")
    with allure.step(f"threshold documented as min n={CALIBRATION_MIN_EVENTS}"):
        assert f"min n={CALIBRATION_MIN_EVENTS}" in markdown
    with allure.step("every score bucket label from code is documented"):
        for index in range(len(BUCKET_BOUNDS) + 1):
            assert f"`{bucket_label(index)}`" in markdown, bucket_label(index)


# ---------------------------------------------------------------------------
# guide.md ↔ guide-RU.md structural parity
# ---------------------------------------------------------------------------

GUIDE_EN = REPO_ROOT / "docs" / "guide.md"
GUIDE_RU = REPO_ROOT / "docs" / "guide-RU.md"

_FENCE_BLOCK_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def _strip_fences(markdown: str) -> str:
    return _FENCE_BLOCK_RE.sub("", markdown)


def _heading_levels(markdown: str) -> list[int]:
    return [len(m) for m in re.findall(r"^(#+)\s", _strip_fences(markdown), re.M)]


def _fence_langs(markdown: str) -> list[str]:
    return re.findall(r"^```(\w*)", markdown, re.M)


def _link_targets(markdown: str) -> list[str]:
    return re.findall(r"\]\(([^)\s]+)\)", markdown)


def _normalize_doc_links(targets: list[str]) -> list[str]:
    """Collapse localized doc links (foo-RU.md → foo.md) for parity diff."""
    return [
        re.sub(r"-RU\.md$", ".md", t) if not t.startswith(("http", "#")) else t
        for t in targets
    ]


def _gh_slug(heading: str) -> str:
    """GitHub-style anchor slug: lowercase, drop punctuation, spaces → -."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text)


@allure.story("Guide parity")
@allure.title("guide.md and guide-RU.md share heading levels, fences, and link sets")
def test_guides_structural_parity() -> None:
    en = GUIDE_EN.read_text(encoding="utf-8")
    ru = GUIDE_RU.read_text(encoding="utf-8")
    assert _heading_levels(en) == _heading_levels(ru), "heading skeleton drifted"
    assert _fence_langs(en) == _fence_langs(ru), "code blocks drifted"
    # #anchor targets are heading-derived, so they legitimately differ between
    # languages — parity means "same count"; validity is checked separately by
    # test_guide_internal_anchors_resolve.
    en_links = _link_targets(en)
    ru_links = _link_targets(ru)
    en_external = sorted(
        _normalize_doc_links([t for t in en_links if not t.startswith("#")])
    )
    ru_external = sorted(
        _normalize_doc_links([t for t in ru_links if not t.startswith("#")])
    )
    assert en_external == ru_external, (
        "external/relative link sets drifted — only -RU.md localization is "
        f"allowed:\nen-only: {sorted(set(en_external) - set(ru_external))}\n"
        f"ru-only: {sorted(set(ru_external) - set(en_external))}"
    )
    assert sum(t.startswith("#") for t in en_links) == sum(
        t.startswith("#") for t in ru_links
    ), "internal anchor-link count drifted"


@allure.story("Guide parity")
@allure.title("every internal #anchor link resolves to a heading in the same doc")
@pytest.mark.parametrize(
    "guide",
    [
        pytest.param(GUIDE_EN, id="en"),
        pytest.param(GUIDE_RU, id="ru"),
    ],
)
def test_guide_internal_anchors_resolve(guide: Path) -> None:
    markdown = guide.read_text(encoding="utf-8")
    headings = re.findall(r"^#+\s+(.+)$", _strip_fences(markdown), re.M)
    slugs = {_gh_slug(h) for h in headings}
    anchors = [t[1:] for t in _link_targets(markdown) if t.startswith("#")]
    dangling = [a for a in anchors if a not in slugs]
    assert not dangling, f"{guide.name} has dangling anchors: {dangling}"
