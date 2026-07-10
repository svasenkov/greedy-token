# CONTRACT freeze — greedy-token v0.5.6

**Root:** `projects/greedy-token-home/greedy-token/`  
**TARGET:** `0.5.6` (tag `v0.5.6`)  
**Frozen:** 2026-07-10  
**Inherits:** `docs/CONTRACT-freeze-v0.5.5.md` (V2–V14 unchanged)

## Must-match (release blockers)

| ID | Claim | SSOT |
|----|-------|------|
| V1 | Version **0.5.6** everywhere | `pyproject.toml`, `__init__.py`, README scope |
| V2 | MCP: 5 tools | `mcp.py` — route, rag, search, usage, pipeline |
| V3 | Pipeline dry-run default | `execute=False` / no `--execute` |
| V4 | Tier order + ollama skip if down | `router.py:TIER_ORDER` |
| V5 | Footer economics (dry-run saved=0; RAG spent factual) | `budget.py`, `pipeline.py` |
| V6 | `search-rag` multi-word + `path=` | `pipeline.py`, `pipelines.yaml` |
| V7 | Path confinement under workspace root | `code_search.py`, `pipeline.py` |
| V8 | Shell quoting for root/args | `tool_paths`, `wrappers` |
| V9 | `config --init` without workspace | `cli.py:cmd_config` — exit 0, creates `~/.greedy-token/config.yaml` |
| V10 | README CLI: `config --init` only | `README.md`, `README-RU.md` |
| V11 | `cursor-setup.md` step 1 aligned | `docs/cursor-setup.md` |
| V12 | `run --execute` + cursor → refuse, exit ≠ 0 | `executors.py`, `cli.py` |
| V13 | Usage ollama model from workspace root | `usage.py:executor_from_decision(decision, root)` |
| V14 | Probes green | `compileall`, `pytest -q`, coverage `fail_under=100` |
| V15 | Search MCP/CLI footer — `est_tokens=0`, no fictitious spend | `mcp.py:greedy_token_search`, `code_search.py` |
| V16 | MCP stdio `pipeline execute=true` e2e covered | `tests/test_mcp_stdio.py` |
| V17 | `SearchResult` without dead `spent_tokens` | `code_search.py:SearchResult` |

## Closed in 0.5.6 (was backlog in v0.5.5)

- **B1:** `SearchResult.spent_tokens` unused field — removed (`code_search.py`)
- **B2:** MCP stdio `pipeline execute=true` e2e gap — `test_mcp_stdio.py`

## Accepted backlog (not v0.5.6 blockers)

- B3–B5: ROADMAP / hub docs out of nested scope
- B6: Per-step vs pipeline total saved semantics (documented in footer)

## Stop criteria

Delta re-audit on changed paths: **0 P0/P1** vs table above (×2 consecutive).
