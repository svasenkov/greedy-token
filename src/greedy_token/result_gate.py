"""Evaluator gate — the single policy deciding what a run result may claim.

Step 2 gave executions a contract verdict (``result_status``: produced /
invalid / not_evaluated / empty) but left every consumer to interpret it
locally: the hook intercepted on output non-emptiness alone, the pipeline kept
walking on ``ok``, and usage credited savings on any executed success.  This
module centralizes the decision so that "is this result an answer", "may the
chain continue", and "may it claim savings" are ruled once, consistently.

The gate is conservative:

* ``invalid`` output is never an answer and never earns savings;
* ``not_evaluated`` on a contract tier (python/script — the canon contract
  exists there but the script did not declare it) is *unverified*: it may
  still answer a prompt when the caller's usefulness check passes, but it is
  fail-closed for savings and reports an ``unknown`` outcome;
* ``not_evaluated`` on non-contract tiers (tool/ollama/rag — the canon
  contract does not apply to their output) falls back to the tier's own
  evaluator: ``ok`` plus the caller-provided ``output_useful``;
* ``produced`` is contract conformance, not correctness — it may answer and
  earn savings; a produced *failure* (``{"ok": false}`` claim + non-zero exit)
  is still an answer worth surfacing but claims no savings;
* a run that never started is pass-through, exactly like before;
* a ``result_status`` outside the known vocabulary is *invalid* — an
  unrecognized verdict can never launder itself into savings.
"""

from __future__ import annotations

from dataclasses import dataclass

from greedy_token.result_contract import (
    RESULT_EMPTY,
    RESULT_INVALID,
    RESULT_NOT_EVALUATED,
    RESULT_PRODUCED,
)

GATE_ACCEPTED = "accepted"
GATE_BYPASSED = "bypassed"

# Machine-readable gate reasons, recorded on telemetry/advisory events.
REASON_ACCEPTED = "accepted"
REASON_NOT_STARTED = "not_started"
REASON_INVALID_CONTRACT = "invalid_contract"
REASON_EMPTY_RESULT = "empty_result"
REASON_OUTPUT_EMPTY = "output_empty"
REASON_TASK_FAILED = "task_failed"
REASON_UNVERIFIED_RESULT = "unverified_result"

# Savings-exclusion vocabulary recorded on usage events.  ``not_executed`` and
# ``task_failed`` predate the gate (usage.py historically defined them); all
# five live here so the policy owns every string it emits.
EXCLUSION_NOT_EXECUTED = "not_executed"
EXCLUSION_TASK_FAILED = "task_failed"
EXCLUSION_EMPTY_RESULT = "empty_result"
EXCLUSION_INVALID_RESULT = "invalid_result"
EXCLUSION_UNVERIFIED_RESULT = "unverified_result"

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_UNKNOWN = "unknown"

# Tiers whose stdout is checked against the SCRIPT-CANON result contract.  On
# every other tier ``not_evaluated`` is the normal state — their evaluator is
# the exit code plus the caller's usefulness check, not a canon claim.
CONTRACT_TIERS = frozenset({"python", "script"})

# The closed ``result_status`` vocabulary.  Anything outside it is an
# unrecognized verdict — treated as ``invalid`` (fail closed) rather than
# silently read as ``not_evaluated``.
RESULT_STATUSES = frozenset(
    {RESULT_PRODUCED, RESULT_INVALID, RESULT_EMPTY, RESULT_NOT_EVALUATED}
)


@dataclass(frozen=True)
class GateDecision:
    """The gate's ruling on one run result.

    ``action`` is the headline verdict — ``accepted`` when ``may_answer`` is
    true (the result may stand as the operation's answer), ``bypassed``
    otherwise.  ``reason`` is the machine-readable cause.  The remaining facets
    let each consumer take the single piece it needs without re-deriving the
    policy: ``continue_chain`` (pipeline), ``succeeded`` (delivered positive
    outcome), ``savings_eligible`` + ``savings_exclusion`` (savings claims),
    and ``outcome`` (the recommended route_outcome value).
    """

    action: str
    reason: str
    result_status: str
    tier: str
    may_answer: bool
    continue_chain: bool
    succeeded: bool
    savings_eligible: bool
    savings_exclusion: str
    outcome: str


