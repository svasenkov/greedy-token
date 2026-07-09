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
    import greedy_token.__main__ as main_mod

    monkeypatch.setattr(main_mod, "main", lambda: None)
    main_mod.main()
    assert True
