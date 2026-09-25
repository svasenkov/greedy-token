# Cut checklist — greedy-token v0.18.1

**Status:** ALIGNED + GATE GREEN. Version pins are `0.18.1`. Push / tag /
`gh release` require explicit user confirmation — see the commands block.

Patch release after v0.18.0: six cleanup commits — canonical candidate
ranking, per-host skills resolution, search exclusions, formatter dedup and
docs corrections. No schema break — usage events stay `v:2`.

## Summary

- **Canonical `rank_candidates` SSOT** (`f163db789`): the package
  `greedy_token.hub.crystallize` is the single ranking implementation;
  `scripts/_crystallize_lib.py` is a thin facade over it. Candidates dedup
  by canonical `crystal_id` + `operation_id`; tmp/pytest sandbox roots and
  fixture telemetry are filtered; `route_outcome` / `script_override`
  events no longer count as task hits — so `crystallize candidates` no
  longer shows duplicate or noise rows.
- **Per-host skills dir** (`06a0ef97a`): `HOST_SKILLS_DIR` resolves skill
  directories per agent host instead of a single hardcoded path.
- **Search exclusions** (`f9bedb73f`): shared `DEFAULT_GLOBS`; all
  host-internal directories are excluded from `rg` search scopes.
- **Shared result formatters** (`23b6287bc`): one formatter source for
  CLI and MCP crystallize output — no drift between surfaces.
- **Docs corrections:** stale LLM-review paragraphs dropped from README
  (`a941d4227`); packaged example rule shows real MCP signatures —
  `capabilities()` takes no args, `invoke` accepts `args`/`query`
  (`a2bf6610b`).

## Honest evidence

The local macOS/Python 3.12 release gate on 2026-09-25 passed
(`./scripts/release-gate.sh 0.18.1` via `projects/greedy-token-home/dev/.venv`):

- coverage run: 1478 passed; 100% branch coverage across 9122
  statements and 3166 branches;
- explicit `0.18.1` release-version gate (1 passed, 1477 deselected);
- Allure `minTestsCount` synced to 1478 (pytest collected);
- smoke: `greedy-token capabilities` lists the derived inventory.

## Contract evidence

| Contract | Evidence |
| --- | --- |
| Version `0.18.1` | `pyproject.toml`, `README.md` / `README-RU.md` footer, `tests/test_evidence_benchmark.py` |
| Canonical candidate ranking | `src/greedy_token/hub/crystallize.py`, `scripts/_crystallize_lib.py`, `tests/test_crystal_ids.py`, `tests/test_hub_gaps.py` |
| Per-host skills / search exclusions | `src/greedy_token/agent_host.py`, `DEFAULT_GLOBS`, `tests/test_agent_host.py` |
| Shared formatters | `src/greedy_token/mcp.py`, `src/greedy_token/cli.py` |
| PyPI only after green Test matrix | `.github/_ethalon/publish.yml` → `scripts/ci/verify_release_matrix.py` |

## Out of scope

- Host pre-router (`v0.18+` aspirational)
- Human trust approval for `python-java-matrix-plan` / `python-sonar-gate-wait`
- `cursor_*` wire-format rename (needs ADR + usage.jsonl migration)
- PyPI publish mechanics (below)

## Release gates (ethalon publish)

Publish workflow: `.github/_ethalon/publish.yml` (runnable copy
`.github/workflows/publish.yml`). Trigger is **GitHub Release published**, not
`twine` from a laptop.

1. Commit the working tree (version pins + ROADMAP rows + this checklist).
2. Local gate must be green; re-run `./scripts/release-gate.sh 0.18.1`
   if the tree changes after this checklist.
3. **No push until confirmed.** Then `git push origin main`.
4. Wait for `Test` workflow, job **`required matrix gate`**, on **that exact
   commit**.
5. Annotated tag `v0.18.1` on that commit only; `git push origin v0.18.1`.
6. `gh release create v0.18.1` → workflow **Publish to PyPI** checks out the
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
  CUT-v0.18.1.md

git commit -m "$(cat <<'EOF'
chore(release): cut v0.18.1

Canonical rank_candidates SSOT (candidate dedup + sandbox/fixture
filters), per-host skills dir, host-internal search exclusions, shared
CLI/MCP formatters, docs corrections.
EOF
)"

# After explicit push OK:
git push origin main

# After origin Test / required matrix gate is green on this commit:
git tag -a v0.18.1 -m "Release v0.18.1: canonical candidate ranking + host/search cleanup"
git push origin v0.18.1
gh release create v0.18.1 --title "v0.18.1 — canonical candidate ranking" --notes-file CUT-v0.18.1.md
```
