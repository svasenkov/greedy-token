from __future__ import annotations

import builtins
import json
import re
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import allure
from greedy_token import advisory, capabilities, capabilities_invoke, hook_policy, paths, router
from greedy_token.capabilities_invoke import invoke_capability as rework_real_invoke
from greedy_token.subprocess_safe import UnsafeCommandError
from tests.test_router import (
    P1_ADVERSARIAL_CASES,
    P1_COUNT_POSITIVE_CASES,
    P1_COUNT_SPEC,
    P1_POSITIVE_CASES,
    P1_REPOSITORY_ADVISORY_CASES,
    P1_REPOSITORY_REFUSAL_CASES,
    P1_REWORK_CONFLICTS,
    P1_REWORK_DERIVATIONS,
    P1_REWORK_QUOTED,
    P1_REWORK_WHITESPACE,
    p1_commits_route,
    p1_json_route,
    p1_rework_conflicting_route,
    p1_rework_derivation_route,
)
from tests.test_router import (
    p1_rework_driver as p1_rework_driver,
)
from tests.test_router import (
    p1_workspace as p1_workspace,
)
from tests.test_router import (
    route_task as rework_real_route_task,
)

pytestmark = [
    allure.epic("Greedy token"),
    allure.parent_suite("Greedy token"),
    allure.feature("Hook policy"),
    allure.suite("Hook policy"),
]

PROMPT = "почему комп тормозит"


