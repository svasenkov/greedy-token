"""Public E2E evidence benchmark contracts (the full run is a CI artifact job)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import allure
from bench import evidence_benchmark as benchmark
from greedy_token.cheap_llm import clear_cheap_llm_probe_cache

pytestmark = [
    allure.epic("Evidence benchmark"),
    allure.parent_suite("Evidence benchmark"),
    allure.feature("Frozen public corpus"),
    allure.suite("Evidence benchmark"),
]

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "bench" / "evidence_corpus.v1.yaml"
LOCK = REPO_ROOT / "bench" / "evidence_corpus.v1.sha256"


def _corpus() -> dict:
    return yaml.safe_load(CORPUS.read_text(encoding="utf-8"))


@allure.story("Freeze")
@allure.title("Versioned corpus hash, provenance, languages, and immutable status agree")
def test_evidence_corpus_frozen_lock_and_provenance() -> None:
    corpus, lock = benchmark._load_corpus(CORPUS, LOCK)
    meta = corpus["corpus"]
    assert lock["verified"] is True
    assert meta["status"] == "frozen"
    assert meta["version"] == "1.0.0"
    assert meta["frozen_at"] == "2026-07-31"
    assert set(meta["languages"]) == {"en", "ru"}
    assert meta["provenance"]["id"] == "synthetic-public-fixture-v1"
    assert meta["exclusions"]["route_examples_reused"] is False
    assert meta["exclusions"]["route_patterns_reused_as_cases"] is False
    assert benchmark._package_version() == "0.19.0"


@allure.story("Freeze")
@allure.title("Corpus does not duplicate route examples or route-pattern entries")
def test_evidence_corpus_has_no_route_example_or_pattern_case_reuse() -> None:
    corpus = _corpus()
    tasks = {
        str(case["task"]).casefold().strip()
        for case in corpus["cases"]
    }
    examples = yaml.safe_load(
        (REPO_ROOT / "bench" / "route_examples.yaml").read_text(encoding="utf-8")
    )
    example_tasks = {
        str(case["task"]).casefold().strip()
        for case in examples["cases"]
    }
    route_files = [
        REPO_ROOT / "src" / "greedy_token" / "config" / "routes.yaml",
        REPO_ROOT / "examples" / "routes" / "workspace-routes.yaml",
    ]
    patterns: set[str] = set()
    for route_file in route_files:
        data = yaml.safe_load(route_file.read_text(encoding="utf-8"))
        for route in data.get("routes") or []:
            patterns.update(
                str(pattern).casefold().strip()
                for pattern in route.get("patterns") or []
            )
    assert tasks.isdisjoint(example_tasks)
    assert tasks.isdisjoint(patterns)
    assert all(
        example not in task
        for task in tasks
        for example in example_tasks
    )


@allure.story("Oracle schema")
@allure.title("Every task has a language, provenance, route target, and specific oracle")
def test_evidence_corpus_task_oracles() -> None:
    cases = _corpus()["cases"]
    assert {case["lang"] for case in cases} == {"en", "ru"}
    assert {
        case["expected_target"] for case in cases
    } == {"tool", "python", "rag", "cursor", "ollama"}
    for case in cases:
        assert case["provenance_id"] == "synthetic-public-fixture-v1"
        oracle = case["oracle"]
        operation = case["operation"]
        if operation == "search":
            assert oracle["expected_files"]
            assert oracle["expected_lines"]
            assert "expected_exit_code" in oracle
        elif operation == "script":
            assert oracle["output_contains"]
            assert "expected_exit_code" in oracle
        elif operation in ("rag", "fallback"):
            assert oracle["expected_chunk_ids"]
        elif operation == "escalation":
            assert oracle["expected_escalation"]
        else:
            assert operation == "route-only"


@allure.story("Deterministic route gate")
@allure.title("Frozen corpus routes in a temp workspace without pattern tuning")
def test_evidence_route_classification_in_temp_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = _corpus()
    with benchmark._ollama_stub() as url:
        monkeypatch.setenv("OLLAMA_URL", url)
        monkeypatch.setenv("OLLAMA_MODEL", "evidence-stub")
        monkeypatch.setenv("GREEDY_TOKEN_LOG", "0")
        clear_cheap_llm_probe_cache()
        benchmark._write_fixture(corpus, tmp_path)
        rows = benchmark._classify_routes(corpus["cases"], tmp_path)
    clear_cheap_llm_probe_cache()
    assert all(row["ok"] for row in rows)
    assert not any(row["false_cheap"] for row in rows)


@allure.story("Oracle scoring")
@allure.title("Failed execution is excluded from every savings field")
def test_failed_observation_never_counts_as_saved() -> None:
    case = next(
        case
        for case in _corpus()["cases"]
        if case["id"] == "tool-search-en"
    )
    observation = benchmark._finalize_observation(
        case,
        "greedy_cli",
        {
            "exit_code": 1,
            "output": "",
            "duration_ms": 15.0,
            "error": None,
        },
    )
    assert observation["success"] is False
    assert observation["savings"] == {
        "eligible": False,
        "status": "excluded_task_failed",
        "tokens_saved": None,
        "cost_saved_usd": None,
    }


@allure.story("Latency")
@allure.title("Nearest-rank p50/p95 includes complete attempt durations")
def test_evidence_percentiles() -> None:
    values = [1.0, 2.0, 3.0, 40.0]
    assert benchmark._nearest_percentile(values, 0.50) == 2.0
    assert benchmark._nearest_percentile(values, 0.95) == 40.0
    assert benchmark._nearest_percentile([], 0.95) is None


@allure.story("Billing")
@allure.title("Only authoritative billing is accepted; Cursor otherwise stays unknown")
def test_evidence_authoritative_metric_guard() -> None:
    unknown = benchmark._normalize_authoritative_metric(
        {"value": 1.25, "authoritative": False, "source": "estimate"},
        unit="USD",
        unknown_reason="no invoice",
    )
    assert unknown["value"] is None
    assert unknown["status"] == "unknown"
    measured = benchmark._normalize_authoritative_metric(
        {"value": 1.25, "authoritative": True, "source": "provider invoice"},
        unit="USD",
        unknown_reason="no invoice",
    )
    assert measured["value"] == 1.25
    assert measured["status"] == "measured"


@allure.story("Billing")
@allure.title("No measured savings while the greedy row has no billing source")
def test_evidence_no_savings_without_authoritative_agent_billing() -> None:
    case = next(
        case
        for case in _corpus()["cases"]
        if case["id"] == "tool-search-en"
    )
    raw = {
        "exit_code": 0,
        "output": "projects/app/config.py:3:E2E_ALPHA_SENTINEL",
        "duration_ms": 10.0,
        "error": None,
        "observation": benchmark._coverage_observed_report(
            _closed_run_events("fixture-run"),
            timed_out=False,
            exit_code=0,
            run_id="fixture-run",
        ),
    }
    cheap = benchmark._finalize_observation(case, "greedy_cli", raw)
    baseline = benchmark._finalize_observation(
        case,
        "agent_baseline",
        raw,
        evidence_level="live_host",
    )
    cheap["repetition"] = baseline["repetition"] = 1
    baseline["llm_tokens"] = benchmark._normalize_authoritative_metric(
        {"value": 100, "authoritative": True, "source": "host usage"},
        unit="tokens",
        unknown_reason="missing",
    )
    baseline["actual_cost_usd"] = benchmark._normalize_authoritative_metric(
        {"value": 0.25, "authoritative": True, "source": "provider invoice"},
        unit="USD",
        unknown_reason="missing",
    )
    benchmark._apply_authoritative_savings([cheap, baseline])
    # The greedy row is fully covered with zero sent requests — but there
    # is no independent billing source, so tokens/USD stay unknown and no
    # savings may be measured.
    assert cheap["savings"]["tokens_saved"] is None
    assert cheap["savings"]["cost_saved_usd"] is None
    assert cheap["savings"]["status"] != (
        "measured_authoritative_same_task_baseline"
    )
    assert baseline["savings"]["tokens_saved"] is None
    assert baseline["savings"]["cost_saved_usd"] is None


@allure.story("Metered guard")
@allure.title("Manual host adapter cannot be metered without explicit opt-in")
def test_evidence_metered_api_denied_by_default() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bench" / "evidence_benchmark.py"),
            "--mode",
            "live",
            "--host-command",
            "echo",
            "--host-billing",
            "metered",
            "--repetitions",
            "1",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "metered host adapter denied" in (proc.stdout + proc.stderr)


@allure.story("Freeze")
@allure.title("Tampered corpus fails before any executor runs")
def test_evidence_corpus_lock_mismatch(tmp_path: Path) -> None:
    tampered = tmp_path / CORPUS.name
    tampered.write_bytes(CORPUS.read_bytes() + b"\n# tampered\n")
    copied_lock = tmp_path / LOCK.name
    copied_lock.write_text(
        f"{hashlib.sha256(CORPUS.read_bytes()).hexdigest()}  {CORPUS.name}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="lock mismatch"):
        benchmark._load_corpus(tampered, copied_lock)


@pytest.mark.parametrize("complete_payload", [False, True])
def test_regrade_preserves_execution_and_rejects_legacy_lexical_pass(tmp_path, monkeypatch, complete_payload):
    corpus = _corpus()
    case = next(c for c in corpus["cases"] if c["operation"] == "fallback")
    raw = _fake_raw_with_complete_observation(case, output="[evidence-fallback-playbook] fallback text")
    row = benchmark._finalize_observation(case, "greedy_cli", raw)
    row["success"] = True
    row["observed_escalation"] = "tool->rag"
    row["repetition"] = 1
    if not complete_payload:
        row.pop("raw_result")
    source = benchmark._build_scorecard(
        corpus=corpus, lock={"verified": True, "sha256": benchmark._sha256(CORPUS)},
        mode="deterministic", repetitions=1, route_rows=[], observations=[row],
        live_probes={}, allow_metered_api=False,
    )
    source["execution_bindings"] = {"code_sha256": "historical-source"}
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    source_hash = benchmark._sha256(path)
    for name in ("_run_all", "_run_observed_cli", "_run_observed_mcp_case", "_classify_routes_observed"):
        monkeypatch.setattr(benchmark, name, lambda *a, **kw: pytest.fail("replay executed a task"))
    result = benchmark._regrade_scorecard(path, corpus, source["benchmark"]["corpus_lock"])
    graded = result["observations"][0]
    assert benchmark._sha256(path) == source_hash
    assert result["execution_bindings"] == source["execution_bindings"]
    assert result["benchmark"] == source["benchmark"]
    assert result["regrade"]["execution_repeated"] is False
    assert result["regrade"]["raw_ledger_replays"] == 1
    assert result["regrade"]["coverage_verdict_changes"] == 0
    assert result["regrade"]["changed_outcome_rows"] == 1
    assert graded["run_id"] == row["run_id"]
    assert graded["success"] is False
    assert graded["correctness_status"] == "unknown"
    assert graded["observed_escalation"] is None
    assert graded["raw_result"]["complete"] is complete_payload
    assert graded["payload"]["output_bytes"] == (len(raw["output"].encode()) if complete_payload else None)
    accounting = result["summary"]["corpus_accounting"]
    assert accounting["unique_tasks"] == len(corpus["cases"])
    assert accounting["measured_applicable_greedy_rows"] == 1
    assert result["gates"]["corpus_rows_complete"] is False
    assert result["summary"]["attempts"]["total_attempts"] is None
    assert result["summary"]["attempts"]["known_attempts_subtotal"] == 0


@pytest.mark.parametrize("key", ["model_attempts", "llm_requests_sent"])
def test_zero_completed_rejects_conflicting_invocation_count(tmp_path, key):
    corpus = _corpus()
    benchmark._write_fixture(corpus, tmp_path)
    case = next(c for c in corpus["cases"] if c["id"] == "rag-retrieval-en")
    body = corpus["fixture"]["files"]["docs/rag/evidence/retention-en.md"]
    raw = _fake_raw_with_complete_observation(case, output="evidence-retention-en " + body)
    raw["observation"]["invocation"][key]["value"] = 1
    row = benchmark._finalize_observation(case, "greedy_cli", raw, root=tmp_path)
    assert row["success"] is True
    assert row["zero_completed"] is False
    row["zero_completed"] = True
    assert benchmark._zero_completion_ok(row) is False


def test_invocation_accounting_separates_complete_and_unknown_subtotals():
    corpus = _corpus()
    case = next(c for c in corpus["cases"] if c["operation"] == "search")
    raw = _fake_raw_with_complete_observation(case, output="irrelevant fixture output")
    complete = benchmark._finalize_observation(case, "greedy_cli", raw)
    incomplete = benchmark._finalize_observation(case, "greedy_mcp_stdio", {
        **raw, "observation": benchmark._coverage_report_unobserved(state="unobserved", reason="fixture gap"),
    })
    accounting = benchmark._corpus_accounting(corpus, [complete, incomplete], 1)
    counts = accounting["invocation_counts"]
    assert counts["complete_observed_rows"] == 1
    assert counts["incomplete_or_unknown_rows"] == 1
    assert counts["complete_observed_subtotals"]["model_attempts"] == 0
    assert counts["all_applicable_totals"]["model_attempts"] == {"value": None, "status": "unknown"}


@allure.story("Scorecard")
@allure.title("Routing and task success remain distinct scorecard sections")
def test_evidence_scorecard_separates_routing_from_task_success() -> None:
    corpus = _corpus()
    cases = {case["id"]: case for case in corpus["cases"]}
    route_rows = [
        {
            "case_id": case["id"],
            "lang": case["lang"],
            "family": case["family"],
            "expected_target": case["expected_target"],
            "actual_target": case["expected_target"],
            "route_id": "fixture",
            "ok": True,
            "false_cheap": False,
            "duration_ms": 1.0,
        }
        for case in corpus["cases"]
    ]
    raw_by_case = {
        "tool-search-en": {
            "exit_code": 0,
            "output": "projects/app/config.py:3:E2E_ALPHA_SENTINEL",
            "duration_ms": 2.0,
            "error": None,
        },
        "rag-retrieval-en": {
            "exit_code": 0,
            "output": "[evidence-retention-en]",
            "duration_ms": 3.0,
            "error": None,
        },
        "false-cheap-edit-en": {
            "exit_code": 0,
            "output": "Route: CURSOR",
            "route_target": "cursor",
            "duration_ms": 4.0,
            "error": None,
        },
    }
    observations = []
    for method in ("greedy_cli", "greedy_mcp_stdio"):
        for case_id, raw in raw_by_case.items():
            observations.append(
                benchmark._finalize_observation(
                    cases[case_id],
                    method,
                    raw,
                )
            )
    scorecard = benchmark._build_scorecard(
        corpus=corpus,
        lock={"verified": True},
        mode="deterministic",
        repetitions=1,
        route_rows=route_rows,
        observations=observations,
        live_probes={},
        allow_metered_api=False,
    )
    assert scorecard["summary"]["routing"]["accuracy"] == 1.0
    assert "task_success" in scorecard["summary"]
    assert scorecard["summary"]["routing"]["false_cheap_rate"] == 0.0
    assert scorecard["gates"]["greedy_cursor_escalation"] is False
    assert scorecard["gates"]["all_passed"] is False


# ---------------------------------------------------------------------------
# D1 — coverage-aware Python/provider observation ledger
# ---------------------------------------------------------------------------


def _p5_d1_evidence(raw):
    observation = raw["observation"]
    print("P5_D1_EVIDENCE " + json.dumps({
        "run_id": raw["run_id"],
        "measurement_status": "observed" if observation["coverage"]["complete"] else "unknown",
        "scope": observation["scope"],
        "source": "D1 independent JSONL ledger",
        "coverage": observation["coverage"],
        "python_scope": {
            "measurement_status": "observed", "scope": observation["scope"],
            "source": "D1 independent JSONL ledger", **observation["python_scope"],
        },
        "invocation": observation["invocation"],
        "ledger": observation["ledger"],
    }, ensure_ascii=False, indent=2))


def test_p5_d1_trusted_child_join_keeps_invocation_observed(minimal_workspace, tmp_path):
    """The pipeline wrapper step now runs through the admitted trusted
    runner: the child's consumed-source/FD/argv/env binding joins the
    parent's registered admission, so coverage stays complete and the
    invocation keeps observed zero-model counters."""
    import textwrap

    ledger = _bootstrapped_ledger(tmp_path)
    code = textwrap.dedent(f"""
        from pathlib import Path
        from greedy_token.executors import ProductInvocation
        from greedy_token.pipeline import run_pipeline
        root = Path({str(minimal_workspace)!r})
        with ProductInvocation(root, max_retries=2) as invocation:
            for _ in range(2):
                result = run_pipeline(
                    'check-meta-sync', root, execute=True, log=False,
                    invocation=invocation, request_id='request-1', input_version=1,
                )
                assert result.steps[0].executed
                assert result.steps[0].ok
                assert 'meta-sync-check-ok' in result.steps[0].output
            assert invocation.counts['dispatch_attempts'] == 1
            assert invocation.counts['duplicates'] == 1
            assert invocation.counts['retries'] == 0
        assert invocation.closed and invocation.counts['active'] == 0
    """)
    raw = benchmark._run_observed_exec(
        code, root=minimal_workspace, ledger_path=ledger,
        case_id="p5-owned-trusted-child", method="isolated_lifecycle", timeout=30.0,
    )
    _p5_d1_evidence(raw)
    assert raw["exit_code"] == 0, raw["output"]
    observation = raw["observation"]
    assert observation["coverage"]["status"] == "complete"
    assert observation["python_scope"]["native_launch_denied"] == 0
    assert observation["python_scope"]["native_launch_admitted"] == 1
    assert observation["python_scope"]["trusted_children"] == 1
    assert observation["invocation"]["model_attempts"] == {
        "value": 0, "status": "observed", "scope": "invocation",
    }
    assert observation["invocation"]["llm_requests_sent"] == {
        "value": 0, "status": "observed", "scope": "invocation",
    }
    events = _observed_events(raw)
    admission = next(
        e for e in events if e["kind"] == "trusted_child_admission"
    )
    bind = next(e for e in events if e["kind"] == "trusted_child_bind")
    assert bind["admission_id"] == admission["admission_id"]
    assert bind["source_sha256"] == admission["source_sha256"]
    assert bind["source_bytes"] == admission["source_bytes"]
    assert bind["runner_sha256"] == admission["runner_sha256"]
    assert bind["env_sha256"] == admission["env_sha256"]
    assert bind["fd_device"] == admission["fd_device"]
    assert bind["fd_inode"] == admission["fd_inode"]
    child_start = next(
        e
        for e in events
        if e["kind"] == "child_start" and e["pid"] == bind["pid"]
    )
    assert child_start["argv_sha256"] == admission["child_argv_sha256"]
    assert child_start["ppid"] == admission["pid"]


def test_p5_d1_owned_provider_fallback_dedup(minimal_workspace, tmp_path):
    import textwrap

    ledger = _bootstrapped_ledger(tmp_path)
    code = textwrap.dedent(f"""
        import os
        from pathlib import Path
        import greedy_token.pipeline as pipeline
        from greedy_token.executors import ProductInvocation
        from greedy_token.llm_invoke import invoke_profile
        from greedy_token.model_select import ModelSpec, ResolvedModel, _spec_to_settings
        os.environ['YANDEX_FOLDER_ID'] = 'isolated-fixture'
        root = Path({str(minimal_workspace)!r})
        spec = ModelSpec(
            id='isolated-yandex', enabled=True, provider='yandex_gpt',
            url='http://127.0.0.1:9/denied', model='fixture',
            profiles=('classify',), billing='free', api_key='fixture-only',
        )
        resolved = ResolvedModel(
            spec=spec, settings=_spec_to_settings(spec, source='isolated-mock'),
            profile='classify', billing_tier='cheap',
        )
        def producer(step, root, **kwargs):
            try:
                invoke_profile(
                    'classify', system='fixture', user='fixture', root=root,
                    resolved=resolved, allow_escalate=False, log=False,
                )
            except RuntimeError as exc:
                return pipeline.StepResult(step, False, 1, str(exc), 0, 0, False)
            raise AssertionError('D1 must deny provider dispatch')
        pipeline._run_step = producer
        with ProductInvocation(root) as invocation:
            params = dict(
                execute=True, log=False, invocation=invocation,
                request_id='request-1', input_version=1,
            )
            first = pipeline.run_pipeline('rag fixture', root, **params)
            second = pipeline.run_pipeline('rag fixture', root, **params)
            assert first.steps[0].output == second.steps[0].output
            assert invocation.counts['duplicates'] == 1
        assert invocation.closed and invocation.retained_results == 0
    """)
    raw = benchmark._run_observed_exec(
        code, root=minimal_workspace, ledger_path=ledger,
        case_id="p5-owned-provider-fallback", method="isolated_lifecycle", timeout=30.0,
    )
    _p5_d1_evidence(raw)
    assert raw["exit_code"] == 0, raw["output"]
    observation = raw["observation"]
    events = _observed_events(raw)
    assert observation["coverage"]["status"] == "complete"
    assert observation["python_scope"]["model_attempts"] == 2
    assert observation["python_scope"]["provider_fallbacks"] == 1
    assert observation["python_scope"]["llm_requests_sent"] == 0
    attempts = [e for e in events if e["kind"] == "model_attempt"]
    assert attempts[1]["parent_attempt_id"] == attempts[0]["attempt_id"]
    assert len([e for e in events if e["kind"] == "product_attempt"]) == 1
    assert observation["invocation"]["model_attempts"] == {
        "value": 2, "status": "observed", "scope": "invocation",
    }


def test_p5_d1_explicit_retry_has_causal_attempts(minimal_workspace, tmp_path):
    import textwrap

    ledger = _bootstrapped_ledger(tmp_path)
    code = textwrap.dedent(f"""
        from pathlib import Path
        import greedy_token.pipeline as pipeline
        from greedy_token.cheap_llm import observe_model_attempt
        from greedy_token.executors import ProductInvocation, ProductRetryError
        root = Path({str(minimal_workspace)!r})
        calls = 0
        parent = ''
        def producer(step, root, **kwargs):
            global calls, parent
            calls += 1
            parent = observe_model_attempt(
                profile='isolated-mock', model_id='no-provider', provider='mock',
                billing='', index=calls - 1,
                cause='candidate' if calls == 1 else 'product_retry',
                parent_attempt_id=parent, detail='isolated pre-terminal mock',
            )
            if calls < 3:
                raise ProductRetryError('isolated pre-terminal transport failure')
            return pipeline.StepResult(
                step, True, 0, 'terminal mock result', 0, 0, True,
                result_status='produced',
            )
        pipeline._run_step = producer
        with ProductInvocation(root, max_attempts=3, max_retries=2) as invocation:
            for _ in range(2):
                result = pipeline.run_pipeline(
                    'rag fixture', root, execute=True, log=False,
                    invocation=invocation, request_id='request-1', input_version=1,
                )
                assert result.steps[0].output == 'terminal mock result'
            assert calls == 3
            assert invocation.counts['retries'] == 2
        assert invocation.closed and invocation.counts['active'] == 0
    """)
    raw = benchmark._run_observed_exec(
        code, root=minimal_workspace, ledger_path=ledger,
        case_id="p5-owned-retry", method="isolated_lifecycle", timeout=30.0,
    )
    _p5_d1_evidence(raw)
    assert raw["exit_code"] == 0, raw["output"]
    observation = raw["observation"]
    assert observation["coverage"]["status"] == "complete"
    assert observation["python_scope"]["model_attempts"] == 3
    assert observation["python_scope"]["llm_requests_sent"] == 0
    events = _observed_events(raw)
    attempts = [e for e in events if e["kind"] == "product_attempt"]
    assert [e["cause"] for e in attempts] == ["step", "retry", "retry"]
    assert all(e["source"] == "owned_product_boundary" for e in attempts)
    assert all(e["scope"] == "declared_product_invocation" for e in attempts)
    assert attempts[1]["parent_attempt_id"] == attempts[0]["attempt_id"]
    assert attempts[2]["parent_attempt_id"] == attempts[1]["attempt_id"]


@pytest.mark.parametrize("stop", ["background", "cancel", "deadline"])
def test_p5_d1_owned_work_closes_before_observer(minimal_workspace, tmp_path, stop):
    import textwrap

    ledger = _bootstrapped_ledger(tmp_path)
    code = textwrap.dedent(f"""
        import threading
        import time
        from pathlib import Path
        import greedy_token.pipeline as pipeline
        from greedy_token.cheap_llm import observe_model_attempt
        from greedy_token.executors import (
            ProductInvocation, ProductLifecycleError, ProductRetryError,
        )
        root = Path({str(minimal_workspace)!r})
        stop = {stop!r}
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        def producer(step, root, **kwargs):
            observe_model_attempt(
                profile='isolated-mock', model_id='no-provider', provider='mock',
                billing='', index=0, detail='owned background mock',
            )
            entered.set()
            assert release.wait(2)
            if stop != 'background':
                raise ProductRetryError('isolated pre-terminal failure')
            return pipeline.StepResult(step, True, 0, 'terminal', 0, 0, True)
        pipeline._run_step = producer
        with ProductInvocation(root, deadline=time.monotonic() + 0.1) as invocation:
            def worker():
                try:
                    pipeline.run_pipeline(
                        'rag fixture', root, execute=True, log=False,
                        invocation=invocation, request_id='request-1', input_version=1,
                    )
                    assert stop == 'background'
                except ProductLifecycleError as exc:
                    assert stop in str(exc)
                finally:
                    finished.set()
            thread = threading.Thread(target=worker)
            thread.start()
            assert entered.wait(2)
            if stop == 'cancel':
                invocation.cancel()
            elif stop == 'deadline':
                time.sleep(0.12)
            release.set()
        thread.join(timeout=2)
        assert not thread.is_alive() and finished.is_set()
        assert invocation.closed and invocation.counts['active'] == 0
        assert invocation.counts['dispatch_attempts'] == 1
        assert invocation.counts['retries'] == 0
        assert invocation.retained_results == 0
    """)
    raw = benchmark._run_observed_exec(
        code, root=minimal_workspace, ledger_path=ledger,
        case_id=f"p5-owned-{stop}", method="isolated_lifecycle", timeout=30.0,
    )
    _p5_d1_evidence(raw)
    assert raw["exit_code"] == 0, raw["output"]
    observation = raw["observation"]
    assert observation["coverage"]["status"] == "complete"
    assert observation["python_scope"]["model_attempts"] == 1
    events = _observed_events(raw)
    closure = [e for e in events if e["kind"] == "product_close"]
    assert len(closure) == 1 and closure[0]["active"] == 0
    child = next(e for e in events if e["kind"] == "child_exit")
    assert closure[0]["epoch"] <= child["epoch"]
    assert all(
        e["epoch"] <= closure[0]["epoch"]
        for e in events if e["kind"] in ("product_attempt", "model_attempt")
    )


def _closed_run_events(
    run_id: str,
    *,
    bootstrap: bool = True,
    case_id: str = "case-x",
) -> list[dict]:
    """Minimal well-formed observed run: bootstrap + boundary + closed child."""
    events = [
        {
            "kind": "run_begin",
            "run_id": run_id,
            "case_id": case_id,
            "pid": 1,
            "seq": 1,
            "epoch": 1000.0,
            "deadline_epoch": 1060.0,
        },
        {
            "kind": "child_start",
            "run_id": run_id,
            "case_id": case_id,
            "pid": 2,
            "ppid": 1,
            "seq": 1,
            "epoch": 1001.0,
        },
        {
            "kind": "child_exit",
            "run_id": run_id,
            "case_id": case_id,
            "pid": 2,
            "ppid": 1,
            "seq": 2,
            "epoch": 1002.0,
        },
        {
            "kind": "run_end",
            "run_id": run_id,
            "case_id": case_id,
            "pid": 1,
            "seq": 2,
            "epoch": 1003.0,
        },
    ]
    if bootstrap:
        events.insert(
            0,
            {
                "kind": "bootstrap",
                "run_id": "",
                "case_id": "",
                "pid": 1,
                "seq": 0,
                "epoch": 999.0,
                "scope": "python_provider",
            },
        )
    return events


def _prepared_observed_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    corpus = _corpus()
    with benchmark._ollama_stub() as url:
        monkeypatch.setenv("OLLAMA_URL", url)
        monkeypatch.setenv("BENCH_MODEL", "evidence-stub")
        benchmark._write_fixture(corpus, tmp_path)
    clear_cheap_llm_probe_cache()
    return tmp_path


def _bootstrapped_ledger(tmp_path: Path) -> Path:
    ledger = tmp_path / "observed.jsonl"
    benchmark._observe_bootstrap(ledger)
    return ledger


def _observed_events(raw: dict) -> list[dict]:
    return raw.get("observation", {}).get("events") or []


@allure.story("Observation ledger")
@allure.title("Positive calibration: one provider attempt is denied before network")
def test_observed_model_attempt_denied_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    code = (
        "from pathlib import Path\n"
        "from greedy_token.llm_invoke import invoke_profile\n"
        "invoke_profile('classify', system='sys', user='u', "
        f"root=Path({str(root)!r}), allow_escalate=False)\n"
    )
    raw = benchmark._run_observed_exec(
        code,
        root=root,
        ledger_path=ledger,
        case_id="calibration-model-attempt",
        method="calibration",
        timeout=60.0,
    )
    observation = raw["observation"]
    assert observation["python_scope"]["model_attempts"] == 1
    assert observation["python_scope"]["llm_requests_sent"] == 0
    assert observation["python_scope"]["llm_requests_denied"] >= 1
    assert raw["exit_code"] != 0
    assert observation["coverage"]["status"] == "complete"
    assert observation["invocation"]["model_attempts"]["value"] == 1
    assert observation["invocation"]["llm_requests_sent"]["value"] == 0
    case = {
        "id": "calibration-model-attempt",
        "operation": "route-only",
        "expected_target": "cursor",
        "oracle": {"expected_exit_code": 1},
    }
    row = benchmark._finalize_observation(case, "greedy_cli", raw)
    assert row["zero_completed"] is False
    # Tokens/USD stay unknown: the ledger is an observation channel, not a
    # billing source — a denied dispatch is not authoritative usage.
    assert row["llm_tokens"]["status"] == "unknown"
    assert row["actual_cost_usd"]["status"] == "unknown"


@allure.story("Observation ledger")
@allure.title("Hidden yandex_gpt native → openai_compat fallback gets causal events")
def test_observed_hidden_provider_fallback_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    code = (
        "import os\n"
        "os.environ['YANDEX_FOLDER_ID'] = 'folder-e2e'\n"
        "from greedy_token.model_select import (\n"
        "    ModelSpec, ResolvedModel, _spec_to_settings,\n"
        ")\n"
        "from greedy_token.llm_invoke import invoke_profile\n"
        "spec = ModelSpec(\n"
        "    id='yx', enabled=True, provider='yandex_gpt',\n"
        "    url='http://127.0.0.1:9/none', model='m',\n"
        "    profiles=('classify',), billing='free',\n"
        "    api_key='test-key',\n"
        ")\n"
        "resolved = ResolvedModel(\n"
        "    spec=spec, settings=_spec_to_settings(spec, source='test'),\n"
        "    profile='classify', billing_tier='expensive',\n"
        ")\n"
        "from pathlib import Path\n"
        "invoke_profile('classify', system='s', user='u', "
        f"root=Path({str(root)!r}), resolved=resolved, allow_escalate=False)\n"
    )
    raw = benchmark._run_observed_exec(
        code,
        root=root,
        ledger_path=ledger,
        case_id="calibration-provider-fallback",
        method="calibration",
        timeout=60.0,
    )
    events = _observed_events(raw)
    attempts = [e for e in events if e["kind"] == "model_attempt"]
    denied = [
        e
        for e in events
        if e["kind"] == "llm_request" and e["decision"] == "denied"
    ]
    assert raw["observation"]["python_scope"]["model_attempts"] == 2
    assert len(attempts) == 2
    parents = {e["attempt_id"]: e.get("parent_attempt_id") for e in attempts}
    leaf = next(e for e in attempts if e.get("cause") == "provider_fallback")
    assert leaf["parent_attempt_id"] in parents
    endpoints = {e["endpoint"] for e in denied}
    assert endpoints == {"yandex_gpt_native", "openai_compat"}
    assert raw["observation"]["python_scope"]["llm_requests_sent"] == 0


@allure.story("Observation ledger")
@allure.title("CLI search selects the python backend before any native launch")
def test_observed_cli_search_python_backend_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    case = next(
        c for c in _corpus()["cases"] if c["id"] == "tool-search-en"
    )
    row = benchmark._run_observed_cli(case, root=root, ledger_path=ledger)
    observation = row["observation"]
    launches = [
        e for e in _observed_events(row) if e["kind"] == "native_launch"
    ]
    assert not any(
        e["decision"] == "denied" for e in launches
    ), f"denied native launches: {launches}"
    backend = [
        e for e in _observed_events(row) if e["kind"] == "search_backend"
    ]
    assert backend, "expected a search_backend selection event"
    assert backend[0]["engine"] == "python"
    assert backend[0]["native"] == "skipped"
    assert observation["coverage"]["status"] == "complete"
    assert observation["invocation"]["model_attempts"]["value"] == 0
    assert observation["invocation"]["llm_requests_sent"]["value"] == 0
    assert row["success"] is True
    assert row["zero_completed"] is True


@allure.story("Observation ledger")
@allure.title("Unadmitted native child launch is still denied before spawn")
def test_observed_native_launch_denied_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    code = (
        "import subprocess\n"
        "subprocess.run(['rg', 'never-admitted', '.'], capture_output=True)\n"
    )
    raw = benchmark._run_observed_exec(
        code,
        root=root,
        ledger_path=ledger,
        case_id="unadmitted-native-denied",
        method="calibration",
        timeout=30.0,
    )
    denied = [
        e
        for e in _observed_events(raw)
        if e["kind"] == "native_launch" and e["decision"] == "denied"
    ]
    assert any(e["argv0"] == "rg" for e in denied)
    assert raw["observation"]["coverage"]["status"] == "incomplete"


@allure.story("Observation ledger")
@allure.title("Missing ledger bootstrap fails closed instead of fabricating a run")
def test_observed_missing_bootstrap_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-ledger.jsonl"
    env = benchmark._observed_child_env(
        tmp_path,
        run_id="missing-bootstrap",
        case_id="missing-bootstrap",
        ledger_path=missing,
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            benchmark.OBSERVED_MODULE_ENTRY,
            "greedy_token",
            "--no-log",
            "route",
            "noop",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60.0,
    )
    assert proc.returncode != 0
    assert not missing.is_file() or "child_start" not in missing.read_text(
        encoding="utf-8"
    )


@allure.story("Observation ledger")
@allure.title("Ledger is an independent channel: events never reach stdout")
def test_observed_ledger_is_independent_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    case = next(
        c for c in _corpus()["cases"] if c["id"] == "rag-retrieval-en"
    )
    row = benchmark._run_observed_cli(case, root=root, ledger_path=ledger)
    events = _observed_events(row)
    assert any(e["kind"] == "child_start" for e in events)
    assert all(
        "child_start" not in line and "model_attempt" not in line
        for line in row["output_excerpt"].splitlines()
    )
    assert ledger.is_file()


@allure.story("Observation ledger")
@allure.title("Sequence loss inside a run is coverage-incomplete, not zero")
def test_coverage_sequence_gap_marks_incomplete() -> None:
    events = _closed_run_events("gap-run")
    events.insert(
        3,
        {
            "kind": "model_attempt",
            "run_id": "gap-run",
            "case_id": "case-x",
            "pid": 2,
            "seq": 4,
            "attempt_id": "2-4",
        },
    )
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="gap-run",
    )
    assert report["coverage"]["status"] == "incomplete"
    assert "sequence_gap" in report["coverage"]["reasons"]
    assert report["python_scope"]["model_attempts"] == 1


@allure.story("Observation ledger")
@allure.title("Missing bootstrap makes coverage incomplete even for a closed run")
def test_coverage_missing_bootstrap_not_complete() -> None:
    events = _closed_run_events("noboot-run", bootstrap=False)
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="noboot-run",
    )
    assert report["coverage"]["status"] != "complete"
    assert "missing_bootstrap" in report["coverage"]["reasons"]


@allure.story("Observation ledger")
@allure.title("Lost sequence prefix (child starts at seq 2) is a violation")
def test_coverage_sequence_prefix_lost() -> None:
    events = _closed_run_events("prefix-run")
    child = [
        e
        for e in events
        if e["kind"] in ("child_start", "child_exit")
    ]
    child[0]["seq"] = 2
    child[1]["seq"] = 3
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="prefix-run",
    )
    assert report["coverage"]["status"] == "incomplete"
    assert "sequence_gap" in report["coverage"]["reasons"]


@allure.story("Observation ledger")
@allure.title("Foreign-run events in the ledger invalidate the run report")
def test_coverage_foreign_run_events_not_complete() -> None:
    events = _closed_run_events("run-a")
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="run-b",
    )
    assert report["coverage"]["status"] != "complete"
    assert "foreign_run_event" in report["coverage"]["reasons"]


@allure.story("Observation ledger")
@allure.title("Child event after child_exit breaks closure")
def test_coverage_event_after_child_exit_not_complete() -> None:
    events = _closed_run_events("late-run")
    events.insert(
        -1,
        {
            "kind": "model_attempt",
            "run_id": "late-run",
            "case_id": "case-x",
            "pid": 2,
            "seq": 3,
            "attempt_id": "2-3",
            "epoch": 1002.5,
        },
    )
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="late-run",
    )
    assert report["coverage"]["status"] == "incomplete"
    assert (
        "event_after_child_exit" in report["coverage"]["reasons"]
        or "sequence_gap" in report["coverage"]["reasons"]
    )


@allure.story("Observation ledger")
@allure.title("Events for another case_id inside the run are a mismatch")
def test_coverage_case_id_mismatch_not_complete() -> None:
    events = _closed_run_events("case-run")
    events.insert(
        2,
        {
            "kind": "model_attempt",
            "run_id": "case-run",
            "case_id": "other-case",
            "pid": 2,
            "seq": 2,
            "attempt_id": "2-2",
            "epoch": 1001.5,
        },
    )
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="case-run",
        case_id="case-x",
    )
    assert report["coverage"]["status"] == "incomplete"
    assert "case_mismatch" in report["coverage"]["reasons"]


@allure.story("Observation ledger")
@allure.title("run_begin fields must match the driver-intended values")
def test_coverage_run_begin_field_mismatch() -> None:
    events = _closed_run_events("begin-run")
    begin = next(e for e in events if e["kind"] == "run_begin")
    begin["env_sha256"] = "forged"
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="begin-run",
        expected={"env_sha256": "expected-hash"},
    )
    assert report["coverage"]["status"] == "incomplete"
    assert "run_begin_mismatch" in report["coverage"]["reasons"]


@allure.story("Observation ledger")
@allure.title("Events past the shared deadline are a coverage violation")
def test_coverage_deadline_exceeded_not_complete() -> None:
    events = _closed_run_events("deadline-run")
    events[-2]["epoch"] = 9999.0  # child_exit long after deadline_epoch=1060
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="deadline-run",
    )
    assert report["coverage"]["status"] == "incomplete"
    assert "deadline_exceeded" in report["coverage"]["reasons"]


@allure.story("Observation ledger")
@allure.title("Duplicate sequence numbers break run integrity")
def test_coverage_duplicate_sequence_not_complete() -> None:
    events = _closed_run_events("dup-run")
    events.insert(
        3,
        {
            "kind": "provider_probe",
            "run_id": "dup-run",
            "case_id": "case-x",
            "pid": 2,
            "seq": 1,
            "endpoint": "ollama_health",
            "epoch": 1001.5,
        },
    )
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="dup-run",
    )
    assert report["coverage"]["status"] == "incomplete"
    assert (
        "sequence_gap" in report["coverage"]["reasons"]
        or "duplicate_event" in report["coverage"]["reasons"]
    )


@allure.story("Observation ledger")
@allure.title("Model POST stub never fabricates a successful model response")
def test_model_post_stub_returns_no_fake_success() -> None:
    import urllib.error
    import urllib.request

    with benchmark._ollama_stub() as url:
        request = urllib.request.Request(
            f"{url}/api/chat",
            data=json.dumps({"model": "evidence-stub", "messages": []}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(request, timeout=5)
        assert excinfo.value.code != 200


@allure.story("Observation ledger")
@allure.title("Closed pure-Python run reports invocation zero with complete coverage")
def test_observed_clean_rag_run_reports_invocation_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    case = next(
        c for c in _corpus()["cases"] if c["id"] == "rag-retrieval-en"
    )
    row = benchmark._run_observed_cli(case, root=root, ledger_path=ledger)
    observation = row["observation"]
    assert row["exit_code"] == 0
    assert observation["coverage"]["status"] == "complete"
    assert observation["invocation"]["model_attempts"] == {
        "value": 0,
        "status": "observed",
        "scope": "invocation",
    }
    assert observation["invocation"]["llm_requests_sent"] == {
        "value": 0,
        "status": "observed",
        "scope": "invocation",
    }
    # Eligible completion (rag oracle success + full coverage + no model
    # events) makes this run a legitimate zero_completed numerator.
    assert row["success"] is True
    assert row["zero_completed"] is True


@allure.story("Observation ledger")
@allure.title("Method labels no longer fabricate zero tokens or attempt counters")
def test_no_method_derived_zero_billing_or_attempts() -> None:
    case = next(
        c for c in _corpus()["cases"] if c["id"] == "tool-search-en"
    )
    raw = {
        "exit_code": 0,
        "output": "projects/app/config.py:3:E2E_ALPHA_SENTINEL",
        "duration_ms": 10.0,
        "error": None,
    }
    row = benchmark._finalize_observation(case, "greedy_cli", raw)
    assert row["llm_tokens"]["authoritative"] is False
    assert row["llm_tokens"]["status"] == "unknown"
    assert row["attempts"]["status"] in {"observed", "unknown"}
    assert isinstance(row["attempts"], dict)


@allure.story("Observation ledger")
@allure.title("MCP search answers via the python backend with complete coverage")
def test_observed_mcp_search_python_backend_zero_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    case = next(
        c for c in _corpus()["cases"] if c["id"] == "tool-search-en"
    )
    row = benchmark._run_observed_mcp_case(case, root=root, ledger_path=ledger)
    observation = row["observation"]
    assert row["success"] is True
    assert not any(
        e["kind"] == "native_launch" and e["decision"] == "denied"
        for e in _observed_events(row)
    )
    backend = [
        e for e in _observed_events(row) if e["kind"] == "search_backend"
    ]
    assert backend and backend[0]["engine"] == "python"
    assert observation["coverage"]["status"] == "complete"
    assert row["zero_completed"] is True
    assert observation["invocation"]["model_attempts"]["status"] == "observed"


# ---------------------------------------------------------------------------
# D1 rework — acceptance findings
# ---------------------------------------------------------------------------


def _fake_raw_with_complete_observation(
    case: dict,
    *,
    output: str,
    exit_code: int = 0,
) -> dict:
    return {
        "exit_code": exit_code,
        "output": output,
        "duration_ms": 5.0,
        "error": None,
        "observation": benchmark._coverage_observed_report(
            _closed_run_events("fake-complete", case_id=case["id"]),
            timed_out=False,
            exit_code=exit_code,
            run_id="fake-complete",
            case_id=case["id"],
        ),
        "run_id": "fake-complete",
    }


@allure.story("Zero completion")
@allure.title("zero_completed requires an oracle-verified successful result")
def test_zero_completed_requires_oracle_success() -> None:
    case = next(
        c for c in _corpus()["cases"] if c["id"] == "rag-retrieval-en"
    )
    raw = _fake_raw_with_complete_observation(
        case, output="no sentinel in this output"
    )
    row = benchmark._finalize_observation(case, "greedy_cli", raw)
    assert row["success"] is False
    assert row["zero_completed"] is False


@allure.story("Zero completion")
@allure.title("Route-only tasks are never zero_completed even when correct")
def test_zero_completed_not_for_route_only() -> None:
    case = next(
        c for c in _corpus()["cases"] if c["id"] == "false-cheap-edit-en"
    )
    raw = _fake_raw_with_complete_observation(
        case, output="Route: CURSOR"
    )
    raw["route_target"] = "cursor"
    row = benchmark._finalize_observation(case, "greedy_cli", raw)
    assert row["success"] is False
    assert row["correctness_status"] == "unknown"
    assert row["checks"]["target"] is True
    assert row["completion_eligible"] is False
    assert row["zero_completed"] is False


@allure.story("Zero completion")
@allure.title("Scorecard re-verifies zero_completed instead of trusting the flag")
def test_scorecard_gate_rejects_forged_zero_completed() -> None:
    case = next(
        c for c in _corpus()["cases"] if c["id"] == "rag-retrieval-en"
    )
    raw = _fake_raw_with_complete_observation(
        case, output="missing sentinel"
    )
    row = benchmark._finalize_observation(case, "greedy_cli", raw)
    assert row["zero_completed"] is False
    row["zero_completed"] = True  # forged flag — gate must catch it
    scorecard = benchmark._build_scorecard(
        corpus=_corpus(),
        lock={"verified": True},
        mode="deterministic",
        repetitions=1,
        route_rows=[],
        observations=[row],
        live_probes={},
        allow_metered_api=False,
    )
    assert scorecard["gates"]["zero_completed_requires_completion"] is False
    assert scorecard["gates"]["all_passed"] is False


@allure.story("Attempts")
@allure.title("Stage counts are unknown — not derived from output text")
def test_attempts_not_derived_from_output_text() -> None:
    case = next(
        c for c in _corpus()["cases"] if c["id"] == "tool-search-en"
    )
    raw = _fake_raw_with_complete_observation(
        case,
        output="User fixture text: fallback — not a pipeline stage",
    )
    row = benchmark._finalize_observation(case, "greedy_cli", raw)
    assert row["attempts"]["status"] == "unknown"
    assert row["attempts"]["value"] is None
    assert row["retries"]["status"] == "unknown"
    assert row["escalations"] == []


@allure.story("Causality")
@allure.title("Attempt ids bind per thread — no cross-thread leakage")
def test_observe_attempt_thread_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    code = (
        "import threading\n"
        "from greedy_token.cheap_llm import (\n"
        "    DispatchDeniedError,\n"
        "    observe_model_attempt, observe_provider_dispatch)\n"
        "def attempt(profile):\n"
        "    observe_model_attempt(profile=profile, model_id='m',\n"
        "                          provider='ollama', billing='stub',\n"
        "                          index=0, cause='first_call')\n"
        "def dispatch(url):\n"
        "    try:\n"
        "        observe_provider_dispatch(provider='ollama', endpoint=url)\n"
        "    except DispatchDeniedError:\n"
        "        pass\n"
        "ready = threading.Event(); go = threading.Event()\n"
        "def worker():\n"
        "    attempt('analyze')\n"
        "    ready.set(); go.wait(5)\n"
        "    dispatch('http://x')\n"
        "t = threading.Thread(target=worker); t.start()\n"
        "ready.wait(5)\n"
        "attempt('classify')\n"
        "go.set(); t.join(5)\n"
        "dispatch('http://y')\n"
    )
    raw = benchmark._run_observed_exec(
        code,
        root=root,
        ledger_path=ledger,
        case_id="causal-threads",
        method="calibration",
        timeout=60.0,
    )
    assert raw["exit_code"] == 0, raw["output"]
    events = _observed_events(raw)
    attempts = {
        e["attempt_id"]: e["thread_ident"]
        for e in events
        if e["kind"] == "model_attempt"
    }
    requests = [e for e in events if e["kind"] == "llm_request"]
    assert len(attempts) == 2
    assert len(requests) == 2
    for request in requests:
        bound = request["attempt_id"]
        assert bound in attempts
        # The dispatch must bind the attempt created on the same thread.
        assert attempts[bound] == request["thread_ident"]
    profile_by_thread = {
        e["thread_ident"]: e["profile"] for e in events if e["kind"] == "model_attempt"
    }
    for request in requests:
        thread = request["thread_ident"]
        expected_profile = profile_by_thread[thread]
        attempt_profile = next(
            e["profile"]
            for e in events
            if e["kind"] == "model_attempt"
            and e["attempt_id"] == request["attempt_id"]
        )
        assert attempt_profile == expected_profile


@allure.story("IO guard")
@allure.title("Non-fixture origins and non-http schemes are denied")
def test_io_denies_non_fixture_origin_and_file_scheme(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    code = (
        "import urllib.request\n"
        "from greedy_token.cheap_llm import DispatchDeniedError\n"
        "blocked = []\n"
        "for target in (\n"
        "    'http://127.0.0.1:1/UNCLASSIFIED_INFERENCE',\n"
        "    'http://127.0.0.1:1/api/tags',\n"
        "    'file:///tmp/UNCLASSIFIED_LOCAL_RESOURCE',\n"
        "):\n"
        "    try:\n"
        "        urllib.request.urlopen(target, timeout=1)\n"
        "    except DispatchDeniedError:\n"
        "        blocked.append(target)\n"
        "assert len(blocked) == 3, blocked\n"
    )
    raw = benchmark._run_observed_exec(
        code,
        root=root,
        ledger_path=ledger,
        case_id="io-admission",
        method="calibration",
        timeout=60.0,
    )
    assert raw["exit_code"] == 0, raw["output"]
    denied = [
        e
        for e in _observed_events(raw)
        if e["kind"] == "io_http" and e["decision"] == "denied"
    ]
    assert len(denied) == 3


@allure.story("IO guard")
@allure.title("The declared fixture health endpoint remains reachable")
def test_io_allows_fixture_health_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    with benchmark._ollama_stub() as url:
        monkeypatch.setenv("OLLAMA_URL", url)
        code = (
            "import urllib.request\n"
            f"resp = urllib.request.urlopen({url + '/api/tags'!r}, timeout=5)\n"
            "assert resp.status == 200\n"
            "body = resp.read().decode()\n"
            "assert 'evidence-stub' in body\n"
        )
        raw = benchmark._run_observed_exec(
            code,
            root=root,
            ledger_path=ledger,
            case_id="io-allowed",
            method="calibration",
            timeout=60.0,
        )
    assert raw["exit_code"] == 0, raw["output"]
    allowed = [
        e
        for e in _observed_events(raw)
        if e["kind"] == "io_http" and e["decision"] == "allowed"
    ]
    assert len(allowed) == 1


@allure.story("IO guard")
@allure.title("Redirect to a non-fixture origin cannot bypass admission")
def test_io_redirect_does_not_bypass_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    code = (
        "import urllib.request\n"
        "from greedy_token.cheap_llm import DispatchDeniedError\n"
        "class EvilRedirect(urllib.request.HTTPRedirectHandler):\n"
        "    def redirect_request(self, req, fp, code, msg, headers, newurl):\n"
        "        return urllib.request.Request('file:///tmp/evil')\n"
        "opener = urllib.request.build_opener(EvilRedirect())\n"
        "blocked = False\n"
        "try:\n"
        "    opener.open('file:///tmp/evil', timeout=1)\n"
        "except DispatchDeniedError:\n"
        "    blocked = True\n"
        "assert blocked\n"
    )
    raw = benchmark._run_observed_exec(
        code,
        root=root,
        ledger_path=ledger,
        case_id="io-redirect",
        method="calibration",
        timeout=60.0,
    )
    assert raw["exit_code"] == 0, raw["output"]


@allure.story("Observation ledger")
@allure.title("Ledger write failure fails closed instead of silent loss")
def test_ledger_write_failure_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    code = (
        "import os\n"
        "from greedy_token.cheap_llm import (\n"
        "    OBSERVE_LEDGER_ENV, DispatchDeniedError,\n"
        "    observe_model_attempt)\n"
        "os.environ[OBSERVE_LEDGER_ENV] = os.path.dirname(\n"
        "    os.environ[OBSERVE_LEDGER_ENV])\n"
        "try:\n"
        "    observe_model_attempt(profile='classify', model_id='m',\n"
        "                          provider='ollama', billing='stub',\n"
        "                          index=0, cause='first_call')\n"
        "except DispatchDeniedError:\n"
        "    raise SystemExit(42)\n"
        "raise SystemExit(43)\n"
    )
    raw = benchmark._run_observed_exec(
        code,
        root=root,
        ledger_path=ledger,
        case_id="ledger-fail-closed",
        method="calibration",
        timeout=60.0,
    )
    assert raw["exit_code"] == 42


@allure.story("MCP")
@allure.title("tool_text joins every content block, not just the first")
def test_mcp_tool_text_joins_all_blocks() -> None:
    from types import SimpleNamespace

    from tests import mcp_stdio_helpers

    result = SimpleNamespace(
        content=[
            SimpleNamespace(text="first"),
            SimpleNamespace(text="second"),
        ]
    )
    assert mcp_stdio_helpers.tool_text(result) == "first\nsecond"


@allure.story("MCP")
@allure.title("Observed MCP params pin cwd and bootstrap args")
def test_observed_mcp_params_pin_cwd_and_args(tmp_path: Path) -> None:
    from tests import mcp_stdio_helpers

    params = mcp_stdio_helpers.mcp_server_params(
        tmp_path,
        observe={
            "ledger_path": tmp_path / "observed.jsonl",
            "run_id": "r",
            "case_id": "c",
        },
    )
    assert getattr(params, "cwd", None) == str(tmp_path)
    args = list(params.args)
    assert "-S" in args  # no site.py before the observer installs
    assert args[args.index("-c") + 1].startswith("from greedy_token.cheap_llm")
    env = params.env or {}
    assert "GREEDY_TOKEN_OBSERVE" in env
    assert env.get("PYTHONNOUSERSITE") == "1"


# ---------------------------------------------------------------------------
# D1 rework — per-case completion contract, attribution, causal integrity
# ---------------------------------------------------------------------------


@allure.story("Completion contract")
@allure.title("Echo-contract script is contract-only — never completion eligible")
def test_script_case_is_contract_only_not_eligible() -> None:
    case = next(
        c for c in _corpus()["cases"] if c["id"] == "python-script-en"
    )
    raw = _fake_raw_with_complete_observation(
        case, output="EVIDENCE_META_SYNC_OK"
    )
    row = benchmark._finalize_observation(case, "greedy_cli", raw)
    # The sentinel oracle still verifies — contract success stays separate
    # from actual task completion.
    assert row["success"] is True
    assert row["completion_eligible"] is False
    assert row["completion_evidence"]["category"] == "contract_only"
    assert row["completion_evidence"]["satisfied"] is False
    assert row["zero_completed"] is False


@allure.story("Completion contract")
@allure.title("Fallback playbook is fallback-only — never completion eligible")
def test_fallback_case_is_fallback_only_not_eligible() -> None:
    case = next(
        c
        for c in _corpus()["cases"]
        if c["id"] == "tool-to-rag-fallback-en"
    )
    raw = _fake_raw_with_complete_observation(
        case, output="[evidence-fallback-playbook] playbook text"
    )
    row = benchmark._finalize_observation(case, "greedy_cli", raw)
    assert row["success"] is False
    assert row["correctness_status"] == "unknown"
    assert row["observed_escalation"] is None
    assert row["completion_eligible"] is False
    assert row["completion_evidence"]["category"] == "fallback_only"
    assert row["zero_completed"] is False


@allure.story("Completion contract")
@allure.title("Routing/escalation rows stay a separate non-completion category")
def test_routing_escalation_is_separate_category() -> None:
    case = next(
        c for c in _corpus()["cases"] if c["id"] == "false-cheap-edit-en"
    )
    raw = _fake_raw_with_complete_observation(case, output="Route: CURSOR")
    raw["route_target"] = "cursor"
    row = benchmark._finalize_observation(case, "greedy_cli", raw)
    assert row["success"] is False
    assert row["correctness_status"] == "unknown"
    assert row["checks"]["target"] is True
    assert row["observed_escalation"] is None
    assert row["completion_evidence"]["category"] == "routing_only"
    assert row["completion_eligible"] is False
    assert row["zero_completed"] is False


@allure.story("Completion contract")
@allure.title("RAG chunk id echo without the frozen excerpt is not completed")
def test_rag_chunk_id_without_excerpt_not_zero(tmp_path: Path) -> None:
    corpus = _corpus()
    benchmark._write_fixture(corpus, tmp_path)
    case = next(c for c in corpus["cases"] if c["id"] == "rag-retrieval-en")
    raw = _fake_raw_with_complete_observation(
        case, output="1. [evidence-retention-en] score=1.0 (evidence)"
    )
    row = benchmark._finalize_observation(
        case, "greedy_cli", raw, root=tmp_path
    )
    assert row["success"] is True  # chunk-id oracle passes
    assert row["completion_eligible"] is True
    assert row["completion_evidence"]["satisfied"] is False
    assert row["zero_completed"] is False


@allure.story("Completion contract")
@allure.title("RAG chunk id plus substantive frozen excerpt is real completion")
def test_rag_chunk_id_with_excerpt_is_zero(tmp_path: Path) -> None:
    corpus = _corpus()
    benchmark._write_fixture(corpus, tmp_path)
    case = next(c for c in corpus["cases"] if c["id"] == "rag-retrieval-en")
    output = (
        "RAG hits for: benchmark evidence retention\n\n"
        "1. [evidence-retention-en] score=1.0 engine=overlap (evidence)\n"
        "   docs/rag/evidence/retention-en.md\n\n"
        "# Evidence retention\n\n"
        "CI keeps the JSON scorecard as an immutable benchmark artifact.\n"
        "Routing accuracy and task success are reported independently.\n"
    )
    raw = _fake_raw_with_complete_observation(case, output=output)
    row = benchmark._finalize_observation(
        case, "greedy_cli", raw, root=tmp_path
    )
    assert row["success"] is True
    assert row["completion_evidence"]["satisfied"] is True
    assert row["zero_completed"] is True
    # The scorecard gate re-verifies the stored excerpt claim against the
    # row's own output evidence.
    assert benchmark._zero_completion_ok(row) is True


@allure.story("Completion contract")
@allure.title("A forged RAG satisfied-flag without excerpt words fails the gate")
def test_scorecard_gate_rejects_forged_excerpt_claim(tmp_path: Path) -> None:
    corpus = _corpus()
    benchmark._write_fixture(corpus, tmp_path)
    case = next(c for c in corpus["cases"] if c["id"] == "rag-retrieval-en")
    raw = _fake_raw_with_complete_observation(
        case, output="1. [evidence-retention-en] score=1.0 (evidence)"
    )
    row = benchmark._finalize_observation(
        case, "greedy_cli", raw, root=tmp_path
    )
    assert row["zero_completed"] is False
    # Forge the stored evidence the way a buggy producer could.
    row["zero_completed"] = True
    row["completion_evidence"]["satisfied"] = True
    row["completion_evidence"]["matched_windows"] = [
        ["nonexistent", "frozen", "document", "words", "not", "present"]
    ]
    assert benchmark._zero_completion_ok(row) is False


@allure.story("Observation ledger")
@allure.title("Unattributed non-bootstrap event is an integrity violation")
def test_coverage_unattributed_event_not_complete() -> None:
    events = _closed_run_events("attr-run")
    events.insert(
        3,
        {
            "kind": "model_attempt",
            "run_id": "",  # unattributed — only bootstrap may be global
            "case_id": "",
            "pid": 2,
            "seq": 2,
            "attempt_id": "2-2",
            "epoch": 1001.5,
        },
    )
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="attr-run",
    )
    assert report["coverage"]["status"] == "incomplete"
    assert "unattributed_event" in report["coverage"]["reasons"]
    assert report["invocation"]["model_attempts"]["status"] == "unknown"


@allure.story("Observation ledger")
@allure.title("llm_request without any attempt id cannot fake a zero run")
def test_coverage_orphan_llm_request_not_complete() -> None:
    events = _closed_run_events("orphan-run")
    events.insert(
        3,
        {
            "kind": "llm_request",
            "run_id": "orphan-run",
            "case_id": "case-x",
            "pid": 2,
            "seq": 2,
            "attempt_id": "",
            "decision": "denied",
            "epoch": 1001.5,
        },
    )
    events[4]["seq"] = 3  # child_exit keeps a contiguous sequence
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="orphan-run",
    )
    assert report["coverage"]["status"] == "incomplete"
    assert "orphan_llm_request" in report["coverage"]["reasons"]


@allure.story("Observation ledger")
@allure.title("llm_request bound to an unregistered attempt id is a violation")
def test_coverage_unbound_llm_request_not_complete() -> None:
    events = _closed_run_events("unbound-run")
    events.insert(
        3,
        {
            "kind": "llm_request",
            "run_id": "unbound-run",
            "case_id": "case-x",
            "pid": 2,
            "seq": 2,
            "attempt_id": "2-99",
            "decision": "denied",
            "epoch": 1001.5,
        },
    )
    events[4]["seq"] = 3
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="unbound-run",
    )
    assert report["coverage"]["status"] == "incomplete"
    assert "orphan_llm_request" in report["coverage"]["reasons"]


@allure.story("Observation ledger")
@allure.title("Causal parent pointing at a missing attempt is a violation")
def test_coverage_broken_causal_parent_not_complete() -> None:
    events = _closed_run_events("causal-run")
    events.insert(
        3,
        {
            "kind": "model_attempt",
            "run_id": "causal-run",
            "case_id": "case-x",
            "pid": 2,
            "seq": 2,
            "attempt_id": "2-2",
            "parent_attempt_id": "2-99",
            "epoch": 1001.5,
        },
    )
    events[4]["seq"] = 3
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="causal-run",
    )
    assert report["coverage"]["status"] == "incomplete"
    assert "broken_causal_link" in report["coverage"]["reasons"]


@allure.story("Observation ledger")
@allure.title("Duplicate attempt ids break causal integrity")
def test_coverage_duplicate_attempt_id_not_complete() -> None:
    events = _closed_run_events("dup-attempt-run")
    for seq, epoch in ((2, 1001.5), (3, 1001.6)):
        events.insert(
            -2,
            {
                "kind": "model_attempt",
                "run_id": "dup-attempt-run",
                "case_id": "case-x",
                "pid": 2,
                "seq": seq,
                "attempt_id": "2-2",
                "epoch": epoch,
            },
        )
    events[-2]["seq"] = 4  # child_exit stays contiguous
    report = benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id="dup-attempt-run",
    )
    assert report["coverage"]["status"] == "incomplete"
    assert "duplicate_attempt_id" in report["coverage"]["reasons"]


@allure.story("Observation ledger")
@allure.title("Direct provider dispatch without candidate gets its own intent")
def test_direct_dispatch_without_candidate_counts_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    code = (
        "from greedy_token.cheap_llm import (\n"
        "    DispatchDeniedError, observe_provider_dispatch)\n"
        "try:\n"
        "    observe_provider_dispatch('ollama', 'ollama_chat')\n"
        "except DispatchDeniedError:\n"
        "    pass\n"
    )
    raw = benchmark._run_observed_exec(
        code,
        root=root,
        ledger_path=ledger,
        case_id="orphan-dispatch",
        method="calibration",
        timeout=60.0,
    )
    assert raw["exit_code"] == 0, raw["output"]
    observation = raw["observation"]
    # The orphan dispatch may never masquerade as a zero-attempt run: it
    # registers its own causal leaf before the denial.
    assert observation["python_scope"]["model_attempts"] == 1
    assert observation["python_scope"]["llm_requests_denied"] == 1
    events = _observed_events(raw)
    attempt = next(e for e in events if e["kind"] == "model_attempt")
    request = next(e for e in events if e["kind"] == "llm_request")
    assert attempt["cause"] == "direct_dispatch"
    assert request["attempt_id"] == attempt["attempt_id"]
    assert observation["coverage"]["status"] == "complete"
    assert observation["invocation"]["model_attempts"] == {
        "value": 1,
        "status": "observed",
        "scope": "invocation",
    }


@allure.story("Observation ledger")
@allure.title("Dispatch bound to an existing candidate is not double-counted")
def test_dispatch_binds_candidate_without_double_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    code = (
        "from greedy_token.cheap_llm import (\n"
        "    DispatchDeniedError, observe_model_attempt,\n"
        "    observe_provider_dispatch)\n"
        "observe_model_attempt(profile='classify', model_id='m',\n"
        "                      provider='ollama', billing='stub',\n"
        "                      index=0, cause='candidate')\n"
        "try:\n"
        "    observe_provider_dispatch('ollama', 'ollama_chat')\n"
        "except DispatchDeniedError:\n"
        "    pass\n"
    )
    raw = benchmark._run_observed_exec(
        code,
        root=root,
        ledger_path=ledger,
        case_id="bound-dispatch",
        method="calibration",
        timeout=60.0,
    )
    assert raw["exit_code"] == 0, raw["output"]
    events = _observed_events(raw)
    attempts = [e for e in events if e["kind"] == "model_attempt"]
    request = next(e for e in events if e["kind"] == "llm_request")
    assert len(attempts) == 1
    assert request["attempt_id"] == attempts[0]["attempt_id"]
    assert raw["observation"]["python_scope"]["model_attempts"] == 1


# ---------------------------------------------------------------------------
# D1 rework — self-contained evidence packaging for standalone replay
# ---------------------------------------------------------------------------


def _json_roundtrip(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _replayed_observation(row: dict) -> dict:
    replayed = benchmark._replay_observation(row["observation"])
    assert replayed is not None
    return replayed


def _assert_replay_matches(row: dict) -> None:
    original = row["observation"]
    replayed = _replayed_observation(row)
    assert replayed["coverage"] == original["coverage"]
    assert replayed["python_scope"] == original["python_scope"]
    assert replayed["invocation"] == original["invocation"]
    assert replayed["events"] == original["events"]
    assert (
        replayed["events_sha256"] == original["events_sha256"]
    )


@allure.story("Evidence replay")
@allure.title("Stored scorecard rows replay the verdict without the fixture")
def test_observation_replay_is_self_contained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    corpus = _corpus()
    rag_case = next(
        c for c in corpus["cases"] if c["id"] == "rag-retrieval-en"
    )
    search_case = next(
        c for c in corpus["cases"] if c["id"] == "tool-search-en"
    )
    rows = [
        benchmark._run_observed_cli(rag_case, root=root, ledger_path=ledger),
        benchmark._run_observed_cli(
            search_case, root=root, ledger_path=ledger
        ),
    ]
    assert rows[0]["observation"]["coverage"]["status"] == "complete"
    # The search case now completes coverage via the python backend —
    # no denied native launch remains to poison the run.
    assert rows[1]["observation"]["coverage"]["status"] == "complete"
    # Raw ledger keeps the bootstrap the verdict depended on.
    raw_events = rows[0]["observation"]["ledger"]["events"]
    assert any(e.get("kind") == "bootstrap" for e in raw_events)
    # Raw and attributed hashes are tracked separately.
    assert (
        rows[0]["observation"]["ledger"]["events_sha256"]
        != rows[0]["observation"]["events_sha256"]
    )
    # The raw ledger hash binds to the real per-run file while it exists.
    per_run = benchmark._per_run_ledger(ledger, rows[0]["run_id"])
    assert (
        hashlib.sha256(per_run.read_bytes()).hexdigest()
        == rows[0]["observation"]["ledger"]["file_sha256"]
    )
    stored = _json_roundtrip(rows)
    shutil.rmtree(root)  # fixture + ledgers are gone — replay stands alone
    for row in stored:
        _assert_replay_matches(row)


@allure.story("Evidence replay")
@allure.title("Replay reconstructs complete coverage from raw events alone")
def test_replay_closed_run_matches_complete() -> None:
    observation = {
        "ledger": {
            "events": _closed_run_events("replay-run"),
            "run_id": "replay-run",
            "case_id": "case-x",
            "timed_out": False,
            "exit_code": 0,
            "expected": {},
        }
    }
    replayed = benchmark._replay_observation(observation)
    assert replayed["coverage"]["status"] == "complete"


@allure.story("Evidence replay")
@allure.title("Dropping the stored bootstrap still fails the replay")
def test_replay_missing_bootstrap_fails_closed() -> None:
    observation = {
        "ledger": {
            "events": _closed_run_events("noboot-replay", bootstrap=False),
            "run_id": "noboot-replay",
            "case_id": "case-x",
            "timed_out": False,
            "exit_code": 0,
            "expected": {},
        }
    }
    replayed = benchmark._replay_observation(observation)
    assert replayed["coverage"]["status"] == "incomplete"
    assert "missing_bootstrap" in replayed["coverage"]["reasons"]


@allure.story("Evidence replay")
@allure.title("Foreign-run events stored in the ledger poison the replay")
def test_replay_foreign_event_fails_closed() -> None:
    events = _closed_run_events("foreign-replay")
    events.append(
        {
            "kind": "model_attempt",
            "run_id": "other-run",
            "case_id": "other-case",
            "pid": 3,
            "seq": 1,
            "attempt_id": "3-1",
        }
    )
    observation = {
        "ledger": {
            "events": events,
            "run_id": "foreign-replay",
            "case_id": "case-x",
            "timed_out": False,
            "exit_code": 0,
            "expected": {},
        }
    }
    replayed = benchmark._replay_observation(observation)
    assert replayed["coverage"]["status"] == "incomplete"
    assert "foreign_run_event" in replayed["coverage"]["reasons"]


@allure.story("Evidence replay")
@allure.title("Unattributed events stored in the ledger poison the replay")
def test_replay_unattributed_event_fails_closed() -> None:
    events = _closed_run_events("unattr-replay")
    events.append(
        {
            "kind": "llm_request",
            "run_id": "",
            "case_id": "",
            "pid": 3,
            "seq": 1,
            "attempt_id": "3-1",
            "decision": "denied",
        }
    )
    observation = {
        "ledger": {
            "events": events,
            "run_id": "unattr-replay",
            "case_id": "case-x",
            "timed_out": False,
            "exit_code": 0,
            "expected": {},
        }
    }
    replayed = benchmark._replay_observation(observation)
    assert replayed["coverage"]["status"] == "incomplete"
    assert "unattributed_event" in replayed["coverage"]["reasons"]


@allure.story("MCP")
@allure.title("Shared deadline covers initialize, call, and cleanup")
def test_mcp_call_observed_deadline_covers_initialize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import time

    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    env = benchmark._observed_child_env(
        root,
        run_id="mcp-deadline",
        case_id="mcp-deadline",
        ledger_path=tmp_path / "observed.jsonl",
    )
    # A server that never speaks MCP makes initialize hang; the shared
    # deadline must abort the whole session, not just call_tool.
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(
            benchmark._mcp_call_observed_async(
                root,
                "greedy_token_route",
                {"task": "x"},
                env=env,
                timeout=2.0,
                cwd=root,
                args=["-c", "import time; time.sleep(120)"],
            )
        )
    assert time.monotonic() - started < 15.0


# ---------------------------------------------------------------------------
# Capability: trusted observed Python child (admission → bind join)
# ---------------------------------------------------------------------------


def _trusted_child_events(run_id: str, *, case_id: str = "case-x") -> list[dict]:
    """Observed run whose primary child admits one trusted runner child."""
    admission = {
        "kind": "trusted_child_admission",
        "pid": 2,
        "ppid": 1,
        "seq": 2,
        "epoch": 1001.5,
        "run_id": run_id,
        "case_id": case_id,
        "admission_id": "ad-1",
        "argv_sha256": "SA",
        "child_argv_sha256": "CA",
        "env_sha256": "E",
        "pass_fds": [7],
        "cwd": "/w",
        "runner_sha256": "R",
        "source_sha256": "S",
        "source_bytes": 42,
        "script_path": "scripts/x.py",
        "authority": "wrapper:scripts/x.py",
        "fd_device": 11,
        "fd_inode": 22,
    }
    launch = {
        "kind": "native_launch",
        "pid": 2,
        "ppid": 1,
        "seq": 3,
        "epoch": 1001.6,
        "run_id": run_id,
        "case_id": case_id,
        "argv0": "python3",
        "argv_sha256": "SA",
        "decision": "admitted",
        "admission_id": "ad-1",
    }
    grandchild = [
        {
            "kind": "child_start",
            "pid": 3,
            "ppid": 2,
            "seq": 1,
            "epoch": 1001.7,
            "run_id": run_id,
            "case_id": case_id,
            "argv_sha256": "CA",
        },
        {
            "kind": "trusted_child_bind",
            "pid": 3,
            "ppid": 2,
            "seq": 2,
            "epoch": 1001.8,
            "run_id": run_id,
            "case_id": case_id,
            "admission_id": "ad-1",
            "source_sha256": "S",
            "source_bytes": 42,
            "script_path": "scripts/x.py",
            "runner_sha256": "R",
            "env_sha256": "E",
            "fd_device": 11,
            "fd_inode": 22,
        },
        {
            "kind": "child_exit",
            "pid": 3,
            "ppid": 2,
            "seq": 3,
            "epoch": 1001.9,
            "run_id": run_id,
            "case_id": case_id,
        },
    ]
    events = _closed_run_events(run_id, case_id=case_id)
    events[2]["ppid"] = 1  # primary child start: parent is the driver pid
    # bootstrap, run_begin, child_start(2) — then admission, launch,
    # grandchild segment, child_exit(2), run_end. The primary exit moves
    # to seq 4 because admission/launch occupy seqs 2–3.
    return [
        *events[:3],
        admission,
        launch,
        *grandchild,
        {**events[3], "seq": 4},
        events[4],
    ]


def _joined_coverage(events: list[dict], run_id: str = "join-run") -> dict:
    return benchmark._coverage_observed_report(
        events,
        timed_out=False,
        exit_code=0,
        run_id=run_id,
        case_id="case-x",
    )


@allure.story("Trusted child join")
@allure.title("Admission + independent child bind keep coverage complete")
def test_coverage_trusted_child_join_complete() -> None:
    report = _joined_coverage(_trusted_child_events("join-run"))
    assert report["coverage"]["status"] == "complete"
    assert report["python_scope"]["native_launch_admitted"] == 1
    assert report["python_scope"]["trusted_children"] == 1
    assert report["python_scope"]["trusted_child_admissions"] == 1
    assert report["invocation"]["model_attempts"]["status"] == "observed"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda ev: [
                e for e in ev if e["kind"] != "trusted_child_bind"
            ],
            "trusted_child_missing_bind",
        ),
        (
            lambda ev: [
                {**e, "source_sha256": "FORGED"}
                if e["kind"] == "trusted_child_bind"
                else e
                for e in ev
            ],
            "trusted_child_mismatch",
        ),
        (
            lambda ev: [
                {**e, "env_sha256": "FORGED"}
                if e["kind"] == "trusted_child_bind"
                else e
                for e in ev
            ],
            "trusted_child_mismatch",
        ),
        (
            lambda ev: [
                {**e, "argv_sha256": "FORGED"}
                if e["kind"] == "child_start" and e["pid"] == 3
                else e
                for e in ev
            ],
            "trusted_child_mismatch",
        ),
        (
            lambda ev: [
                {**e, "fd_inode": 99}
                if e["kind"] == "trusted_child_bind"
                else e
                for e in ev
            ],
            "trusted_child_mismatch",
        ),
        (
            lambda ev: [
                e for e in ev if e["kind"] != "trusted_child_admission"
            ],
            "admitted_launch_without_admission",
        ),
        (
            lambda ev: [
                {**e, "admission_id": "ad-2"}
                if e["kind"] == "trusted_child_bind"
                else e
                for e in ev
            ],
            "trusted_child_orphan_bind",
        ),
    ],
)
def test_coverage_trusted_child_tamper_fails_closed(mutate, reason) -> None:
    events = mutate(_trusted_child_events("join-run"))
    report = _joined_coverage(events)
    assert report["coverage"]["status"] == "incomplete"
    assert reason in report["coverage"]["reasons"]
    assert report["invocation"]["model_attempts"]["status"] == "unknown"


def test_coverage_admission_without_child_unjoined() -> None:
    """An admission the child never consumes cannot claim a trusted child."""
    events = [
        e
        for e in _trusted_child_events("join-run")
        if e["kind"] not in ("trusted_child_bind",)
        and not (e["kind"] == "child_start" and e["pid"] == 3)
        and not (e["kind"] == "child_exit" and e["pid"] == 3)
    ]
    report = _joined_coverage(events)
    assert report["coverage"]["status"] == "incomplete"
    assert "trusted_child_unjoined" in report["coverage"]["reasons"]


def test_coverage_bind_replay_across_two_children() -> None:
    """One admission must bind exactly one child — a second bind replays."""
    events = _trusted_child_events("join-run")
    replay = [
        {
            "kind": "child_start",
            "pid": 4,
            "ppid": 2,
            "seq": 1,
            "epoch": 1002.1,
            "run_id": "join-run",
            "case_id": "case-x",
            "argv_sha256": "CA",
        },
        {
            "kind": "trusted_child_bind",
            "pid": 4,
            "ppid": 2,
            "seq": 2,
            "epoch": 1002.2,
            "run_id": "join-run",
            "case_id": "case-x",
            "admission_id": "ad-1",
            "source_sha256": "S",
            "source_bytes": 42,
            "script_path": "scripts/x.py",
            "runner_sha256": "R",
            "env_sha256": "E",
            "fd_device": 11,
            "fd_inode": 22,
        },
        {
            "kind": "child_exit",
            "pid": 4,
            "ppid": 2,
            "seq": 3,
            "epoch": 1002.3,
            "run_id": "join-run",
            "case_id": "case-x",
        },
    ]
    # Insert the second grandchild before the primary child_exit.
    idx = next(
        i
        for i, e in enumerate(events)
        if e["kind"] == "child_exit" and e["pid"] == 2
    )
    events = [*events[:idx], *replay, *events[idx:]]
    report = _joined_coverage(events)
    assert report["coverage"]["status"] == "incomplete"
    assert "trusted_child_replay" in report["coverage"]["reasons"]


@allure.story("Trusted child join")
@allure.title("Public CLI script runs under the admitted trusted runner")
def test_observed_cli_script_executes_via_trusted_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_observed_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    case = next(
        c for c in _corpus()["cases"] if c["id"] == "python-script-en"
    )
    row = benchmark._run_observed_cli(case, root=root, ledger_path=ledger)
    observation = row["observation"]
    assert "EVIDENCE_META_SYNC_OK" in row["output_excerpt"] or row["success"]
    assert observation["coverage"]["status"] == "complete"
    assert observation["python_scope"]["native_launch_denied"] == 0
    assert observation["python_scope"]["native_launch_admitted"] == 1
    assert observation["python_scope"]["trusted_children"] == 1
    # The fixture script is contract-only: it executes for real but can
    # never count as original-task completion.
    assert row["completion_evidence"]["category"] == "contract_only"
    assert row["zero_completed"] is False
