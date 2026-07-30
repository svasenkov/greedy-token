"""Held-out routing classifier gate — not task execution/retrieval success."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import allure
import pytest
import yaml

from greedy_token.router import route_task
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Routing"),
    allure.parent_suite("Routing"),
    allure.feature("Benchmark corpus"),
    allure.suite("Routing corpus"),
]

CORPUS_PATH = Path(__file__).resolve().parents[1] / "bench" / "routing_corpus.yaml"
VALID_TARGETS = frozenset({"tool", "python", "rag", "ollama", "cursor"})
VALID_LANGS = frozenset({"en", "ru"})
FALSE_CHEAP_FAMILY = "false-cheap-edit"


def _load_corpus() -> dict:
    return yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


@allure.story("Scorecard")
@allure.title("Held-out routing corpus meets every v3 quality threshold")
def test_routing_corpus_quality_gate(ollama_workspace: Path) -> None:
    """ollama_workspace stubs cheap LLM so ollama-target cases are deterministic."""
    corpus = _load_corpus()
    cases = corpus["cases"]
    thresholds = corpus["thresholds"]
    rows: list[dict] = []
    hits = 0
    expected_n: Counter[str] = Counter()
    predicted_n: Counter[str] = Counter()
    true_positive: Counter[str] = Counter()
    family_n: Counter[str] = Counter()
    family_hit: Counter[str] = Counter()
    language_n: Counter[str] = Counter()
    language_hit: Counter[str] = Counter()
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    false_cheap_ids: list[str] = []

    for case in cases:
        decision = route_task(case["task"], ollama_workspace)
        expected = case["expected_target"]
        actual = decision.target
        family = case["family"]
        language = case["lang"]
        ok = decision.target == expected
        expected_n[expected] += 1
        predicted_n[actual] += 1
        family_n[family] += 1
        language_n[language] += 1
        confusion[expected][actual] += 1
        if ok:
            hits += 1
            true_positive[expected] += 1
            family_hit[family] += 1
            language_hit[language] += 1
        if family == FALSE_CHEAP_FAMILY and actual != "cursor":
            false_cheap_ids.append(case["id"])
        rows.append(
            {
                "id": case["id"],
                "task": case["task"],
                "expected": expected,
                "actual": actual,
                "route_id": decision.route_id,
                "lang": language,
                "family": family,
                "ok": ok,
            }
        )

    n = len(cases)
    exact_match_accuracy = _ratio(hits, n)
    # In single-label multiclass classification, micro-precision equals
    # exact-match accuracy.  Keep both names explicit; neither is task success.
    micro_precision = exact_match_accuracy
    precision_by_target = {
        target: _ratio(true_positive[target], predicted_n[target])
        for target in sorted(VALID_TARGETS)
    }
    recall_by_target = {
        target: _ratio(true_positive[target], expected_n[target])
        for target in sorted(VALID_TARGETS)
    }
    accuracy_by_family = {
        family: _ratio(family_hit[family], count)
        for family, count in sorted(family_n.items())
    }
    accuracy_by_language = {
        language: _ratio(language_hit[language], count)
        for language, count in sorted(language_n.items())
    }
    false_cheap_n = family_n[FALSE_CHEAP_FAMILY]
    false_cheap_rate = _ratio(len(false_cheap_ids), false_cheap_n)
    confusion_matrix = {
        expected: {
            actual: confusion[expected][actual]
            for actual in sorted(VALID_TARGETS)
        }
        for expected in sorted(VALID_TARGETS)
    }
    attach_json(
        "scorecard",
        {
            "metric_scope": "route classification only; execution/retrieval success is separate",
            "exact_match_accuracy": exact_match_accuracy,
            "micro_precision": micro_precision,
            "hits": hits,
            "n": n,
            "confusion_matrix": confusion_matrix,
            "precision_by_target": precision_by_target,
            "recall_by_target": recall_by_target,
            "accuracy_by_family": accuracy_by_family,
            "accuracy_by_language": accuracy_by_language,
            "false_cheap_rate": false_cheap_rate,
            "false_cheap_ids": false_cheap_ids,
            "rows": rows,
        },
    )
    attach_text(
        "summary",
        f"{hits}/{n} = {exact_match_accuracy:.2%} exact-match accuracy / "
        f"micro-precision; false-cheap={false_cheap_rate:.2%}; "
        f"target_precision={precision_by_target}; target_recall={recall_by_target}",
    )
    misses = [r["id"] for r in rows if not r["ok"]]
    assert exact_match_accuracy >= float(thresholds["exact_match_accuracy"]), (
        f"routing exact-match accuracy {exact_match_accuracy:.2%} below threshold; "
        f"misses={misses}"
    )
    assert micro_precision >= float(thresholds["micro_precision"])
    assert false_cheap_rate == float(thresholds["false_cheap_rate"]) == 0.0, (
        f"false-cheap cases routed below cursor: {false_cheap_ids}"
    )
    for target, target_thresholds in thresholds["per_target"].items():
        assert precision_by_target[target] >= float(target_thresholds["precision"]), (
            f"{target} precision {precision_by_target[target]:.2%} below threshold"
        )
        assert recall_by_target[target] >= float(target_thresholds["recall"]), (
            f"{target} recall {recall_by_target[target]:.2%} below threshold"
        )
    for family, threshold in thresholds["per_family"].items():
        assert accuracy_by_family[family] >= float(threshold), (
            f"{family} exact-match accuracy {accuracy_by_family[family]:.2%} below threshold"
        )
    for language, threshold in thresholds["per_language"].items():
        assert accuracy_by_language[language] >= float(threshold), (
            f"{language} exact-match accuracy {accuracy_by_language[language]:.2%} below threshold"
        )


@allure.story("Scorecard")
@allure.title("Corpus v3 schema separates examples and covers every gated slice")
def test_routing_corpus_schema() -> None:
    corpus = _load_corpus()
    assert corpus.get("version") == 3
    assert corpus.get("split") == "held-out-adversarial"
    assert "min_precision" not in corpus
    cases = corpus.get("cases") or []
    min_cases = int(corpus.get("min_cases", 50))
    min_per = int(corpus.get("min_per_target", 3))
    min_per_family = int(corpus.get("min_per_family", 3))
    required = list(corpus.get("required_targets") or sorted(VALID_TARGETS))
    thresholds = corpus["thresholds"]
    assert len(cases) >= min_cases, f"need ≥{min_cases} cases, got {len(cases)}"

    counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    langs: set[str] = set()
    ids: set[str] = set()
    tasks: set[str] = set()
    adversarial = 0
    for case in cases:
        cid = case["id"]
        assert cid not in ids, f"duplicate corpus id: {cid}"
        ids.add(cid)
        task = case.get("task")
        assert task, f"{cid}: empty task"
        normalized_task = str(task).casefold().strip()
        assert normalized_task not in tasks, f"duplicate corpus task: {task}"
        tasks.add(normalized_task)
        expected = case["expected_target"]
        assert expected in VALID_TARGETS, f"{cid}: bad target {expected}"
        lang = case.get("lang")
        assert lang in VALID_LANGS, f"{cid}: lang must be en|ru, got {lang!r}"
        langs.add(lang)
        family = case.get("family")
        assert family, f"{cid}: family required in v3"
        counts[expected] += 1
        family_counts[family] += 1
        if case.get("adversarial"):
            adversarial += 1
            assert family == FALSE_CHEAP_FAMILY

    assert langs == VALID_LANGS, f"need both en and ru, got {langs}"
    for target in required:
        assert counts[target] >= min_per, (
            f"target {target}: {counts[target]} cases < min_per_target {min_per}"
        )
    for family, count in family_counts.items():
        assert count >= min_per_family, (
            f"family {family}: {count} cases < min_per_family {min_per_family}"
        )
    assert adversarial >= 14
    assert set(thresholds["per_target"]) == set(required)
    assert set(thresholds["per_family"]) == set(family_counts)
    assert set(thresholds["per_language"]) == VALID_LANGS
    assert float(thresholds["false_cheap_rate"]) == 0.0

    examples_path = CORPUS_PATH.parent / corpus["route_examples"]
    examples = yaml.safe_load(examples_path.read_text(encoding="utf-8"))
    assert examples.get("split") == "route-examples"
    assert examples.get("gating") is False
    example_tasks = {
        str(case["task"]).casefold().strip()
        for case in examples.get("cases", [])
    }
    assert example_tasks
    assert tasks.isdisjoint(example_tasks), (
        "held-out gate must not reuse canonical route example prompts"
    )
    attach_json(
        "coverage",
        {
            "target_counts": dict(counts),
            "family_counts": dict(family_counts),
            "langs": sorted(langs),
            "adversarial": adversarial,
            "n": len(cases),
        },
    )
