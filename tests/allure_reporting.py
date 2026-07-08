"""Allure steps for TestOps project 5276.

TestOps «Сценарий из тестового результата» turns file attachments inside steps into
opaque «Attachment [id] from TestResult» links — names from allure.attach are lost.

Use nested *text* steps (title carries the payload preview) so the manual scenario
stays readable. Full blobs: open the launch result attachments tab, not the case card.
"""

from __future__ import annotations

import json
from typing import Any

import allure

_PREVIEW_LIMIT = 240


def _preview(body: str, limit: int = _PREVIEW_LIMIT) -> str:
    flat = " | ".join(line.strip() for line in body.splitlines() if line.strip())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3] + "..."


def attach_text(name: str, body: str) -> None:
    """Record text as a nested step title (TestOps-friendly), not a file attachment."""
    if body:
        with allure.step(f"{name}: {_preview(body)}"):
            return


def attach_json(name: str, payload: Any) -> None:
    """Record JSON as a nested step title (TestOps-friendly)."""
    attach_text(
        name,
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
    )


def attach_blob(name: str, body: str, *, attachment_type=allure.attachment_type.TEXT) -> None:
    """Optional file attachment for launch/debug only — avoid inside case scenario steps."""
    if body:
        allure.attach(body, name=name, attachment_type=attachment_type)
