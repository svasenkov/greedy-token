# Cut checklist — greedy-token v0.10.0

**Status:** CUT — bump + gate green, tag/push per explicit user command.

## Summary

Beyond-Cursor release: agent hosts (Claude Desktop, Continue), metered bulk
APIs as a spend-guarded cheap executor tier (ADR-0002), calibration without
manual discipline, team route presets.

- **agent hosts**: `agent_host: cursor | claude | continue` config key
  (user < workspace `.greedy-token.yaml` < `GREEDY_AGENT_HOST` env, default
  `cursor`). `audit-context` and the naive-chat baseline count the host's
  always-on rule conventions — `.cursor/rules/*.mdc`, `CLAUDE.md` +
  `.claude/rules/*.md`, `.continuerules` + `.continue/rules/*.md`. Starter
  kits `examples/claude/` (`claude_desktop_config.json`, `CLAUDE.md`) and
  `examples/continue/` (`config.yaml`, `continuerules.md`); setup guides
  `docs/claude-setup.md` / `docs/continue-setup.md` (+RU). Host-generic
  footer/rationale strings now say "agent chat" / "expensive agent path";
  telemetry keys (`cursor_baseline`, tier id `cursor`) unchanged for
  compatibility.
- **metered bulk APIs (ADR-0002)**: a metered remote model with derived tier
  *cheap* (e.g. a $0.05/1M classify API from the unified `llm.models[]` pool)
  serves the bulk (`ollama`) executor tier when the local runtime is down —
  strictly opt-in (`llm.metered.opt_in: true` / `GREEDY_METERED_LLM=1` /
  `--allow-expensive`). Every metered call passes the spend guard
  (`check_metered_allowed`: shared daily cap + monthly metered cap); the v2
  telemetry block logs `billing.tier: metered` + `cost_usd` (legacy
  `billing_tier` keeps the derived tier), `budget --verbose/--json` show the
  cheap-bulk vs expensive split, footers distinguish `metered` from
  `local free`.
- **calibration without discipline** (closes the "calibration needs telemetry
  discipline" review nit): `route` / `report` print a one-line nudge
  (`baseline uncalibrated — run greedy-token calibrate`, once per call) while
  the source is `default-estimate`; `greedy-token doctor` gains a **Baseline**
  block + warning when no `baseline:` section exists; the calibration cache is
  invalidated by `usage.jsonl` mtime/size, so a long-lived MCP server picks up
  fresh telemetry without a restart.
- **team route presets**: `greedy-token init --preset <name|url|path>` merges
  shared routes into `<root>/.greedy-token.yaml` — a bundled preset name
  (`team-default`, packaged as `greedy_token/route_presets/`), an `https://`
  URL, or a file path; merge-by-id, idempotent.

## CONTRACT — evidence

| Claim | Evidence |
|-------|----------|
| version 0.10.0 | `pyproject.toml` (SSOT via `src/greedy_token/version.py`) |
| agent_host config + host audit | `settings.get_agent_host`, `context_audit.HOST_RULE_GLOBS`; `tests/test_agent_host.py` |
| ADR-0002 accepted | `docs/adr/0002-metered-bulk-cheap-tier.md` |
| every metered call spend-guarded | `check_metered_allowed` in `spend_guard.py`; `llm_invoke` gates on `spec.billing == "metered"`; `tests/test_metered_bulk.py` |
| budget metered split | `BudgetSnapshot.metered_cheap_spent_usd` / `metered_expensive_spent_usd`; `budget --verbose/--json` |
| calibration nudge + doctor + mtime cache | `baseline.uncalibrated_nudge`, `resource_probe` Baseline block, `calibration._log_signature`; `tests/test_baseline.py`, `tests/test_calibration.py`, `tests/test_resource_probe.py` |
| route presets by name/url/path | `paths.load_route_preset` + `init --preset`; `tests/test_cli_handlers.py` |
| mutation clean on touched hot modules | targeted `mutmut run` on `router` / `pipeline` / `spend_guard` functions — 0 surviving non-registry mutants |

## Gate (scripts/release-gate.sh 0.10.0)

```text
948 passed
1 @release passed (pyproject + __version__ == 0.10.0)
minTestsCount synced → 948
workflows match _ethalon
release gate OK: 0.10.0
```

Line + branch coverage on `src/greedy_token/` stays at 100% (`fail_under = 100`).
Doc-drift guards (`tests/test_doc_sync.py`, `tests/test_mutation_equivalents.py`) green.

## Cut commands (run only on explicit command)

```bash
cd projects/greedy-token-home/greedy-token

# 1) Tag
git tag -a v0.10.0 -m "Release v0.10.0: agent hosts beyond Cursor, metered bulk APIs (ADR-0002), calibration nudges + mtime cache, team route presets"

# 2) Push main + tag
git push origin main
git push origin v0.10.0

# 3) GitHub Release (triggers PyPI via publish.yml)
gh release create v0.10.0 --title "v0.10.0" --notes-file - <<'EOF'
## Summary
- Agent hosts beyond Cursor: `agent_host: cursor|claude|continue` — context audit + naive-chat baseline follow the host conventions (`CLAUDE.md`, `.continuerules`); starter kits and setup guides for Claude Desktop and Continue (EN+RU); host-generic footers say "agent chat" (telemetry keys unchanged)
- Metered bulk APIs (ADR-0002): metered cheap models from `llm.models[]` can serve the bulk executor tier when Ollama is down — opt-in `llm.metered.opt_in` / `GREEDY_METERED_LLM`, every metered call spend-guarded (daily + monthly caps), `billing.tier: metered` + `cost_usd` telemetry, `budget` shows the cheap-bulk vs expensive split
- Calibration without discipline: uncalibrated-baseline nudge in `route`/`report`, doctor Baseline block, calibration cache invalidated by usage.jsonl mtime/size (long-lived MCP servers pick up fresh telemetry)
- Team route presets: `greedy-token init --preset <name|url|path>` (bundled `team-default`, shared URL, or file)
- minTestsCount 948

## Install
pip install greedy-token==0.10.0
EOF
```

## After PyPI

- Verify: `pip index versions greedy-token` / PyPI page
- Monorepo hub README: set published PyPI pin to v0.10.0
