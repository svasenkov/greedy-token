"""SCRIPT-CANON result contract for script-tier stdout.

A script may *declare* the canonical result contract by emitting a JSON object
with an ``"ok"`` key on a line of its own.  Only a line that starts like a JSON
object counts as a claim — prose mentioning ``{"ok": …}`` inside a sentence
declares nothing.  When a claim is found, ``ok`` must be a boolean and must
agree with the process exit code (``ok: true`` ↔ exit 0, ``ok: false`` ↔ any
non-zero exit).  Anything else is ``invalid`` — the output claims the contract
but contradicts the observed exit status.

Without a claim the status is ``not_evaluated``: exit 0 is an observed fact
about the process, not a validated result.
"""

from __future__ import annotations

import json
import re

RESULT_NOT_EVALUATED = "not_evaluated"
RESULT_PRODUCED = "produced"
RESULT_EMPTY = "empty"
RESULT_INVALID = "invalid"

_CLAIM_START = re.compile(r'^\{\s*"ok"\s*:')


def evaluate_script_result(stdout: str, exit_code: int) -> str:
    """Classify script stdout against the canon contract.

    Returns ``produced`` when a canon claim agrees with the exit code,
    ``invalid`` when a claim contradicts it or is malformed, and
    ``not_evaluated`` when no claim exists.
    """
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            if _CLAIM_START.match(stripped):
                return RESULT_INVALID
            continue
        if not isinstance(data, dict) or "ok" not in data:
            continue
        if not isinstance(data["ok"], bool):
            return RESULT_INVALID
        # Canon exits: 0 = success, 1 = failure, 2 = usage error. Any other
        # exit disagrees with a declared ok flag.
        if data["ok"]:
            return RESULT_PRODUCED if exit_code == 0 else RESULT_INVALID
        return RESULT_PRODUCED if exit_code in (1, 2) else RESULT_INVALID
    return RESULT_NOT_EVALUATED
