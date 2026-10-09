from __future__ import annotations

import hashlib
import json
import os
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import allure
import greedy_token.executors as product
from greedy_token.executors import execute_task
from greedy_token.router import RouteDecision
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Routing"),
    allure.parent_suite("Routing"),
    allure.feature("Task execution"),
    allure.suite("Task execution"),
]


@pytest.fixture
def p5_boundary(minimal_workspace, monkeypatch):
    decision = RouteDecision(
        target="python", route_id="owned-fixture", confidence=1.0,
        matched=[], command="python scripts/fixture.py", note="", domains=[],
        read_only=True,
    )
    plan = product.RunPlan(
        decision=decision, command=decision.command, dry_run_output="fixture",
        executable=True, argv=("python", "scripts/fixture.py"),
        cwd=minimal_workspace, authorization="registered:fixture",
    )
    runner = Mock(return_value=product.PlanRunResult(
        0, '{"ok": true}', started=True, result_status="produced",
    ))
    monkeypatch.setattr(product, "execute_plan", runner)
    return SimpleNamespace(root=minimal_workspace, decision=decision, plan=plan, runner=runner)


def _p5_execute(state, invocation, task="owned task", **params):
    options = {
        "request_id": "request-1", "input_version": "v1",
        "decision": state.decision, "plan": state.plan,
    }
    options.update(params)
    return execute_task(task, state.root, invocation=invocation, **options)


def test_p5_same_request_input_is_one_chain(p5_boundary):
    state = p5_boundary
    with product.ProductInvocation(state.root) as invocation:
        first = _p5_execute(state, invocation)
        first.output = "consumer mutation"
        second = _p5_execute(state, invocation)
        assert second.output == '{"ok": true}'
        assert state.runner.call_count == 1
        assert invocation.counts["duplicates"] == 1
        assert invocation.counts["source"] == "owned_product_boundary"
        assert invocation.counts["scope"] == "declared_product_invocation"
    assert invocation.closed
    assert invocation.retained_results == 0


@pytest.mark.parametrize("change", [
    "version", "task", "op", "params", "independent_id", "missing_id", "missing_version",
    "typed_version",
])
def test_p5_changed_or_missing_identity_never_replays(p5_boundary, change):
    state = p5_boundary
    with product.ProductInvocation(state.root) as invocation:
        _p5_execute(state, invocation, input_version=1)
        params = {"input_version": 1}
        task = "owned task"
        if change == "version":
            params["input_version"] = 2
        elif change == "typed_version":
            params["input_version"] = "1"
        elif change == "task":
            task = "changed input"
        elif change == "op":
            params["decision"] = replace(state.decision, route_id="independent-op")
        elif change == "params":
            params["plan"] = replace(state.plan, argv=(*state.plan.argv, "--count", "2"))
        elif change == "independent_id":
            params["request_id"] = "request-2"
        elif change == "missing_id":
            params["request_id"] = None
        else:
            params["input_version"] = None
        _p5_execute(state, invocation, task, **params)
        assert state.runner.call_count == 2


def test_p5_owned_input_snapshot_does_not_mix_concurrent_versions(p5_boundary):
    state = p5_boundary
    entered = threading.Event()
    release = threading.Event()
    argv = state.plan.argv

    def producer(plan, **kwargs):
        if state.runner.call_count == 1:
            entered.set()
            assert release.wait(2)
        return product.PlanRunResult(
            0, " ".join(plan.argv), started=True, result_status="produced",
        )

    state.runner.side_effect = producer
    with product.ProductInvocation(state.root) as invocation, ThreadPoolExecutor(1) as pool:
        first = pool.submit(_p5_execute, state, invocation)
        try:
            assert entered.wait(2)
            state.plan.argv = (*argv, "--count", "2")
            second = _p5_execute(state, invocation, input_version="v2")
        finally:
            release.set()
        assert first.result(timeout=2).output == " ".join(argv)
        assert second.output == " ".join(state.plan.argv)
        assert state.runner.call_count == 2


def test_p5_missing_ids_are_independent_even_with_identical_text(p5_boundary):
    state = p5_boundary
    with product.ProductInvocation(state.root) as invocation:
        for _ in range(2):
            _p5_execute(state, invocation, request_id=None)
    assert state.runner.call_count == 2


def test_p5_lifetime_cleanup_and_root_confinement(p5_boundary):
    state = p5_boundary
    with product.ProductInvocation(state.root) as first:
        _p5_execute(state, first)
        with pytest.raises(product.ProductLifecycleError, match="root"):
            execute_task(
                "owned task", state.root / "outside", invocation=first,
                request_id="request-1", input_version="v1",
            )
    with pytest.raises(product.ProductLifecycleError, match="closed"):
        _p5_execute(state, first)
    with product.ProductInvocation(state.root) as second:
        _p5_execute(state, second)
    assert state.runner.call_count == 2
    assert first.retained_results == second.retained_results == 0


def test_p5_concurrent_duplicate_joins_one_producer(p5_boundary):
    state = p5_boundary
    ready = threading.Barrier(3)
    entered = threading.Event()
    release = threading.Event()
    terminal = state.runner.return_value

    def producer(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return terminal

    def request(invocation):
        ready.wait(timeout=2)
        return _p5_execute(state, invocation)

    state.runner.side_effect = producer
    with product.ProductInvocation(state.root) as invocation, ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(request, invocation) for _ in range(2)]
        try:
            ready.wait(timeout=2)
            assert entered.wait(2)
        finally:
            release.set()
        results = [future.result(timeout=2) for future in futures]
        assert results[0].output == results[1].output
        assert state.runner.call_count == 1
        assert invocation.counts["duplicates"] == 1


@pytest.mark.parametrize("status,code,started", [
    ("produced", 0, True), ("invalid", 0, True), ("empty", 0, True),
    ("not_evaluated", 0, True), ("not_evaluated", 1, False),
])
def test_p5_terminal_result_is_not_a_retry_signal(p5_boundary, status, code, started):
    state = p5_boundary
    state.runner.return_value = product.PlanRunResult(
        code, "terminal result", started=started, result_status=status,
    )
    with product.ProductInvocation(state.root, max_retries=2) as invocation:
        first = _p5_execute(state, invocation)
        second = _p5_execute(state, invocation)
        assert (first.exit_code, first.result_status) == (second.exit_code, second.result_status)
        assert state.runner.call_count == 1
        assert invocation.counts["retries"] == 0


