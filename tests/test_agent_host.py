"""Phase «beyond Cursor»: agent_host config + host-aware context audit.

Covers the config key ``agent_host: cursor|claude|continue`` (user < workspace
< env), the host conventions in ``audit_context`` (CLAUDE.md, .continuerules),
and the naive-chat baseline picking up host rules via ``cursor_baseline``.
"""

from __future__ import annotations

from pathlib import Path

import allure
import pytest
import yaml

import greedy_token.settings as st
from greedy_token.context_audit import (
    HOST_LABELS,
    HOST_RULE_GLOBS,
    audit_context,
    render_audit,
    resolve_host,
)
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Greedy token"),
    allure.parent_suite("Greedy token"),
    allure.feature("Agent hosts"),
    allure.suite("Agent hosts"),
]


def _set_workspace_host(root: Path, host: str) -> None:
    cfg_path = root / ".greedy-token.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cfg["agent_host"] = host
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _write_claude_rules(root: Path) -> None:
    (root / "CLAUDE.md").write_text("# project claude rules\nalways on\n", encoding="utf-8")
    extra = root / ".claude" / "rules"
    extra.mkdir(parents=True)
    (extra / "style.md").write_text("claude style rule\n", encoding="utf-8")


def _write_continue_rules(root: Path) -> None:
    (root / ".continuerules").write_text("continue always-on rules\n", encoding="utf-8")
    extra = root / ".continue" / "rules"
    extra.mkdir(parents=True)
    (extra / "team.md").write_text("continue team rule\n", encoding="utf-8")


