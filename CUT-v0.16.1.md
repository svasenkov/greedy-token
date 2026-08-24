# Cut checklist — greedy-token v0.16.1

**Status:** READY FOR RELEASE. Create the tag and GitHub release only after the
exact commit passes the mandatory `required matrix gate`.

Patch release after a Cursor MCP hang: FastMCP sync tools block the event loop,
and `search_code` treated a finished `rg` miss (`exit 1`) or timeout (`124`) as
failure, then walked the workspace with `Path.rglob`. That stalls the stdio
server past Cursor’s idle timeout and collapses the tool catalog.

## Summary

- **`rg` miss is final:** exit `0` (hits) and `1` (no matches) are authoritative.
  Python tree walk is not used after a completed `rg`.
- **`rg` errors stay errors:** timeout `124` and other `rg` failures (`2`, …)
  return error text. They do not fall through to a disk walk.
- **Python fallback is only for a missing `rg`:** binary absent, `126`, `127`,
  or `"command not found"`.
- **Tests lock the contract:** gap and branch-coverage cases that previously
  required Python after `(1, "")` now expect engine `rg` and “No matches”.
- **Hub session fixture:** `list_sessions(since="30d")` uses timestamps relative
  to now so a July 2026 fixture cannot fall out of the window.
- **Workspace overlay:** `python-gen-env` points at the tests-meta ethalon script
  so nested-clone `scripts lint` matches the script-tier catalog.

## Honest evidence

Fill after `./scripts/release-gate.sh 0.16.1` on this cut.

## Contract evidence

| Contract | Evidence |
| --- | --- |
| Version `0.16.1` | `pyproject.toml`, `tests/test_evidence_benchmark.py` |
| Completed `rg` is authoritative | `src/greedy_token/code_search.py` (`_rg_completed`, `_search_from_rg`) |
| No Python walk after miss/timeout | `tests/test_code_search_gaps.py`, `tests/test_branch_coverage.py` |
| Python fallback only if `rg` cannot run | `_rg_not_runnable` |

## Out of scope

- Retagging `v0.16.0`
- Hub accumulated-savings work
- Host pre-router

## Release gates

1. Run `./scripts/release-gate.sh 0.16.1` in a clean environment.
2. Push the cut commit to `main` (`git push origin release/0.16.1:main`).
3. Require the exact commit's `Test / required matrix gate` to succeed.
4. Annotated tag `v0.16.1` on that commit only.
5. `gh release create v0.16.1` → Publish to PyPI.

## Tag / publish

```bash
git tag -a v0.16.1 -m "Release v0.16.1: do not Python-walk after a finished rg miss"
git push origin v0.16.1
gh release create v0.16.1 --title "v0.16.1 — Code search MCP hang" --notes-file CUT-v0.16.1.md
```
