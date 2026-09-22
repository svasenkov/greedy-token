"""Evaluator gate — the single accept/continue/savings policy over run results.

The gate consumes Step 2 facts (``started`` / ``ok`` / ``result_status``) and
rules once on: may the result stand as an answer, may the chain continue, may
it claim savings, and what outcome should telemetry record.  These tests pin
the whole matrix so no consumer can quietly soften it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import allure
import greedy_token.pipeline as pl
from greedy_token.pipeline import PipelineResult, PipelineStep, StepResult
from greedy_token.result_contract import (
    RESULT_EMPTY,
    RESULT_INVALID,
    RESULT_NOT_EVALUATED,
    RESULT_PRODUCED,
)
from greedy_token.result_gate import (
    GATE_ACCEPTED,
    GATE_BYPASSED,
    REASON_ACCEPTED,
    REASON_EMPTY_RESULT,
    REASON_INVALID_CONTRACT,
    REASON_NOT_STARTED,
    REASON_OUTPUT_EMPTY,
    REASON_TASK_FAILED,
    REASON_UNVERIFIED_RESULT,
    evaluate_result_gate,
)
from greedy_token.usage import (
    EXCLUSION_EMPTY_RESULT,
    EXCLUSION_INVALID_RESULT,
    EXCLUSION_NOT_EXECUTED,
    EXCLUSION_TASK_FAILED,
    EXCLUSION_UNVERIFIED_RESULT,
    build_outcome_event,
    build_route_event,
)

pytestmark = [
    allure.epic("Routing"),
    allure.parent_suite("Routing"),
    allure.feature("Evaluator gate"),
    allure.suite("Evaluator gate"),
]


# (started, status, tier, ok, output_useful) →
# (action, reason, may_answer, continue_chain, succeeded, savings_eligible,
#  savings_exclusion, outcome)
GATE_MATRIX = [
    # --- refusal / never started -------------------------------------------
    (
        dict(started=False, result_status=RESULT_NOT_EVALUATED, tier="python", ok=False),
        (GATE_BYPASSED, REASON_NOT_STARTED, False, False, False, False,
         EXCLUSION_NOT_EXECUTED, "failure"),
    ),
    (
        # Dry-run step: never started but not a failure — chain may continue.
        dict(started=False, result_status=RESULT_NOT_EVALUATED, tier="python", ok=True),
        (GATE_BYPASSED, REASON_NOT_STARTED, False, True, False, False,
         EXCLUSION_NOT_EXECUTED, "unknown"),
    ),
    # --- produced -----------------------------------------------------------
    (
        dict(started=True, result_status=RESULT_PRODUCED, tier="python", ok=True),
        (GATE_ACCEPTED, REASON_ACCEPTED, True, True, True, True, "", "success"),
    ),
    (
        # Contract-honest failure ({"ok": false} + exit!=0): the validated
        # verdict is an answer worth intercepting — but never savings.
        dict(started=True, result_status=RESULT_PRODUCED, tier="python", ok=False),
        (GATE_ACCEPTED, REASON_TASK_FAILED, True, False, False, False,
         EXCLUSION_TASK_FAILED, "failure"),
    ),
    (
        dict(started=True, result_status=RESULT_PRODUCED, tier="python",
             ok=True, output_useful=False),
        (GATE_BYPASSED, REASON_OUTPUT_EMPTY, False, True, False, False,
         EXCLUSION_EMPTY_RESULT, "failure"),
    ),
    # --- invalid ------------------------------------------------------------
    (
        # {"ok": true} + exit!=0 (or vice versa): a lie — never an answer,
        # never feeds a chain, never savings.
        dict(started=True, result_status=RESULT_INVALID, tier="python", ok=True),
        (GATE_BYPASSED, REASON_INVALID_CONTRACT, False, False, False, False,
         EXCLUSION_INVALID_RESULT, "failure"),
    ),
    (
        dict(started=True, result_status=RESULT_INVALID, tier="python", ok=False),
        (GATE_BYPASSED, REASON_INVALID_CONTRACT, False, False, False, False,
         EXCLUSION_INVALID_RESULT, "failure"),
    ),
    # --- empty --------------------------------------------------------------
    (
        # Ran clean, delivered nothing: not an answer, but the chain may walk on.
        dict(started=True, result_status=RESULT_EMPTY, tier="rag", ok=True),
        (GATE_BYPASSED, REASON_EMPTY_RESULT, False, True, False, False,
         EXCLUSION_EMPTY_RESULT, "failure"),
    ),
    # --- not_evaluated ------------------------------------------------------
    (
        # Contract tier without a canon claim: unverified — may answer when
        # the caller's usefulness check passes, but never savings, and the
        # honest outcome is "unknown", not "success".
        dict(started=True, result_status=RESULT_NOT_EVALUATED, tier="python",
             ok=True, output_useful=True),
        (GATE_ACCEPTED, REASON_UNVERIFIED_RESULT, True, True, False, False,
         EXCLUSION_UNVERIFIED_RESULT, "unknown"),
    ),
    (
        dict(started=True, result_status=RESULT_NOT_EVALUATED, tier="python",
             ok=True, output_useful=False),
        (GATE_BYPASSED, REASON_UNVERIFIED_RESULT, False, True, False, False,
         EXCLUSION_UNVERIFIED_RESULT, "unknown"),
    ),
    (
        # Non-contract tiers keep their own evaluator: ok + useful → accepted.
        dict(started=True, result_status=RESULT_NOT_EVALUATED, tier="tool",
             ok=True, output_useful=True),
        (GATE_ACCEPTED, REASON_ACCEPTED, True, True, True, True, "", "success"),
    ),
    (
        dict(started=True, result_status=RESULT_NOT_EVALUATED, tier="tool",
             ok=True, output_useful=False),
        (GATE_BYPASSED, REASON_OUTPUT_EMPTY, False, True, False, False,
         EXCLUSION_EMPTY_RESULT, "failure"),
    ),
    (
        dict(started=True, result_status=RESULT_NOT_EVALUATED, tier="ollama", ok=True),
        (GATE_ACCEPTED, REASON_ACCEPTED, True, True, True, True, "", "success"),
    ),
    (
        dict(started=True, result_status=RESULT_NOT_EVALUATED, tier="rag", ok=True),
        (GATE_ACCEPTED, REASON_ACCEPTED, True, True, True, True, "", "success"),
    ),
    (
        # A plain failed run (no contract claim): never an answer.
        dict(started=True, result_status=RESULT_NOT_EVALUATED, tier="python", ok=False),
        (GATE_BYPASSED, REASON_TASK_FAILED, False, False, False, False,
         EXCLUSION_TASK_FAILED, "failure"),
    ),
    (
        dict(started=True, result_status=RESULT_NOT_EVALUATED, tier="tool", ok=False),
        (GATE_BYPASSED, REASON_TASK_FAILED, False, False, False, False,
         EXCLUSION_TASK_FAILED, "failure"),
    ),
    # Unknown/empty status string normalizes to not_evaluated.
    (
        dict(started=True, result_status="", tier="python", ok=True, output_useful=True),
        (GATE_ACCEPTED, REASON_UNVERIFIED_RESULT, True, True, False, False,
         EXCLUSION_UNVERIFIED_RESULT, "unknown"),
    ),
]


@allure.story("Gate matrix")
@allure.title("status × tier × facts → one accept/continue/savings ruling")
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    GATE_MATRIX,
    ids=[
        "refused",
        "dry-run",
        "produced-ok",
        "produced-failed-verdict",
        "produced-empty-output",
        "invalid-ok",
        "invalid-failed",
        "empty-ok",
        "unverified-contract-tier",
        "unverified-empty-output",
        "tool-evaluator-pass",
        "tool-evaluator-empty",
        "ollama-evaluator-pass",
        "rag-evaluator-pass",
        "python-failed",
        "tool-failed",
        "empty-status-normalizes",
    ],
)
def test_gate_matrix(kwargs: dict, expected: tuple) -> None:
    action, reason, may_answer, continue_chain, succeeded, eligible, exclusion, outcome = expected
    gate = evaluate_result_gate(**kwargs)
    assert gate.action == action
    assert gate.reason == reason
    assert gate.may_answer is may_answer
    assert gate.continue_chain is continue_chain
    assert gate.succeeded is succeeded
    assert gate.savings_eligible is eligible
    assert gate.savings_exclusion == exclusion
    assert gate.outcome == outcome
    # may_answer and action are two spellings of the same verdict.
    assert (gate.action == GATE_ACCEPTED) == gate.may_answer


@allure.story("Gate matrix")
@allure.title("hook decision = gate.may_answer: every status, refused pass-through")
def test_hook_decision_per_status() -> None:
    """The live hook intercepts iff ``gate.may_answer``; refusal never does."""
    with allure.step("produced → intercept"):
        assert evaluate_result_gate(
            started=True, result_status=RESULT_PRODUCED, tier="python",
            ok=True, output_useful=True,
        ).may_answer is True
    with allure.step("invalid → pass through, never intercept"):
        assert evaluate_result_gate(
            started=True, result_status=RESULT_INVALID, tier="python",
            ok=True, output_useful=True,
        ).may_answer is False
    with allure.step("empty → pass through"):
        assert evaluate_result_gate(
            started=True, result_status=RESULT_EMPTY, tier="tool",
            ok=True, output_useful=False,
        ).may_answer is False
    with allure.step("refusal/not_started → pass through (preserved Step-2 semantics)"):
        assert evaluate_result_gate(
            started=False, result_status=RESULT_NOT_EVALUATED, tier="python",
            ok=False, output_useful=False,
        ).may_answer is False
    with allure.step("unverified + useful output → intercept (no worse than before)"):
        assert evaluate_result_gate(
            started=True, result_status=RESULT_NOT_EVALUATED, tier="python",
            ok=True, output_useful=True,
        ).may_answer is True
    with allure.step("crashed script (no claim) → pass through to Agent"):
        assert evaluate_result_gate(
            started=True, result_status=RESULT_NOT_EVALUATED, tier="python",
            ok=False, output_useful=True,
        ).may_answer is False


def _step(tier: str = "python", **kw) -> StepResult:
    base = dict(
        step=PipelineStep("check-meta-sync", tier, "label", command="c"),
        ok=True, exit_code=0, output="o", duration_ms=1, est_tokens=10,
        executed=True,
    )
    base.update(kw)
    return StepResult(**base)


@allure.story("Pipeline continuation")
@allure.title("step_delivered = gate.succeeded: produced only; unverified earns none")
def test_step_delivered_gate_semantics() -> None:
    assert pl.step_delivered(_step(result_status=RESULT_PRODUCED)) is True
    assert pl.step_delivered(_step(result_status=RESULT_INVALID)) is False
    assert pl.step_delivered(_step(result_status=RESULT_EMPTY)) is False
    assert pl.step_delivered(_step(result_status=RESULT_NOT_EVALUATED)) is False
    # Non-contract tiers still deliver on a clean evaluator pass.
    assert pl.step_delivered(_step(tier="ollama")) is True
    # Refused / dry-run never delivers.
    assert pl.step_delivered(_step(executed=False, ok=True)) is False
    assert pl.step_delivered(_step(executed=False, ok=False)) is False


@allure.story("Pipeline continuation")
@allure.title("invalid contract stops the chain even when the exit code was clean")
def test_run_pipeline_stops_on_invalid_contract(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """{"ok": true} + exit 1 is invalid — the next step must not consume it."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        pl.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1, stdout='{"ok": true}\n', stderr=""
        ),
    )
    result = pl.run_pipeline(
        "check-meta-sync then rag baseUrl",
        minimal_workspace,
        execute=True,
        log=False,
    )
    assert result.steps[0].result_status == RESULT_INVALID
    assert result.stopped_early is True
    assert len(result.steps) == 1


