# Cut checklist — greedy-token v0.18.0

**Status:** ALIGNED + GATE GREEN. Version pins are `0.18.0`. Push / tag /
`gh release` require explicit user confirmation — see the commands block.

Minor release after v0.17.1: the five-step enforcement roadmap lands —
telemetry contract, guarded execution with a validated result, one evaluator
gate, derived capability surface, and an auditable crystallize lifecycle —
plus the adoption pass that turns those primitives into an enforced
route-first workflow. No schema break — usage events stay `v:2` (additive
fields only).

## Summary

- **Telemetry contract:** every request/outcome event carries
  `operation_id` (`parent_operation_id` links pipeline steps); phases
  distinguish `recommended` / `planned` / `executed`; savings are credited
  only after observed non-failure execution — recommendations earn nothing.
- **Guarded execution + result contract:** `execute_plan` returns a
  validated `ExecutionResult` (`produced` / `empty` / `invalid` /
  `not_evaluated` / `not_started`) instead of a bare exit code.
- **Evaluator gate (fail-closed):** `result_gate` is the single
  accept/continue/savings policy. Unknown statuses normalize to `invalid`;
  empty or unverified output earns no savings; pipeline `all_ok` and its
  exit code follow the gate, so an invalid contract fails the run.
- **Capabilities surface:** derived op inventory over merged routes +
  wrappers + trust manifest — `capabilities list/show/invoke` in CLI and
  `greedy_token_capabilities` / `greedy_token_invoke` in MCP (8 tools
  total). Invoke reuses the guarded path; non-readiness is reported with a
  reason, never executed.
- **Auditable crystallize lifecycle:** `candidate → proposed → approved →
  applied` with SHA-256-pinned approvals (`approved_sha256`), actor/reason
  audit fields, workspace-bound approvals, post-promote re-verification and
  reject/revoke cleanup. Promotion goes through the real trust manifest —
  no auto-apply.
- **Adoption/enforcement:** `GREEDY_HOOK_MODE` gate/intercept hook
  profiles (Devin `GREEDY_DEVIN_SOFT_GATE=1` passes context instead of
  blocking); crystallize candidates split into `covered` vs gap, with a
  tmp/pytest sandbox filter; route-first invoke rule — 9 read-only
  workspace scripts adopted into trust.
- **Argv / confinement hardening:** literal `rg` pattern after `--` in
  code_search and invoke; realpath confinement for every wrapper argv
  token (bare-word symlink escape refused); `params: [args]` routes with
  fixed-arg preservation and structured `invalid_params` refusals.
- **Hygiene:** `capabilities.py` split into derive/invoke/format;
  ruff clean on `src/` and `tests/`; F401 sweep; per-test
  `GREEDY_TOKEN_HOME` isolation stops trust-manifest leaks; coverage back
  to 100% branch.

## Honest evidence

The local macOS/Python 3.12 release gate on 2026-09-24 passed
(`./scripts/release-gate.sh 0.18.0` via `projects/greedy-token-home/dev/.venv`):

- coverage run: 1467 passed; 100% branch coverage across 9029
  statements and 3130 branches;
- explicit `0.18.0` release-version gate (1 passed, 1466 deselected);
- Allure `minTestsCount` synced to 1467 (pytest collected);
- smoke: `greedy-token capabilities` lists the derived inventory.

## Contract evidence

| Contract | Evidence |
| --- | --- |
| Version `0.18.0` | `pyproject.toml`, `README.md` / `README-RU.md` footer, `tests/test_evidence_benchmark.py` |
| Telemetry phases + operation ids | `src/greedy_token/usage.py`, `tests/test_usage*` |
| Result contract + evaluator gate | `src/greedy_token/executors.py`, `src/greedy_token/result_gate.py`, `tests/test_result_gate.py` |
| Capabilities list/show/invoke | `src/greedy_token/capabilities*.py`, `src/greedy_token/mcp.py`, `tests/test_capabilities.py` |
| Crystallize lifecycle + SHA pin | `src/greedy_token/crystallize_l3.py`, `src/greedy_token/hub/crystallize.py`, `tests/test_crystallize_lifecycle.py` |
| Hook modes + covered split | `87884c136`, `.cursor/hooks/greedy-token-route.*`, `.cursor/rules/greedy-token.mdc` |
| Argv confinement | `src/greedy_token/subprocess_safe.py`, `src/greedy_token/code_search.py`, `tests/test_security.py` |
| PyPI only after green Test matrix | `.github/_ethalon/publish.yml` → `scripts/ci/verify_release_matrix.py` |

## Out of scope

- Host pre-router (`v0.18+` aspirational)
- Human trust approval for `python-java-matrix-plan` / `python-sonar-gate-wait`
- PyPI publish mechanics (below)

## Release gates (ethalon publish)

Publish workflow: `.github/_ethalon/publish.yml` (runnable copy
`.github/workflows/publish.yml`). Trigger is **GitHub Release published**, not
`twine` from a laptop.

1. Commit the working tree (version pins + ROADMAP rows + this checklist).
2. Local gate must be green; re-run `./scripts/release-gate.sh 0.18.0`
   if the tree changes after this checklist.
3. **No push until confirmed.** Then `git push origin main`.
4. Wait for `Test` workflow, job **`required matrix gate`**, on **that exact
   commit**.
5. Annotated tag `v0.18.0` on that commit only; `git push origin v0.18.0`.
6. `gh release create v0.18.0` → workflow **Publish to PyPI** checks out the
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
  allure/quality-gate.mjs .github/workflows/test.yml .github/_ethalon/test.yml \
  CUT-v0.18.0.md

git commit -m "$(cat <<'EOF'
chore(release): cut v0.18.0

Enforcement roadmap: telemetry contract, guarded execution + result gate,
capabilities invoke surface, auditable crystallize lifecycle, hook modes
and route-first adoption; argv confinement and 100% coverage restored.
EOF
)"

# After explicit push OK:
git push origin main

# After origin Test / required matrix gate is green on this commit:
git tag -a v0.18.0 -m "Release v0.18.0: enforcement roadmap — gate, invoke, lifecycle"
git push origin v0.18.0
gh release create v0.18.0 --title "v0.18.0 — enforcement roadmap" --notes-file CUT-v0.18.0.md
```
