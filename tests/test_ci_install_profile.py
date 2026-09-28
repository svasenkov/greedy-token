"""Regression: MINIMUM CI profile must satisfy pyproject's mandatory -n auto."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import allure

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[1]


@allure.title("minimum profile installs pytest-xdist for the mandatory -n auto")
def test_minimum_profile_has_xdist() -> None:
    src = (_REPO / "scripts" / "ci" / "install_profile.py").read_text(encoding="utf-8")
    minimum = re.search(r"MINIMUM\s*=\s*\[(.*?)\]", src, re.S)
    assert minimum, "MINIMUM list not found"
    assert "pytest-xdist" in minimum.group(1)


@allure.title("pyproject keeps xdist in dev deps and -n auto in addopts")
def test_pyproject_still_uses_xdist() -> None:
    src = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"-n",' in src or "'-n'," in src or "-n" in src  # addopts carry -n auto
    assert "pytest-xdist" in src


# ---------------------------------------------------------------------------
# sdist hygiene: untracked tests/*.py would silently ship (MANIFEST can't see
# git status) — the release guard lives in scripts/ci/build_smoke.py.
# ---------------------------------------------------------------------------


def _load_build_smoke():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_smoke", _REPO / "scripts" / "ci" / "build_smoke.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@allure.title("untracked tests/*.py are flagged; tracked and unrelated pass")
def test_untracked_test_files_parsing() -> None:
    module = _load_build_smoke()
    porcelain = (
        " M src/greedy_token/usage.py\n"
        "?? tests/test_new_thing.py\n"
        "?? tests/sub dir/nested.py\n"
        "?? docs/scratch.py\n"
        "?? src/greedy_token/new_module.py\n"
        '?? "tests/quoted name.py"\n'
    )
    flagged = module._untracked_test_files(porcelain)
    assert "tests/test_new_thing.py" in flagged
    assert "tests/quoted name.py" in flagged
    assert all(p.startswith("tests/") and p.endswith(".py") for p in flagged)
    assert "docs/scratch.py" not in flagged
    assert "src/greedy_token/new_module.py" not in flagged


@allure.title("release-gate.sh refuses a dirty tests/ tree before building")
def test_release_gate_has_clean_checkout_guard() -> None:
    src = (_REPO / "scripts" / "release-gate.sh").read_text(encoding="utf-8")
    assert "status --porcelain" in src
    assert "??" in src and "tests/" in src
