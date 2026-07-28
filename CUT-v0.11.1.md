# Cut checklist — greedy-token v0.11.1

**Status:** SHIPPED — 2026-07-28 · tag `v0.11.1` · PyPI `0.11.1`.

## Summary

Patch: ship promoted access-diag example routes after v0.11.0.

- **access-diag live**: profile routes `python-access-diag-jenkins` /
  `selenoid` / `testops` plus dispatcher `--all-standard` in
  `examples/routes/workspace-routes.yaml`.
- **retire**: `python-auth-storage-probe` patterns → testops access-diag;
  provider-balance re-shadowed (0 hits).

## CONTRACT — evidence

| Claim | Evidence |
|-------|----------|
| version 0.11.1 | `pyproject.toml` (SSOT via `src/greedy_token/version.py`) |
| routes | `examples/routes/workspace-routes.yaml` |
| docs | ROADMAP changelog; README license footer |

## Gate (scripts/release-gate.sh 0.11.1)

```bash
./scripts/release-gate.sh 0.11.1
```

## Tag / publish (explicit user command only)

```bash
git tag -a v0.11.1 -m "Release v0.11.1: access-diag live routes"
# push / gh release / PyPI — only when asked
```
