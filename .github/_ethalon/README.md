# GitHub Actions ethalon (greedy-token)

Same pattern as monorepo `generators/ethalon/tests-java/.github/_ethalon/`  
(skill `sync-github-workflows-ethalon`, RAG `ci-workflow-ethalon` in zero-design-system).

## Layout

| Path | Role |
|------|------|
| `_ethalon/gha-actions.yaml` | **SSOT** pinned action versions (checkout@v7, setup-python@v6, …) |
| `_ethalon/test.yml` | Ethalon: pytest + Allure 3 + TestOps |
| `_ethalon/publish.yml` | Ethalon: PyPI on release |
| `workflows/*.yml` | **Runnable** copies — must match `_ethalon/` same basename |
| `_new.yml` | Inbox: workflow steps from consumer not yet in ethalon |
| `_modified.yml` | Inbox: intentional deltas before merge to ethalon |

GHA **does not** run files under `_ethalon/`.

## Sync rule

After editing ethalon:

```bash
cp .github/_ethalon/test.yml .github/workflows/test.yml
cp .github/_ethalon/publish.yml .github/workflows/publish.yml
```

Bump `actions/*` versions in `_ethalon/gha-actions.yaml` first, then apply to all ethalon YAMLs.

## Quality gate count

`minTestsCount` lives in `allure/quality-gate.mjs`. When it changes, update the job summary line in `_ethalon/test.yml` (search `minTestsCount`).

## Monorepo alignment

| Action | greedy-token | tests-java ethalon |
|--------|--------------|-------------------|
| checkout | v7 | v7 |
| setup-python | v6 | — (Java uses setup-java) |
| setup-node | v6 | v6 |

Do not mix v4/v5 in one repo without updating `gha-actions.yaml` and both ethalon trees.
