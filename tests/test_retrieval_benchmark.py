from __future__ import annotations

from pathlib import Path

from bench.retrieval_benchmark import benchmark_latency, evaluate, load_corpus
from greedy_token.rag_search import RagHit


def _hit(chunk_id: str) -> RagHit:
    return RagHit(
        chunk_id=chunk_id,
        path=f"docs/rag/{chunk_id}.md",
        domain="config",
        score=1.0,
        excerpt=chunk_id,
        engine="fts5-bm25",
    )


def test_retrieval_corpus_declares_expected_ids_and_required_groups() -> None:
    corpus = load_corpus(
        Path(__file__).resolve().parents[1] / "bench" / "retrieval_corpus.jsonl"
    )

    assert len(corpus) >= 15
    assert {case["locale"] for case in corpus} == {"ru", "en"}
    assert {"morphology", "paraphrase"} <= {
        case["case_type"] for case in corpus
    }
    assert all(case["expected"] for case in corpus)


def test_evaluate_reports_recall_mrr_and_group_breakdowns(tmp_path: Path) -> None:
    cases = [
        {
            "query": "first",
            "expected": ["a"],
            "locale": "en",
            "domain": "config",
            "case_type": "exact",
        },
        {
            "query": "second",
            "expected": ["b", "c"],
            "locale": "ru",
            "domain": "testing",
            "case_type": "paraphrase",
        },
    ]

    def search(query: str, root: Path, **kwargs) -> list[RagHit]:
        del root, kwargs
        return [_hit("a"), _hit("x")] if query == "first" else [_hit("x"), _hit("b")]

    result = evaluate(cases, tmp_path, search=search)

    assert result["overall"] == {
        "cases": 2,
        "recall_at_1": 0.5,
        "recall_at_3": 0.75,
        "recall_at_5": 0.75,
        "mrr": 0.75,
    }
    assert result["by_locale"]["ru"]["recall_at_3"] == 0.5
    assert result["by_domain"]["config"]["mrr"] == 1.0
    assert result["by_case_type"]["paraphrase"]["mrr"] == 0.5


def test_latency_benchmark_reports_cold_and_warm_samples(tmp_path: Path) -> None:
    cases = [
        {
            "query": "query",
            "expected": ["a"],
            "locale": "en",
            "domain": "config",
            "case_type": "exact",
        }
    ]

    def search(query: str, root: Path, **kwargs) -> list[RagHit]:
        del query, root, kwargs
        return [_hit("a")]

    result = benchmark_latency(cases, tmp_path, repeats=2, search=search)

    assert result["cold_index_ms"] >= 0
    assert result["warm_query_median_ms"] >= 0
    assert result["warm_query_p95_ms"] >= 0
    assert result["warm_samples"] == 2
