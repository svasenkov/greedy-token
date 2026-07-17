"""Unit tests for router shadow/status/thin-context edge branches (fail_under=100)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import allure

import greedy_token.router as router
from greedy_token.router import RouteDecision

pytestmark = [
    allure.epic("Router"),
    allure.parent_suite("Router"),
    allure.feature("Route status"),
    allure.suite("Router gaps"),
]


@allure.title("_parse_shadow_until handles junk, naive, and empty values")
def test_parse_shadow_until() -> None:
    assert router._parse_shadow_until({"shadow_until": "not-a-date"}) is None
    assert router._parse_shadow_until({}) is None
    naive = router._parse_shadow_until({"shadow_until": "2020-01-01T00:00:00"})
    assert naive is not None and naive.tzinfo is not None


@allure.title("_route_status maps shadow window, disabled, and active")
def test_route_status() -> None:
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert router._route_status({"shadow_until": future}) == "shadow"
    assert router._route_status({"enabled": False}) == "inactive"
    assert router._route_status({}) == "active"


def _decision(**kw) -> RouteDecision:
    base = dict(
        target="tool", route_id="r", confidence=0.9, matched=["m"], command=None,
        note="", domains=[], complexity="low", est_tokens=0, rationale="",
    )
    base.update(kw)
    return RouteDecision(**base)


@allure.title("_apply_thin_context_penalty skips duplicate note/rationale text")
def test_thin_context_penalty_dedup() -> None:
    note = router.THIN_CONTEXT_NOTE
    dec = _decision(note=note, rationale=note)
    out = router._apply_thin_context_penalty(dec, "fix the bug now")
    assert out.note == note
    assert out.rationale == note
    assert out.confidence < dec.confidence


@allure.title("_apply_thin_context_penalty is a no-op for non-cheap tiers or no edit verbs")
def test_thin_context_penalty_noop() -> None:
    assert router._apply_thin_context_penalty(_decision(target="cursor"), "fix it") .target == "cursor"
    assert router._apply_thin_context_penalty(_decision(target="tool"), "just look around").confidence == 0.9


@allure.title("format_decision surfaces shadow match line")
def test_format_decision_shadow(tmp_path: Path) -> None:
    dec = _decision(shadow_route_id="shadow-1", matched=["x"], note="n", rationale="because")
    out = router.format_decision(dec, "task", tmp_path)
    assert "Shadow match (log-only): shadow-1" in out
