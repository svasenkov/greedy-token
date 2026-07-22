# ADR-0001: Unified ModelSpec with orthogonal attributes and derived tier

- Status: **Accepted**
- Date: 2026-07-23
- Scope: `src/greedy_token/` — `model_select.py`, `settings.py`, `spend_guard.py`,
  `budget_ledger.py`, `budget_policy.py`, `usage.py`, `cheap_llm.py`,
  `expensive_llm.py`, packaged presets, docs.

## Context

The model registry hardcodes the cheap/expensive split three times:

1. `ModelSpec.tier` is a **stored** field, assigned from the config section the
   entry was parsed from (`llm.cheap.models[]` → `"cheap"`,
   `llm.expensive.models[]` → `"expensive"`).
2. `LlmRegistry` keeps two separate pools (`cheap_models` / `expensive_models`).
3. Selection (`resolve_model`, `_enabled_pool`, escalation) branches on the
   pool, not on model attributes.

The stored tier conflates three orthogonal facts — where the model runs, whether
calls cost money, and how much — and drifts from reality. The packaged
`cursor-like-catalog` preset lists the *same* physical model
(`llama-3.3-70b-versatile` on Groq, $0.05/1M) once as `cheap` (`groq-llama`) and
once as `expensive` (`groq-70b`). A local model behind a metered gateway cannot
be expressed at all.

## Decision

### 1. Orthogonal `ModelSpec` attributes

`ModelSpec` describes a model with independent attributes; **no stored tier**:

| Attribute | Type | Meaning |
|---|---|---|
| `provider` | `ollama \| openai_compat \| yandex_gpt` | wire protocol (unchanged) |
| `locality` | `local \| remote` | where the runtime lives |
| `billing` | `free \| metered` | whether calls cost real money |
| `cost_per_1m_usd` | `float \| None` | USD per 1M generated tokens; `None` = unknown |

`cost_per_1m_usd` becomes nullable: an *absent* cost is "unknown", which is not
the same as an explicit `0`. This is what keeps legacy `llm.expensive.models[]`
entries without a cost on the expensive tier (see derive rule below).

### 2. `derive_tier` — the only place tier is computed

```python
def derive_tier(spec, *, cheap_cost_threshold_per_1m_usd=DEFAULT_CHEAP_COST_THRESHOLD_PER_1M_USD):
    if spec.billing == "free":
        return "cheap"
    # metered: decided purely by cost
    if spec.cost_per_1m_usd is not None and spec.cost_per_1m_usd <= cheap_cost_threshold_per_1m_usd:
        return "cheap"
    return "expensive"   # metered with cost above threshold — or unknown cost
```

Rules, fixed by this ADR:

- `billing == "free"` → `cheap`.
- `billing == "metered"` and `cost_per_1m_usd <= cheap_cost_threshold` → `cheap`.
- otherwise → `expensive`. A metered model with **unknown** cost
  (`cost_per_1m_usd is None`) is conservatively `expensive`.
- `locality` **never** affects the tier — `local + metered` is a valid
  combination and is decided by cost alone.

Threshold: config key `llm.cheap_cost_threshold_per_1m_usd`, default
**`0.2`** (USD per 1M tokens, same unit as `cost_per_1m_usd`). Rationale: it
cleanly covers every metered model currently shipped in the cheap catalog
(max $0.15/1M — `gpt-4o-mini` class) while every intentionally-expensive
catalog entry costs ≥ $0.27/1M. The threshold lives on `LlmRegistry`; all
call sites with registry access pass it explicitly.

`ModelSpec.tier` (stored field) is **removed**. `derive_tier(spec, ...)` in
`model_select.py` is the single source of truth; no property alias, so a stale
default threshold can never disagree with the configured one.

### 3. Attribute defaults when parsing config

- `billing`: explicit `billing:` key wins; else `metered` if
  `cost_per_1m_usd > 0`, else `free`.
  **Legacy shim:** entries from `llm.expensive.models[]` default to `metered`
  even without a cost (their unknown cost then derives `expensive`, preserving
  old behaviour).
- `locality`: explicit `locality:` key wins; else inferred from the effective
  URL host — `localhost` / `127.0.0.1` / `0.0.0.0` / `::1` → `local`, anything
  else (including no URL, e.g. YandexGPT native API) → `remote`.
- `cost_per_1m_usd`: explicit value (legacy alias `token_price` still read);
  absent or unparsable → `None` (unknown).

### 4. Single model pool

`LlmRegistry.cheap_models` / `expensive_models` stored fields are replaced by a
single `models: tuple[ModelSpec, ...]`. Selection filters this pool by
`derive_tier`. `cheap_models` / `expensive_models` remain as **deprecated
read-only properties** (derived via `derive_tier`) for internal callers.

### 5. New YAML shape (phase 3)

