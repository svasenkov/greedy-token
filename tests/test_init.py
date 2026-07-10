from __future__ import annotations

import allure
import pytest

from greedy_token import __version__
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
