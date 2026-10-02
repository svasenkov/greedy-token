# Cut checklist — greedy-token v0.18.4

**Status:** PREPARED, not tagged. Local tree only — push/tag/release per the
gates below after confirmation.

Patch release after v0.18.3: workshop bench-matrix findings F1–F5 — PyPI
install root resolution, remote LLM timeout clamp, prompt-derived args on
the CLI path, honest hook-mode savings, RAG payload cap, path-aware rg
scoping. No schema break — usage events stay `v:2` (additive `hook_mode`).

## Summary

- **Workspace root resolution (PyPI).** `find_workspace_root` walks up from
  cwd for the nearest `.greedy-token.yaml`; marker pair checked at the same
  depth (nearest declaration wins); package-file marker walk stays as the
  dev fallback. `init` warns when no workspace is detected. Previously every
  command died with `Cannot find workspace root` on pip installs unless
  `GREEDY_TOKEN_ROOT` was set.
- **Remote LLM timeouts.** `locality: remote` calls clamp to 20 s
  (`timeout_s` per model overrides); transport errors hint "unreachable or
  unauthorized", URL stays redacted. Dead hosts no longer hang 77–152 s.
- **`args_from_prompt` everywhere** (F4 + earlier hook path): `plan_run`
  applies `derive_prompt_args` for `params: [args]` routes — same
  first-match order as intercept; derived tokens pass
  `trusted_script_argv` confinement. `run "покажи последние 50 коммитов"
  --execute` → `git-recent.py --count 50 --compact` (was: `count=5`).
- **`git-recent --compact`** (F1): drops `files` lists; global
  `MAX_OUTPUT_BYTES` 20KB cap emits `files_truncated: N` — 50 commits:
  58.9KB → 7.2KB. Listing prompts route to `--compact` via
  `args_from_prompt`.
- **Hook-aware savings** (F2): `append_event` stamps `hook_mode`
  (`effective_hook_mode` — unset+`min_confidence`≤1.0 counts as intercept);
  footers under advisory/gate print `saved ~N (if intercept; turn-shared
  otherwise ~M)` with `M = max(0, baseline − cursor_overhead() − est)`.
- **RAG payload cap** (F3): `rag.max_payload_tokens` (default 8000, `0`
  disables, env `GREEDY_TOKEN_RAG_MAX_PAYLOAD_TOKENS`) — cumulative
  `est_tokens` cap, overflowing hits skipped, response reports
  `truncated` / `hits_dropped`.
- **Path-aware rg scoping** (F5): prompt tokens that resolve to existing
  paths under the workspace root become rg operands instead of pattern
  terms — `find email in lab/users.json` now runs `rg … -- email
  lab/users.json` (was: repo-wide pattern `lab/users.json`, 3,823B noise
  and zero target lines). Raw prompt threaded through `execute_task`,
  `invoke_capability(task=…)`, and the intercept hook. Tool output capped
  at 30 lines with a truncation marker.
- **Benchmark page**: `docs/benchmark.html` — standalone GitHub Pages
  artifact: greedy vs без greedy vs saved tok + per-model $ rates
  (editable), small fixtures, big volumes, live subagent-measured pairs
  incl. honest negatives.

## Honest evidence

- Full suite **2352 passed, 1 skipped**; coverage `fail_under=100` branch —
  green (pre-existing gaps in `cli.py`/`paths.py`/`model_select.py` closed).
- `ruff check` clean on changed files.
- Live workshop: `run --execute` derived-args verified
  (`--count 50 --compact`); rg scoped run returns only the 2 `email` lines
  of `lab/users.json` (149B vs 3,823B pre-fix).
- RAG cap live: monorepo query 6,592 est → cap 4000 → 3,221 est,
  `hits_dropped: 2`.
- Footer live: `advisory: saved ~15,246 (if intercept; turn-shared
  otherwise ~0)`.

## Contract evidence

| Contract | Evidence |
| --- | --- |
| Version `0.18.4` | `pyproject.toml`, `README.md`/`README-RU.md`, `docs/guide*.md`, `docs/ROADMAP*.md`, `tests/test_evidence_benchmark.py` |
| Root resolution | `src/greedy_token/paths.py`; `tests/test_paths.py` |
| Remote timeout | `src/greedy_token/llm_invoke.py` (`timeout_s`); `tests/test_llm_invoke.py` |
| derive_prompt_args in plan_run | `src/greedy_token/executors.py` `_prompt_derived_args`; `tests/test_executors*.py` |
| hook_mode + dual saved | `src/greedy_token/{usage,budget,advisory}.py`; `tests/test_{usage,budget,advisory}.py` |
| RAG cap | `src/greedy_token/rag_search.py`; `tests/test_rag_search*.py` |
| Path scoping | `src/greedy_token/router.py` `_extract_search_targets`; `tests/test_router_gaps.py`, `tests/test_search.py` |
| Benchmark page | `docs/benchmark.html` (standalone, no deps) |

## Out of scope

- Host pre-router / multi-IDE adapter split — tracked separately
- `cursor_*` wire-format rename (needs ADR + usage.jsonl migration)
- IDE-reported token counts for the live table — subagent-measured content
  stands in until a 3-model IDE run replaces the calibrated overhead
- PyPI publish mechanics (below)

## Release gates (ethalon publish)

Publish workflow: `.github/_ethalon/publish.yml` (runnable copy
`.github/workflows/publish.yml`). Trigger is **GitHub Release published**, not
`twine` from a laptop.

1. Commit the working tree (version pins + ROADMAP rows + this checklist).
2. Local gate green: `./scripts/release-gate.sh 0.18.4`.
3. **No push until confirmed.** Then `git push origin main`.
4. Wait for `Test` workflow, job **`required matrix gate`**, on that commit.
5. Annotated tag `v0.18.4` on that commit only; `git push origin v0.18.4`.
6. `gh release create v0.18.4` → publish workflow verifies matrix → PyPI.

## Commit / tag / publish (run only after confirmation)

```bash
cd projects/greedy-token-home/greedy-token

git add \
  pyproject.toml README.md README-RU.md \
  docs/guide.md docs/guide-RU.md \
  docs/ROADMAP.md docs/ROADMAP-RU.md \
  tests/test_evidence_benchmark.py \
  CUT-v0.18.4.md

git commit -m "chore(release): cut v0.18.4"

# After explicit push OK:
git push origin main

# After origin Test / required matrix gate is green on this commit:
git tag -a v0.18.4 -m "Release v0.18.4: bench-driven fixes F1-F5"
git push origin v0.18.4
gh release create v0.18.4 --title "v0.18.4 — bench-driven fixes" --notes-file CUT-v0.18.4.md
```
