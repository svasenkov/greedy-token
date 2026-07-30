# Cut checklist — greedy-token v0.11.2

**Status:** READY in `main` — awaiting tag/PyPI (explicit user command).

Honest Trust cut A docs shipped earlier (`04362c41`). This cut closes the
**execution** half of the v0.11.x hotfix: pin `mcp` so CI/install keeps
`mcp.server.fastmcp`.

## Summary

- **mcp pin**: optional extra `mcp>=1.0,<2` (mcp 2.0 removed `fastmcp`).
- **CI path**: `pip install -e ".[dev,mcp]"` then `from mcp.server.fastmcp import FastMCP`.
- **docs (already on main)**: ★ $82/$820 = illustrative CLI/pipeline mix (not
  MCP-chat); `route_task` = single tier; RAG = lexical; confidence ≠ correctness.

## CONTRACT — evidence

| Claim | Evidence |
|-------|----------|
| pin | `pyproject.toml` optional `mcp = ["mcp>=1.0,<2"]` |
| import | `tests/test_trust_cut.py::test_mcp_fastmcp_importable` |
| clean venv | `python -m venv … && pip install -e ".[dev,mcp]"` → mcp 1.x + FastMCP |
| honest docs | README / WHY / guide / ROADMAP near-term (Trust cut A) |

## Gate (when releasing)

```bash
./scripts/release-gate.sh 0.11.2
```

Bump `pyproject.toml` version to `0.11.2` before the gate if not already.

## Tag / publish (explicit user command only)

```bash
git tag -a v0.11.2 -m "Release v0.11.2: pin mcp for fastmcp / CI green"
# push / gh release / PyPI — only when asked
```

## Related

v0.12 (edit-escalation + RU tokenize) landed in the same execution window —
see near-term table in `docs/ROADMAP.md` / `CUT-v0.12.0.md`.