def test_p5_terminal_exception_is_not_retried(p5_boundary):
    state = p5_boundary
    state.runner.side_effect = RuntimeError("terminal refusal")
    with product.ProductInvocation(state.root, max_retries=2) as invocation:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="terminal refusal"):
                _p5_execute(state, invocation)
    assert state.runner.call_count == 1


@pytest.mark.parametrize("succeeds", [True, False])
def test_p5_explicit_retry_is_bounded_and_terminal_is_retained(p5_boundary, succeeds):
    state = p5_boundary
    retry = product.ProductRetryError("isolated pre-terminal transport failure")
    state.runner.side_effect = [
        retry, state.runner.return_value if succeeds else retry,
    ]
    with product.ProductInvocation(state.root, max_retries=1) as invocation:
        for _ in range(2):
            if succeeds:
                assert _p5_execute(state, invocation).result_status == "produced"
            else:
                with pytest.raises(product.ProductLifecycleError, match="retry limit"):
                    _p5_execute(state, invocation)
        assert invocation.counts["retries"] == 1
        assert invocation.counts["dispatch_attempts"] == 2
    assert state.runner.call_count == 2


def test_p5_total_attempt_limit_stops_before_another_dispatch(p5_boundary):
    state = p5_boundary
    state.runner.side_effect = product.ProductRetryError("pre-terminal failure")
    with product.ProductInvocation(state.root, max_attempts=1, max_retries=2) as invocation:
        with pytest.raises(product.ProductLifecycleError, match="attempt limit"):
            _p5_execute(state, invocation)
    assert state.runner.call_count == 1


@pytest.mark.parametrize("limit", [0, 1])
def test_p5_fallback_limit_does_not_restart_terminal_executor(p5_boundary, monkeypatch, limit):
    state = p5_boundary
    state.decision.target = "tool"
    state.runner.return_value = product.PlanRunResult(1, "", started=True)
    search = Mock(return_value=[])
    monkeypatch.setattr(product, "search_rag", search)
    with product.ProductInvocation(state.root, max_fallbacks=limit) as invocation:
        for _ in range(2):
            with pytest.raises(product.ProductLifecycleError, match="fallback limit"):
                _p5_execute(state, invocation, "find test config")
    assert state.runner.call_count == 1
    assert search.call_count == limit


def test_p5_request_bound_never_evicts_and_reexecutes(p5_boundary):
    state = p5_boundary
    with product.ProductInvocation(state.root, max_requests=1) as invocation:
        _p5_execute(state, invocation)
        with pytest.raises(product.ProductLifecycleError, match="request limit"):
            _p5_execute(state, invocation, request_id="request-2")
        _p5_execute(state, invocation)
        assert state.runner.call_count == 1


@pytest.fixture
def p5_manifest_boundary(minimal_workspace, monkeypatch):
    script_path = "scripts/meta-sync-check.py"
    decision = RouteDecision(
        target="python", route_id="manifest-fixture", confidence=1.0,
        matched=[], command=f"python {script_path}", note="", domains=[], read_only=True,
    )
    plan = product.RunPlan(
        decision=decision, command=decision.command, dry_run_output="manifest fixture",
        executable=True, argv=("python", script_path), cwd=minimal_workspace,
        authorization=f"manifest:{script_path}", script_path=script_path, script_type="python",
    )
    verified = product.VerifiedScript(
        entry=SimpleNamespace(script_type="python"),
        fd=os.open(minimal_workspace / script_path, os.O_RDONLY),
    )
    close = Mock(wraps=verified.close)
    monkeypatch.setattr(verified, "close", close)
    verifier = Mock(return_value=verified)
    binder = product.bind_verified_argv
    bind = Mock(wraps=binder)
    confinement = Mock(wraps=product.trusted_script_argv)
    native = Mock(return_value=SimpleNamespace(
        returncode=0, stdout='{"ok": true}', stderr="",
    ))
    observer = Mock()
    monkeypatch.setattr(product, "verify_script", verifier)
    monkeypatch.setattr(product, "bind_verified_argv", bind)
    monkeypatch.setattr(product, "trusted_script_argv", confinement)
    monkeypatch.setattr(product.subprocess, "run", native)
    monkeypatch.setattr("greedy_token.cheap_llm._observe_emit", observer)
    try:
        yield SimpleNamespace(
            root=minimal_workspace, decision=decision, plan=plan, verified=verified,
            close=close, verifier=verifier, bind=bind, binder=binder,
            confinement=confinement, native=native, observer=observer,
        )
    finally:
        if verified.fd >= 0:
            verified.close()


def test_p5_timeout_uses_one_clock_sample(minimal_workspace, monkeypatch):
    clock = Mock(side_effect=[0.9, 1.1])
    monkeypatch.setattr(product, "time", SimpleNamespace(monotonic=clock))
    invocation = product.ProductInvocation(minimal_workspace, deadline=1.0)
    budget = invocation.timeout(10.0)
    print(f"P5_TIMEOUT_EVIDENCE budget={budget!r} clock_calls={clock.call_count}")
    assert budget == pytest.approx(0.1)
    clock.assert_called_once()


@pytest.mark.parametrize("now", [1.0, 1.1])
def test_p5_timeout_denies_nonpositive_remaining(minimal_workspace, monkeypatch, now):
    clock = Mock(return_value=now)
    monkeypatch.setattr(product, "time", SimpleNamespace(monotonic=clock))
    invocation = product.ProductInvocation(minimal_workspace, deadline=1.0)
    with pytest.raises(product.ProductLifecycleError, match="deadline"):
        invocation.timeout(10.0)
    clock.assert_called_once()


@pytest.mark.parametrize("default", [0.0, -1.0, float("nan")])
def test_p5_timeout_denies_impossible_default(minimal_workspace, default):
    invocation = product.ProductInvocation(minimal_workspace)
    with pytest.raises(product.ProductLifecycleError, match="budget"):
        invocation.timeout(default)


