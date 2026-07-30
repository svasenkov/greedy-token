# Cut checklist — greedy-token v0.14.0

**Status:** READY — Corpus v2 (not shipped; pending release gate + explicit «релиз»).

Post–Trust cut track **C**: expand routing benchmark corpus for zone
coverage (tool / python / rag / ollama / cursor), RU+EN prompts, and a
stricter precision gate. No Windows / FTS / host pre-router in this cut.

## Summary

- **Corpus v2**: `bench/routing_corpus.yaml` `version: 2`, **45 cases**
  (≥30), `min_precision: 0.90`, `min_per_target: 3`, both `lang: en|ru`.
  Families: search, false-cheap-edit, wiring, docs, script, cheap-llm.
- **Scorecard**: overall precision + per-target recall + per-lang split
  in `tests/test_routing_corpus.py` (uses `ollama_workspace` so ollama
  cases are CI-deterministic). Measured **45/45 = 100%** with workspace
  routes overlay (`examples/routes/workspace-routes.yaml`).
- **Honesty invariants unchanged**: ★ $82/$820 illustrative CLI/pipeline;
  no auto-chain from `route_task`; rag = lexical; confidence ≠ correctness.

## CONTRACT — evidence

| Claim | Evidence |
|-------|----------|
| corpus v2 schema | `bench/routing_corpus.yaml` (`version: 2`, 45 cases) |
| precision gate 0.90 | `min_precision` + `test_routing_corpus_precision` (100%) |
| zone coverage | `required_targets` + `test_routing_corpus_schema` (≥3/target) |
| RU+EN + families | `lang` + `family` on every case; schema test |
| ROADMAP near-term | `docs/ROADMAP.md` / `ROADMAP-RU.md` row **v0.14** |

## Out of scope

- Windows CI matrix (→ v0.15+)
- FTS / embedding “vector RAG”
- Host-level pre-router (needs host API)

## Gate (when shipping)

```bash
./scripts/release-gate.sh 0.14.0
```

Bump `pyproject.toml` version → tag `v0.14.0` only after gate green and
explicit publish OK. Nested push only on explicit «запушь».
