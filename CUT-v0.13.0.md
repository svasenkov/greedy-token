# Cut checklist — greedy-token v0.13.0

**Status:** READY in `main` — awaiting tag/PyPI (explicit user command).

Trust cut: prove routing usefulness with a published corpus, and close the
`shell=True` + workspace-YAML supply-chain boundary.

## Summary

- **Routing corpus**: `bench/routing_corpus.yaml` + `tests/test_routing_corpus.py`
  scorecard (`min_precision`, expected_target per case).
- **Shell harden**: `subprocess_safe.command_to_argv` peels `cd <root> &&`,
  runs `shell=False`, rejects bare operator / substitution tokens.
- **Quote search_paths** in `_build_tool_command` (workspace YAML cannot inject
  unquoted metacharacters into the rg command string).
- Call sites: `executors`, `pipeline`, `code_search._run_rg`, `cli` scripts.

## CONTRACT — evidence

| Claim | Evidence |
|-------|----------|
| no `shell=True` in executors | `src/greedy_token/executors.py` + `rg shell=True src/` empty (except docs in subprocess_safe) |
| argv path | `subprocess_safe.command_to_argv` / `run_command` |
| search_paths quoted | `test_build_tool_command_quotes_search_paths` |
| fail closed on `&&` / `$(…)` | `test_command_to_argv_fail_closed_on_operators` |
| corpus scorecard | `test_routing_corpus_precision` (≥ `min_precision`) |

## Gate (when releasing)

```bash
./scripts/release-gate.sh 0.13.0
```

Bump `pyproject.toml` version to `0.13.0` before the gate if not already.

## Tag / publish (explicit user command only)

```bash
git tag -a v0.13.0 -m "Release v0.13.0: routing corpus + shell=False harden"
# push / gh release / PyPI — only when asked
```

## Out of scope (stay on ROADMAP)

- v0.14+ Windows / FTS / host pre-router
- Expanding corpus to full precision/recall by zone (iterate in follow-ups)