@pytest.mark.parametrize("stage", ["verification", "binding"])
@pytest.mark.parametrize("stop", ["deadline", "cancel"])
def test_p5_launch_admission_denies_stop_during_trust(p5_manifest_boundary, monkeypatch, stage, stop):
    state = p5_manifest_boundary
    clock = [0.0]
    monkeypatch.setattr(product, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    with product.ProductInvocation(state.root, deadline=5.0, max_retries=2) as invocation:
        def stopped():
            if stop == "cancel":
                invocation.cancel()
            else:
                clock[0] = 6.0

        if stage == "verification":
            def verify(*args):
                stopped()
                return state.verified

            state.verifier.side_effect = verify
        else:
            def bind(*args):
                bound = state.binder(*args)
                stopped()
                return bound

            state.bind.side_effect = bind
        result = None
        denial = None
        try:
            result = _p5_execute(state, invocation)
        except product.ProductLifecycleError as exc:
            denial = exc
        print(
            f"P5_ADMISSION_EVIDENCE stage={stage} stop={stop} clock={clock[0]} "
            f"native_calls={state.native.call_count} "
            f"timeout={state.native.call_args.kwargs['timeout'] if state.native.called else None} "
            f"started={result.started if result is not None else False} "
            f"exit_code={result.exit_code if result is not None else None} denial={denial!r} "
            f"fd={state.verified.fd} close_calls={state.close.call_count}"
        )
        state.close.assert_called_once_with()
        assert state.verified.fd == -1
        state.native.assert_not_called()
        assert result is None and stop in str(denial)
        with pytest.raises(product.ProductLifecycleError, match=stop):
            _p5_execute(state, invocation)
        state.verifier.assert_called_once_with(state.root, state.plan.script_path)
        state.bind.assert_called_once_with(state.verified, state.plan.argv)
        state.confinement.assert_called_once_with(
            state.plan.argv, cwd=state.root, root=state.root,
            manifest_script_paths=(state.plan.script_path,),
        )
        assert invocation.retained_results == 1
        assert invocation.counts["requests"] == invocation.counts["dispatch_attempts"] == 1
        assert invocation.counts["retries"] == invocation.counts["fallbacks"] == 0
        assert invocation.counts["active"] == 0
    calls = state.observer.call_args_list
    assert [call.args[0] for call in calls] == [
        "product_attempt", "product_attempt_end", "product_close",
    ]
    assert calls[1].kwargs["outcome"] == "error"
    assert calls[1].kwargs["attempt_id"] == calls[0].kwargs["attempt_id"]
    assert calls[2].kwargs["active"] == 0
    assert all(call.kwargs["source"] == "owned_product_boundary" for call in calls)
    assert all(call.kwargs["scope"] == "declared_product_invocation" for call in calls)
    assert len({call.kwargs["product_invocation_id"] for call in calls}) == 1
    assert invocation.closed and invocation.retained_results == 0


def test_p5_launch_admission_recomputes_budget_after_verification(p5_manifest_boundary, monkeypatch):
    state = p5_manifest_boundary
    clock = [0.0]
    monkeypatch.setattr(product, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def verify(*args):
        clock[0] = 3.0
        return state.verified

    state.verifier.side_effect = verify
    fd = state.verified.fd
    with product.ProductInvocation(state.root, deadline=5.0) as invocation:
        result = _p5_execute(state, invocation)
        assert result.started and result.exit_code == 0
        assert state.native.call_args.kwargs["timeout"] == 2.0
        assert state.native.call_args.kwargs["pass_fds"] == (fd,)
        assert state.native.call_args.args[0][2:] == [str(fd), state.plan.script_path]
    state.native.assert_called_once()
    state.close.assert_called_once_with()
    assert state.verified.fd == -1


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan")])
def test_p5_launch_admission_denies_impossible_timeout(p5_manifest_boundary, timeout):
    state = p5_manifest_boundary
    with pytest.raises(product.ProductLifecycleError, match="budget"):
        product.execute_plan(state.plan, timeout=timeout)
    state.native.assert_not_called()
    state.close.assert_called_once_with()
    assert state.verified.fd == -1


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_p5_launch_admitted_work_drains_before_fd_cleanup(p5_manifest_boundary, monkeypatch, stop):
    state = p5_manifest_boundary
    clock = [0.0]
    monkeypatch.setattr(product, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    entered = threading.Event()
    release = threading.Event()
    terminal = state.native.return_value
    fd = state.verified.fd

    def native(*args, **kwargs):
        assert kwargs["timeout"] == 5.0
        assert kwargs["pass_fds"] == (fd,)
        entered.set()
        assert release.wait(2)
        return terminal

    state.native.side_effect = native
    invocation = product.ProductInvocation(state.root, deadline=5.0)
    with invocation, ThreadPoolExecutor(1) as pool:
        future = pool.submit(_p5_execute, state, invocation)
        closer = threading.Thread(target=invocation.close)
        try:
            assert entered.wait(2)
            if stop == "cancel":
                invocation.cancel()
            else:
                clock[0] = 6.0
            with pytest.raises(product.ProductLifecycleError, match=stop):
                _p5_execute(state, invocation)
            closer.start()
            assert not invocation.closed and invocation.counts["active"] == 1
            assert state.verified.fd == fd
            state.close.assert_not_called()
            assert "product_close" not in [call.args[0] for call in state.observer.call_args_list]
        finally:
            release.set()
            if closer.ident is not None:
                closer.join(timeout=2)
        result = future.result(timeout=2)
        assert result.started and result.exit_code == 0
        assert not closer.is_alive()
    state.native.assert_called_once()
    state.close.assert_called_once_with()
    assert state.verified.fd == -1
    assert invocation.closed and invocation.retained_results == invocation.counts["active"] == 0
    assert [call.args[0] for call in state.observer.call_args_list] == [
        "product_attempt", "product_attempt_end", "product_close",
    ]
    assert state.observer.call_args_list[1].kwargs["outcome"] == "terminal"


@pytest.mark.parametrize("stage", ["verification", "binding"])
@pytest.mark.parametrize("stop", ["deadline", "cancel"])
def test_p5_d1_launch_admission_denial_is_covered(minimal_workspace, tmp_path, stage, stop):
    from bench import evidence_benchmark as benchmark
    from tests.test_evidence_benchmark import _bootstrapped_ledger

    ledger = _bootstrapped_ledger(tmp_path)
    source_hashes = {
        "executor": hashlib.sha256(Path(product.__file__).read_bytes()).hexdigest(),
        "regression": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    code = textwrap.dedent(f"""
        import hashlib
        from pathlib import Path
        from unittest.mock import Mock
        from pytest import MonkeyPatch
        import greedy_token.executors as product
        import tests.test_executors as regression
        from greedy_token.cheap_llm import _observe_emit
        from tests.test_executors import (
            p5_manifest_boundary, test_p5_launch_admission_denies_stop_during_trust,
        )
        assert hashlib.sha256(Path(product.__file__).read_bytes()).hexdigest() == {source_hashes['executor']!r}
        assert hashlib.sha256(Path(regression.__file__).read_bytes()).hexdigest() == {source_hashes['regression']!r}
        native_guard = product.subprocess.run
        with MonkeyPatch.context() as patcher:
            fixture = p5_manifest_boundary.__wrapped__(Path({str(minimal_workspace)!r}), patcher)
            state = next(fixture)
            state.native.side_effect = native_guard
            state.observer = Mock(wraps=_observe_emit)
            patcher.setattr('greedy_token.cheap_llm._observe_emit', state.observer)
            try:
                test_p5_launch_admission_denies_stop_during_trust(
                    state, patcher, {stage!r}, {stop!r},
                )
            finally:
                fixture.close()
    """)
    raw = benchmark._run_observed_exec(
        code, root=minimal_workspace, ledger_path=ledger,
        case_id=f"p5-launch-admission-{stop}-{stage}", method="isolated_lifecycle", timeout=30.0,
    )
    observation = raw["observation"]
    print(raw["output"])
    print("P5_D1_ADMISSION_EVIDENCE " + json.dumps({
        "run_id": raw["run_id"], "source": "D1 independent JSONL ledger",
        "scope": observation["scope"], "coverage": observation["coverage"],
        "source_sha256": source_hashes,
        "python_scope": observation["python_scope"], "invocation": observation["invocation"],
        "events_sha256": observation["events_sha256"],
        "ledger_sha256": observation["ledger"]["file_sha256"],
        "expected": observation["ledger"]["expected"],
    }, sort_keys=True))
    assert raw["exit_code"] == 0, raw["output"]
    assert observation["coverage"]["complete"]
    assert observation["python_scope"]["native_launch_denied"] == 0
    assert observation["python_scope"]["native_launch_allowed"] == 0
    for metric in ("model_attempts", "llm_requests_sent"):
        assert observation["invocation"][metric] == {
            "value": 0, "status": "observed", "scope": "invocation",
        }
    events = observation["events"]
    owned = [event for event in events if event["kind"].startswith("product_")]
    assert [event["kind"] for event in owned] == [
        "product_attempt", "product_attempt_end", "product_close",
    ]
    assert owned[1]["outcome"] == "error"
    assert owned[1]["attempt_id"] == owned[0]["attempt_id"]
    assert owned[2]["active"] == 0
    assert owned[2]["epoch"] <= next(event["epoch"] for event in events if event["kind"] == "child_exit")
    assert all(event["source"] == "owned_product_boundary" for event in owned)
    assert all(event["scope"] == "declared_product_invocation" for event in owned)
    replay = benchmark._replay_observation(observation)
    assert replay["coverage"] == observation["coverage"]
    assert replay["invocation"] == observation["invocation"]


def test_p5_expired_deadline_denies_before_dispatch(p5_boundary):
    state = p5_boundary
    with product.ProductInvocation(state.root, deadline=time.monotonic() - 1) as invocation:
        with pytest.raises(product.ProductLifecycleError, match="deadline"):
            _p5_execute(state, invocation)
    state.runner.assert_not_called()


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_p5_cancel_or_deadline_stops_retry_without_late_dispatch(p5_boundary, monkeypatch, stop):
    state = p5_boundary
    clock = [0.0]
    monkeypatch.setattr(product.time, "monotonic", lambda: clock[0])
    with product.ProductInvocation(state.root, deadline=5.0, max_retries=2) as invocation:
        def producer(*args, **kwargs):
            if stop == "cancel":
                invocation.cancel()
            else:
                clock[0] = 6.0
            raise product.ProductRetryError("pre-terminal failure")

        state.runner.side_effect = producer
        with pytest.raises(product.ProductLifecycleError, match=stop):
            _p5_execute(state, invocation)
    assert state.runner.call_count == 1
    assert invocation.closed


def test_p5_close_drains_owned_background_before_cleanup(p5_boundary):
    state = p5_boundary
    entered = threading.Event()
    release = threading.Event()
    terminal = state.runner.return_value

    def producer(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return terminal

    state.runner.side_effect = producer
    invocation = product.ProductInvocation(state.root)
    with invocation, ThreadPoolExecutor(1) as pool:
        future = pool.submit(_p5_execute, state, invocation)
        closer = threading.Thread(target=invocation.close)
        try:
            assert entered.wait(2)
            closer.start()
            assert not invocation.closed
            assert invocation.counts["active"] == 1
        finally:
            release.set()
        assert future.result(timeout=2).result_status == "produced"
        closer.join(timeout=2)
        assert not closer.is_alive()
        assert invocation.closed
        assert invocation.counts["active"] == 0
        assert invocation.retained_results == 0


def test_p5_duplicate_deadline_does_not_abandon_owned_producer(p5_boundary):
    state = p5_boundary
    entered = threading.Event()
    release = threading.Event()
    terminal = state.runner.return_value

    def producer(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return terminal

    state.runner.side_effect = producer
    with product.ProductInvocation(state.root, deadline=time.monotonic() + 0.1) as invocation:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(_p5_execute, state, invocation)
            try:
                assert entered.wait(2)
                with pytest.raises(product.ProductLifecycleError, match="deadline"):
                    _p5_execute(state, invocation)
                assert invocation.counts["active"] == 1
            finally:
                release.set()
            assert future.result(timeout=2).result_status == "produced"
    assert invocation.closed and invocation.counts["active"] == 0
    assert state.runner.call_count == 1


def test_p5_recursive_same_request_is_denied_instead_of_looping(p5_boundary):
    state = p5_boundary
    with product.ProductInvocation(state.root) as invocation:
        state.runner.side_effect = lambda *a, **k: _p5_execute(state, invocation)
        with pytest.raises(product.ProductLifecycleError, match="recursive"):
            _p5_execute(state, invocation)
    assert state.runner.call_count == 1


@pytest.mark.parametrize("params", [
    {"max_requests": 0}, {"max_attempts": 0}, {"max_retries": -1},
    {"max_fallbacks": -1}, {"deadline": float("nan")},
])
def test_p5_invalid_lifecycle_bounds_are_rejected(p5_boundary, params):
    with pytest.raises(ValueError):
        product.ProductInvocation(p5_boundary.root, **params)


def _tool_invocation(root: Path) -> dict:
    return {
        "command_argv": (
            "rg",
            "-n",
            "-F",
            "baseUrl",
            "--max-count",
            "50",
            ".",
        ),
        "command_cwd": root,
    }


@allure.story("Tool tier")
@allure.title("Execute task runs ripgrep and returns matches without RAG fallback")
def test_execute_task_tool_finds_baseurl(minimal_workspace: Path) -> None:
    projects = minimal_workspace / "projects"
    for idx in range(3):
        (projects / f"sample-{idx}.js").write_text(
            f"const baseUrl = 'http://localhost/{idx}';\n",
            encoding="utf-8",
        )
    with allure.step("Execute find task via tool tier"):
        result = execute_task("find baseUrl in sample.js", minimal_workspace)
        attach_text("output", result.output)
        attach_json("decision", {"target": result.decision.target, "exit_code": result.exit_code})
    with allure.step("Verify ripgrep match without RAG fallback"):
        assert result.decision.target == "tool"
        assert result.used_rag_fallback is False
        assert result.exit_code == 0
        assert "baseUrl" in result.output
        assert "sample.js" in result.output


@allure.story("RAG tier")
@allure.title("Execute task on RAG route returns formatted doc hits")
def test_execute_task_rag_route_returns_hits(minimal_workspace: Path) -> None:
    with allure.step("Execute RAG-routed documentation question"):
        result = execute_task("which -D flag for baseUrl", minimal_workspace)
        attach_text("output", result.output)
        attach_json("decision", {"target": result.decision.target, "used_rag_fallback": result.used_rag_fallback})
    with allure.step("Verify RAG hits in output"):
        assert result.decision.target == "rag"
        assert "RAG hits" in result.output or "baseUrl" in result.output
        assert result.used_rag_fallback is False


@allure.story("Execute safety")
@allure.title("Execute plan refuses non-read-only Ollama route")
def test_execute_plan_refuses_non_readonly(minimal_workspace: Path) -> None:
    from greedy_token.executors import execute_plan, plan_run
    from greedy_token.router import RouteDecision

    with allure.step("Build non-read-only Ollama plan (batch-inventory)"):
        decision = RouteDecision(
            target="ollama",
            route_id="ollama-inventory",
            confidence=0.9,
            matched=["batch inventory"],
            command="./scripts/ollama/batch-inventory.sh",
            note="",
            domains=[],
            read_only=False,
        )
        attach_json("decision", {"target": decision.target, "read_only": decision.read_only})
        plan = plan_run(decision, "batch inventory", minimal_workspace)
        code, out = execute_plan(plan)
        attach_text("execute output", out)
        attach_text("exit code", str(code))
    with allure.step("Verify execute is refused for non-readonly"):
        assert plan.executable is False
        assert code == 1
        assert "Refusing --execute" in out


@allure.story("Execute safety")
@allure.title("plan_run marks stdout-only audit-skill as executable")
def test_plan_run_audit_skill_executable(minimal_workspace: Path) -> None:
    from greedy_token.executors import plan_run
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="ollama",
        route_id="ollama-audit-skill",
        confidence=0.9,
        matched=["audit skill"],
        command="./scripts/ollama/audit-skill.sh",
        note="",
        domains=[],
        read_only=True,
    )
    plan = plan_run(decision, "audit skill configurator-boolean", minimal_workspace)
    assert plan.executable is True
    assert "audit-skill" in (plan.command or "")


@patch("greedy_token.executors._rag_fallback_output")
@patch("greedy_token.executors.execute_plan")
@patch("greedy_token.executors.route_task")
@allure.story("RAG fallback")
@allure.title("Task executor falls back to RAG when ripgrep output is empty")
def test_execute_task_rag_fallback_on_weak_rg(
    mock_route,
    mock_execute,
    mock_rag_fallback,
    minimal_workspace: Path,
) -> None:
    from greedy_token.router import RouteDecision

    mock_route.return_value = RouteDecision(
        target="tool",
        route_id="tool-rg-search",
        confidence=0.9,
        matched=["find"],
        command="rg ...",
        note="",
        domains=[],
        read_only=True,
        tool="rg",
        **_tool_invocation(minimal_workspace),
    )
    mock_execute.return_value = (0, "")
    mock_rag_fallback.return_value = "RAG hits for: baseUrl\n\n1. chunk"
    with allure.step("Execute find task with empty ripgrep output"):
        result = execute_task("find baseUrl in missing-file.html", minimal_workspace)
        attach_text("output", result.output)
        attach_text("used_rag_fallback", str(result.used_rag_fallback))
    with allure.step("Verify RAG fallback was used"):
        assert result.used_rag_fallback is True
        assert "fallback RAG" in result.output or "RAG hits" in result.output


@allure.story("Cursor tier")
@allure.title("Task executor refuses --execute on cursor tier with guidance")
def test_execute_task_cursor_refuses_execute(minimal_workspace: Path) -> None:
    with allure.step("Execute refactor task routed to cursor"):
        result = execute_task("refactor monolithic header shell layout", minimal_workspace)
        attach_json("decision", {"target": result.decision.target, "exit_code": result.exit_code})
        attach_text("output", result.output)
    with allure.step("Verify cursor tier refuses execute with Agent chat guidance"):
        assert result.decision.target == "cursor"
        assert result.exit_code == 1
        assert "Refusing --execute" in result.output
        assert "Cursor" in result.output or "Agent chat" in result.output


@patch("greedy_token.executors.subprocess.run")
@allure.story("Execute safety")
@allure.title("execute_plan returns exit 124 when the command times out")
def test_execute_plan_timeout(mock_run, minimal_workspace: Path) -> None:
    import subprocess

    from greedy_token.executors import execute_plan, plan_run
    from greedy_token.router import RouteDecision

    mock_run.side_effect = subprocess.TimeoutExpired("cmd", 120)
    decision = RouteDecision(
        target="python",
        route_id="script-check-meta-sync",
        confidence=1.0,
        matched=["meta"],
        command="python scripts/meta-sync-check.py",
        note="",
        domains=[],
        read_only=True,
    )
    plan = plan_run(decision, "check meta", minimal_workspace)
    with allure.step("Run an executable plan whose subprocess times out"):
        code, out = execute_plan(plan)
        attach_text("execute output", out)
        attach_text("exit code", str(code))
    with allure.step("Verify timeout is caught and surfaced"):
        assert code == 124
        assert "timed out" in out


@allure.story("Execute safety")
@allure.title("execute_plan converts process launch OS errors into stable exit codes")
@pytest.mark.parametrize(
    ("error", "expected_code", "message"),
    [
        (FileNotFoundError("missing-bin"), 127, "Executable not found"),
        (OSError("exec format"), 126, "Cannot execute command"),
    ],
)
def test_execute_plan_handles_process_launch_errors(
    minimal_workspace: Path,
    error: OSError,
    expected_code: int,
    message: str,
) -> None:
    from greedy_token.executors import execute_plan, plan_run
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="python",
        route_id="script-check-meta-sync",
        confidence=1.0,
        matched=["meta"],
        command="python scripts/meta-sync-check.py",
        note="",
        domains=[],
        read_only=True,
    )
    plan = plan_run(decision, "check meta", minimal_workspace)
    assert plan.executable is True
    with patch("greedy_token.executors.subprocess.run", side_effect=error):
        code, out = execute_plan(plan)
    assert code == expected_code
    assert message in out


@allure.story("Execute safety")
@allure.title("execute_plan refuses executable flag without trusted structured argv")
def test_execute_plan_refuses_missing_structured_argv() -> None:
    from greedy_token.executors import RunPlan, execute_plan
    from greedy_token.router import RouteDecision

    plan = RunPlan(
        decision=RouteDecision(
            target="python",
            route_id="forged",
            confidence=1.0,
            matched=[],
            command="echo unsafe",
            note="",
            domains=[],
            read_only=True,
        ),
        command="echo unsafe",
        dry_run_output="echo unsafe",
        executable=True,
    )
    code, out = execute_plan(plan)
    assert code == 1
    assert "structured trusted argv is missing" in out


@allure.story("Execute safety")
@allure.title("plan_run refuses missing or forged tool invocation metadata")
@pytest.mark.parametrize(
    ("argv", "cwd"),
    [
        (None, Path(".")),
        (("rg", "--max-count", "50", "."), None),
        (("rm", "--max-count", "50", "."), Path(".")),
    ],
)
def test_plan_run_refuses_untrusted_tool_metadata(
    minimal_workspace: Path,
    argv: tuple[str, ...] | None,
    cwd: Path | None,
) -> None:
    from greedy_token.executors import plan_run
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="tool",
        route_id="forged-tool",
        confidence=1.0,
        matched=["find"],
        command="forged",
        note="",
        domains=[],
        read_only=True,
        tool="rg",
        command_argv=argv,
        command_cwd=minimal_workspace if cwd is not None else None,
    )
    plan = plan_run(decision, "find x", minimal_workspace)
    assert plan.executable is False
    assert plan.refusal_reason


@allure.story("Plan run")
@allure.title("plan_run builds python tier command with wrapper read_only")
def test_plan_run_python(minimal_workspace: Path) -> None:
    from greedy_token.executors import plan_run
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="python",
        route_id="script-check-meta-sync",
        confidence=1.0,
        matched=["meta"],
        command="python scripts/meta-sync-check.py",
        note="",
        domains=[],
        read_only=True,
    )
    plan = plan_run(decision, "check meta", minimal_workspace)
    assert plan.executable is True
    assert "meta-sync-check.py" in plan.command


@allure.story("Plan run")
@allure.title("plan_run returns RAG dry-run output")
def test_plan_run_rag(minimal_workspace: Path) -> None:
    from greedy_token.executors import plan_run
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="rag",
        route_id="rag-lookup",
        confidence=1.0,
        matched=["rag"],
        command=None,
        note="",
        domains=["config"],
    )
    plan = plan_run(decision, "baseUrl -D flag", minimal_workspace)
    assert plan.executable is False
    assert "RAG hits" in plan.dry_run_output or "No RAG hits" in plan.dry_run_output


@allure.story("Plan run")
@allure.title("plan_run returns cursor guidance")
def test_plan_run_cursor(minimal_workspace: Path) -> None:
    from greedy_token.executors import plan_run
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="cursor",
        route_id="cursor-fallback",
        confidence=0.3,
        matched=[],
        command=None,
        note="",
        domains=[],
    )
    plan = plan_run(decision, "refactor everything", minimal_workspace)
    assert "Cursor chat" in plan.dry_run_output