def evaluate_result_gate(
    *,
    started: bool,
    result_status: str,
    tier: str,
    ok: bool,
    output_useful: bool | None = None,
) -> GateDecision:
    """Rule on one run result.

    ``started``/``ok`` are observed facts (the executor started; the process
    verdict was clean).  ``result_status`` is the Step 2 contract verdict;
    ``""`` normalizes to ``not_evaluated`` and any value outside the known
    vocabulary fails closed as ``invalid``.  ``output_useful`` is the caller's
    tier-native usefulness check on the *observed* output (hook
    ``cheap_output_empty``, tool ``_tool_output_weak``, or the honest
    non-empty-stdout floor); ``None`` means the caller attested nothing —
    which still answers an unverified contract-tier result but never makes a
    non-contract result useful.
    """
    status = result_status or RESULT_NOT_EVALUATED
    if status not in RESULT_STATUSES:
        status = RESULT_INVALID
    useful = True if output_useful is None else output_useful

    if not started:
        return GateDecision(
            action=GATE_BYPASSED,
            reason=REASON_NOT_STARTED,
            result_status=status,
            tier=tier,
            may_answer=False,
            # A dry-run step (executed=False, ok=True) must not stop a planned
            # chain; a refused step (ok=False) stops it, same as exit!=0 did.
            continue_chain=ok,
            succeeded=False,
            savings_eligible=False,
            savings_exclusion=EXCLUSION_NOT_EXECUTED,
            outcome=OUTCOME_UNKNOWN if ok else OUTCOME_FAILURE,
        )
    if status == RESULT_INVALID:
        # A contract claim that contradicts the observed exit is untrustworthy:
        # never an answer, never savings, and it must not feed a chain.
        return GateDecision(
            action=GATE_BYPASSED,
            reason=REASON_INVALID_CONTRACT,
            result_status=status,
            tier=tier,
            may_answer=False,
            continue_chain=False,
            succeeded=False,
            savings_eligible=False,
            savings_exclusion=EXCLUSION_INVALID_RESULT,
            outcome=OUTCOME_FAILURE,
        )
    if status == RESULT_EMPTY:
        return GateDecision(
            action=GATE_BYPASSED,
            reason=REASON_EMPTY_RESULT,
            result_status=status,
            tier=tier,
            may_answer=False,
            continue_chain=True,
            succeeded=False,
            savings_eligible=False,
            savings_exclusion=EXCLUSION_EMPTY_RESULT,
            outcome=OUTCOME_FAILURE,
        )
    if not ok:
        if status == RESULT_PRODUCED:
            # Contract-honest failure ({"ok": false} + non-zero exit): the
            # validated negative verdict is itself a usable answer — it just
            # is not a success and earns no savings.
            return GateDecision(
                action=GATE_ACCEPTED,
                reason=REASON_TASK_FAILED,
                result_status=status,
                tier=tier,
                may_answer=True,
                continue_chain=False,
                succeeded=False,
                savings_eligible=False,
                savings_exclusion=EXCLUSION_TASK_FAILED,
                outcome=OUTCOME_FAILURE,
            )
        return GateDecision(
            action=GATE_BYPASSED,
            reason=REASON_TASK_FAILED,
            result_status=status,
            tier=tier,
            may_answer=False,
            continue_chain=False,
            succeeded=False,
            savings_eligible=False,
            savings_exclusion=EXCLUSION_TASK_FAILED,
            outcome=OUTCOME_FAILURE,
        )
    if status == RESULT_PRODUCED:
        if not useful:
            return GateDecision(
                action=GATE_BYPASSED,
                reason=REASON_OUTPUT_EMPTY,
                result_status=status,
                tier=tier,
                may_answer=False,
                continue_chain=True,
                succeeded=False,
                savings_eligible=False,
                savings_exclusion=EXCLUSION_EMPTY_RESULT,
                outcome=OUTCOME_FAILURE,
            )
        return GateDecision(
            action=GATE_ACCEPTED,
            reason=REASON_ACCEPTED,
            result_status=status,
            tier=tier,
            may_answer=True,
            continue_chain=True,
            succeeded=True,
            savings_eligible=True,
            savings_exclusion="",
            outcome=OUTCOME_SUCCESS,
        )
    # not_evaluated: contract tier without a claim is unverified — fail-closed
    # for savings and honest "unknown" outcome; may still answer when the
    # caller's usefulness check passes (no worse than the previous output-only
    # intercept test).
    if tier in CONTRACT_TIERS:
        return GateDecision(
            action=GATE_ACCEPTED if useful else GATE_BYPASSED,
            reason=REASON_UNVERIFIED_RESULT,
            result_status=status,
            tier=tier,
            may_answer=useful,
            continue_chain=True,
            succeeded=False,
            savings_eligible=False,
            savings_exclusion=EXCLUSION_UNVERIFIED_RESULT,
            outcome=OUTCOME_UNKNOWN,
        )
    # Non-contract tier: ``output_useful`` is the *only* usefulness evidence —
    # the canon contract does not apply.  ``None`` means the caller attested
    # nothing about the observed output, so the result cannot claim to be
    # useful: savings are earned by non-empty actual output, never by the
    # absence of a check.
    if not output_useful:
        return GateDecision(
            action=GATE_BYPASSED,
            reason=REASON_OUTPUT_EMPTY,
            result_status=status,
            tier=tier,
            may_answer=False,
            continue_chain=True,
            succeeded=False,
            savings_eligible=False,
            savings_exclusion=EXCLUSION_EMPTY_RESULT,
            outcome=OUTCOME_FAILURE,
        )
    return GateDecision(
        action=GATE_ACCEPTED,
        reason=REASON_ACCEPTED,
        result_status=status,
        tier=tier,
        may_answer=True,
        continue_chain=True,
        succeeded=True,
        savings_eligible=True,
        savings_exclusion="",
        outcome=OUTCOME_SUCCESS,
    )
