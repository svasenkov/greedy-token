from __future__ import annotations

import builtins
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import allure
from greedy_token import advisory, capabilities, capabilities_invoke, hook_policy, paths, router

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