@allure.story("Plan run")
@allure.title("plan_run returns fallback for unknown target")
def test_plan_run_unknown(minimal_workspace: Path) -> None:
    from greedy_token.executors import plan_run
    from greedy_token.router import RouteDecision

    decision = RouteDecision(
        target="unknown",
        route_id="x",
        confidence=0.0,
        matched=[],
        command=None,
        note="",
        domains=[],
    )
    plan = plan_run(decision, "task", minimal_workspace)
    assert plan.dry_run_output == "No executor."


@patch("greedy_token.executors._rag_fallback_output")
@patch("greedy_token.executors.execute_plan")
@patch("greedy_token.executors.route_task")
@allure.story("Filtered output")
@allure.title("Task executor appends RAG when filtered rg output is short")
def test_execute_task_filtered_short_rg(
    mock_route,
    mock_execute,
    mock_rag_fallback,
    minimal_workspace: Path,
) -> None:
    from greedy_token.router import RouteDecision

    mock_route.return_value = RouteDecision(
        target="tool",
        route_id="tool-rg-search",
        confidence=0.9,
        matched=["find"],
        command="rg ...",
        note="",
        domains=[],
        read_only=True,
        tool="rg",
        **_tool_invocation(minimal_workspace),
    )
    mock_execute.return_value = (0, ".cursor/hooks/noise\nbaseUrl\n")
    mock_rag_fallback.return_value = "RAG hits\n\n1. chunk"
    result = execute_task("find baseUrl", minimal_workspace)
    assert result.used_rag_fallback is True
    assert "Additional RAG" in result.output or "RAG hits" in result.output