@pytest.fixture
def policy_state(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_policy, "ASK_GATE_DIR", tmp_path / "ask-gate")
    monkeypatch.setenv("GREEDY_HOOK_MODE", "intercept")
    monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", "0.65")
    monkeypatch.setenv("GREEDY_OVERKILL_GATE", "0")
    monkeypatch.setenv("GREEDY_ADVISORY", "1")
    monkeypatch.setenv("GREEDY_ADVISORY_LOG", str(tmp_path / "advisory.jsonl"))
    monkeypatch.delenv("GREEDY_TOKEN_TTY", raising=False)
    decision = router.RouteDecision(
        target="python", route_id="python-pc-performance", confidence=0.69,
        matched=[PROMPT], command="python scripts/pc-performance.py", note="",
        domains=[], read_only=True,
    )
    cap = capabilities.Capability(
        id=decision.route_id, source="route", origin="workspace", tier="python",
        read_only=True, status="active", readiness="ready", reason="", invocable=True,
        patterns=(PROMPT, "check performance", "git log", "what changed", "nested git", "git repos"),
    )
    result = SimpleNamespace(
        executed=True, exit_code=0, result_status="produced", tier="python",
        output='{"ok": true, "cpu": 10}', gate_action="accepted", gate_reason="accepted",
    )
    route = Mock(return_value=decision)
    probe = Mock(return_value=cap)
    runner = Mock(return_value=result)
    monkeypatch.setattr(router, "route_task", route)
    monkeypatch.setattr(paths, "find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(capabilities, "capability_by_id", probe)
    monkeypatch.setattr(capabilities_invoke, "invoke_capability", runner)
    return SimpleNamespace(
        root=tmp_path, decision=decision, cap=cap, result=result,
        route=route, probe=probe, runner=runner,
    )


def evaluate(state, prompt=PROMPT, *, soft_gate=False, data=None):
    return hook_policy.evaluate(
        prompt, data if data is not None else {"prompt": prompt, "session_id": "fixture"},
        soft_gate=soft_gate,
    )


def last_event(state):
    return json.loads((state.root / "advisory.jsonl").read_text(encoding="utf-8").splitlines()[-1])


@pytest.mark.parametrize("identity", ["request_id", "prompt_id"])
def test_p5_policy_uses_only_declared_request_identity(policy_state, identity):
    from greedy_token.executors import ProductInvocation

    state = policy_state
    data = {"session_id": "owned-fixture", identity: "request-1", "input_version": 1}
    with ProductInvocation(state.root) as invocation:
        first = hook_policy.evaluate(PROMPT, data, invocation=invocation)
        second = hook_policy.evaluate(PROMPT, data, invocation=invocation)
    assert first.kind == second.kind == "intercept"
    assert state.route.call_count == state.runner.call_count == 1


@pytest.mark.parametrize("data", [
    {"session_id": "owned-fixture", "input_version": 1},
    {"session_id": "owned-fixture", "operation_id": "not-a-host-request", "input_version": 1},
    {"session_id": "owned-fixture", "prompt_id": "" , "input_version": 1},
    {"session_id": "owned-fixture", "request_id": "request-1"},
])
def test_p5_policy_missing_ids_or_versions_are_not_inferred(policy_state, data):
    from greedy_token.executors import ProductInvocation

    state = policy_state
    with ProductInvocation(state.root) as invocation:
        for _ in range(2):
            assert hook_policy.evaluate(PROMPT, data, invocation=invocation).kind == "intercept"
    assert state.runner.call_count == 2


@pytest.mark.parametrize("negative", ["validation", "delivery_refusal", "execution_error"])
def test_p5_policy_negative_terminal_does_not_restart_producer(policy_state, negative):
    from greedy_token.executors import ProductInvocation

    state = policy_state
    if negative == "validation":
        state.result.result_status = "invalid"
    elif negative == "delivery_refusal":
        state.result.gate_action = "refused"
        state.result.gate_reason = "delivery_refusal"
    else:
        state.runner.side_effect = RuntimeError("terminal execution refusal")
    data = {"session_id": "owned-fixture", "prompt_id": "prompt-1", "input_version": 1}
    with ProductInvocation(state.root) as invocation:
        for _ in range(2):
            assert hook_policy.evaluate(PROMPT, data, invocation=invocation).kind == "pass"
    assert state.runner.call_count == 1


def test_p5_policy_delivery_metadata_does_not_split_request(policy_state):
    from greedy_token.executors import ProductInvocation

    state = policy_state
    data = {
        "session_id": "owned-fixture", "prompt_id": "prompt-1", "input_version": 1,
        "operation_id": "telemetry-1", "timestamp": 1,
    }
    with ProductInvocation(state.root) as invocation:
        assert hook_policy.evaluate(PROMPT, data, invocation=invocation).kind == "intercept"
        redelivery = {**data, "operation_id": "telemetry-2", "timestamp": 2}
        assert hook_policy.evaluate(PROMPT, redelivery, invocation=invocation).kind == "intercept"
    assert state.runner.call_count == 1


def test_p5_policy_mode_is_a_parameter_not_request_identity(policy_state, monkeypatch):
    from greedy_token.executors import ProductInvocation

    state = policy_state
    data = {"session_id": "owned-fixture", "prompt_id": "prompt-1", "input_version": 1}
    with ProductInvocation(state.root) as invocation:
        assert hook_policy.evaluate(PROMPT, data, invocation=invocation).kind == "intercept"
        monkeypatch.setenv("GREEDY_HOOK_MODE", "advisory")
        assert hook_policy.evaluate(PROMPT, data, invocation=invocation).kind == "pass"
    assert state.runner.call_count == 1


def test_intercept_returns_neutral_payload_and_never_prints(policy_state, capsys):
    response = evaluate(policy_state)
    assert response.kind == "intercept"
    assert response.payload == {
        "target": "PYTHON", "prompt": PROMPT, "body": policy_state.result.output,
        "pretty": advisory.render_result_output(policy_state.result.output),
        "root": policy_state.root,
    }
    policy_state.route.assert_called_once_with(PROMPT, policy_state.root)
    policy_state.runner.assert_called_once_with(policy_state.root, policy_state.cap.id)
    assert capsys.readouterr().out == ""
    assert not (policy_state.root / ".greedy-token" / "last-intercept.md").exists()
    event = last_event(policy_state)
    assert (event["kind"], event["action"], event["blocked"]) == ("intercept", "blocked", True)
    assert event["confidence"] == 0.69


@pytest.mark.parametrize("prompt", ["", "short"])
def test_short_prompt_clears_ask_gate_without_routing(policy_state, prompt):
    hook_policy.set_ask_gate("fixture", True)
    assert evaluate(policy_state, prompt).kind == "pass"
    assert not (policy_state.root / "ask-gate" / "fixture.active").exists()
    policy_state.route.assert_not_called()


@pytest.mark.parametrize("prefix", ["cursor:", "agent:", "gt-skip:", "nogreedy:", "NOGREEDY:"])
def test_bypass_preserves_telemetry_and_skips_routing(policy_state, prefix):
    assert evaluate(policy_state, f"{prefix} {PROMPT}").kind == "pass"
    policy_state.route.assert_not_called()
    policy_state.runner.assert_not_called()
    event = last_event(policy_state)
    assert (event["kind"], event["action"], event["route_id"], event["target"]) == (
        "bypass", "pass", "bypass", "cursor",
    )
    assert event["confidence"] == 1.0
    assert event["est_tokens"] == 0


@pytest.mark.parametrize("prefix", ["ask:", "?"])
def test_ask_prefix_is_stripped_and_session_gate_is_armed(policy_state, prefix):
    response = evaluate(policy_state, f"{prefix} {PROMPT}")
    assert response.kind == "intercept"
    assert response.payload["prompt"] == PROMPT
    policy_state.route.assert_called_once_with(PROMPT, policy_state.root)
    gate = policy_state.root / "ask-gate" / "fixture.active"
    assert json.loads(gate.read_text(encoding="utf-8"))["active"] is True


@pytest.mark.parametrize("data,filename", [
    ({"conversation_id": "conversation"}, "conversation.active"), ({}, "active"),
])
def test_ask_gate_session_fallbacks(policy_state, data, filename):
    evaluate(policy_state, f"ask: {PROMPT}", data=data)
    assert (policy_state.root / "ask-gate" / filename).is_file()
    hook_policy.set_ask_gate(data.get("conversation_id"), False)
    assert not (policy_state.root / "ask-gate" / filename).exists()


@pytest.mark.parametrize("mode,threshold,action", [
    ("advisory", "0.65", "advisory"), ("intercept", "0.8", "low_confidence"),
])
def test_non_enforcing_modes_keep_advisory_events(policy_state, monkeypatch, mode, threshold, action):
    monkeypatch.setenv("GREEDY_HOOK_MODE", mode)
    monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", threshold)
    assert evaluate(policy_state).kind == "pass"
    policy_state.probe.assert_not_called()
    policy_state.runner.assert_not_called()
    event = last_event(policy_state)
    assert (event["kind"], event["action"], event["blocked"]) == ("pass", action, False)


@pytest.mark.parametrize("soft", [False, True])
def test_gate_uses_only_resolved_capability_not_host_environment(policy_state, monkeypatch, soft):
    monkeypatch.setenv("GREEDY_HOOK_MODE", "gate")
    monkeypatch.setenv("GREEDY_DEVIN_SOFT_GATE", "0" if soft else "1")
    response = evaluate(policy_state, soft_gate=soft)
    assert response.kind == "gate"
    expected = {"op_id": policy_state.cap.id} if soft else {
        "continue": False,
        "user_message": advisory.format_gate_user_message(PROMPT, op_id=policy_state.cap.id),
    }
    assert response.payload == expected
    policy_state.runner.assert_not_called()
    event = last_event(policy_state)
    assert (event["action"], event["blocked"]) == ("soft_gate" if soft else "blocked", not soft)


@pytest.mark.parametrize("mode", ["intercept", "gate"])
@pytest.mark.parametrize("changes", [
    None, {"invocable": False}, {"invocable": 1}, {"read_only": False}, {"read_only": 1},
    {"readiness": "not_approved"}, {"readiness": "stale_bytes"},
    {"readiness": "disabled_or_shadow"},
])
def test_ineligible_capabilities_fail_open(policy_state, monkeypatch, mode, changes):
    monkeypatch.setenv("GREEDY_HOOK_MODE", mode)
    policy_state.probe.return_value = (
        None if changes is None else replace(policy_state.cap, **changes)
    )
    assert evaluate(policy_state).kind == "pass"
    policy_state.runner.assert_not_called()
    event = last_event(policy_state)
    assert event["action"] == ("gate_skip" if mode == "gate" else "intercept_skip")
    assert event["blocked"] is False


def test_capability_probe_failure_is_pass_through(policy_state):
    policy_state.probe.side_effect = RuntimeError("state unavailable")
    assert evaluate(policy_state).kind == "pass"
    policy_state.runner.assert_not_called()
    assert last_event(policy_state)["action"] == "intercept_skip"


def test_missing_capability_fields_are_not_invocable(policy_state):
    policy_state.probe.return_value = SimpleNamespace(invocable=True)
    assert evaluate(policy_state).kind == "pass"
    policy_state.runner.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"params": ("args",)}, {"patterns": ("other request",)},
])
def test_intent_skip_keeps_telemetry(policy_state, changes):
    policy_state.probe.return_value = replace(policy_state.cap, **changes)
    assert evaluate(policy_state).kind == "pass"
    assert last_event(policy_state)["action"] == "intent_skip"
    policy_state.runner.assert_not_called()


