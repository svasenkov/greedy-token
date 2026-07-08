from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token.context_audit import audit_context, render_audit
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Token economy"),
    allure.parent_suite("Token economy"),
    allure.feature("Context audit"),
    allure.suite("Context audit"),
]


@allure.story("Rules scan")
@allure.title("Context audit discovers always-on Cursor rules")
def test_audit_context_finds_rules(minimal_workspace: Path) -> None:
    with allure.step("Audit workspace Cursor context"):
        items = audit_context(minimal_workspace)
        attach_text("item kinds", ", ".join(sorted({i.kind for i in items})))
        attach_text("always-on count", str(len([i for i in items if i.always_on])))
    with allure.step("Verify always-on rules are discovered"):
        kinds = {i.kind for i in items}
        assert "rule" in kinds
        always_on = [i for i in items if i.always_on]
        assert len(always_on) >= 1


@allure.story("Report rendering")
@allure.title("Audit report includes totals section")
def test_render_audit_includes_totals(minimal_workspace: Path) -> None:
    with allure.step("Render context audit report"):
        items = audit_context(minimal_workspace)
        out = render_audit(items)
        attach_text("audit report", out)
    with allure.step("Verify report sections and totals"):
        assert "Cursor context audit" in out
        assert "Always-on rules" in out
        assert "TOTAL" in out
