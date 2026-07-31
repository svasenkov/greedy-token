#!/usr/bin/env python
"""Evaluate lexical RAG quality and cold/warm SQLite FTS5 latency."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

from greedy_token.rag_fts import index_path
from greedy_token.rag_search import RagHit, search_rag

DEFAULT_CORPUS = Path(__file__).with_name("retrieval_corpus.jsonl")
Search = Callable[..., list[RagHit]]


def load_corpus(path: Path) -> list[dict]:
    cases: list[dict] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        case = json.loads(raw)
        required = ("query", "expected", "locale", "domain", "case_type")
        if any(not case.get(key) for key in required):
            raise ValueError(f"{path}:{line_number}: missing required corpus field")
        cases.append(case)
    return cases


def _metrics(rows: list[dict]) -> dict:
    count = len(rows)
    if not count:
        return {"cases": 0, "recall_at_1": 0.0, "recall_at_3": 0.0, "recall_at_5": 0.0, "mrr": 0.0}
    return {
        "cases": count,
        "recall_at_1": round(sum(row["recall_at_1"] for row in rows) / count, 4),
        "recall_at_3": round(sum(row["recall_at_3"] for row in rows) / count, 4),
        "recall_at_5": round(sum(row["recall_at_5"] for row in rows) / count, 4),
        "mrr": round(sum(row["reciprocal_rank"] for row in rows) / count, 4),
    }


def evaluate(cases: list[dict], root: Path, search: Search = search_rag) -> dict:
    rows: list[dict] = []
    groups: dict[str, dict[str, list[dict]]] = {
        "locale": defaultdict(list),
        "domain": defaultdict(list),
        "case_type": defaultdict(list),
    }
    for case in cases:
        expected = set(case["expected"])
        hits = search(
            case["query"], root, domains=[case["domain"]], limit=5
        )
        ranked = [hit.chunk_id for hit in hits]
        recalls = {
            cutoff: len(expected.intersection(ranked[:cutoff])) / len(expected)
            for cutoff in (1, 3, 5)
        }
        first_rank = next(
            (rank for rank, chunk_id in enumerate(ranked, start=1) if chunk_id in expected),
            None,
        )
        row = {
            "query": case["query"],
            "expected": sorted(expected),
            "ranked": ranked,
            "locale": case["locale"],
            "domain": case["domain"],
            "case_type": case["case_type"],
            "recall_at_1": recalls[1],
            "recall_at_3": recalls[3],
            "recall_at_5": recalls[5],
            "reciprocal_rank": 1.0 / first_rank if first_rank else 0.0,
        }
        rows.append(row)
        for group_name in groups:
            groups[group_name][str(case[group_name])].append(row)
    return {
        "overall": _metrics(rows),
        "by_locale": {key: _metrics(value) for key, value in groups["locale"].items()},
        "by_domain": {key: _metrics(value) for key, value in groups["domain"].items()},
        "by_case_type": {
            key: _metrics(value) for key, value in groups["case_type"].items()
        },
        "results": rows,
    }


def benchmark_latency(
    cases: list[dict],
    root: Path,
    *,
    repeats: int = 3,
    search: Search = search_rag,
) -> dict:
    if not cases:
        return {"cold_index_ms": 0.0, "warm_query_median_ms": 0.0, "warm_query_p95_ms": 0.0}
    cache = index_path(root)
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(f"{cache}{suffix}").unlink()
        except FileNotFoundError:
            pass
    first = cases[0]
    started = time.perf_counter()
    search(first["query"], root, domains=[first["domain"]], limit=5)
    cold_ms = (time.perf_counter() - started) * 1000
    samples: list[float] = []
    for _ in range(max(1, repeats)):
        for case in cases:
            started = time.perf_counter()
            search(case["query"], root, domains=[case["domain"]], limit=5)
            samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    p95_index = max(0, int(0.95 * len(ordered) + 0.999999) - 1)
    return {
        "cold_index_ms": round(cold_ms, 3),
        "warm_query_median_ms": round(statistics.median(samples), 3),
        "warm_query_p95_ms": round(ordered[p95_index], 3),
        "warm_samples": len(samples),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(os.environ.get("GREEDY_TOKEN_ROOT", "."))
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    cases = load_corpus(args.corpus.expanduser().resolve())
    result = evaluate(cases, root)
    result["latency"] = benchmark_latency(cases, root, repeats=args.repeats)
    result["engine"] = "lexical-bm25-fts5"
    result["corpus"] = str(args.corpus)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
