# Cut checklist — greedy-token v0.13.0

**Status:** SHIPPED — 2026-07-30 · tag `v0.13.0` · PyPI `0.13.0`.

Trust cut fold: honest docs (A) + mcp pin (0.11.2) + edit-escalation/RU
tokenize (0.12) + routing corpus + `shell=False` harden (0.13).

## Summary

- **Honest docs**: ★ $82/$820 = CLI/pipeline illustrative, not MCP-chat;
  lexical RAG; no auto-chain from `route_task`.
- **mcp pin**: optional extra `mcp>=1.0,<2` (keeps `mcp.server.fastmcp`).
- **Edit-escalation + RU tokenize**: false-cheap edit → cursor; Unicode `_tokenize`.
- **Routing corpus**: `bench/routing_corpus.yaml` + precision scorecard in CI.
- **Shell harden**: `subprocess_safe.command_to_argv` → `shell=False`; quoted
  `search_paths`.

## CONTRACT — evidence

| Claim | Evidence |
|-------|----------|
| version 0.13.0 | `pyproject.toml` (SSOT via `src/greedy_token/version.py`) |
| mcp pin | `pyproject.toml` optional `mcp = ["mcp>=1.0,<2"]` |
| edit escalate | `router` + `tests/test_trust_cut.py` |
| corpus | `bench/routing_corpus.yaml` + `tests/test_routing_corpus.py` |
| shell=False | `src/greedy_token/subprocess_safe.py` |

## Gate

```bash
./scripts/release-gate.sh 0.13.0
```

## Tag / publish

```bash
git tag -a v0.13.0 -m "Release v0.13.0: Trust cut (docs, mcp pin, edit escalate, corpus, shell harden)"
git push origin main v0.13.0
gh release create v0.13.0 --title "v0.13.0 — Trust cut" --notes-file …
# PyPI via .github/workflows/publish.yml on release published
```