@pytest.mark.parametrize("prompt,expected", [
    (PROMPT, True), ("Please, check performance?", True),
    ("Пожалуйста, почему комп тормозит?", True),
    ("git log — what changed?", True), ("nested git repos", True),
    ("show git log", True), ("check performance in this repository", True),
    ("не выполняй: почему комп тормозит", False),
    ('show "git log"', False), ("git log and fix a bug", False),
    ("check performance; explain", False), ("show git log\nand explain", False),
    ("what does check performance mean", False), ("show git log as documentation", False),
    ("update performance report", False),
])
def test_intent_guard_semantics(policy_state, prompt, expected):
    assert hook_policy.has_invocation_intent(prompt, policy_state.cap) is expected


@pytest.mark.parametrize("prompt,expected", [("find baseUrl", True), ("delete baseUrl", False)])
def test_tool_intent_and_query_threading(policy_state, prompt, expected):
    cap = replace(policy_state.cap, id="tool-rg-search", tier="tool", params=("query",))
    assert hook_policy.has_invocation_intent(prompt, cap) is expected
    policy_state.probe.return_value = cap
    policy_state.decision.target = "tool"
    policy_state.decision.route_id = cap.id
    response = evaluate(policy_state, prompt)
    assert response.kind == ("intercept" if expected else "pass")
    if expected:
        # The raw prompt is passed as `task` so path-like tokens can scope rg.
        policy_state.runner.assert_called_once_with(policy_state.root, cap.id, task=prompt)
    else:
        policy_state.runner.assert_not_called()


def test_invocation_failure_does_not_intercept(policy_state):
    policy_state.runner.side_effect = RuntimeError("unavailable")
    assert evaluate(policy_state).kind == "pass"
    assert last_event(policy_state)["action"] == "execution_error"


@pytest.mark.parametrize("changes,reason", [
    ({"executed": False}, "not_started"),
    ({"output": "", "result_status": "empty"}, "empty_result"),
    ({"result_status": "invalid"}, "invalid_contract"),
    ({"exit_code": 1, "result_status": "not_evaluated", "tier": "tool"}, "task_failed"),
    ({"gate_action": "bypassed", "gate_reason": "custom_refusal"}, "custom_refusal"),
])
def test_result_gate_refusals_are_preserved(policy_state, changes, reason):
    for key, value in changes.items():
        setattr(policy_state.result, key, value)
    assert evaluate(policy_state).kind == "pass"
    assert last_event(policy_state)["action"] == reason
    policy_state.runner.assert_called_once()


@pytest.mark.parametrize("output,target,exit_code,expected", [
    ("", "python", 0, True), ("  ", "python", 0, True),
    ("No RAG hits for query", "rag", 0, True),
    ("useful answer", "python", 0, False),
    ("error", "tool", 2, True), ("no matches", "tool", 1, True),
    ("src/example.py:1:value", "tool", 1, False),
    ("useful answer", "tool", 0, False),
])
def test_output_usefulness_is_unchanged(output, target, exit_code, expected):
    assert hook_policy.cheap_output_empty(output, target=target, exit_code=exit_code) is expected


