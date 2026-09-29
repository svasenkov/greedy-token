# Cut checklist — greedy-token v0.18.2

**Status:** RELEASED 2026-09-30. Version pins are `0.18.2`; tag
`v0.18.2` lives on `4a94c546c` (matrix gate green), GitHub Release +
PyPI publish done.

Patch release after v0.18.1: mutation-testing campaign on the hot modules —
kill tests plus a proven-equivalent registry, 100% line+branch coverage, one
flaky timing assert removed. No schema break — usage events stay `v:2`.

## Summary

- **Focused-review fixes:** malformed provider responses (Ollama /
  OpenAI-compatible / Yandex) now raise `MalformedResponseError`;
  `.ignore` fallback moved to `pathspec` GitIgnoreSpec (rg parity:
  anchors, `**/`, `[!x]`, ancestor files, literal unclosed `[`);
  symlink-safe traversal confined to root; `path:line:content` parsing
  tolerates `:` in filenames; hub summary dedupes byte-identical events;
  spend ledger starts a new line after a torn tail; benchmark cache key
  includes `YANDEX_FOLDER_ID`; `-uall` guards nested untracked `tests/`
  dirs; `rag_fts` over-fetches so stale rows can't starve `LIMIT`.
- **Router/crystallize refactor:** tier constants and the ISO timestamp
  parser are SSOT in `usage.py`; `first_matching_route_id` gives a
  match-only fast path; `rank_candidates` splits into passes with an
  early exit (snapshot-identical output verified).
- **Coverage closed:** every previously uncovered line/branch in
  `spend_ledger`, `llm_invoke`, `usage`, `budget`, `budget_ledger`,
  `budget_policy`, `advisory` is exercised — 100.00% with zero pragmas.
- **Mutation campaign:** `mutmut` scoped per module
  (`only_mutate` + `-n0`, since xdist controllers never fire the worker
  hooks mutmut uses for test attribution). ~160 new kill tests: golden
  contracts for event builders / reports / toasts, env-helper and
  rotation boundaries, watch-loop truncation replay, open()/decode() arg
  spies, frozen `datetime.now`/`perf_counter` for TTL and duration paths.
- **Proven-equivalent registry:** every remaining survivor is documented
  — a `# equivalent:` source marker plus a written proof in
  `docs/mutation-equivalents.yaml`, kept in sync by the drift guard
  `tests/test_mutation_equivalents.py`. Survivor counts after the
  campaign: spend_ledger 20, llm_invoke 29, usage 34, budget 15,
  advisory 17 — all documented; budget_ledger/budget_policy 0.
- **Flake fix:** dropped `latency_ms != cached_value` in
  `test_resource_probe_gaps` — wall-clock latency can land on any value
  under xdist; `eval_tokens` already distinguishes the miss.

## Honest evidence

The local macOS/Python 3.14 release gate on 2026-09-30 passed
(`./scripts/release-gate.sh 0.18.2` via `projects/greedy-token-home/greedy-token/.venv`):

- coverage run: 2032 collected (plus 1 skipped in the plain suite run);
  100% branch coverage across 9671 statements and 3386 branches;
- explicit `0.18.2` release-version gate (1 passed);
- workflows match `_ethalon`; Allure `minTestsCount` synced to 2032
  (pytest collected);
- `uv build` produced `dist/greedy_token-0.18.2.tar.gz` +
  `greedy_token-0.18.2-py3-none-any.whl`; clean-venv smoke install
  reports `__version__ == "0.18.2"`.

CI/released: Test workflow `required matrix gate` green on `4a94c546c`
(three fix iterations: Windows/POSIX test portability, pathspec in the
minimum dep profile, minTestsCount resync). Tag `v0.18.2` pushed on that
commit, GitHub Release published, `greedy-token==0.18.2` on PyPI.

## Contract evidence

| Contract | Evidence |
| --- | --- |
| Version `0.18.2` | `pyproject.toml`, `README.md` / `README-RU.md` footer, `docs/guide*.md`, `docs/ROADMAP*.md`, `tests/test_evidence_benchmark.py` |
| Mutation equivalents registry | `docs/mutation-equivalents.yaml`, `# equivalent:` markers in `src/greedy_token/{spend_ledger,llm_invoke,usage,budget,advisory}.py`, `tests/test_mutation_equivalents.py` |
| mutmut config (scoping, `-n0`, copies) | `pyproject.toml` `[tool.mutmut]` |
| PyPI only after green Test matrix | `.github/_ethalon/publish.yml` → `scripts/ci/verify_release_matrix.py` |

## Out of scope

- Host pre-router (`v0.18+` aspirational)
- Human trust approval for `python-java-matrix-plan` / `python-sonar-gate-wait`
- `cursor_*` wire-format rename (needs ADR + usage.jsonl migration)
- Mutation campaigns on the remaining `only_mutate` modules
  (router/pipeline/executors/subprocess_safe/trust/spend_guard/
  code_search/tool_paths/rag_*) — tracked separately
- PyPI publish mechanics (below)

## Release gates (ethalon publish)

Publish workflow: `.github/_ethalon/publish.yml` (runnable copy
`.github/workflows/publish.yml`). Trigger is **GitHub Release published**, not
`twine` from a laptop.

1. Commit the working tree (version pins + ROADMAP rows + this checklist).
2. Local gate must be green; re-run `./scripts/release-gate.sh 0.18.2`
   if the tree changes after this checklist.
3. **No push until confirmed.** Then `git push origin main`.
4. Wait for `Test` workflow, job **`required matrix gate`**, on **that exact
   commit**.
5. Annotated tag `v0.18.2` on that commit only; `git push origin v0.18.2`.
6. `gh release create v0.18.2` → workflow **Publish to PyPI** checks out the
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
  allure/quality-gate.mjs .github/workflows/test.yml .github/_ethalon/test.yml \
  CUT-v0.18.2.md

git commit -m "$(cat <<'EOF'
chore(release): cut v0.18.2

Mutation-hardening campaign: ~160 kill tests, proven-equivalent
registry (markers + drift guard), 100% line+branch coverage, flaky
latency assert dropped.
EOF
)"

# After explicit push OK:
git push origin main

# After origin Test / required matrix gate is green on this commit:
git tag -a v0.18.2 -m "Release v0.18.2: mutation hardening + equivalent registry"
git push origin v0.18.2
gh release create v0.18.2 --title "v0.18.2 — mutation hardening" --notes-file CUT-v0.18.2.md
```
