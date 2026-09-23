from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

import allure
from greedy_token import tool_paths
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
@allure.title("External tool resolution can be disabled for hermetic unit tests")
def test_resolve_tools_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_DISABLE_EXTERNAL_TOOLS", "1")
    assert tool_paths.resolve_rg() is None
    assert tool_paths.resolve_jq() is None


@allure.story("Tool resolution")
@allure.title("Generic resolver handles jq and rejects unknown tools")
def test_generic_tool_resolution_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_JQ", raising=False)
    monkeypatch.setattr(tool_paths.shutil, "which", lambda _name: None)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert list(
        tool_paths._tool_candidates("jq", override_var="GREEDY_TOKEN_JQ")
    ) == [tmp_path / "jq"]
    with pytest.raises(ValueError, match="unsupported tool"):
        tool_paths.resolve_tool("unknown")


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


# --- Mutation kill-tests: _rg_candidates exact ordering and literals ---

_NODE_MODULES = "Contents/Resources/app/node_modules/@vscode"
_DEVIN_SYS_PARENT = Path("/Applications/Devin.app") / _NODE_MODULES
_DEVIN_RG_SUFFIX = "ripgrep-universal/bin/darwin-arm64/rg"
_DEVIN_SYS_RG = _DEVIN_SYS_PARENT / _DEVIN_RG_SUFFIX


def _fixed_literals(home: Path) -> list[Path]:
    return [
        Path("/opt/homebrew/bin/rg"),
        Path("/usr/local/bin/rg"),
        home / ".greedy-token/bin/rg",
        Path(f"/Applications/Cursor.app/{_NODE_MODULES}/ripgrep/bin/rg"),
        Path(f"/Applications/Visual Studio Code.app/{_NODE_MODULES}/ripgrep/bin/rg"),
        _DEVIN_SYS_RG,
    ]


def _devin_home_rg(home: Path) -> Path:
    return home / "Applications/Devin.app" / _NODE_MODULES / _DEVIN_RG_SUFFIX


def _home_based(home: Path) -> list[Path]:
    suffix = f"{_NODE_MODULES}/ripgrep/bin/rg"
    return [
        home / "Applications" / "Cursor.app" / suffix,
        home / "Applications" / "Visual Studio Code.app" / suffix,
        _devin_home_rg(home),
    ]


def _fake_devin_glob(home: Path):  # type: ignore[no-untyped-def]
    """Deterministic Path.glob: one Devin hit per known parent, real pattern only."""
    home_parent = home / "Applications/Devin.app" / _NODE_MODULES

    def fake_glob(self: Path, pattern: str) -> Iterator[Path]:
        if pattern != "ripgrep*/bin/*/rg":
            return iter(())
        if self == _DEVIN_SYS_PARENT:
            return iter([_DEVIN_SYS_RG])
        if self == home_parent:
            return iter([_devin_home_rg(home)])
        return iter(())

    return fake_glob


