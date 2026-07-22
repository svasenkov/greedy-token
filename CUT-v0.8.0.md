# Cut checklist — greedy-token v0.8.0

**Status:** PREP — bump + gate green, **not tagged/published**.
**Do not run tag/push/publish without an explicit user command.**

## Summary

Crystallization L3 (safe mode) release: the telemetry → draft script → human
review → active route loop closes with **no silent auto-apply**, plus the
portable-routes / baseline-calibration / calibrated-confidence work from the
previous cycle.

- **crystallize L3 (safe mode)**: `crystallize draft ID` generates a
  reviewable Python draft in `.greedy-token/drafts/ID.py` — body from the
  cheap LLM (`cheap_llm` provider) when available, deterministic template
  skeleton (docstring pattern/hits, argparse, TODO body) otherwise; draft
  passes the existing `scripts lint`. A **shadow route** is registered in the
  workspace config (`.greedy-token.yaml`, never the bundled `routes.yaml`):
  `shadow_until` +7d, `enabled: false` — log-only, never affects `route_task`.
  `crystallize promote ID` (after human review) flips shadow → active;
  `crystallize reject ID` deletes the draft + route. Lifecycle events
  `draft → shadow → promoted / rejected` land in
  `~/.greedy-token/crystallize-lifecycle.jsonl`; hub crystals show the stages.
- **portable routes**: `init --routes-from FILE` merges a shared routes YAML
  into `<root>/.greedy-token.yaml`; `init --routes-scaffold` generates a
  `tool-rg-search` route with `search_paths` detected from the project tree.
- **calibrate**: `greedy-token calibrate [--overhead N | --from-file DUMP]`
  writes `baseline:` to `~/.greedy-token/config.yaml`; every footer marks the
  savings baseline source (`measured` / `calibrated` / `default-estimate`).
- **calibrated confidence**: route confidence is calibrated against telemetry
  score buckets (`≥ 20` events → `calibrated (n=…)`, else
  `formula (uncalibrated)`); `report` gains a calibration block
  (bucket → predicted vs actual vs n); provenance in `route` / `estimate` /
  `explain_route()` (CLI + MCP).

## CONTRACT — evidence

| Claim | Evidence |
|-------|----------|
| version 0.8.0 | `pyproject.toml` (SSOT via `src/greedy_token/version.py`) |
| L3 draft / promote / reject | `src/greedy_token/crystallize_l3.py` + `crystallize` subcommands in `src/greedy_token/cli.py` |
| shadow route is log-only | `_route_status()` / `_best_shadow_match()` in `src/greedy_token/router.py`; test `test_shadow_route_does_not_affect_route_task` |
| lifecycle stages | `append_lifecycle_event()` in `src/greedy_token/hub/crystallize.py`; hub `STAGES` in `hub/static/app.js` |
| draft passes scripts lint | `lint_routes()` call in `draft_crystal()`; test `test_draft_passes_scripts_lint` |
| workspace-config routes (not bundled) | `upsert_workspace_routes` / `remove_workspace_route` in `src/greedy_token/paths.py` |
| portable routes | `cmd_init_routes()` in `src/greedy_token/cli.py` |
| baseline calibration | `greedy-token calibrate` in `src/greedy_token/cli.py`, `src/greedy_token/baseline.py` |
| calibrated confidence | `src/greedy_token/calibration.py`, `confidence_for_score()` wired in `router.py` |

## Gate (scripts/release-gate.sh 0.8.0)

```text
886 passed in ~90s
1 @release passed (pyproject + __version__ == 0.8.0)
minTestsCount synced → 886
workflows match _ethalon
release gate OK: 0.8.0
```

Line + branch coverage on `src/greedy_token/` stays at 100% (`fail_under = 100`).
Doc-drift guard (`tests/test_doc_sync.py`) green with the new `crystallize
draft/promote/reject` CLI rows in both READMEs.

## Manual cut commands (confirm before run)

```bash
cd projects/greedy-token-home/greedy-token

# 1) Tag
git tag -a v0.8.0 -m "Release v0.8.0: crystallization L3 safe mode, portable routes, baseline calibrate, calibrated confidence"

# 2) Push main + tag
git push origin main
git push origin v0.8.0

# 3) GitHub Release (triggers PyPI via publish.yml)
gh release create v0.8.0 --title "v0.8.0" --notes-file - <<'EOF'
## Summary
- Crystallization L3 (safe mode): `crystallize draft` → reviewable script + shadow route (log-only, +7d) → `promote` / `reject`; lifecycle draft → shadow → promoted / rejected in hub
- Draft body via cheap LLM, deterministic template fallback; passes `scripts lint`
- Portable routes: `init --routes-from FILE` / `--routes-scaffold`
- `greedy-token calibrate`: baseline source (`measured` / `calibrated` / `default-estimate`) in every footer
- Calibrated route confidence: telemetry score buckets, `report` calibration block
- minTestsCount 886

## Install
pip install greedy-token==0.8.0
EOF
```

## After PyPI

- Verify: `pip index versions greedy-token` / PyPI page
- Reload MCP server in Cursor so the new `route`/`report` output ships
- Monorepo hub README: set published PyPI pin to v0.8.0
