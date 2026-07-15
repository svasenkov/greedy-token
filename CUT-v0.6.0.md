# Cut checklist — greedy-token v0.6.0

**Status:** RELEASE-READY (phase E)  
**Nested HEAD:** commit after `release-gate.sh 0.6.0` (396 passed)  
**Do not run tag/push/publish without an explicit user command.**

## CONTRACT freeze (phase A) — evidence OK

| Claim | Evidence |
|-------|----------|
| version 0.6.0 | `pyproject.toml` |
| MCP tools search/rag/pipeline/route/usage | `src/greedy_token/mcp.py` |
| CLI `override`, `scripts lint`, `hub serve` | `src/greedy_token/cli.py` |
| `script_override` | `src/greedy_token/usage.py` |
| `shadow_route_id` | `src/greedy_token/router.py` |

## Gate (phase D)

```text
396 passed in ~74s
1 @release passed
minTestsCount synced → 396
release gate OK: 0.6.0
```

Crystallize-related tests covered in suite: `test_usage`, `test_cli`, `test_router`, `test_hub` (+ related).

## Manual cut commands (phase F — confirm before run)

```bash
cd projects/greedy-token-home/greedy-token

# 1) Tag
git tag -a v0.6.0 -m "Release v0.6.0: crystallize L2, hub, budget policy"

# 2) Push main + tag
git push origin main
git push origin v0.6.0

# 3) GitHub Release (triggers PyPI via publish.yml)
gh release create v0.6.0 --title "v0.6.0" --notes-file - <<'EOF'
## Summary
- Crystallize L2: script_override telemetry, CLI override, scripts lint, shadow routes
- Local hub (hub serve) + crystallize dashboard API
- Budget policy / llm invoke / spend guards
- minTestsCount 396

## Install
pip install greedy-token==0.6.0
EOF
```

## After PyPI

- Verify: `pip index versions greedy-token` / PyPI page
- Monorepo hub README: set published PyPI pin to v0.6.0 (draft already updated locally)
