"""P6 extension evidence corpus contracts — independent of frozen v1.

The p6 corpus adds RU/EN, adversarial, malformed and large-input cases on top
of the frozen v1 acceptance corpus. It never edits v1 ids, prompts, fixture
or hashes; its own sha256 lock freezes it once authored.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import allure
from bench import evidence_benchmark as benchmark
from greedy_token.cheap_llm import clear_cheap_llm_probe_cache

pytestmark = [
    allure.epic("Evidence benchmark"),
    allure.parent_suite("Evidence benchmark"),
    allure.feature("P6 extension corpus"),
    allure.suite("Evidence benchmark"),
]

REPO_ROOT = Path(__file__).resolve().parents[1]
V1_CORPUS = REPO_ROOT / "bench" / "evidence_corpus.v1.yaml"
V1_LOCK = REPO_ROOT / "bench" / "evidence_corpus.v1.sha256"
P6_CORPUS = REPO_ROOT / "bench" / "evidence_corpus.p6.yaml"
P6_LOCK = REPO_ROOT / "bench" / "evidence_corpus.p6.sha256"
PROVENANCE = "synthetic-p6-acceptance-fixture"


def _p6_corpus() -> dict:
    return yaml.safe_load(P6_CORPUS.read_text(encoding="utf-8"))


def _prepared_p6_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    corpus = _p6_corpus()
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


@allure.story("Freeze")
@allure.title("P6 corpus hash, provenance, languages and frozen status agree; v1 untouched")
def test_p6_corpus_frozen_lock_and_provenance() -> None:
    corpus, lock = benchmark._load_corpus(P6_CORPUS, P6_LOCK)
    meta = corpus["corpus"]
    assert lock["verified"] is True
    assert meta["status"] == "frozen"
    assert meta["version"] == "6.0.0"
    assert set(meta["languages"]) == {"en", "ru"}
    assert meta["provenance"]["id"] == PROVENANCE
    assert meta["extends"] == "greedy-token-public-e2e-evidence@1.0.0"
    assert meta["exclusions"]["route_examples_reused"] is False
    assert meta["exclusions"]["route_patterns_reused_as_cases"] is False
    assert meta["exclusions"]["v1_corpus_modified"] is False
    # The extension corpus must leave frozen v1 verifiably untouched.
    _, v1_lock = benchmark._load_corpus(V1_CORPUS, V1_LOCK)
    assert v1_lock["verified"] is True


@allure.story("Freeze")
@allure.title("P6 cases are disjoint from v1 ids/tasks, route examples and patterns")
def test_p6_corpus_independent_of_v1_and_routes() -> None:
    corpus = _p6_corpus()
    v1 = yaml.safe_load(V1_CORPUS.read_text(encoding="utf-8"))
    v1_ids = {str(case["id"]) for case in v1["cases"]}
    v1_tasks = {
        str(case["task"]).casefold().strip() for case in v1["cases"]
    }
    p6_ids = [str(case["id"]) for case in corpus["cases"]]
    assert len(p6_ids) == len(set(p6_ids))
    assert all(case_id.startswith("p6-") for case_id in p6_ids)
    assert set(p6_ids).isdisjoint(v1_ids)

    tasks = {
        str(case["task"]).casefold().strip() for case in corpus["cases"]
    }
    assert tasks.isdisjoint(v1_tasks)

    examples = yaml.safe_load(
        (REPO_ROOT / "bench" / "route_examples.yaml").read_text(encoding="utf-8")
    )
    example_tasks = {
        str(case["task"]).casefold().strip() for case in examples["cases"]
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
        example not in task for task in tasks for example in example_tasks
    )


@allure.story("Oracle schema")
@allure.title("Every P6 task has language, provenance, route target and a specific oracle")
def test_p6_corpus_task_oracles() -> None:
    cases = _p6_corpus()["cases"]
    assert {case["lang"] for case in cases} == {"en", "ru"}
    assert {case["expected_target"] for case in cases} == {
        "tool",
        "rag",
        "cursor",
    }
    for case in cases:
        assert case["provenance_id"] == PROVENANCE
        oracle = case["oracle"]
        operation = case["operation"]
        if operation == "search":
            assert "expected_exit_code" in oracle
            # A hit asserts file/line; a negative case asserts the miss text.
            assert (
                oracle.get("expected_files") and oracle.get("expected_lines")
            ) or oracle.get("output_contains")
        elif operation == "rag":
            assert oracle["expected_chunk_ids"]
        elif operation == "fallback":
            assert oracle["expected_chunk_ids"]
            assert oracle["expected_escalation"]
        elif operation == "escalation":
            assert oracle["expected_escalation"]
        else:  # pragma: no cover - schema guard for future cases
            raise AssertionError(f"unexpected p6 operation {operation}")
        if case["family"] == "false-cheap-edit":
            assert case["adversarial"] is True


@allure.story("Completion contract")
@allure.title("Every P6 case id has a pre-declared completion category in code")
def test_p6_completion_contract_predeclared() -> None:
    cases = _p6_corpus()["cases"]
    categories = benchmark._COMPLETION_CATEGORY
    p6_declared = {
        case_id
        for case_id in categories
        if case_id.startswith("p6-")
    }
    assert p6_declared == {case["id"] for case in cases}
    for case in cases:
        category = categories[case["id"]]
        assert category != "unclassified"
        if case["operation"] == "rag":
            assert category in benchmark._TASK_COMPLETION_CATEGORIES
        else:
            # Search under denied-native coverage, fallback, routing and
            # malformed-input cases are honest non-completion evidence.
            assert (
                category not in benchmark._TASK_COMPLETION_CATEGORIES
                or case["operation"] == "search"
            )


@allure.story("Deterministic route gate")
@allure.title("P6 corpus routes in a temp workspace without pattern tuning")
def test_p6_route_classification_in_temp_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = _p6_corpus()
    with benchmark._ollama_stub() as url:
        monkeypatch.setenv("OLLAMA_URL", url)
        monkeypatch.setenv("BENCH_MODEL", "evidence-stub")
        monkeypatch.setenv("GREEDY_TOKEN_LOG", "0")
        clear_cheap_llm_probe_cache()
        benchmark._write_fixture(corpus, tmp_path)
        rows = benchmark._classify_routes(corpus["cases"], tmp_path)
    clear_cheap_llm_probe_cache()
    assert all(row["ok"] for row in rows)
    assert not any(row["false_cheap"] for row in rows)


@pytest.mark.parametrize(
    "output",
    [
        "p6-fallback-playbook",
        "p6-fallback-playbook: the document discusses fallback policy",
        "p6-fallback-playbook: tool->rag transition executed successfully",
    ],
)
@pytest.mark.parametrize("source", ["missing", "foreign", "incomplete", "unbound"])
def test_p6_escalation_requires_bound_transition_source(output, source):
    case = next(c for c in _p6_corpus()["cases"] if c["id"] == "p6-fallback-ru")
    raw = {"exit_code": 0, "output": output, "route_target": "tool", "error": None}
    if source != "missing":
        raw["transition_source"] = {
            "run_id": "foreign" if source == "foreign" else "fixture-run",
            "case_id": case["id"],
            "complete": source != "incomplete",
            "transition": "tool->rag",
            "source": "stdout" if source == "unbound" else "claimed guarded driver",
        }
    evaluated = benchmark._evaluate(case, raw)
    assert evaluated["observed_escalation"] is None
    assert evaluated["success"] is False
    assert evaluated["escalation_evidence"]["status"] == "unknown"


@allure.story("Observation ledger")
@allure.title("P6 closed pure-Python rag run earns zero_completed with complete coverage")
def test_p6_observed_rag_run_reports_invocation_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_p6_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    case = next(
        c for c in _p6_corpus()["cases"] if c["id"] == "p6-rag-quota-en"
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
    assert row["success"] is True
    assert row["completion_evidence"]["category"] == "rag_excerpt"
    assert row["completion_evidence"]["satisfied"] is True
    assert row["zero_completed"] is True


@allure.story("Observation ledger")
@allure.title("P6 CLI search selects the python backend before any native launch")
def test_p6_observed_cli_search_python_backend_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _prepared_p6_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    case = next(
        c for c in _p6_corpus()["cases"] if c["id"] == "p6-search-gamma-en"
    )
    row = benchmark._run_observed_cli(case, root=root, ledger_path=ledger)
    observation = row["observation"]
    events = observation["events"]
    launches = [e for e in events if e["kind"] == "native_launch"]
    assert not any(
        e["decision"] == "denied" for e in launches
    ), f"denied native launches: {launches}"
    backend = [e for e in events if e["kind"] == "search_backend"]
    assert backend, "expected a search_backend selection event"
    assert backend[0]["engine"] == "python"
    assert backend[0]["native"] == "skipped"
    assert observation["coverage"]["status"] == "complete"
    assert observation["invocation"]["model_attempts"]["status"] == "observed"
    assert observation["invocation"]["llm_requests_sent"]["status"] == "observed"
    assert row["success"] is True
    assert row["zero_completed"] is True


@pytest.mark.parametrize("method", ["greedy_cli", "greedy_mcp_stdio"])
def test_p6_actual_guarded_fallback_transition_bound(tmp_path, monkeypatch, method):
    root = _prepared_p6_workspace(tmp_path, monkeypatch)
    ledger = _bootstrapped_ledger(tmp_path)
    case = next(c for c in _p6_corpus()["cases"] if c["id"] == "p6-fallback-ru")
    runner = (
        benchmark._run_observed_cli
        if method == "greedy_cli"
        else benchmark._run_observed_mcp_case
    )
    row = runner(case, root=root, ledger_path=ledger)
    assert row["zero_completed"] is False  # fallback_only is never a completion
    evidence = row["escalation_evidence"]
    assert evidence["status"] == "verified"
    assert evidence["request_kind"] == (
        "dynamic_fallback_candidate" if method == "greedy_cli" else "explicit_pipeline"
    )
    assert evidence["source_binding"]["run_id"] == row["run_id"]
    assert row["observation"]["coverage"]["status"] == "complete"
    if method == "greedy_mcp_stdio":
        # Explicit pipeline: search misses, then the rag step answers —
        # the declared tier handoff is observed on the ledger.
        assert evidence["transitions"] == ["tool->rag"]
        assert row["observed_escalation"] == "tool->rag"
        assert row["success"] is True
    print("P6_TRANSITION_PROBE " + str({
        "method": method, "run_id": row["run_id"],
        "checks": row["checks"], "escalation_evidence": evidence,
    }))