@pytest.mark.parametrize("overkill,blocked", [(False, False), (True, False), (True, True)])
def test_cursor_tier_overkill_events_are_preserved(policy_state, monkeypatch, overkill, blocked):
    policy_state.decision.target = "cursor"
    policy_state.decision.route_id = "cursor-fallback"
    monkeypatch.setattr(advisory, "is_overkill", lambda *args, **kwargs: overkill)
    monkeypatch.setattr(advisory, "overkill_recommendations", lambda **kwargs: ["use rag"])
    monkeypatch.setenv("GREEDY_OVERKILL_GATE", "1" if blocked else "0")
    data = {"prompt": PROMPT, "attachments": [{"file_path": "source.py"}]}
    response = evaluate(policy_state, soft_gate=True, data=data)
    assert response.kind == ("gate" if blocked else "pass")
    event = last_event(policy_state)
    assert event["attachment_count"] == 1
    assert event["kind"] == ("overkill" if overkill else "pass")
    assert event["action"] == ("blocked" if blocked else "warn" if overkill else "pass")
    assert event["blocked"] is blocked
    if blocked:
        assert response.payload == {
            "continue": False,
            "user_message": advisory.format_overkill_user_message(
                PROMPT, attachment_count=1, est_tokens=policy_state.decision.est_tokens,
                route_id=policy_state.decision.route_id,
            ),
        }
    policy_state.runner.assert_not_called()


@pytest.mark.parametrize("raw,expected", [(None, 1.01), ("", 1.01), ("bad", 1.01), ("0.65", 0.65)])
def test_fallback_threshold_preserves_legacy_settings(raw, expected, monkeypatch):
    if raw is None:
        monkeypatch.delenv("GREEDY_HOOK_MIN_CONFIDENCE", raising=False)
    else:
        monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", raw)
    assert hook_policy.min_confidence_threshold() == expected


@pytest.mark.parametrize("case", ["intercept", "low_confidence", "cursor", "bypass", "ineligible", "refusal"])
def test_missing_advisory_preserves_legacy_policy(policy_state, monkeypatch, case):
    monkeypatch.setattr(hook_policy, "_try_advisory", lambda: None)
    if case == "low_confidence":
        monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", "0.8")
    if case == "cursor":
        policy_state.decision.target = "cursor"
    if case == "ineligible":
        policy_state.probe.return_value = None
    if case == "refusal":
        policy_state.result.gate_action = "bypassed"
    prompt = f"nogreedy: {PROMPT}" if case == "bypass" else PROMPT
    response = evaluate(policy_state, prompt)
    assert response.kind == ("intercept" if case == "intercept" else "pass")
    if case == "intercept":
        assert response.payload["pretty"] is None
    assert not (policy_state.root / "advisory.jsonl").exists()


