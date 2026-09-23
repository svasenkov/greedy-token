"""SCRIPT-CANON result contract evaluation (script-tier JSON primitives)."""

from __future__ import annotations

import pytest

import allure
from greedy_token.result_contract import (
    RESULT_INVALID,
    RESULT_NOT_EVALUATED,
    RESULT_PRODUCED,
    evaluate_script_result,
)

pytestmark = [
    allure.epic("Routing"),
    allure.parent_suite("Routing"),
    allure.feature("Script result contract"),
    allure.suite("Script result contract"),
]


@allure.story("Contract detection")
@allure.title("stdout shapes classify produced / invalid / not_evaluated")
@pytest.mark.parametrize(
    ("stdout", "exit_code", "expected"),
    [
        # Canon-conformant results.
        ('{"ok": true, "data": 1}', 0, RESULT_PRODUCED),
        ('{"ok": false, "error": "denied"}', 1, RESULT_PRODUCED),
        ('{"ok": false, "error": "usage"}', 2, RESULT_PRODUCED),
        # Contract violated — the output claims canon but disagrees with exit.
        ('{"ok": false}', 0, RESULT_INVALID),
        ('{"ok": true}', 1, RESULT_INVALID),
        ('{"ok": true}', 2, RESULT_INVALID),
        ('{"ok": false}', 3, RESULT_INVALID),
        # No contract declared — honest not_evaluated.
        ("plain stdout text", 0, RESULT_NOT_EVALUATED),
        ("plain stdout text", 1, RESULT_NOT_EVALUATED),
        ('{"data": 1, "no_ok_key": true}', 0, RESULT_NOT_EVALUATED),
        ("", 0, RESULT_NOT_EVALUATED),
        ("", 1, RESULT_NOT_EVALUATED),
        ("[1, 2, 3]", 0, RESULT_NOT_EVALUATED),
        ("42", 0, RESULT_NOT_EVALUATED),
    ],
)
def test_evaluate_script_result(stdout: str, exit_code: int, expected: str) -> None:
    assert evaluate_script_result(stdout, exit_code) == expected


@allure.story("Contract detection")
@allure.title("a canon JSON summary on the last line still binds the contract")
def test_last_line_canon_detection() -> None:
    stdout = "probe progress line\nanother line\n{\"ok\": true}\n"
    assert evaluate_script_result(stdout, 0) == RESULT_PRODUCED
    assert evaluate_script_result(stdout, 1) == RESULT_INVALID


@allure.story("Contract detection")
@allure.title("malformed output that still claims an ok flag is invalid, not skipped")
def test_malformed_canon_claim() -> None:
    assert evaluate_script_result('{"ok": true', 0) == RESULT_INVALID
    # Prose merely mentioning an ok field declares nothing.
    assert evaluate_script_result('log {"ok": true} more', 0) == RESULT_NOT_EVALUATED
