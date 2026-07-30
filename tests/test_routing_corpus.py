"""Routing benchmark corpus v2 — precision/recall scorecard (zones, RU+EN)."""

from __future__ import annotations

from collections import defaultdict
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


def _load_corpus() -> dict:
    return yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))


@allure.story("Scorecard")
@allure.title("Published routing corpus meets min_precision (v2)")
def test_routing_corpus_precision(ollama_workspace: Path) -> None:
    """ollama_workspace stubs cheap LLM so ollama-target cases are deterministic."""
    corpus = _load_corpus()
    cases = corpus["cases"]
    min_precision = float(corpus.get("min_precision", 0.90))
    rows: list[dict] = []
    hits = 0
    per_target_hit: dict[str, int] = defaultdict(int)
    per_target_n: dict[str, int] = defaultdict(int)
    per_lang_hit: dict[str, int] = defaultdict(int)
    per_lang_n: dict[str, int] = defaultdict(int)

    for case in cases:
        decision = route_task(case["task"], ollama_workspace)
        expected = case["expected_target"]
        ok = decision.target == expected
        if ok:
            hits += 1
            per_target_hit[expected] += 1
            per_lang_hit[case.get("lang", "?")] += 1
        per_target_n[expected] += 1
        per_lang_n[case.get("lang", "?")] += 1
        rows.append(
            {
                "id": case["id"],
                "task": case["task"],
                "expected": expected,
                "actual": decision.target,
                "route_id": decision.route_id,
                "lang": case.get("lang"),
                "family": case.get("family"),
                "ok": ok,
            }
        )

    n = len(cases)
    precision = hits / n if n else 0.0
    # Per-class recall = share of cases with that expected_target that hit.
    recall_by_target = {
        t: (per_target_hit[t] / per_target_n[t] if per_target_n[t] else 0.0)
        for t in sorted(per_target_n)
    }
    precision_by_lang = {
        lang: (per_lang_hit[lang] / per_lang_n[lang] if per_lang_n[lang] else 0.0)
        for lang in sorted(per_lang_n)
    }
    attach_json(
        "scorecard",
        {
            "precision": precision,
            "hits": hits,
            "n": n,
            "recall_by_target": recall_by_target,
            "precision_by_lang": precision_by_lang,
            "rows": rows,
        },
    )
    attach_text(
        "summary",
        f"{hits}/{n} = {precision:.2%} (min {min_precision:.0%}); "
        f"recall={recall_by_target}; lang={precision_by_lang}",
    )
    misses = [r["id"] for r in rows if not r["ok"]]
    assert precision >= min_precision, (
        f"routing precision {precision:.2%} < {min_precision:.0%}; misses={misses}"
    )


@allure.story("Scorecard")
@allure.title("Corpus v2 schema: version, coverage, lang, zones")
def test_routing_corpus_schema() -> None:
    corpus = _load_corpus()
    assert corpus.get("version") == 2
    cases = corpus.get("cases") or []
    min_cases = int(corpus.get("min_cases", 30))
    min_per = int(corpus.get("min_per_target", 3))
    required = list(corpus.get("required_targets") or sorted(VALID_TARGETS))
    assert len(cases) >= min_cases, f"need ≥{min_cases} cases, got {len(cases)}"

    counts: dict[str, int] = defaultdict(int)
    langs: set[str] = set()
    ids: set[str] = set()
    for case in cases:
        cid = case["id"]
        assert cid not in ids, f"duplicate corpus id: {cid}"
        ids.add(cid)
        expected = case["expected_target"]
        assert expected in VALID_TARGETS, f"{cid}: bad target {expected}"
        lang = case.get("lang")
        assert lang in VALID_LANGS, f"{cid}: lang must be en|ru, got {lang!r}"
        langs.add(lang)
        assert case.get("task"), f"{cid}: empty task"
        assert case.get("family"), f"{cid}: family required in v2"
        counts[expected] += 1

    assert langs == VALID_LANGS, f"need both en and ru, got {langs}"
    for target in required:
        assert counts[target] >= min_per, (
            f"target {target}: {counts[target]} cases < min_per_target {min_per}"
        )
    attach_json("coverage", {"counts": dict(counts), "langs": sorted(langs), "n": len(cases)})
