# Cut checklist — greedy-token v0.17.0

**Status:** ALIGNED + GATE GREEN — NOT TAGGED. Version pins are `0.17.0`. Do
not `git push`, `git tag`, `twine`, or `gh release` until the commands below
are confirmed.

Minor after v0.16.2: crystal public name is the kebab **stem**; `python-` is the
script-tier **executor prefix**, not a language. Hub / L3 / overlay / `scripts
--run` telemetry share `python-{stem}`. Follow-up: no doubled `python-python-*`;
pipeline combined Saved is 0 when every executed step failed.

## Summary

- **`python-{stem}` lock:** `crystal_ids.py` is the naming canon. Promote /
  draft ids must be `python-{stem}` (2–4 kebab tokens). L3 `draft_crystal`
  rejects `script-{slugify(prompt)}` and doubled executor prefixes.
- **Telemetry:** `scripts --run` records `route_id` via `wrapper_route_id`
  (`python-{stem}` from the wrapper path), not `script-{wrapper_id}`.
- **Hub:** workspace crystals show the stem; ranking uses `crystal_id_for_pattern`.
  Invalid generated ids are skipped. Patterns that already are a valid
  `python-{stem}` stay as-is; leading `python`/`script` tokens are stripped so
  ids cannot become `python-python-*`.
- **Empty script path:** `stem_from_script_path` returns `None` on blank /
  whitespace (no `IndexError` on `.split()[0]`).
- **Pipeline footer:** combined Saved is `0` when every executed step failed
  (same honesty as per-step failed; dry-run still claims no savings).
- **Out of this cut:** hub README PyPI pin in `projects/greedy-token-home/`
  (optional later chat); host pre-router (`v0.18+`).

## Honest evidence

The local macOS/Python 3.12 release gate on 2026-09-18 passed
(`./scripts/release-gate.sh 0.17.0` via `projects/greedy-token-home/dev/.venv`):

- coverage run: 1231 passed, 3 skipped; 100% branch coverage across 7693
  statements and 2630 branches;
- explicit `0.17.0` release-version gate (1 passed, 1233 deselected);
- Allure `minTestsCount` synced to 1234 (pytest collected);
- smoke: `greedy-token pipeline --list` lists named recipes.

## Contract evidence

| Contract | Evidence |
| --- | --- |
| Version `0.17.0` | `pyproject.toml`, `README.md` / `README-RU.md` footer, `tests/test_evidence_benchmark.py` |
| Naming canon `python-{stem}` | `src/greedy_token/crystal_ids.py`, `tests/test_crystal_ids.py` |
| L3 reject bad promote ids | `src/greedy_token/crystallize_l3.py`, `tests/test_crystallize_l3.py` |
| Wrapper telemetry route_id | `src/greedy_token/wrappers.py` `wrapper_route_id`, `src/greedy_token/cli.py`, `src/greedy_token/usage.py` |
| No doubled executor prefix | `choose_stem` / `crystal_id_for_pattern`, `tests/test_hub.py` |
| Failed execute Saved=0 | `src/greedy_token/pipeline.py` `format_pipeline_footer`, `tests/test_pipeline.py` |
| PyPI only after green Test matrix | `.github/_ethalon/publish.yml` → `scripts/ci/verify_release_matrix.py` |

## Out of scope

- Push / annotated tag / GitHub release / PyPI without an explicit command
- Hub `projects/greedy-token-home/README.md` PyPI version line
- Host pre-router
- Workspace wiring (`.cursor/mcp.json`, hooks, `greedy-token.mdc`)

## Release gates (ethalon publish)

Publish workflow: `.github/_ethalon/publish.yml` (runnable copy
`.github/workflows/publish.yml`). Trigger is **GitHub Release published**, not
`twine` from a laptop.

1. Commit the working tree below (feature `574cad5da` is already on nested
   `main`, 1 commit ahead of `origin/main`). Do not tag `574cad5da` alone.
2. Local gate already green; re-run `./scripts/release-gate.sh 0.17.0` only
   if the tree changes after this checklist.
3. **No push until confirmed.** Then `git push origin main`.
4. Wait for `Test` workflow, job **`required matrix gate`**, on **that exact
   commit**.
5. Annotated tag `v0.17.0` on that commit only; `git push origin v0.17.0`.
6. `gh release create v0.17.0` → workflow **Publish to PyPI** checks out the
   tag, runs `verify_release_matrix.py <tag>` (must see successful Test run +
   `required matrix gate` for the tag SHA), then `python -m build` +
   `pypa/gh-action-pypi-publish` (OIDC).

## Commit / tag / publish (run only after confirmation)

```bash
cd projects/greedy-token-home/greedy-token

git add \
  src/greedy_token/crystal_ids.py \
  src/greedy_token/hub/crystallize.py \
  src/greedy_token/pipeline.py \
  tests/test_crystal_ids.py \
  tests/test_hub.py \
  tests/test_pipeline.py \
  tests/test_crystallize_l3.py \
  tests/test_wrappers.py \
  tests/test_evidence_benchmark.py \
  tests/pyramid_layers.py \
  pyproject.toml README.md README-RU.md \
  docs/ROADMAP.md docs/ROADMAP-RU.md \
  CUT-v0.17.0.md \
  allure/quality-gate.mjs \
  .github/_ethalon/test.yml \
  .github/workflows/test.yml

git commit -m "$(cat <<'EOF'
chore(release): cut v0.17.0

Lock python-{stem} naming, strip doubled executor prefixes, and do not claim
pipeline Saved when every executed step failed.
EOF
)"

# After explicit push OK:
git push origin main

# After origin Test / required matrix gate is green on this commit:
git tag -a v0.17.0 -m "Release v0.17.0: lock python-{stem} crystal ids"
git push origin v0.17.0
gh release create v0.17.0 --title "v0.17.0 — python-{stem} crystal ids" --notes-file CUT-v0.17.0.md
```