@patch("greedy_token.executors.execute_plan")
@patch("greedy_token.executors.route_task")
@allure.story("Filtered output")
@allure.title("Task executor returns filtered rg output without RAG when sufficient")
def test_execute_task_filtered_sufficient(
    mock_route,
    mock_execute,
    minimal_workspace: Path,
) -> None:
    from greedy_token.router import RouteDecision

    mock_route.return_value = RouteDecision(
        target="tool",
        route_id="tool-rg-search",
        confidence=0.9,
        matched=["find"],
        command="rg ...",
        note="",
        domains=[],
        read_only=True,
        tool="rg",
        **_tool_invocation(minimal_workspace),
    )
    lines = "\n".join(f"line{i}: baseUrl" for i in range(5))
    mock_execute.return_value = (0, ".cursor/hooks/noise\n" + lines)
    result = execute_task("find baseUrl", minimal_workspace)
    assert result.used_rag_fallback is False
    assert "baseUrl" in result.output


@patch("greedy_token.executors._rag_fallback_output", return_value=None)
@patch("greedy_token.executors.execute_plan")
@patch("greedy_token.executors.route_task")
@allure.story("RAG fallback")
@allure.title("Task executor returns raw output when RAG fallback empty")
def test_execute_task_weak_rg_no_fallback(
    mock_route,
    mock_execute,
    mock_rag,
    minimal_workspace: Path,
) -> None:
    from greedy_token.router import RouteDecision

    mock_route.return_value = RouteDecision(
        target="tool",
        route_id="tool-rg-search",
        confidence=0.9,
        matched=["find"],
        command="rg ...",
        note="",
        domains=[],
        read_only=True,
        tool="rg",
        **_tool_invocation(minimal_workspace),
    )
    mock_execute.return_value = (2, "")
    result = execute_task("find baseUrl", minimal_workspace)
    assert result.used_rag_fallback is False


