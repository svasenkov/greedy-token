"""Mutation-equivalents drift guard: keep docs/mutation-equivalents.yaml honest.

Repo policy: every surviving mutant is either killed by a test or proven
equivalent, marked in the source with an ``# equivalent: <proof>`` comment
(plus ``# pragma: no mutate`` when the mutation is also suppressed) and
inventoried in the golden registry ``docs/mutation-equivalents.yaml``.

These tests scan ``src/greedy_token/`` for markers and compare against the
registry in both directions, so a new suppression can never land without a
reviewed registry entry, and a stale entry can never outlive its marker.
Anchors are file + normalized marker text — never mutmut ids (unstable).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import allure
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "greedy_token"
REGISTRY_PATH = REPO_ROOT / "docs" / "mutation-equivalents.yaml"

EQUIVALENT_RE = re.compile(r"#\s*equivalent:\s*(.*)$")
PRAGMA_RE = re.compile(r"#\s*pragma:\s*no mutate\s*$")
COMMENT_LINE_RE = re.compile(r"^\s*#")

pytestmark = [
    allure.epic("Docs"),
    allure.parent_suite("Docs"),
    allure.feature("Mutation-equivalents guard"),
    allure.suite("Mutation-equivalents guard"),
]


@dataclass(frozen=True)
class Marker:
    module: str
    line: int
    reason: str
    pragma: bool

    @property
    def key(self) -> tuple[str, str, bool]:
        return (self.module, _normalize(self.reason), self.pragma)


def _normalize(text: str) -> str:
    return " ".join(str(text).split())


def _source_files() -> list[Path]:
    return sorted(
        path
        for path in SRC_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _scan_module(path: Path) -> tuple[list[Marker], list[int]]:
    """Extract equivalent-marker sites and naked-pragma line numbers.

    A site is an ``# equivalent:`` comment line plus its continuation comment
    lines; the first non-comment line after the block is the anchored code line
    and determines whether the site carries ``# pragma: no mutate``.
    """
    module = str(path.relative_to(SRC_ROOT))
    lines = path.read_text(encoding="utf-8").splitlines()
    markers: list[Marker] = []
    covered_pragma_lines: set[int] = set()
    i = 0
    while i < len(lines):
        match = EQUIVALENT_RE.search(lines[i])
        if match and COMMENT_LINE_RE.match(lines[i]):
            parts = [match.group(1).strip()]
            j = i + 1
            while (
                j < len(lines)
                and COMMENT_LINE_RE.match(lines[j])
                and not EQUIVALENT_RE.search(lines[j])
            ):
                parts.append(lines[j].strip().lstrip("#").strip())
                j += 1
            pragma = j < len(lines) and bool(PRAGMA_RE.search(lines[j]))
            if pragma:
                covered_pragma_lines.add(j)
            markers.append(
                Marker(
                    module=module,
                    line=i + 1,
                    reason=" ".join(part for part in parts if part),
                    pragma=pragma,
                )
            )
            i = j + 1
            continue
        i += 1
    naked_pragmas = [
        idx + 1
        for idx, line in enumerate(lines)
        if PRAGMA_RE.search(line) and idx not in covered_pragma_lines
    ]
    return markers, naked_pragmas


def _scan_source() -> tuple[list[Marker], dict[str, list[int]]]:
    markers: list[Marker] = []
    naked: dict[str, list[int]] = {}
    for path in _source_files():
        found, naked_lines = _scan_module(path)
        markers.extend(found)
        if naked_lines:
            naked[str(path.relative_to(SRC_ROOT))] = naked_lines
    return markers, naked


def _load_registry() -> list[dict]:
    data = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    entries = data.get("equivalents")
    assert isinstance(entries, list) and entries, (
        "docs/mutation-equivalents.yaml must have a non-empty `equivalents` list"
    )
    return entries


def _fmt_keys(keys: list[tuple[str, str, bool]]) -> str:
    return "\n".join(
        f"{module} [pragma={'yes' if pragma else 'no'}] {reason}"
        for module, reason, pragma in sorted(keys)
    )


@allure.story("Registry sync")
@allure.title("Every source marker has a registry entry and vice versa")
def test_source_markers_match_registry() -> None:
    markers, _ = _scan_source()
    entries = _load_registry()
    source_keys = Counter(marker.key for marker in markers)
    registry_keys = Counter(
        (str(entry["module"]), _normalize(entry["reason"]), bool(entry["pragma"]))
        for entry in entries
    )
    allure.attach(
        _fmt_keys(list(source_keys.elements())), "source markers", allure.attachment_type.TEXT
    )
    allure.attach(
        _fmt_keys(list(registry_keys.elements())), "registry entries", allure.attachment_type.TEXT
    )
    missing = list((source_keys - registry_keys).elements())
    stale = list((registry_keys - source_keys).elements())
    assert not missing, (
        "Source markers without a registry entry — add them to "
        f"docs/mutation-equivalents.yaml (proof goes through review):\n{_fmt_keys(missing)}"
    )
    assert not stale, (
        "Registry entries without a source marker — remove or re-anchor them in "
        f"docs/mutation-equivalents.yaml:\n{_fmt_keys(stale)}"
    )


@allure.story("Naked pragma")
@allure.title("Every `# pragma: no mutate` carries an `# equivalent:` proof")
def test_every_pragma_has_equivalent_proof() -> None:
    _, naked = _scan_source()
    allure.attach(
        "\n".join(f"{module}:{line}" for module, lines in naked.items() for line in lines)
        or "(none)",
        "naked pragmas",
        allure.attachment_type.TEXT,
    )
    assert not naked, (
        "`# pragma: no mutate` without an adjacent `# equivalent:` proof "
        f"comment: {naked}"
    )


@allure.story("Registry entries")
@allure.title("Registry entries are complete: module exists, symbol defined, proof present")
def test_registry_entries_are_complete() -> None:
    entries = _load_registry()
    problems: list[str] = []
    for index, entry in enumerate(entries):
        label = f"equivalents[{index}] ({entry.get('module')}/{entry.get('symbol')})"
        for field in ("module", "symbol", "reason", "proof"):
            if not _normalize(entry.get(field, "")):
                problems.append(f"{label}: empty `{field}`")
        if not isinstance(entry.get("pragma"), bool):
            problems.append(f"{label}: `pragma` must be a boolean")
        module_path = SRC_ROOT / str(entry.get("module", ""))
        if not module_path.is_file():
            problems.append(f"{label}: module not found under src/greedy_token/")
            continue
        symbol = str(entry.get("symbol", ""))
        if symbol and f"def {symbol}(" not in module_path.read_text(encoding="utf-8"):
            problems.append(f"{label}: `def {symbol}(` not found in {entry['module']}")
    allure.attach("\n".join(problems) or "(none)", "problems", allure.attachment_type.TEXT)
    assert not problems, "\n".join(problems)