@allure.story("Host resolution")
@allure.title("agent_host defaults to cursor; workspace config and env override")
def test_agent_host_resolution(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with allure.step("default → cursor"):
        settings = st.get_agent_host(minimal_workspace)
        assert (settings.host, settings.source) == ("cursor", "default")

    with allure.step("workspace .greedy-token.yaml → claude"):
        _set_workspace_host(minimal_workspace, "claude")
        settings = st.get_agent_host(minimal_workspace)
        assert (settings.host, settings.source) == ("claude", "workspace")

    with allure.step("env GREEDY_AGENT_HOST wins over config"):
        monkeypatch.setenv("GREEDY_AGENT_HOST", "continue")
        settings = st.get_agent_host(minimal_workspace)
        assert (settings.host, settings.source) == ("continue", "env")


@allure.story("Host resolution")
@allure.title("agent_host from user config; junk values fall back to default")
def test_agent_host_user_level_and_junk(
    tmp_path: Path, minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with allure.step("user-level config sets the host"):
        user_cfg = tmp_path / "user-config.yaml"
        user_cfg.write_text("agent_host: claude\n", encoding="utf-8")
        monkeypatch.setattr(st, "user_config_path", lambda: user_cfg)
        settings = st.get_agent_host(minimal_workspace)
        assert (settings.host, settings.source) == ("claude", "user")

    with allure.step("unknown workspace value is ignored"):
        _set_workspace_host(minimal_workspace, "copilot")
        settings = st.get_agent_host(minimal_workspace)
        assert (settings.host, settings.source) == ("claude", "user")

    with allure.step("unknown env value is ignored"):
        monkeypatch.setenv("GREEDY_AGENT_HOST", "vim")
        settings = st.get_agent_host(minimal_workspace)
        assert (settings.host, settings.source) == ("claude", "user")

    with allure.step("normalizer edge cases"):
        assert st._normalize_agent_host(None) is None
        assert st._normalize_agent_host("") is None
        assert st._normalize_agent_host(" Continue ") == "continue"


@allure.story("Host resolution")
@allure.title("get_agent_host tolerates a missing workspace root")
def test_get_agent_host_no_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "greedy_token.paths.find_workspace_root",
        lambda: (_ for _ in ()).throw(SystemExit(1)),
    )
    assert st.get_agent_host(root=None).source == "default"


@allure.story("Host resolution")
@allure.title("resolve_host: explicit host wins; junk falls back to config")
def test_resolve_host_explicit(minimal_workspace: Path) -> None:
    assert resolve_host(minimal_workspace, "claude") == "claude"
    _set_workspace_host(minimal_workspace, "continue")
    assert resolve_host(minimal_workspace, None) == "continue"
    assert resolve_host(minimal_workspace, "not-a-host") == "continue"


@allure.story("Claude convention")
@allure.title("audit_context(host=claude) counts CLAUDE.md + .claude/rules as always-on")
def test_audit_claude_convention(minimal_workspace: Path) -> None:
    _write_claude_rules(minimal_workspace)
    items = audit_context(minimal_workspace, host="claude")
    always = {i.path for i in items if i.always_on}
    attach_text("always-on", "\n".join(sorted(always)))
    assert always == {"CLAUDE.md", ".claude/rules/style.md"}
    with allure.step("Cursor .mdc rules are not charged for the claude host"):
        assert not any(p.endswith(".mdc") for p in always)
    with allure.step("render header names the host"):
        out = render_audit(items, host="claude")
        attach_text("audit report", out)
        assert "== Claude context audit ==" in out
        assert "Always-on rules (CLAUDE.md + .claude/rules/*.md):" in out


@allure.story("Continue convention")
@allure.title("audit_context(host=continue) counts .continuerules as always-on")
def test_audit_continue_convention(minimal_workspace: Path) -> None:
    _write_continue_rules(minimal_workspace)
    items = audit_context(minimal_workspace, host="continue")
    always = {i.path for i in items if i.always_on}
    assert always == {".continuerules", ".continue/rules/team.md"}
    out = render_audit(items, host="continue")
    attach_text("audit report", out)
    assert "== Continue context audit ==" in out
    assert "Always-on rules (.continuerules + .continue/rules/*.md):" in out


@allure.story("Host switch")
@allure.title("Host switch via workspace config — no explicit host argument")
def test_audit_host_switch_via_config(minimal_workspace: Path) -> None:
    _write_claude_rules(minimal_workspace)
    with allure.step("default host: cursor rules are always-on"):
        always = {i.path for i in audit_context(minimal_workspace) if i.always_on}
        assert always == {".cursor/rules/test.mdc"}
    with allure.step("agent_host: claude flips the audited rule set"):
        _set_workspace_host(minimal_workspace, "claude")
        always = {i.path for i in audit_context(minimal_workspace) if i.always_on}
        assert always == {"CLAUDE.md", ".claude/rules/style.md"}


@allure.story("Host switch")
@allure.title("Naive-chat baseline uses the host's always-on rules (CLAUDE.md)")
def test_baseline_uses_host_rules(minimal_workspace: Path) -> None:
    from greedy_token.baseline import cursor_overhead
    from greedy_token.estimator import cursor_baseline
    from greedy_token.tokens import count_tokens

    task = "find baseUrl"
    cursor_rules = cursor_baseline(minimal_workspace, task)

    _write_claude_rules(minimal_workspace)
    _set_workspace_host(minimal_workspace, "claude")
    claude_rules_tokens = sum(
        i.estimate.tokens
        for i in audit_context(minimal_workspace, host="claude")
        if i.always_on
    )
    expected = claude_rules_tokens + count_tokens(task).tokens + cursor_overhead()
    assert cursor_baseline(minimal_workspace, task) == expected
    assert cursor_baseline(minimal_workspace, task) != cursor_rules


@allure.story("Host registry")
@allure.title("Every declared host has globs, label, and hint")
def test_host_registry_complete() -> None:
    from greedy_token.context_audit import HOST_RULES_HINT

    assert set(HOST_RULE_GLOBS) == {"cursor", "claude", "continue"}
    assert set(HOST_LABELS) == set(HOST_RULE_GLOBS)
    assert set(HOST_RULES_HINT) == set(HOST_RULE_GLOBS)
    with allure.step("workspace config example documents the key"):
        assert "agent_host:" in st.example_workspace_config()