```yaml
llm:
  policy: auto
  cheap_cost_threshold_per_1m_usd: 0.2   # optional, default 0.2
  models:
    - id: ollama-fast
      provider: ollama
      url: http://localhost:11434
      model: qwen2.5-coder:7b-instruct-q4_K_M
      locality: local        # optional — inferred from url
      billing: free          # optional — inferred from cost
      profiles: [classify, compress]
    - id: openai-gpt4o
      provider: openai_compat
      url: https://api.openai.com/v1
      model: gpt-4o
      billing: metered
      cost_per_1m_usd: 2.5
      api_key_env: OPENAI_API_KEY
      profiles: [generate]
  cheap:                      # tier *policy* keys stay — only model lists move
    default_id: ollama-fast
    selection: auto
  expensive:
    opt_in: false
    daily_cap_usd: 10
    default_id: openai-gpt4o
  escalation:
    chain: [ollama-fast, openai-gpt4o]
```

Merge rule: `llm.models[]` is parsed first; legacy `llm.cheap.models[]` and
`llm.expensive.models[]` are still parsed and appended, skipping duplicate ids
(first occurrence wins). Tier-policy scalars (`cheap.default_id`,
`cheap.selection`, `expensive.opt_in`, `expensive.daily_cap_usd`,
`expensive.default_id`) are unchanged — they configure tier behaviour, not
model storage.

## Backward compatibility (contract)

All of the following keep working unchanged:

- **Old YAML**: `llm.cheap.models[]` / `llm.expensive.models[]` sections,
  top-level `cheap_llm:` / `ollama:` config, presets in the old shape.
- **Env**: `CHEAP_LLM_*`, `OLLAMA_*`, `GREEDY_LLM_MODEL_ID`,
  `GREEDY_LLM_PROFILE`, `GREEDY_LLM_TIER` (now exports the derived tier —
  same value set).
- **Telemetry** (`usage.jsonl`): the `billing.tier`
  (`metered | cheap | cursor_estimate`) and legacy `billing_tier`
  (`cheap | expensive`) schema does **not** change. Stored events are read
  as-is; new events write the *derived* value into the same fields.
  `report` and `spend_guard._load_today_spend` must stay correct on a mixed
  old/new log (guarded by a dedicated test).
- **External contracts**: MCP tools, footer, billing split in reports, and the
  `llm list` output format are unchanged (`[cheap]` / `[expensive]` labels are
  now derived, same strings).

Known, intentional behaviour corrections (derive rule wins over the section a
model was declared in):

- A metered model at or below the threshold derives `cheap` even if it was
  declared under `llm.expensive.models[]` (e.g. `groq-70b` at $0.05/1M). It no
  longer needs the expensive opt-in; its logged `cost_usd` is unchanged.
- A model declared under `llm.cheap.models[]` with a cost **above** the
  threshold derives `expensive` and becomes subject to the opt-in gate and
  daily/monthly caps. No packaged preset is affected (max cheap cost $0.15).

## Migration plan

Each phase lands as one commit with the full suite green
(100% line+branch coverage, doc-drift and mutation-equivalents guards, no
version bump).

1. **Phase 1 — attributes + derive + old config parsing.**
   `locality` / `billing` / nullable cost on `ModelSpec`; `derive_tier` +
   threshold on `LlmRegistry` (parsed from `llm.cheap_cost_threshold_per_1m_usd`);
   stored `tier` field removed; all `spec.tier` readers switch to `derive_tier`
   (`llm list`, spend guard, escalation, env export). Pools still section-based.
2. **Phase 2 — single pool + attribute-based selection.**
   `LlmRegistry.models`; `_enabled_pool` / defaults / escalation filter by
   `derive_tier`; `cheap_models` / `expensive_models` become deprecated derived
   properties. Mixed-log telemetry test (report + `_load_today_spend`).
3. **Phase 3 — new YAML `llm.models[]` + presets/docs migration.**
   Parse `llm.models[]` (dedupe with legacy sections); migrate packaged and
   example presets to the unified shape (merging the duplicated Groq entry);
   update README / presets README / config help text.

## Deprecated (kept for compatibility, removal not scheduled)

- `llm.cheap.models[]` / `llm.expensive.models[]` model lists (read, deduped
  after `llm.models[]`).
- `token_price` cost alias.
- `LlmRegistry.cheap_models` / `expensive_models` properties.
- `CHEAP_LLM_MODEL` / `OLLAMA_MODEL` env model override (already warned as
  deprecated; unchanged here).
- Top-level `cheap_llm:` / `ollama:` config (legacy single-model path;
  unchanged).

## Consequences

- Tier is an economic judgement computed in exactly one place; adding a model
  requires stating facts (locality, billing, cost), not a verdict.
- `spend_guard` gating and the budget ledger stay consistent with each other
  because both consume the same derived tier.
- Sub-threshold metered spend is accounted under the cheap tier (cost is still
  logged per event); the metered ledger tracks models above the threshold —
  acceptable because the threshold bounds the error at $0.2 per 1M tokens.