@allure.story("RAG domains")
@allure.title("_infer_rag_domains detects config and stacks tokens")
def test_infer_rag_domains() -> None:
    from greedy_token.executors import _infer_rag_domains

    config_domains = _infer_rag_domains("explain baseUrl in testconfig")
    assert config_domains == ["config"]
    stacks = _infer_rag_domains("openapi spring stack flows")
    assert "stacks" in stacks
    analytics = _infer_rag_domains("allure dashboard quality gate")
    assert analytics == ["analytics"]
    testing = _infer_rag_domains("page object locator pattern")
    assert testing == ["testing"]
    assert _infer_rag_domains("random question") is None


# Every keyword must map to its exact domain. This pins the routing vocabulary so
# any single-token edit (case flip, wording change) is caught.
_DOMAIN_TOKENS = [
    ("quality gate", "analytics"),
    ("allure dashboard", "analytics"),
    ("analytics grid", "analytics"),
    ("sparkline", "analytics"),
    ("allure agent", "analytics"),
    ("metrics catalog", "analytics"),
    ("chart matrix", "analytics"),
    ("allure shell", "analytics"),
    ("analytics index", "analytics"),
    ("page object", "testing"),
    ("po locator", "testing"),
    ("selenide", "testing"),
    ("test pyramid", "testing"),
    ("test layer", "testing"),
    ("ci workflow", "testing"),
    ("allurerc", "testing"),
    ("testconfig", "config"),
    ("test config", "config"),
    ("baseurl", "config"),
    ("base url", "config"),
    ("healthcheck", "config"),
    ("configurator", "config"),
    ("-d flag", "config"),
    ("property override", "config"),
    ("stack", "stacks"),
    ("openapi", "stacks"),
    ("spring", "stacks"),
    ("flows/login", "stacks"),
]


