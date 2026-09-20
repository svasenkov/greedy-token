# Cut checklist — greedy-token v0.17.1

**Status:** ALIGNED + GATE GREEN. Version pins are `0.17.1`. Release was
confirmed by the user — push / tag / `gh release` approved for this cut.

Patch after v0.17.0: usage telemetry fields, router question-form fixes,
`rg` resolution for minimal IDE PATH, and two crystallized git routes.
No schema break — usage events stay `v:2` (additive fields only).

## Summary

- **`rg` resolution:** `tool_paths` resolves `~/.greedy-token/bin/rg` after
  system paths and before IDE bundles, and globs Devin's
  `ripgrep-universal` arch subdir under `/Applications` and
  `~/Applications`. Tool-tier search works under Devin's minimal MCP PATH.
- **Router question-forms:** `SEARCH_PREFIXES` gains `does` / `where are`;
  Titlecase tokens get a +6 score boost (`Jenkins` wins the alphabetical
  tie-break); yes/no scaffolds extract the last non-filler token as the
  object (`does X export to prometheus` → prometheus).
- **New routes (workspace overlay):** `python-git-recent`,
  `python-git-repos`, `python-usage-stats`, `python-crystal-status`,
  ollama-health alive/status patterns — all read-only script tier, 0 LLM.
- **Usage events:** `session_id` resolved from `GREEDY_TOKEN_SESSION` →
  IDE session file (`GREEDY_TOKEN_SESSION_FILE`, default
  `~/.greedy-token/session`) → omitted; explicit keys win (closes #20).
- **Calibration telemetry:** route events log `calibration_n`, score
  bucket, and matched patterns (≤10 entries / 256 chars);
  `confidence_source` declared on every non-formula decision (`fixed` /
  `none`); floored edit-escalation reports `fixed` (closes #21).
- **CI:** `minTestsCount` synced to the post-question-form collection.

## Honest evidence

The local macOS/Python 3.12 release gate on 2026-09-21 passed
(`./scripts/release-gate.sh 0.17.1` via `projects/greedy-token-home/dev/.venv`):

- coverage run: 1254 passed, 3 skipped; 100% branch coverage across 7753
  statements and 2656 branches;
- explicit `0.17.1` release-version gate (1 passed, 1256 deselected);
- Allure `minTestsCount` synced to 1257 (pytest collected);
- smoke: `greedy-token pipeline --list` lists named recipes.

Two pre-existing coverage gaps from the unreleased fixes were closed in this
cut (CI on `7279055d1` failed the same gate): `session_file()` default path
(`usage.py`) and `confidence_label` fixed/none labels (`router.py`).

## Contract evidence

| Contract | Evidence |
| --- | --- |
| Version `0.17.1` | `pyproject.toml`, `README.md` / `README-RU.md` footer, `tests/test_evidence_benchmark.py` |
| Own/Devin `rg` resolution | `src/greedy_token/tool_paths.py`, `tests/test_tool_paths.py` |
| Question-form routing | `src/greedy_token/router.py`, `tests/test_router_gaps.py`, `bench/routing_corpus.yaml` (w02 cases) |
| Git crystal routes | `examples/routes/workspace-routes.yaml`, `src/greedy_token/config/routes.yaml` |
| `session_id` on events | `src/greedy_token/usage.py`, `tests/test_usage.py` |
| Calibration fields | `src/greedy_token/calibration.py`, `src/greedy_token/usage.py`, `tests/test_calibration.py` |
| PyPI only after green Test matrix | `.github/_ethalon/publish.yml` → `scripts/ci/verify_release_matrix.py` |

## Out of scope

- Host pre-router (`v0.18+`)
- Workspace wiring (`.cursor/mcp.json`, hooks, `greedy-token.mdc`)

## Release gates (ethalon publish)

Publish workflow: `.github/_ethalon/publish.yml` (runnable copy
`.github/workflows/publish.yml`). Trigger is **GitHub Release published**, not
`twine` from a laptop.

1. Commit the working tree (version pins + ROADMAP rows + this checklist).
2. Local gate must be green; re-run `./scripts/release-gate.sh 0.17.1`
   if the tree changes after this checklist.
3. **No push until confirmed.** Then `git push origin main`.
4. Wait for `Test` workflow, job **`required matrix gate`**, on **that exact
   commit**.
5. Annotated tag `v0.17.1` on that commit only; `git push origin v0.17.1`.
6. `gh release create v0.17.1` → workflow **Publish to PyPI** checks out the
   tag, runs `verify_release_matrix.py <tag>` (must see successful Test run +
   `required matrix gate` for the tag SHA), then `python -m build` +
   `pypa/gh-action-pypi-publish` (OIDC).

## Commit / tag / publish (run only after confirmation)

```bash
cd projects/greedy-token-home/greedy-token

git add \
  pyproject.toml README.md README-RU.md \
  docs/ROADMAP.md docs/ROADMAP-RU.md \
  tests/test_evidence_benchmark.py \
  CUT-v0.17.1.md

git commit -m "$(cat <<'EOF'
chore(release): cut v0.17.1

Usage telemetry session_id + calibration fields, router question-form
fixes, rg resolution for minimal IDE PATH, git-recent/git-repos routes.
EOF
)"

# After explicit push OK:
git push origin main

# After origin Test / required matrix gate is green on this commit:
git tag -a v0.17.1 -m "Release v0.17.1: usage telemetry and router fixes"
git push origin v0.17.1
gh release create v0.17.1 --title "v0.17.1 — usage telemetry and router fixes" --notes-file CUT-v0.17.1.md
```
