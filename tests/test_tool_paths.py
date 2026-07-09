from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.tool_paths import (
    RG_TIMEOUT,
    resolve_rg,
    rg_path_for_shell,
    root_cd_prefix,
    sh_quote,
    shell_args,
)
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Tool paths"),
    allure.parent_suite("Tool paths"),
    allure.feature("Ripgrep resolution"),
    allure.suite("Ripgrep resolution"),
]


@allure.story("Shell quoting")
@allure.title("sh_quote leaves safe tokens unquoted")
def test_sh_quote_safe() -> None:
    assert sh_quote("sample.js") == "sample.js"


@allure.story("Shell quoting")
@allure.title("sh_quote wraps unsafe characters")
def test_sh_quote_unsafe() -> None:
    quoted = sh_quote("file with spaces")
    attach_text("quoted", quoted)
    assert quoted.startswith("'")
    assert "spaces" in quoted


@allure.story("Shell quoting")
@allure.title("sh_quote escapes embedded single quotes")
def test_sh_quote_embedded_quote() -> None:
    quoted = sh_quote("it's fine")
    assert "'\"'\"'" in quoted or quoted.count("'") >= 2


@allure.story("Shell args")
@allure.title("shell_args returns empty for blank extra args")
def test_shell_args_empty() -> None:
    assert shell_args("   ") == ""


@allure.story("Shell args")
@allure.title("shell_args quotes non-empty extra args")
def test_shell_args_nonempty() -> None:
    assert shell_args("foo bar") == sh_quote("foo bar")


@allure.story("Root prefix")
@allure.title("root_cd_prefix emits cd command for workspace")
def test_root_cd_prefix(minimal_workspace: Path) -> None:
    prefix = root_cd_prefix(minimal_workspace)
    attach_text("prefix", prefix)
    assert prefix.startswith("cd ")
    assert "&&" in prefix


@allure.story("Ripgrep")
@allure.title("resolve_rg honors GREEDY_TOKEN_RG override")
def test_resolve_rg_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rg = tmp_path / "custom-rg"
    rg.write_text("#!/bin/sh\necho rg\n", encoding="utf-8")
    rg.chmod(0o755)
    monkeypatch.setenv("GREEDY_TOKEN_RG", str(rg))
    with allure.step("Resolve rg from override"):
        found = resolve_rg()
        attach_text("resolved", str(found))
    assert found == rg.resolve()


@allure.story("Ripgrep")
@allure.title("resolve_rg skips duplicate and invalid candidates")
def test_resolve_rg_skips_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    broken = tmp_path / "broken-rg"
    monkeypatch.setenv("PATH", str(tmp_path))
    with patch("greedy_token.tool_paths._rg_candidates") as mock_candidates:
        mock_candidates.return_value = iter([broken, broken])
        found = resolve_rg()
    assert found is None


@allure.story("Ripgrep")
@allure.title("rg_path_for_shell falls back to rg when not found")
def test_rg_path_for_shell_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    with patch("greedy_token.tool_paths.resolve_rg", return_value=None):
        assert rg_path_for_shell() == "rg"


@allure.story("Constants")
@allure.title("RG timeout is configured")
def test_rg_timeout_constant() -> None:
    assert RG_TIMEOUT == 30