@allure.story("RAG domains")
@allure.title("_infer_rag_domains maps every keyword to its exact domain")
@pytest.mark.parametrize(("token", "domain"), _DOMAIN_TOKENS)
def test_infer_rag_domains_every_token(token: str, domain: str) -> None:
    from greedy_token.executors import _infer_rag_domains

    result = _infer_rag_domains(f"please handle {token} for me")
    assert result is not None
    assert domain in result


@allure.story("Execute safety")
@allure.title("execute_plan uses RG_TIMEOUT for tool tier and SCRIPT_TIMEOUT otherwise")
@pytest.mark.parametrize(
    ("target", "expected_attr"),
    [("tool", "RG_TIMEOUT"), ("python", "SCRIPT_TIMEOUT")],
)
def test_execute_plan_timeout_selection(
    target: str, expected_attr: str, minimal_workspace: Path
) -> None:
    from greedy_token import tool_paths
    from greedy_token.executors import RunPlan, execute_plan
    from greedy_token.router import RouteDecision

    plan = RunPlan(
        decision=RouteDecision(
            target=target, route_id="x", confidence=1.0, matched=[], command="echo hi",
            note="", domains=[], read_only=True,
        ),
        command="echo hi",
        dry_run_output="echo hi",
        executable=True,
        argv=("echo", "hi"),
        cwd=minimal_workspace,
        authorization="test-fixture",
    )
    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stdout = "out"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _Proc()

    with patch("greedy_token.executors.subprocess.run", fake_run):
        code, _ = execute_plan(plan)
    assert captured["timeout"] == getattr(tool_paths, expected_attr)
    assert code == 0


@allure.story("Execute safety")
@allure.title("execute_plan concatenates stdout and stderr and passes returncode")
def test_execute_plan_stdout_stderr_concat(tmp_path: Path) -> None:
    from greedy_token.executors import RunPlan, execute_plan
    from greedy_token.router import RouteDecision

    plan = RunPlan(
        decision=RouteDecision(
            target="python", route_id="x", confidence=1.0, matched=[], command="c",
            note="", domains=[], read_only=True,
        ),
        command="c",
        dry_run_output="DRY",
        executable=True,
        argv=("echo", "hi"),
        cwd=tmp_path,
        authorization="test-fixture",
    )

    class _Proc:
        returncode = 7
        stdout = "OUT-"
        stderr = "ERR"

    with patch("greedy_token.executors.subprocess.run", lambda cmd, **kw: _Proc()):
        code, out = execute_plan(plan)
    assert code == 7
    assert out == "OUT-ERR"


@allure.story("Execute safety")
@allure.title("execute_plan reports the observed output — empty stays empty")
def test_execute_plan_empty_output_stays_empty(tmp_path: Path) -> None:
    from greedy_token.executors import RunPlan, execute_plan
    from greedy_token.router import RouteDecision

    plan = RunPlan(
        decision=RouteDecision(
            target="python", route_id="x", confidence=1.0, matched=[], command="c",
            note="", domains=[], read_only=True,
        ),
        command="c",
        dry_run_output="DRY-RUN-FALLBACK",
        executable=True,
        argv=("echo", "hi"),
        cwd=tmp_path,
        authorization="test-fixture",
    )

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    with patch("greedy_token.executors.subprocess.run", lambda cmd, **kw: _Proc()):
        code, out = execute_plan(plan)
    # The invocation description is not the run's output — padding empty
    # stdout with it would feed the evaluator a result that never happened.
    assert out == ""
    assert code == 0


@allure.story("Execute safety")
@allure.title("execute_plan refuse message is exact and returns exit 1")
def test_execute_plan_refuse_message_exact() -> None:
    from greedy_token.executors import RunPlan, execute_plan
    from greedy_token.router import RouteDecision

    plan = RunPlan(
        decision=RouteDecision(
            target="ollama", route_id="x", confidence=1.0, matched=[], command="c",
            note="", domains=[], read_only=False,
        ),
        command="c",
        dry_run_output="DRY",
        executable=False,
    )
    code, out = execute_plan(plan)
    assert code == 1
    assert out == (
        "Refusing --execute: route is not authorised for execution.\n"
        "Dry-run:\nDRY\n\n"
        "read_only is metadata, not execution authority."
    )