@allure.story("Pipeline continuation")
@allure.title("unverified contract-tier step does not stop the chain")
def test_run_pipeline_continues_on_unverified(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A script that ran clean but declared no contract is unverified — the
    chain continues (its output may still inform later steps), it just earns
    no savings."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        pl.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="prose report\n", stderr=""
        ),
    )
    monkeypatch.setattr(pl, "search_rag", lambda q, r, limit: [])
    monkeypatch.setattr(pl, "format_hits", lambda q, h: "No RAG hits")
    monkeypatch.setattr(pl, "_estimate_step_tokens", lambda s, o, r: 0)
    result = pl.run_pipeline(
        "check-meta-sync then rag baseUrl",
        minimal_workspace,
        execute=True,
        log=False,
    )
    assert result.steps[0].result_status == RESULT_NOT_EVALUATED
    assert result.stopped_early is False
    assert len(result.steps) == 2


@allure.story("Savings gate")
@allure.title("compute_step_savings: invalid/unverified steps claim nothing")
def test_step_savings_gate_labels(minimal_workspace: Path) -> None:
    steps = [
        _step(result_status=RESULT_INVALID),
        _step(result_status=RESULT_NOT_EVALUATED),
        _step(result_status=RESULT_PRODUCED),
        _step(tier="tool", result_status=RESULT_NOT_EVALUATED),
    ]
    rows = pl.compute_step_savings(PipelineResult(task="t", steps=steps), minimal_workspace)
    assert rows[0].saved == 0
    assert rows[0].billing == "invalid result — no savings claimed"
    assert rows[1].saved == 0
    assert rows[1].billing == "unverified result — no savings claimed"
    # produced and a clean tool-tier pass stay savings-eligible.
    assert rows[2].saved > 0
    assert rows[3].saved > 0


