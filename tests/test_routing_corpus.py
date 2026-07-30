"""Routing benchmark corpus — product usefulness scorecard (Trust cut v0.13)."""

from __future__ import annotations

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


def _load_corpus() -> dict:
    return yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))


@allure.story("Scorecard")
@allure.title("Published routing corpus meets min_precision")
def test_routing_corpus_precision(minimal_workspace: Path) -> None:
    corpus = _load_corpus()
    cases = corpus["cases"]
    min_precision = float(corpus.get("min_precision", 0.85))
    rows: list[dict] = []
    hits = 0

    for case in cases:
        decision = route_task(case["task"], minimal_workspace)
        ok = decision.target == case["expected_target"]
        if ok:
            hits += 1
        rows.append(
            {
                "id": case["id"],
                "task": case["task"],
                "expected": case["expected_target"],
                "actual": decision.target,
                "route_id": decision.route_id,
                "ok": ok,
            }
        )

    precision = hits / len(cases) if cases else 0.0
    attach_json("scorecard", {"precision": precision, "hits": hits, "n": len(cases), "rows": rows})
    attach_text("summary", f"{hits}/{len(cases)} = {precision:.2%} (min {min_precision:.0%})")
    assert precision >= min_precision, (
        f"routing precision {precision:.2%} < {min_precision:.0%}; "
        f"misses={[r['id'] for r in rows if not r['ok']]}"
    )


@allure.story("Scorecard")
@allure.title("Corpus file is non-empty and versioned")
def test_routing_corpus_schema() -> None:
    corpus = _load_corpus()
    assert corpus.get("version") == 1
    assert len(corpus.get("cases") or []) >= 8
    for case in corpus["cases"]:
        assert case["expected_target"] in {"tool", "python", "rag", "ollama", "cursor"}