@allure.story("RAG fallback")
@allure.title("_rag_fallback_output threads inferred domains and limit=5 into search_rag")
def test_rag_fallback_output_search_args(minimal_workspace: Path) -> None:
    from greedy_token import executors

    calls: list[dict] = []

    def fake_search(task, root, *, domains=None, limit=None):
        calls.append({"task": task, "root": root, "domains": domains, "limit": limit})
        return ["hit"] if domains else []

    with patch.object(executors, "search_rag", fake_search), patch.object(
        executors, "format_hits", lambda task, hits: f"FMT:{task}:{len(hits)}"
    ):
        out = executors._rag_fallback_output("allure dashboard metrics", minimal_workspace)
    # First call uses inferred domains + limit=5; result formatted via format_hits.
    assert calls[0]["domains"] == ["analytics"]
    assert calls[0]["limit"] == 5
    assert out == "FMT:allure dashboard metrics:1"


@allure.story("RAG fallback")
@allure.title("_rag_fallback_output retries with domains=None then returns None when empty")
def test_rag_fallback_output_second_call_and_empty(minimal_workspace: Path) -> None:
    from greedy_token import executors

    calls: list[dict] = []

    def fake_search(task, root, *, domains=None, limit=None):
        calls.append({"domains": domains, "limit": limit})
        return []

    with patch.object(executors, "search_rag", fake_search):
        out = executors._rag_fallback_output("allure dashboard", minimal_workspace)
    assert out is None
    # Second (fallback) call drops the domain filter but keeps limit=5.
    assert calls[1]["domains"] is None
    assert calls[1]["limit"] == 5


@allure.story("Plan run")
@allure.title("plan_run rag passes task and hits to format_hits in order")
def test_plan_run_rag_format_args(minimal_workspace: Path) -> None:
    from greedy_token import executors
    from greedy_token.router import RouteDecision

    seen: dict[str, object] = {}

    with patch.object(executors, "search_rag", lambda task, root, **kw: ["h1", "h2"]), patch.object(
        executors,
        "format_hits",
        lambda task, hits: seen.update({"task": task, "hits": hits}) or "FMT",
    ):
        decision = RouteDecision(
            target="rag", route_id="rag", confidence=1.0, matched=[], command=None,
            note="", domains=["config"],
        )
        plan = executors.plan_run(decision, "baseUrl question", minimal_workspace)
    assert plan.dry_run_output == "FMT"
    assert seen["task"] == "baseUrl question"
    assert seen["hits"] == ["h1", "h2"]


_GIT_RECENT_STUB = (
    "import json, sys\n"
    "print(json.dumps({'ok': True, 'argv': sys.argv[1:]}))\n"
)


def _approved_git_recent(root: Path) -> None:
    from greedy_token.trust import approve_script

    script = root / "scripts" / "git-recent.py"
    script.write_text(_GIT_RECENT_STUB, encoding="utf-8")
    script.chmod(0o755)
    approve_script(root, "scripts/git-recent.py")


@allure.story("Prompt-derived args")
@allure.title("run --execute passes prompt-derived --count/--compact to the script")
def test_execute_task_derives_prompt_args(minimal_workspace: Path) -> None:
    _approved_git_recent(minimal_workspace)
    with allure.step("Numbered listing prompt routes to python-git-recent"):
        result = execute_task("покажи последние 50 коммитов", minimal_workspace)
        attach_json(
            "result",
            {
                "route_id": result.decision.route_id,
                "exit_code": result.exit_code,
                "started": result.started,
            },
        )
    with allure.step("argv carries the first-match args_from_prompt render"):
        assert result.decision.route_id == "python-git-recent"
        assert result.started is True
        assert result.exit_code == 0
        argv = json.loads(result.output.strip().splitlines()[-1])["argv"]
        assert argv == ["--count", "50", "--compact"]


@allure.story("Prompt-derived args")
@allure.title("prompt without an args_from_prompt match runs the fixed argv")
def test_execute_task_no_prompt_args_match(minimal_workspace: Path) -> None:
    _approved_git_recent(minimal_workspace)
    with allure.step("Alias prompt routes to python-git-recent but matches no spec"):
        result = execute_task("what changed in recent commits", minimal_workspace)
    with allure.step("argv stays the route's fixed command args"):
        assert result.decision.route_id == "python-git-recent"
        assert result.exit_code == 0
        argv = json.loads(result.output.strip().splitlines()[-1])["argv"]
        assert argv == []



@allure.story("Tool tier")
@allure.title("Execute task scopes rg to the existing prompt path — F5")
def test_execute_task_tool_scopes_prompt_path(minimal_workspace: Path) -> None:
    lab = minimal_workspace / "lab"
    lab.mkdir()
    (lab / "users.json").write_text(
        '[\n'
        '  {"id": 1, "email": "ada@example.com"},\n'
        '  {"id": 2},\n'
        '  {"id": 3, "email": "grace@example.com"}\n'
        "]\n",
        encoding="utf-8",
    )
    # Self-referential noise: docs mentioning the path must not outrank the
    # file itself — the scoped operand makes the file the whole search space.
    (minimal_workspace / "notes.md").write_text(
        "mentions email and lab/users.json\n" * 40, encoding="utf-8"
    )
    with allure.step("Execute scoped find — pattern email, operand lab/users.json"):
        result = execute_task("find email in lab/users.json", minimal_workspace)
        attach_text("output", result.output)
    with allure.step("argv carries pattern + scoped operand; only file lines return"):
        assert result.decision.target == "tool"
        assert result.exit_code == 0
        assert result.decision.command_argv is not None
        sep = result.decision.command_argv.index("--")
        assert result.decision.command_argv[sep + 1 :] == ("email", "lab/users.json")
        assert "ada@example.com" in result.output
        assert "grace@example.com" in result.output
        assert "notes.md" not in result.output


@allure.story("Tool tier")
@allure.title("Tool output is capped with a truncation marker")
def test_execute_task_tool_output_cap(minimal_workspace: Path) -> None:
    (minimal_workspace / "big.py").write_text(
        "".join(f"x{i} = 'needle'\n" for i in range(40)), encoding="utf-8"
    )
    with allure.step("Execute scoped find returning more hits than the cap"):
        result = execute_task("find needle in big.py", minimal_workspace)
        attach_text("output", result.output)
    with allure.step("Output holds the cap plus the truncation marker"):
        assert result.exit_code == 0
        assert "needle" in result.output
        assert "truncated" in result.output
        assert len(result.output.splitlines()) <= 31
