# Cut checklist — greedy-token v0.18.3

**Status:** IN PROGRESS. Version pins are `0.18.3`; tag `v0.18.3` pending
matrix gate.

Patch release after v0.18.2: trust-boundary fix in script execution,
intercept rendering polish, and hot-path caching that cuts ~0.4s of audit
overhead off every route decision. No schema break — usage events stay `v:2`.

## Summary

- **Trust boundary:** bare `python`/`python3` (incl. `python3.12`,
  `python.exe`) in trusted script argv now pins to `sys.executable` —
  previously a bare interpreter floated through PATH to whatever shadow
  install came first, handing trusted scripts to an unvetted environment.
  On this machine `~/.local/bin/python` (3.14, no project deps) broke every
  route op whose script imports `greedy_token` (`usage-stats`,
  `crystallize-report`, `rag-eval`, `greedy-context-smoke`, …) with
  `ModuleNotFoundError`. The pin stays **unresolved** — resolving the venv
  launcher to its base binary would drop `pyvenv.cfg` discovery and the venv
  site-packages. `resolve_wrapper_invocation` pins the same way.
- **Intercept rendering:** nested dict values no longer arrive as truncated
  `json.dumps` blobs — flat dicts render `k=v` pairs, nested dicts render
  indented bullet lines; table cells keep the compact JSON form.
- **Hot-path caching** (`route_task` 0.574 → 0.152 s cProfile, ~3.8×):
  - `aggregate_budget` snapshots memoize+disk-cache by the `(mtime_ns, size)`
    signature of every ledger file read plus shaping settings; each snapshot
    carries `expires_at` covering time drift (reservation TTL, rolling
    windows). Kill-switch `GREEDY_BUDGET_CACHE=0`.
  - GPU probe (`system_profiler`/`nvidia-smi` subprocess) disk-caches by
    `node|machine|system` identity under `PROBE_CACHE_TTL_S`; RAM stays live.
    Kill-switch `GREEDY_HW_CACHE=0`.
  - `load_yaml` memoizes per path signature with TTL/bounds, `deepcopy`
    isolation, and a re-stat before write (no TOCTOU). Kill-switch
    `GREEDY_YAML_CACHE=0`.
  - `count_tokens` defers past the tool/python early exit — tiktoken never
    initializes for deterministic tiers.
- **Route coverage:** +187 RU/EN everyday patterns across 15 invocable ops in
  the workspace overlay (0 collisions, 225/225 corpus match); two dormant
  ops retired (`python-java-matrix-plan`, `python-sonar-gate-wait`).
- **Settings:** `hook:` YAML section — durable workspace opt-in for
  mode/threshold (`advisory` default unchanged; env overrides stay
  supported).

## Honest evidence

- Suite: **2129 passed, 1 skipped** on macOS/Python 3.12 (dev venv);
  100% line+branch coverage reported by the campaign gate.
- cProfile `route_task("почему комп тормозит")`: first call 0.574 → 0.152 s,
  warm repeat 0.083 → 0.0165 s; route decision, confidence, and savings
  fields unchanged.
- Live smoke after the pin fix: `greedy_token_invoke python-usage-stats`
  returns real stats (exit 0) — previously `ModuleNotFoundError: pathspec`;
  `crystallize-report`, `greedy-context-smoke`, `ollama-health`,
  `meta-sync-check`, `git-recent`, `rag-eval` all `exit=0`.

## Contract evidence

| Contract | Evidence |
| --- | --- |
| Version `0.18.3` | `pyproject.toml`, `README.md` / `README-RU.md` footer, `docs/guide*.md`, `docs/ROADMAP*.md`, `tests/test_evidence_benchmark.py` |
| Interpreter pin | `src/greedy_token/subprocess_safe.py` (`trusted_script_argv`), `src/greedy_token/wrappers.py`; regression: `tests/test_cross_platform_execution.py`, `tests/test_security.py`, `tests/test_pipeline_gaps.py` |
| Cache kill-switches | `GREEDY_BUDGET_CACHE`, `GREEDY_HW_CACHE`, `GREEDY_YAML_CACHE` |
| PyPI only after green Test matrix | `.github/_ethalon/publish.yml` → `scripts/ci/verify_release_matrix.py` |

## Out of scope

- Host pre-router / multi-IDE adapter split — tracked separately
- `cursor_*` wire-format rename (needs ADR + usage.jsonl migration)
- Mutation campaigns on the remaining `only_mutate` modules —
  tracked separately
- SQLite `ResourceWarning` tail — non-blocking
- PyPI publish mechanics (below)

## Release gates (ethalon publish)

Publish workflow: `.github/_ethalon/publish.yml` (runnable copy
`.github/workflows/publish.yml`). Trigger is **GitHub Release published**, not
`twine` from a laptop.

1. Commit the working tree (version pins + ROADMAP rows + this checklist).
2. Local gate must be green; re-run `./scripts/release-gate.sh 0.18.3`
   if the tree changes after this checklist.
3. **No push until confirmed.** Then `git push origin main`.
4. Wait for `Test` workflow, job **`required matrix gate`**, on **that exact
   commit**.
5. Annotated tag `v0.18.3` on that commit only; `git push origin v0.18.3`.
6. `gh release create v0.18.3` → workflow **Publish to PyPI** checks out the
   tag, runs `verify_release_matrix.py <tag>` (must see successful Test run +
   `required matrix gate` for the tag SHA), then `python -m build` +
   `pypa/gh-action-pypi-publish` (OIDC).

## Commit / tag / publish (run only after confirmation)

```bash
cd projects/greedy-token-home/greedy-token

git add \
  pyproject.toml README.md README-RU.md \
  docs/guide.md docs/guide-RU.md \
  docs/ROADMAP.md docs/ROADMAP-RU.md \
  tests/test_evidence_benchmark.py \
  CUT-v0.18.3.md

git commit -m "$(cat <<'EOF'
chore(release): cut v0.18.3

Interpreter pinning for trusted script argv (trust boundary),
nested-dict intercept rendering, hot-path caches (budget snapshot,
GPU probe, YAML memo), +187 workspace route patterns.
EOF
)"

# After explicit push OK:
git push origin main

# After origin Test / required matrix gate is green on this commit:
git tag -a v0.18.3 -m "Release v0.18.3: trust-boundary pin + hot-path caches"
git push origin v0.18.3
gh release create v0.18.3 --title "v0.18.3 — trust pin + perf" --notes-file CUT-v0.18.3.md
```
