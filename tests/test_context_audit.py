from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.context_audit import audit_context, render_audit

pytestmark = [allure.epic("Token economy"), allure.feature("Context audit")]


@allure.story("Rules scan")
@allure.title("audit_context discovers always-on Cursor rules")
def test_audit_context_finds_rules(minimal_workspace: Path) -> None:
    items = audit_context(minimal_workspace)
    kinds = {i.kind for i in items}
    assert "rule" in kinds
    always_on = [i for i in items if i.always_on]
    assert len(always_on) >= 1


@allure.story("Report rendering")
@allure.title("render_audit includes totals section")
def test_render_audit_includes_totals(minimal_workspace: Path) -> None:
    items = audit_context(minimal_workspace)
    out = render_audit(items)
    assert "Cursor context audit" in out
    assert "Always-on rules" in out
    assert "TOTAL" in out