@allure.story("Ripgrep")
@allure.title("_rg_candidates yields which + PATH + hardcoded + home paths in exact order")
def test_rg_candidates_exact_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    seen_which: list[str] = []

    def fake_which(name: str) -> str:
        seen_which.append(name)
        return "/bin/rgwhich"

    home = tmp_path / "home"
    monkeypatch.setattr(tool_paths.shutil, "which", fake_which)
    monkeypatch.setenv("PATH", os.pathsep.join(["/d1", "", "/d2"]))
    monkeypatch.setattr(tool_paths.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(tool_paths.Path, "glob", _fake_devin_glob(home))

    cands = list(tool_paths._rg_candidates())

    with allure.step("shutil.which is queried with the exact 'rg' name"):
        assert seen_which == ["rg"]  # kills which("XXrgXX") / which("RG") / which=None
    with allure.step("full candidate list matches exactly (kills every literal/case/XX mutant)"):
        expected = (
            [Path("/bin/rgwhich"), Path("/d1") / "rg", Path("/d2") / "rg"]
            + _fixed_literals(home)
            + _home_based(home)
        )
        assert cands == expected
    with allure.step("no 'XXXX' override sentinel when GREEDY_TOKEN_RG is unset"):
        assert Path("XXXX") not in cands  # kills os.environ.get(..., "XXXX")


@allure.story("Ripgrep")
@allure.title("_rg_candidates tolerates an unset PATH (kills None/'XXXX' PATH defaults)")
def test_rg_candidates_path_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    monkeypatch.delenv("PATH", raising=False)
    home = tmp_path / "home"
    monkeypatch.setattr(tool_paths.shutil, "which", lambda name: None)
    monkeypatch.setattr(tool_paths.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(tool_paths.Path, "glob", _fake_devin_glob(home))

    # get("PATH", None)/get("PATH") would raise on None.split(); "XXXX" would inject a path.
    cands = list(tool_paths._rg_candidates())
    assert Path("XXXX") / "rg" not in cands  # kills PATH default "XXXX"
    assert cands == _fixed_literals(home) + _home_based(home)  # no PATH-derived entries


@allure.story("Ripgrep")
@allure.title("_rg_candidates yields the GREEDY_TOKEN_RG override first when set")
def test_rg_candidates_override_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override = tmp_path / "my-rg"
    monkeypatch.setenv("GREEDY_TOKEN_RG", str(override))
    monkeypatch.setattr(tool_paths.shutil, "which", lambda name: None)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(tool_paths.Path, "home", staticmethod(lambda: tmp_path / "home"))
    cands = list(tool_paths._rg_candidates())
    assert cands[0] == override.expanduser()


# --- Mutation kill-tests: resolve_rg continue-not-break + rg_path_for_shell ---


@allure.story("Ripgrep")
@allure.title("resolve_rg: an OSError candidate is skipped via continue, not break")
def test_resolve_rg_oserror_continue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    valid = tmp_path / "valid-rg"
    valid.write_text("#!/bin/sh\n", encoding="utf-8")
    valid.chmod(0o755)

    class _Boom:
        def resolve(self):  # type: ignore[no-untyped-def]
            raise OSError("boom")

    monkeypatch.setattr(tool_paths, "_rg_candidates", lambda: iter([_Boom(), valid]))
    # break on the OSError candidate would never reach `valid`
    assert resolve_rg() == valid.resolve()


@allure.story("Ripgrep")
@allure.title("resolve_rg: a duplicate candidate is skipped via continue, not break")
def test_resolve_rg_duplicate_continue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dup = tmp_path / "dup-missing"  # never created → not a file, resolves fine
    valid = tmp_path / "valid-rg"
    valid.write_text("#!/bin/sh\n", encoding="utf-8")
    valid.chmod(0o755)
    monkeypatch.setattr(tool_paths, "_rg_candidates", lambda: iter([dup, dup, valid]))
    # break on the second (already-seen) dup would never reach `valid`
    assert resolve_rg() == valid.resolve()


@allure.story("Ripgrep")
@allure.title("resolve_rg: a repeated candidate is stat'ed once (seen-set dedup works)")
def test_resolve_rg_dedup_stats_duplicate_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    valid = tmp_path / "valid-rg"
    valid.write_text("#!/bin/sh\n", encoding="utf-8")
    valid.chmod(0o755)

    class _CountingCandidate:
        """Hashable candidate that records how often it is stat'ed."""

        def __init__(self) -> None:
            self.stat_calls = 0

        def resolve(self) -> _CountingCandidate:
            return self

        def is_file(self) -> bool:
            self.stat_calls += 1
            return False

    dup = _CountingCandidate()
    monkeypatch.setattr(tool_paths, "_rg_candidates", lambda: iter([dup, dup, valid]))
    with allure.step("Duplicate candidate resolves but is stat'ed exactly once"):
        assert resolve_rg() == valid.resolve()
        attach_text("stat calls", str(dup.stat_calls))
        # kills seen.add(None) / dropped seen.add(resolved): with a broken seen
        # set the second occurrence would be re-checked (stat_calls == 2).
        assert dup.stat_calls == 1


@allure.story("Ripgrep")
@allure.title("rg_path_for_shell quotes the resolved rg path when found")
def test_rg_path_for_shell_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_paths, "resolve_rg", lambda: Path("/x/rg bin"))
    # kills found=None (which would fall back to the literal "rg")
    assert rg_path_for_shell() == sh_quote("/x/rg bin")


# --- Own rg copy + Devin bundle resolution ---


@allure.story("Ripgrep")
@allure.title("resolve_rg prefers ~/.greedy-token/bin/rg over IDE bundles")
def test_resolve_rg_own_copy_beats_ide_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    home = tmp_path / "home"
    own = home / ".greedy-token/bin/rg"
    own.parent.mkdir(parents=True)
    own.write_text("#!/bin/sh\n", encoding="utf-8")
    own.chmod(0o755)
    monkeypatch.setattr(tool_paths.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(tool_paths.shutil, "which", lambda _name: None)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(tool_paths.Path, "glob", _fake_devin_glob(home))
    # Hide real-world installs (homebrew, IDE bundles) so only fixtures resolve.
    real_is_file = Path.is_file
    monkeypatch.setattr(
        tool_paths.Path,
        "is_file",
        lambda self: real_is_file(self) and str(self).startswith(str(tmp_path)),
    )
    with allure.step("own copy wins over Cursor/VS Code/Devin candidates"):
        assert resolve_rg() == own.resolve()


@allure.story("Ripgrep")
@allure.title("~/Applications Devin glob picks up the ripgrep-universal arch subdir")
def test_devin_home_bundle_globbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    home = tmp_path / "home"
    bundled = _devin_home_rg(home)
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#!/bin/sh\n", encoding="utf-8")
    bundled.chmod(0o755)
    monkeypatch.setattr(tool_paths.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(tool_paths.shutil, "which", lambda _name: None)
    monkeypatch.setenv("PATH", "")
    cands = list(tool_paths._rg_candidates())
    with allure.step("home-level Devin bundle is a candidate"):
        assert bundled in cands


@allure.story("Ripgrep")
@allure.title("/Applications Devin glob finds a real executable rg on this machine")
@pytest.mark.skipif(
    not _DEVIN_SYS_PARENT.is_dir(), reason="Devin.app is not installed"
)
def test_devin_system_bundle_real() -> None:
    hits = sorted(_DEVIN_SYS_PARENT.glob("ripgrep*/bin/*/rg"))
    attach_text("devin rg hits", "\n".join(str(h) for h in hits))
    assert hits
    assert all(h.is_file() and os.access(h, os.X_OK) for h in hits)
