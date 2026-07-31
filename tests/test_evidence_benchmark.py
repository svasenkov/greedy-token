"""Public E2E evidence benchmark contracts (the full run is a CI artifact job)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import allure
import pytest
import yaml

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
    assert benchmark._package_version() == "0.15.0"


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
@allure.title("Savings appear only for authoritative successful same-task baselines")
def test_evidence_authoritative_same_task_savings() -> None:
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
    assert cheap["savings"]["tokens_saved"] == 100
    assert cheap["savings"]["cost_saved_usd"] == 0.25
    assert cheap["savings"]["status"] == (
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
                    attempts=1,
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
    assert scorecard["gates"]["all_passed"] is True
