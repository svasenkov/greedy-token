# Cut checklist — greedy-token v0.9.0

**Status:** CUT — bump + gate green, tag/push per explicit user command.

## Summary

Unified model registry release (ADR-0001) + mutation-equivalents golden
registry, plus the MCP crystallize tool.

- **unified ModelSpec (ADR-0001)**: orthogonal attributes — `locality`
  (`local | remote`), `billing` (`free | metered`), `cost_per_1m_usd`
  (nullable: absent = unknown ≠ 0), `provider` unchanged. The cheap/expensive
  tier is **derived** in exactly one place, `derive_tier()` in
  `model_select.py` (free → cheap; metered ≤ threshold → cheap; else —
  including unknown cost — expensive); no stored tier field. One `llm.models[]`
  pool replaces `cheap_models` / `expensive_models`; selection and escalation
  resolve by derived tier. Packaged presets migrated (the refactor exposed and
  fixed a real contradiction: `cursor-like-catalog` listed the same Groq model
  as both cheap and expensive).
- **backward compatibility**: legacy YAML (`llm.cheap` / `llm.expensive` /
  `cheap_llm:`), env (`CHEAP_LLM_*` / `OLLAMA_*` / `GREEDY_LLM_*`),
  `token_price` alias, and usage.jsonl `billing_tier` all keep working; old
  telemetry events are read as-is, new events log the derived value into the
  same field; `report` / spend caps are correct on mixed logs.
- **mutation-equivalents golden registry**: `docs/mutation-equivalents.yaml`
  — one entry per `# equivalent:` / `# pragma: no mutate` marker in
  `src/greedy_token/`, anchored on file + normalized marker text (not mutmut
  ids). Two-way drift guard `tests/test_mutation_equivalents.py`: marker
  without entry, entry without marker, or pragma without proof → red.
- **MCP**: 6th tool `greedy_token_crystallize`
  (`action=draft|promote|reject` + `crystal_id`, plain text, no auto-apply).
- **README reviews**: Fable 5 re-review card (EN+RU) — 10/10 after all four
  first-review gaps were closed and re-verified.

## CONTRACT — evidence

| Claim | Evidence |
|-------|----------|
| version 0.9.0 | `pyproject.toml` (SSOT via `src/greedy_token/version.py`) |
| derive_tier in one place | `derive_tier()` in `src/greedy_token/model_select.py`; no stored tier |
| ADR accepted | `docs/adr/0001-unified-model-spec-derived-tier.md` |
| unified pool | `LlmRegistry.models` + tier-derived selection in `model_select.py` |
| legacy config/env/telemetry compat | `tests/test_model_select_gaps.py`, `tests/test_settings*.py`, mixed-log tests |
| golden registry two-way guard | `docs/mutation-equivalents.yaml` + `tests/test_mutation_equivalents.py` |
| MCP crystallize tool | `greedy_token_crystallize` in `src/greedy_token/mcp.py`; stdio e2e in `tests/test_mcp_stdio.py` |

## Gate (scripts/release-gate.sh 0.9.0)

```text
905 passed
1 @release passed (pyproject + __version__ == 0.9.0)
minTestsCount synced → 905
workflows match _ethalon
release gate OK: 0.9.0
```

Line + branch coverage on `src/greedy_token/` stays at 100% (`fail_under = 100`).
Doc-drift guards (`tests/test_doc_sync.py`, `tests/test_mutation_equivalents.py`) green.

## Cut commands

```bash
cd projects/greedy-token-home/greedy-token

# 1) Tag
git tag -a v0.9.0 -m "Release v0.9.0: unified ModelSpec (derived tier, ADR-0001), mutation-equivalents golden registry, MCP crystallize tool"

# 2) Push main + tag
git push origin main
git push origin v0.9.0

# 3) GitHub Release (triggers PyPI via publish.yml)
gh release create v0.9.0 --title "v0.9.0" --notes-file - <<'EOF'
## Summary
- Unified model registry (ADR-0001): orthogonal ModelSpec (`locality`, `billing`, `cost_per_1m_usd`), cheap/expensive derived by a single `derive_tier()`, one `llm.models[]` pool; presets migrated
- Full backward compatibility: legacy `llm.cheap`/`llm.expensive` YAML, `CHEAP_LLM_*`/`OLLAMA_*` env, usage.jsonl `billing_tier`
- Golden registry of mutation equivalents (`docs/mutation-equivalents.yaml`) with a two-way drift guard — new suppressions without a reviewed proof fail CI
- 6th MCP tool `greedy_token_crystallize` (`action=draft|promote|reject`, no auto-apply)
- minTestsCount 905

## Install
pip install greedy-token==0.9.0
EOF
```

## After PyPI

- Verify: `pip index versions greedy-token` / PyPI page
- Reload MCP server in Cursor so `greedy_token_crystallize` appears (6 tools)
- Monorepo hub README: set published PyPI pin to v0.9.0
