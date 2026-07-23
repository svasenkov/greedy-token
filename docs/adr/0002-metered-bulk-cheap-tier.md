# ADR-0002: Paid bulk APIs as a spend-guarded cheap executor tier

- Status: **Accepted**
- Date: 2026-07-23
- Depends on: [ADR-0001](0001-unified-model-spec-derived-tier.md) (unified
  `ModelSpec`, derived tier)
- Scope: `model_select.py`, `spend_guard.py`, `llm_invoke.py`, `router.py`,
  `usage.py`, `budget_ledger.py`, `budget.py`, docs.

## Context

Bulk tasks (classify / audit / summarize) route to the cheap-LLM executor
tier, which historically meant **local Ollama only**: when the local runtime
is down, the router skips the tier and the task falls through to the
expensive agent-chat path. ADR-0001 made "cheap" an economic judgement
(`derive_tier`): a metered remote model at or below
`llm.cheap_cost_threshold_per_1m_usd` derives `cheap`. But three gaps kept
metered cheap models from actually serving the bulk tier:

1. Tier availability was hardwired to the local Ollama probe.
2. `spend_guard` gated only the **expensive** derived tier; sub-threshold
   metered calls bypassed opt-in and caps entirely.
3. The budget ledger classified metered-but-cheap spend as `cheap`
   (ADR-0001 accepted this as bounded error; real routing volume through the
   tier makes it unacceptable).

## Decision

### 1. Route tier id stays `"ollama"` — no alias

The executor tier keeps its id `"ollama"` in routes, `TIER_ORDER`,
`selected_tier` telemetry, and `CHEAP_TIERS`. Renaming or aliasing would
break stored telemetry, workspace `routes.yaml` overlays, and the
route-quality attribution for zero semantic gain. The tier id is a **slot
name** ("cheap LLM bulk executor"), not a runtime claim; footers and
rationale carry the honest billing label (see §4).

### 2. Metered cheap fallback for tier availability

`metered_cheap_fallback(root)` (`model_select.py`) returns the first enabled
model from the unified `llm.models[]` pool with `billing == "metered"` and
derived tier `cheap`. The router treats the `ollama` tier as available when
the local probe succeeds **or** such a fallback exists *and* the metered
opt-in is granted. Est-token math is unchanged (cheap tier spend), the
rationale says the call is metered and spend-guarded.

### 3. Every metered call passes spend_guard

New opt-in, separate from the expensive one (a $0.05/1M bulk classify and a
$5/1M agent escalation are different decisions):

- config: `llm.metered.opt_in: true` (default **false**);
- env: `GREEDY_METERED_LLM=1` (same accepted tokens as the expensive env);
- CLI: `--allow-expensive` also grants it (superset permission).

`check_metered_allowed(spec, ...)`:

- `billing == "free"` → allowed (nothing to guard);
- derived tier `expensive` → delegate to `check_expensive_allowed`
  (unchanged semantics: expensive opt-in + daily + monthly caps);
- derived tier `cheap` + metered → require the metered opt-in, then the same
  `llm.expensive.daily_cap_usd` daily cap and the monthly metered cap
  (`headroom()`); one daily cap covers **all** metered spend so the two
  opt-ins cannot stack above it.

`llm_invoke` gates every metered candidate through this check (it used to
check only `billing_tier == "expensive"`).

### 4. Telemetry and budget split

- Legacy `billing_tier` field: **unchanged** — still the derived tier
  (`cheap` / `expensive`), old readers keep working.
- v2 `billing.tier` block: metered calls write `"metered"` (previously only
  expensive ones did) plus `cost_usd` and `model_id`. `spend_guard` daily
  accounting and `budget_ledger` monthly accounting both count the block, so
  caps see the full metered spend.
- `BudgetSnapshot` gains `metered_cheap_spent_usd` /
  `metered_expensive_spent_usd` (split by the derived tier recorded in
  `billing_tier`); `budget --verbose` and `budget --json` show the split.
- Footers: the cheap-LLM billing label distinguishes
  `cheap LLM (metered: <model>)` from `cheap LLM (local free: <model>)`,
  resolved via the served model's `ModelSpec.billing`.

## Consequences

- Bulk work no longer falls through to the expensive agent path just because
  Ollama is down — but only with an explicit, capped opt-in.
- Supersedes the ADR-0001 consequence "sub-threshold metered spend is
  accounted under the cheap tier": metered spend is now metered in the
  ledger regardless of derived tier; the derived tier only picks the
  executor and the selection pools.
- Old logs (no `billing` block on cheap events) aggregate exactly as before.