def test_advisory_import_failure_is_optional(monkeypatch):
    original = builtins.__import__

    def without_advisory(name, *args, **kwargs):
        if name == "greedy_token":
            raise ImportError("advisory unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_advisory)
    assert hook_policy._try_advisory() is None


def test_policy_dependency_import_failure_is_pass_through(policy_state, monkeypatch):
    original = builtins.__import__

    def without_result_gate(name, *args, **kwargs):
        if name == "greedy_token.result_gate":
            raise ImportError("result gate unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_result_gate)
    assert evaluate(policy_state).kind == "pass"
    policy_state.runner.assert_not_called()
    policy_state.route.assert_not_called()


ARGS_SPEC = [{"regex": r"([0-9]{1,4}) +коммит", "args": "--count {0}"}]


def _patch_args_spec(monkeypatch, op_id, spec=ARGS_SPEC):
    monkeypatch.setattr(
        paths, "load_routes_config",
        lambda *a, **k: {"routes": [{"id": op_id, "args_from_prompt": spec}]},
    )


@allure.title("params:[args] op + args_from_prompt — numbered prompt intercepts with derived args")
def test_params_args_op_derives_args(policy_state, monkeypatch):
    _patch_args_spec(monkeypatch, policy_state.cap.id)
    cap = replace(policy_state.cap, params=("args",))
    policy_state.probe.return_value = cap
    response = evaluate(policy_state, "покажи последних 11 коммитов")
    assert response.kind == "intercept"
    policy_state.runner.assert_called_once_with(
        policy_state.root, cap.id, args="--count 11"
    )


@allure.title("params:[args] op + args_from_prompt — alias prompt runs the fixed argv")
def test_params_args_op_alias_runs_default_argv(policy_state, monkeypatch):
    _patch_args_spec(monkeypatch, policy_state.cap.id)
    cap = replace(policy_state.cap, params=("args",))
    policy_state.probe.return_value = cap
    response = evaluate(policy_state, PROMPT)
    assert response.kind == "intercept"
    policy_state.runner.assert_called_once_with(policy_state.root, cap.id)


@allure.title("params:[args] op — number without request verb is not an invoke intent")
def test_params_args_op_derived_args_still_need_request_verb(
    policy_state, monkeypatch
):
    _patch_args_spec(monkeypatch, policy_state.cap.id)
    cap = replace(policy_state.cap, params=("args",))
    policy_state.probe.return_value = cap
    assert evaluate(policy_state, "в последних 3 коммитах сломался тест").kind == "pass"
    assert last_event(policy_state)["action"] == "intent_skip"
    policy_state.runner.assert_not_called()


@allure.title("derive_prompt_args covers malformed spec entries and template errors")
@pytest.mark.parametrize("spec,prompt,expected", [
    (["not-a-dict"], "покажи 5 коммитов", ""),
    ([{"args": "--count {0}"}], "покажи 5 коммитов", ""),
    ([{"regex": "[0-9]+"}], "покажи 5 коммитов", ""),
    ([{"regex": "[bad(", "args": "--count {0}"}], "покажи 5 коммитов", ""),
    ([{"regex": "zzz", "args": "--count {0}"}], "покажи 5 коммитов", ""),
    ([{"regex": "([0-9]+)", "args": "--count {9}"}], "покажи 5 коммитов", ""),
    ([{"regex": "([0-9]+)", "args": "--count {name}"}], "покажи 5 коммитов", ""),
    ([{"regex": "([0-9]+)", "args": "--count {0}"}], "покажи 5 коммитов", "--count 5"),
    ([{"regex": "zzz"}, {"regex": "([0-9]+)", "args": "--count {0}"}],
     "покажи 7 коммитов", "--count 7"),
])
def test_derive_prompt_args_edges(spec, prompt, expected):
    assert hook_policy.derive_prompt_args(prompt, spec) == expected


@allure.title("_args_from_prompt_spec fails open: error, missing route, non-list spec")
def test_args_from_prompt_spec_edges(policy_state, monkeypatch):
    monkeypatch.setattr(
        paths, "load_routes_config",
        lambda *a, **k: {"routes": [{"id": "x", "args_from_prompt": "nope"}]},
    )
    assert hook_policy._args_from_prompt_spec("missing-id", policy_state.root) == []
    assert hook_policy._args_from_prompt_spec("x", policy_state.root) == []
    monkeypatch.setattr(
        paths, "load_routes_config",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert hook_policy._args_from_prompt_spec("x", policy_state.root) == []


@allure.title("has_invocation_intent: params args-op needs verb or alias, no spec stays skipped")
@pytest.mark.parametrize("prompt,with_spec,expected", [
    ("покажи последних 11 коммитов", True, True),
    ("почему комп тормозит", True, True),          # alias-exact → fixed argv
    ("покажи непонятные 11 вещей", True, False),    # verb, but derive misses
    ("в последних 3 коммитах", True, False),        # derived args, no request verb
    ("покажи последних 11 коммитов", False, False), # params op without spec
])
def test_intent_params_args_op(policy_state, monkeypatch, prompt, with_spec, expected):
    if with_spec:
        _patch_args_spec(monkeypatch, policy_state.cap.id)
    cap = replace(policy_state.cap, params=("args",))
    assert (
        hook_policy.has_invocation_intent(prompt, cap, root=policy_state.root)
        is expected
    )


def assert_rejected_derivation(prompt, spec, *, root=None):
    if any(re.search(entry["regex"], prompt.strip(), re.IGNORECASE) for entry in spec):
        with pytest.raises(UnsafeCommandError, match="unsupported|contradicts|escapes"):
            hook_policy.derive_prompt_args(prompt, spec, root=root)
    else:
        assert hook_policy.derive_prompt_args(prompt, spec, root=root) == ""


def _p1_capability(state, monkeypatch, path="lab/users.json", keys=("id", "email")):
    lab = state.root / "lab"
    lab.mkdir(exist_ok=True)
    for name in ("users.json", "events.json", "User Data.json", "Users.json"):
        (lab / name).write_text("[]", encoding="utf-8")
    route = p1_json_route(path, keys)
    cap = replace(state.cap, id=route["id"], command=route["command"], patterns=tuple(route["patterns"]))
    state.probe.return_value = cap
    state.decision.route_id = cap.id
    state.decision.confidence = 0.95
    monkeypatch.setattr(paths, "load_routes_config", lambda *a, **kw: {"routes": [route]})
    return cap


def test_p1_archive_ru_original_regression(policy_state, monkeypatch):
    prompt = "в lab/users.json у каждого объекта есть id и email"
    cap = _p1_capability(policy_state, monkeypatch)
    assert hook_policy.has_invocation_intent(prompt, cap, root=policy_state.root) is True
    assert evaluate(policy_state, prompt).kind == "intercept"
    policy_state.runner.assert_called_once_with(policy_state.root, cap.id)


@pytest.mark.parametrize("case_id,prompt,path,keys", P1_POSITIVE_CASES, ids=[c[0] for c in P1_POSITIVE_CASES])
def test_p1_positive_policy_preserves_exact_slots(policy_state, monkeypatch, case_id, prompt, path, keys):
    cap = _p1_capability(policy_state, monkeypatch, path, keys)
    slots = router.parse_json_keys_intent(prompt, policy_state.root)
    assert (slots.intent, slots.path, slots.keys) == ("json_keys", path, keys)
    assert hook_policy.has_invocation_intent(prompt, cap, root=policy_state.root)
    assert evaluate(policy_state, prompt).kind == "intercept"
    policy_state.runner.assert_called_once_with(policy_state.root, cap.id)


@pytest.mark.parametrize("mode", ["intercept", "gate"])
@pytest.mark.parametrize("case_id,prompt", P1_ADVERSARIAL_CASES, ids=[c[0] for c in P1_ADVERSARIAL_CASES])
def test_p1_adversarial_no_false_intercept(policy_state, monkeypatch, mode, case_id, prompt):
    cap = _p1_capability(policy_state, monkeypatch)
    cap = replace(cap, patterns=("каждого объекта", "each object", "check json keys"))
    if case_id == "omitted-path":
        cap = replace(cap, command="python lab/check_users.py")
    policy_state.probe.return_value = cap
    monkeypatch.setenv("GREEDY_HOOK_MODE", mode)
    assert hook_policy.has_invocation_intent(prompt, cap, root=policy_state.root) is False
    assert evaluate(policy_state, prompt).kind == "pass"
    policy_state.runner.assert_not_called()


@pytest.mark.parametrize("prompt", [
    "покажи последних 11 коммитов с неизвестным параметром",
    "покажи последних 11 коммитов --force",
    "show last 11 commits and send report",
    "покажи последних 11 коммитов path=../outside",
    "покажи последних 0 коммитов",
    "покажи последних 10000 коммитов",
    "покажи последних 11 коммитов $(id)",
    "не покажи последних 11 коммитов",
])
def test_p1_partial_args_spec_cannot_admit_extra_text(policy_state, monkeypatch, prompt):
    _patch_args_spec(monkeypatch, policy_state.cap.id)
    cap = replace(policy_state.cap, params=("args",))
    policy_state.probe.return_value = cap
    assert_rejected_derivation(prompt, ARGS_SPEC)
    assert hook_policy.has_invocation_intent(prompt, cap, root=policy_state.root) is False
    assert evaluate(policy_state, prompt).kind == "pass"
    policy_state.runner.assert_not_called()


@pytest.mark.parametrize("template", ["--force {0}", "--count {0} --unknown", "--count {0}; send", "--count {0} --count {0}"])
def test_p1_unknown_derived_parameters_are_not_admitted(policy_state, monkeypatch, template):
    spec = [{"regex": r"([0-9]{1,4}) +коммит", "args": template}]
    _patch_args_spec(monkeypatch, policy_state.cap.id, spec)
    cap = replace(policy_state.cap, params=("args",))
    assert_rejected_derivation("покажи последних 11 коммитов", spec)
    assert hook_policy.has_invocation_intent("покажи последних 11 коммитов", cap, root=policy_state.root) is False


def test_p1_json_params_thread_exact_path_and_keys(policy_state, monkeypatch):
    cap = _p1_capability(policy_state, monkeypatch, "lab/User Data.json")
    cap = replace(cap, params=("args",), command="python lab/check_users.py")
    spec = [{"regex": r'check json keys path="([^"]+)" keys=([A-Za-z_,]+)', "args": '--path "{0}" --keys {1}'}]
    _patch_args_spec(monkeypatch, cap.id, spec)
    policy_state.probe.return_value = cap
    prompt = 'check json keys path="lab/User Data.json" keys=id,email'
    assert hook_policy.has_invocation_intent(prompt, cap, root=policy_state.root)
    assert evaluate(policy_state, prompt).kind == "intercept"
    policy_state.runner.assert_called_once_with(policy_state.root, cap.id, args='--path "lab/User Data.json" --keys id,email')


@pytest.mark.parametrize("case_id,prompt,count", P1_COUNT_POSITIVE_CASES, ids=[c[0] for c in P1_COUNT_POSITIVE_CASES])
def test_p1_count_policy_exact_parameter(policy_state, monkeypatch, case_id, prompt, count):
    _patch_args_spec(monkeypatch, policy_state.cap.id, P1_COUNT_SPEC)
    cap = replace(policy_state.cap, params=("args",))
    policy_state.probe.return_value = cap
    assert hook_policy.derive_prompt_args(prompt, P1_COUNT_SPEC) == f"--count {count}"
    assert evaluate(policy_state, prompt).kind == "intercept"
    policy_state.runner.assert_called_once_with(policy_state.root, cap.id, args=f"--count {count}")


@pytest.mark.parametrize("command", [
    "python lab/check_users.py --path lab/events.json --keys id,email",
    "python lab/check_users.py --path lab/users.json --keys id,role",
    "python lab/check_users.py",
])
def test_p1_fixed_policy_command_cannot_contradict_slots(policy_state, monkeypatch, command):
    cap = _p1_capability(policy_state, monkeypatch)
    cap = replace(cap, command=command)
    policy_state.probe.return_value = cap
    prompt = "в lab/users.json у каждого объекта есть id и email"
    assert not hook_policy.has_invocation_intent(prompt, cap, root=policy_state.root)
    assert evaluate(policy_state, prompt).kind == "pass"
    policy_state.runner.assert_not_called()


def test_p1_fixed_json_slots_do_not_guess_defaults(policy_state, monkeypatch):
    cap = _p1_capability(policy_state, monkeypatch)
    omitted = "проверь, что у каждого объекта есть id и email"
    assert hook_policy.has_invocation_intent(omitted, cap, root=policy_state.root)
    for prompt in ("в lab/events.json у каждого объекта есть id и email", "в lab/users.json у каждого объекта есть id и role"):
        assert not hook_policy.has_invocation_intent(prompt, cap, root=policy_state.root)
    ambiguous = replace(cap, patterns=(*cap.patterns, "в lab/events.json у каждого объекта есть id и email"))
    assert not hook_policy.has_invocation_intent(omitted, ambiguous, root=policy_state.root)
    assert not hook_policy.has_invocation_intent(omitted, replace(cap, command="python lab/check_users.py", patterns=("каждого объекта", "id и email")), root=policy_state.root)


@pytest.mark.parametrize("mode,threshold,changes", [
    ("advisory", "0.65", {}), ("intercept", "0.99", {}),
    ("intercept", "0.65", {"read_only": False}),
    ("intercept", "0.65", {"readiness": "not_approved", "invocable": False}),
    ("intercept", "0.65", {"readiness": "stale_bytes", "invocable": False}),
])
def test_p1_json_admission_does_not_relax_policy(policy_state, monkeypatch, mode, threshold, changes):
    cap = _p1_capability(policy_state, monkeypatch)
    policy_state.probe.return_value = replace(cap, **changes)
    monkeypatch.setenv("GREEDY_HOOK_MODE", mode)
    monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", threshold)
    assert evaluate(policy_state, "в lab/users.json у каждого объекта есть id и email").kind == "pass"
    policy_state.runner.assert_not_called()


@pytest.mark.parametrize("params", [(), ("args",)])
@pytest.mark.parametrize("prompt", P1_REPOSITORY_ADVISORY_CASES)
def test_p1_repository_exact_alias_never_authorizes_unbound_scope(policy_state, monkeypatch, prompt, params):
    route = p1_commits_route()
    cap = replace(policy_state.cap, id=route["id"], command=route["command"],
                  params=params, patterns=(prompt, *route["patterns"]))
    _patch_args_spec(monkeypatch, cap.id, route["args_from_prompt"])
    assert not hook_policy.has_invocation_intent(prompt, cap, root=policy_state.root)
    assert_rejected_derivation(prompt, route["args_from_prompt"], root=policy_state.root)


@pytest.mark.parametrize("mode,soft", [("intercept", False), ("gate", False), ("gate", True)])
@pytest.mark.parametrize("prompt", P1_REPOSITORY_ADVISORY_CASES)
def test_p1_repository_ready_high_confidence_recommendation_only_passes(policy_state, monkeypatch, prompt, mode, soft):
    route = p1_commits_route()
    cap = replace(policy_state.cap, id=route["id"], command=route["command"],
                  params=("args",), patterns=(prompt, *route["patterns"]))
    policy_state.probe.return_value = cap
    policy_state.decision.route_id = cap.id
    policy_state.decision.confidence = 1.0
    policy_state.decision.command = None
    _patch_args_spec(monkeypatch, cap.id, route["args_from_prompt"])
    monkeypatch.setenv("GREEDY_HOOK_MODE", mode)
    response = evaluate(policy_state, prompt, soft_gate=soft)
    assert response.kind == "pass"
    assert response.payload == {}
    policy_state.runner.assert_not_called()
    event = last_event(policy_state)
    assert event["action"] == "intent_skip"
    assert event["blocked"] is False


@pytest.mark.parametrize("mode", ["intercept", "gate"])
@pytest.mark.parametrize("prompt", P1_REPOSITORY_REFUSAL_CASES)
def test_p1_repository_invalid_exact_alias_cannot_bypass_policy(policy_state, monkeypatch, prompt, mode):
    route = p1_commits_route()
    cap = replace(policy_state.cap, id=route["id"], command=route["command"],
                  params=("args",), patterns=(prompt, *route["patterns"]))
    policy_state.probe.return_value = cap
    policy_state.decision.route_id = cap.id
    policy_state.decision.confidence = 1.0
    _patch_args_spec(monkeypatch, cap.id, route["args_from_prompt"])
    monkeypatch.setenv("GREEDY_HOOK_MODE", mode)
    assert not hook_policy.has_invocation_intent(prompt, cap, root=policy_state.root)
    assert_rejected_derivation(prompt, route["args_from_prompt"], root=policy_state.root)
    assert evaluate(policy_state, prompt).kind == "pass"
    policy_state.runner.assert_not_called()


@pytest.mark.parametrize("prompt", P1_REPOSITORY_ADVISORY_CASES[:2])
def test_p1_repository_real_routing_recommendation_is_not_hook_execution(policy_state, monkeypatch, prompt):
    import greedy_token.budget_policy as budget_policy
    from tests.test_router import route_task as real_route_task

    route = p1_commits_route()
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    monkeypatch.setattr(router, "_token_estimate_for_route", lambda target, **kw: ("low", 0, "mock"))
    monkeypatch.setattr(budget_policy, "apply_budget_policy", lambda decision, *a: decision)
    _patch_args_spec(monkeypatch, route["id"], route["args_from_prompt"])
    decision = real_route_task(prompt, policy_state.root)
    assert decision.target == "python"
    decision.confidence = 1.0
    policy_state.route.return_value = decision
    policy_state.probe.return_value = replace(
        policy_state.cap, id=route["id"], command=route["command"],
        params=("args",), patterns=(prompt, *route["patterns"]),
    )
    assert evaluate(policy_state, prompt).kind == "pass"
    policy_state.runner.assert_not_called()
    assert last_event(policy_state)["action"] == "intent_skip"


@pytest.fixture
def p1_rework_policy(policy_state, p1_rework_driver, monkeypatch):
    runner = Mock(wraps=rework_real_invoke)
    monkeypatch.setattr(capabilities_invoke, "invoke_capability", runner)

    def route(prompt, root):
        decision = rework_real_route_task(prompt, root)
        decision.confidence = 1.0
        return decision

    monkeypatch.setattr(router, "route_task", route)
    return SimpleNamespace(state=policy_state, driver=p1_rework_driver, runner=runner)


@pytest.mark.parametrize("mode,soft", [("intercept", False), ("gate", False), ("gate", True)])
@pytest.mark.parametrize("conflict", P1_REWORK_CONFLICTS)
def test_p1_rework_conflicting_binding_neutral_policy(p1_rework_policy, monkeypatch, conflict, mode, soft):
    ctx = p1_rework_policy
    ctx.driver.install(p1_rework_conflicting_route(conflict, ctx.driver.root))
    monkeypatch.setenv("GREEDY_HOOK_MODE", mode)
    response = evaluate(ctx.state, "check json keys path=lab/users.json keys=id,email", soft_gate=soft)
    assert (response.kind, response.payload) == ("pass", {})
    ctx.runner.assert_not_called()
    ctx.driver.execute.assert_not_called()
    assert not (ctx.state.root / "ask-gate" / "fixture.active").exists()


@pytest.mark.parametrize("mode,soft", [("intercept", False), ("gate", False), ("gate", True)])
@pytest.mark.parametrize("prompt", P1_REWORK_WHITESPACE)
def test_p1_rework_whitespace_neutral_policy(p1_rework_policy, monkeypatch, prompt, mode, soft):
    ctx = p1_rework_policy
    route = p1_json_route()
    route["patterns"].append(prompt)
    ctx.driver.install(route)
    monkeypatch.setenv("GREEDY_HOOK_MODE", mode)
    response = evaluate(ctx.state, prompt, soft_gate=soft)
    assert (response.kind, response.payload) == ("pass", {})
    ctx.runner.assert_not_called()
    ctx.driver.execute.assert_not_called()


@pytest.mark.parametrize("mode,soft", [("intercept", False), ("gate", False), ("gate", True)])
@pytest.mark.parametrize("prompt", P1_REWORK_DERIVATIONS)
def test_p1_rework_rejected_derivation_neutral_policy(p1_rework_policy, monkeypatch, prompt, mode, soft):
    ctx = p1_rework_policy
    cap = ctx.driver.install(p1_rework_derivation_route(prompt))
    monkeypatch.setenv("GREEDY_HOOK_MODE", mode)
    response = evaluate(ctx.state, prompt, soft_gate=soft)
    assert (response.kind, response.payload) == ("pass", {})
    assert not hook_policy.has_invocation_intent(prompt, cap, root=ctx.driver.root)
    ctx.runner.assert_not_called()
    ctx.driver.execute.assert_not_called()


@pytest.mark.parametrize("prompt,path,keys", P1_REWORK_QUOTED)
def test_p1_rework_quoted_literal_actual_invocation(p1_rework_policy, prompt, path, keys):
    ctx = p1_rework_policy
    route = p1_json_route(path, keys)
    route["argv"] = ["python", "lab/check_users.py", "--path", path, "--keys", ",".join(keys)]
    cap = ctx.driver.install(route)
    response = evaluate(ctx.state, prompt)
    print(json.dumps({"finding": "F4", "prompt": prompt, "response": response.kind,
                      "execute_plan_calls": ctx.driver.execute.call_count}, sort_keys=True))
    assert response.kind == "intercept"
    ctx.runner.assert_called_once_with(ctx.driver.root, cap.id)
    ctx.driver.planner.assert_called_once()
    ctx.driver.execute.assert_called_once()
    plan = ctx.driver.execute.call_args.args[0]
    assert plan.argv[1:] == tuple(route["argv"][1:])
    assert (plan.cwd, plan.executable) == (ctx.driver.root, True)


@pytest.mark.parametrize("derived", [False, True])
def test_p1_rework_planner_produced_capability_argv(p1_rework_policy, monkeypatch, derived):
    ctx = p1_rework_policy
    path, keys = ("lab/User Data.json", ("ID", "eMail")) if derived else ("lab/users.json", ("id", "email"))
    prompt = f'check json keys path="{path}" keys={",".join(keys)}'
    route = p1_json_route(path, keys)
    if derived:
        route.update(command="python lab/check_users.py", params=["args"], args_from_prompt=[
            {"regex": r'check json keys path="([^"]+)" keys=([A-Za-z_,]+)', "args": '--path "{0}" --keys {1}'},
        ])
    cap = ctx.driver.install(route)
    readiness, reason, authorization, script_path, argv, script_type = capabilities._probe_script_route(
        route, ctx.driver.root, {"lab/check_users.py": SimpleNamespace(ok=True)},
    )
    assert readiness == "ready"
    cap = replace(cap, argv=argv, authorization=authorization, script_path=script_path, script_type=script_type)
    monkeypatch.setattr(capabilities, "capability_by_id", lambda *a: cap)
    monkeypatch.setattr(capabilities_invoke, "capability_by_id", lambda *a: cap)
    response = evaluate(ctx.state, prompt)
    assert response.kind == "intercept", (reason, argv)
    ctx.driver.execute.assert_called_once()
    plan = ctx.driver.execute.call_args.args[0]
    assert plan.argv[-4:] == ("--path", path, "--keys", ",".join(keys))
    assert plan.cwd == ctx.driver.root
    print(json.dumps({"case": "derived" if derived else "fixed", "actual_argv": plan.argv,
                      "cwd": str(plan.cwd), "execution": "mock-only"}, sort_keys=True))