@allure.story("Savings gate")
@allure.title("refused pipeline step bills as 'not executed', not dry-run")
def test_step_savings_refused_label(minimal_workspace: Path) -> None:
    refused = _step(executed=False, ok=False, exit_code=1)
    dry = _step(executed=False, ok=True)
    rows = pl.compute_step_savings(
        PipelineResult(task="t", steps=[refused, dry]), minimal_workspace
    )
    assert rows[0].billing == "not executed — no savings claimed"
    assert rows[1].billing == "dry-run — not executed"


@allure.story("Telemetry")
@allure.title("_log_pipeline records gate verdict: invalid → failure + exclusion")
def test_log_pipeline_gate_fields(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    invalid = _step(result_status=RESULT_INVALID)
    unverified = _step(result_status=RESULT_NOT_EVALUATED)
    produced = _step(result_status=RESULT_PRODUCED)
    pl._log_pipeline(
        PipelineResult(task="t", steps=[invalid, unverified, produced]),
        minimal_workspace,
        parent_operation_id="op-parent",
    )
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 6
    for idx, (status, action, reason, outcome, exclusion) in enumerate(
        [
            (RESULT_INVALID, GATE_BYPASSED, REASON_INVALID_CONTRACT, "failure",
             EXCLUSION_INVALID_RESULT),
            (RESULT_NOT_EVALUATED, GATE_ACCEPTED, REASON_UNVERIFIED_RESULT,
             "unknown", EXCLUSION_UNVERIFIED_RESULT),
            (RESULT_PRODUCED, GATE_ACCEPTED, REASON_ACCEPTED, "success", None),
        ]
    ):
        request, outcome_ev = events[idx * 2 : idx * 2 + 2]
        with allure.step(f"step {status}: request event carries gate fields"):
            assert request["result_status"] == status
            assert request["gate_action"] == action
            assert request["gate_reason"] == reason
            if exclusion:
                assert request["savings_eligible"] is False
                assert request["savings_exclusion"] == exclusion
            else:
                assert "savings_exclusion" not in request
        with allure.step(f"step {status}: outcome event mirrors the gate"):
            assert outcome_ev["outcome"] == outcome
            assert outcome_ev["result_status"] == status
            assert outcome_ev["gate_action"] == action
            assert outcome_ev["gate_reason"] == reason
            assert outcome_ev["savings_eligible"] is (outcome == "success")
            if exclusion:
                assert outcome_ev["savings_exclusion"] == exclusion


@allure.story("Telemetry")
@allure.title("build_route_event auto-gates result_status: invalid → invalid_result")
def test_route_event_invalid_exclusion(minimal_workspace: Path) -> None:
    from greedy_token.calibration import SOURCE_FIXED
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="python", route_id="python-check", confidence=1.0,
        confidence_source=SOURCE_FIXED, matched=[], command="c", note="",
        domains=[], est_tokens=10,
    )
    event = build_route_event(
        cmd="run", task="t", root=minimal_workspace, decision=decision,
        executed=True, outcome_success=None, result_status=RESULT_INVALID,
    )
    assert event["result_status"] == RESULT_INVALID
    assert event["gate_action"] == GATE_BYPASSED
    assert event["gate_reason"] == REASON_INVALID_CONTRACT
    assert event["savings_eligible"] is False
    assert event["savings_exclusion"] == EXCLUSION_INVALID_RESULT
    assert event["cursor_saved"] == 0


