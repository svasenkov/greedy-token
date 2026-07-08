"""Allure steps + attachments helpers for TestOps project 5276."""

from __future__ import annotations

import json
from typing import Any

import allure

_TEXT = allure.attachment_type.TEXT
_JSON = allure.attachment_type.JSON


def attach_text(name: str, body: str) -> None:
    if body:
        allure.attach(body, name=name, attachment_type=_TEXT)


def attach_json(name: str, payload: Any) -> None:
    allure.attach(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        name=name,
        attachment_type=_JSON,
    )
