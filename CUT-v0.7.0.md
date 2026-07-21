# Cut checklist — greedy-token v0.7.0

**Status:** PREP — bump + gate green, **not tagged/published**.
**Do not run tag/push/publish without an explicit user command.**

## Summary

Route-quality release: explainable routing, safe mode, init profiles, honest
cheap-tier override attribution, and hub operational metrics.

- **report / hub**: route quality block — `override_rate` / `cheap_hold_rate`,
  `by_crystal` worst-first, per-tier `cheap_hits_by_tier`; surfaced in CLI text/
  `--json` and hub `/api/summary` next to `coverage_pct`.
- **router**: `explain_route()` — `Why` / `Runner-up` / `Saved est` in
  `format_decision` (flows into MCP `route` and CLI `route`).
- **model_select**: `safe` policy alias for `cheap_only`.
- **cli**: `init --profile solo|team|ci` bootstrap over config/doctor (`--apply`/`--json`).
- **usage**: auto-override attribution now covers **every cheap tier**
  (`tool`/`python`/`ollama`/`rag`/`script`, `CHEAP_TIERS`), so `cheap_hold_rate`
  is honest across all cheap tiers (no fake 100% from unmeasured tiers).
- **hub**: operational metrics — latency p50/p95 + cost/task next to coverage.

## CONTRACT — evidence

| Claim | Evidence |
|-------|----------|
| version 0.7.0 | `pyproject.toml` (SSOT via `src/greedy_token/version.py`) |
| route quality SSOT | `quality_metrics()` in `src/greedy_token/usage.py` |
| cheap-tier attribution | `CHEAP_TIERS`, `find_prior_cheap_hit`, `maybe_emit_auto_script_override` |
| explain routing | `explain_route()` in `src/greedy_token/router.py` |
| safe policy | `src/greedy_token/model_select.py` |
| init profiles | `greedy-token init` in `src/greedy_token/cli.py` |
| hub metrics | `_operational_metrics()` in `src/greedy_token/hub/api.py` |

## Gate (scripts/release-gate.sh 0.7.0)

```text
562 passed in ~50s
1 @release passed (pyproject + __version__ == 0.7.0)
minTestsCount synced → 562
workflows match _ethalon
release gate OK: 0.7.0
```

Branch coverage on `src/greedy_token/` stays at 100% (`fail_under = 100`).

## Manual cut commands (confirm before run)

```bash
cd projects/greedy-token-home/greedy-token

# 1) Tag
git tag -a v0.7.0 -m "Release v0.7.0: route quality, explainable routing, safe mode, init, cheap-tier attribution"

# 2) Push main + tag
git push origin main
git push origin v0.7.0

# 3) GitHub Release (triggers PyPI via publish.yml)
gh release create v0.7.0 --title "v0.7.0" --notes-file - <<'EOF'
## Summary
- Route quality: override_rate / cheap_hold_rate, worst-crystal leaderboard (CLI + hub)
- Explainable routing: Why / Runner-up / Saved est
- Safe mode policy alias; `init --profile solo|team|ci`
- Honest cheap_hold_rate across all cheap tiers (tool/python/ollama/rag/script)
- Hub operational metrics: latency + cost/task
- minTestsCount 562

## Install
pip install greedy-token==0.7.0
EOF
```

## After PyPI

- Verify: `pip index versions greedy-token` / PyPI page
- Reload MCP server in Cursor so the new `route`/`report` output ships
- Monorepo hub README: set published PyPI pin to v0.7.0
