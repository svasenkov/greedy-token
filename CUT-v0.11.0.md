# Cut checklist — greedy-token v0.11.0

**Status:** SHIPPED — 2026-07-28 · tag `v0.11.0` · PyPI `0.11.0`.

## Summary

Time-economy alongside token-economy: estimate wall-clock saved vs a naive
agent turn, surface it in MCP footers, `report`, and hub Overview.

- **time baseline**: `naive_agent_ms = overhead_ms + baseline_tokens × ms_per_1k_tokens / 1000`
  (defaults `12000` + `800`; same `baseline:` section / provenance labels as
  token overhead: `default-estimate` | `calibrated` | `measured`).
- **telemetry**: events gain `cursor_baseline_ms` + `time_saved_ms` (when
  `duration_ms` is known); aggregates feed `report` and hub metrics.
- **surfaces**: compact footer `saved **~N** · ~Xs`; full/markdown time
  columns; hub card **Time saved**; `estimate` shows naive wall-clock.
- **compat**: `write_baseline_config` merges and preserves time knobs when
  recalibrating token overhead.

## CONTRACT — evidence

| Claim | Evidence |
|-------|----------|
| version 0.11.0 | `pyproject.toml` (SSOT via `src/greedy_token/version.py`) |
| time baseline math | `baseline.naive_agent_ms` / `time_saved_ms`; `tests/test_time_baseline.py` |
| footer + report + hub | `budget.py`, `usage.format_report`, `hub/api._operational_metrics` |
| docs | `docs/guide.md` / `guide-RU.md` Time savings section; ROADMAP changelog |

## Gate (scripts/release-gate.sh 0.11.0)

```bash
./scripts/release-gate.sh 0.11.0
```

## Tag / publish (explicit user command only)

```bash
git tag -a v0.11.0 -m "Release v0.11.0: time_saved_ms vs naive agent wall-clock"
# push / gh release / PyPI — only when asked
```
