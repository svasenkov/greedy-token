from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token import __version__
from greedy_token.version import metadata_version, read_pyproject_version, resolve_version
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Package"),
    allure.parent_suite("Package"),
    allure.feature("Package metadata"),
    allure.suite("Package metadata"),
]


@allure.story("Version")
@allure.title("Package exposes version string")
def test_package_version() -> None:
    attach_text("version", __version__)
    assert isinstance(__version__, str)
    assert len(__version__) >= 3


@allure.story("Version")
@allure.title("resolve_version uses pyproject.toml in source checkout")
def test_resolve_version_uses_pyproject_in_source_tree() -> None:
    expected = read_pyproject_version()
    attach_text("pyproject version", expected)
    assert resolve_version() == expected


@allure.story("Version")
@allure.title("resolve_version honors GREEDY_TOKEN_RELEASE_VERSION without pyproject or metadata")
def test_resolve_version_release_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = Path("/tmp/greedy-token-version-fallback-root")
    monkeypatch.setattr("greedy_token.version.repo_root", lambda: fake_root)
    monkeypatch.setattr(
        "greedy_token.version.metadata_version",
        lambda: (_ for _ in ()).throw(RuntimeError("no metadata")),
    )
    monkeypatch.setenv("GREEDY_TOKEN_RELEASE_VERSION", "9.8.7")
    assert resolve_version() == "9.8.7"


@allure.story("Version")
@allure.title("metadata_version reads installed distribution")
def test_metadata_version_reads_installed_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("importlib.metadata.version", lambda name: "2.0.0")
    assert metadata_version() == "2.0.0"


@allure.story("Version")
@allure.title("resolve_version uses metadata outside source checkout")
def test_resolve_version_uses_metadata_outside_source_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = Path("/tmp/greedy-token-version-metadata-root")
    monkeypatch.setattr("greedy_token.version.repo_root", lambda: fake_root)
    monkeypatch.setattr("greedy_token.version.metadata_version", lambda: "1.0.0")
    assert resolve_version() == "1.0.0"


@allure.story("Version")
@allure.title("resolve_version raises when version cannot be resolved")
def test_resolve_version_raises_when_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = Path("/tmp/greedy-token-version-unresolvable-root")
    monkeypatch.setattr("greedy_token.version.repo_root", lambda: fake_root)
    monkeypatch.setattr(
        "greedy_token.version.metadata_version",
        lambda: (_ for _ in ()).throw(RuntimeError("no metadata")),
    )
    monkeypatch.delenv("GREEDY_TOKEN_RELEASE_VERSION", raising=False)
    with pytest.raises(RuntimeError, match="GREEDY_TOKEN_RELEASE_VERSION"):
        resolve_version()


@pytest.mark.release
@allure.story("Version")
@allure.title("Release gate: pyproject and package version match CLI target")
def test_release_version_gate(release_version: str | None) -> None:
    if not release_version:
        pytest.skip("pass --release-version or set GREEDY_TOKEN_RELEASE_VERSION")
    expected = read_pyproject_version()
    attach_text("release target", release_version)
    attach_text("pyproject version", expected)
    attach_text("package __version__", __version__)
    assert expected == release_version
    assert __version__ == release_version


@allure.story("Main")
@allure.title("__main__ module delegates to cli.main")
def test_main_module(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    import greedy_token.cli as cli

    called: list[int] = []
    with allure.step("Monkeypatch cli.main and rebind __main__"):
        monkeypatch.setattr(cli, "main", lambda: called.append(1))
        main_mod = importlib.reload(importlib.import_module("greedy_token.__main__"))
    with allure.step("Invoke __main__ entry"):
        main_mod.main()
        attach_text("calls", str(called))
    with allure.step("Verify cli.main was called"):
        assert called == [1]