@allure.story("Telemetry")
@allure.title("build_outcome_event cannot label a rejected result as savings-eligible")
def test_outcome_event_gate_overrides_success(minimal_workspace: Path) -> None:
    from greedy_token.calibration import SOURCE_FIXED
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="python", route_id="python-check", confidence=1.0,
        confidence_source=SOURCE_FIXED, matched=[], command="c", note="",
        domains=[], est_tokens=10,
    )
    gate = evaluate_result_gate(
        started=True, result_status=RESULT_INVALID, tier="python", ok=True
    )
    event = build_outcome_event(
        task="t", root=minimal_workspace, decision=decision,
        outcome="success", layer="executor", gate=gate,
    )
    # An inconsistent "success" label does not launder an invalid result.
    assert event["savings_eligible"] is False
    assert event["savings_exclusion"] == EXCLUSION_INVALID_RESULT
    assert event["result_status"] == RESULT_INVALID


@allure.story("MCP outward results")
@allure.title("wrap_mcp_response records gate verdict from result_status")
def test_mcp_wrap_gate_fields(
    minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.budget import wrap_mcp_response

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    wrap_mcp_response(
        "No matches",
        task="search: nothing",
        tier="tool",
        est_tokens=0,
        route_id="mcp-search",
        root=minimal_workspace,
        outcome="failure",
        outcome_layer="executor",
        result_status=RESULT_EMPTY,
    )
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 2
    request, outcome_ev = events
    assert request["result_status"] == RESULT_EMPTY
    assert request["gate_action"] == GATE_BYPASSED
    assert request["gate_reason"] == REASON_EMPTY_RESULT
    assert request["savings_exclusion"] == EXCLUSION_EMPTY_RESULT
    assert outcome_ev["result_status"] == RESULT_EMPTY
    assert outcome_ev["savings_eligible"] is False


@allure.story("Outward scripts")
@allure.title("scripts --run records gate verdict: invalid claim → failure + exclusion")
def test_cmd_scripts_gate_invalid(
    minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """{"ok": true} + exit 1 from a script run is invalid — it must not be
    logged as a savings-eligible success."""
    from argparse import Namespace

    from greedy_token import cli

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    script = minimal_workspace / "scripts" / "meta-sync-check.py"
    script.write_text(
        "#!/usr/bin/env python\nimport sys\nprint('{\"ok\": true}')\nsys.exit(1)\n",
        encoding="utf-8",
    )
    ns = Namespace(
        list=False, run="check-meta-sync", args="", execute=True, no_log=False
    )
    assert cli.cmd_scripts(ns) == 1
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    script_ev, outcome_ev = events[0], events[1]
    assert script_ev["result_status"] == RESULT_INVALID
    assert script_ev["gate_reason"] == REASON_INVALID_CONTRACT
    assert script_ev["savings_exclusion"] == EXCLUSION_INVALID_RESULT
    assert outcome_ev["outcome"] == "failure"
    assert outcome_ev["result_status"] == RESULT_INVALID


@allure.story("Outward scripts")
@allure.title("scripts --run produced contract → success + savings eligible")
def test_cmd_scripts_gate_produced(
    minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from argparse import Namespace

    from greedy_token import cli

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    ns = Namespace(
        list=False, run="check-meta-sync", args="", execute=True, no_log=False
    )
    assert cli.cmd_scripts(ns) == 0
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert events[0]["result_status"] == RESULT_PRODUCED
    assert events[0]["gate_action"] == GATE_ACCEPTED
    assert "savings_exclusion" not in events[0]
    assert events[1]["outcome"] == "success"
    assert events[1]["savings_eligible"] is True


@allure.story("Status display")
@allure.title("pipeline labels: UNVERIFIED for contract tier, INVALID for lies")
def test_step_status_labels() -> None:
    assert pl._step_status_label(_step(result_status=RESULT_PRODUCED)) == "OK"
    assert pl._step_status_label(_step(result_status=RESULT_INVALID)) == "INVALID"
    assert pl._step_status_label(_step(result_status=RESULT_EMPTY)) == "EMPTY"
    assert pl._step_status_label(_step(result_status=RESULT_NOT_EVALUATED)) == "UNVERIFIED"
    assert pl._step_status_label(_step()) == "UNVERIFIED"  # "" normalizes
    # Non-contract tiers and non-executed steps keep plain OK.
    assert pl._step_status_label(_step(tier="ollama")) == "OK"
    assert pl._step_status_label(_step(executed=False)) == "OK"
    assert pl._step_status_label(_step(ok=False, exit_code=2)) == "FAIL"
