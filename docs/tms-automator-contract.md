# TMS automator integration contract

Headless LLM contract for `autotests-ai-tms-automator` on Selectel CL21R (Ollama 7b+14b) without Cursor MCP.

## Profiles

| Profile | Model tier | Use case |
|---------|------------|----------|
| `tms-classify` | cheap / fast (7b) | TestOps step classification |
| `tms-generate` | cheap / smart (14b) | Java test generation / skill audit |
| `tms-auth-probe` | cheap / fast (7b) | Auth / storage discovery |
| `tms-generate-fallback` | expensive (opt-in) | Escalation when cheap output weak |

## CLI (Jenkins / Docker)

```bash
export GREEDY_TOKEN_ROOT=/path/to/autotests-ai-tms-automator
export GREEDY_EXPENSIVE_LLM=0   # set 1 + llm.expensive.opt_in for YandexGPT

greedy-token llm invoke \
  --profile tms-classify \
  --system-file prompts/classify.txt \
  --user-file case.json \
  --tags project=tms-automator,step=classify \
  --json
```

Pipeline recipes:

```bash
greedy-token pipeline "pipeline: tms-classify path=case.json" --execute
greedy-token pipeline "pipeline: tms-generate skill=automate-manual-test" --execute
greedy-token pipeline "pipeline: tms-auth-probe path=auth-context.json" --execute
```

## Library API (preferred in automator hot path)

```python
from greedy_token.llm_invoke import invoke_profile

result = invoke_profile(
    profile="tms-generate",
    system="...",
    user="...",
    tags={"project": "tms-automator", "step": "generate"},
    allow_escalate=True,
    allow_expensive=settings.expensive_llm_enabled,
)
# result.text, result.model_id, result.tier_billing, result.escalated_from
```

## Migration from CHEAP_LLM_MODEL

| Before | After |
|--------|-------|
| `export CHEAP_LLM_MODEL=qwen:7b` per step | `--profile tms-classify` |
| `export CHEAP_LLM_MODEL=qwen:14b` | `--profile tms-generate` |
| Shell wrapper sets model | Pipeline sets `GREEDY_LLM_MODEL_ID` via profile |

Legacy `CHEAP_LLM_MODEL` / `OLLAMA_MODEL` env still override for two releases (deprecated).

## Config (CL21R)

Preset (recommended):

```bash
greedy-token config --init --preset tms-automator
# or selectel-cl21r — same Ollama layout, alias of examples/selectel/greedy-token.yaml
```

Full file: [examples/presets/tms-automator.yaml](../examples/presets/tms-automator.yaml) · legacy path [examples/selectel/greedy-token.yaml](../examples/selectel/greedy-token.yaml)

## Telemetry tags

Log fields in `~/.greedy-token/usage.jsonl`:

- `tags.project` — `tms-automator`
- `tags.step` — `classify` | `generate` | `auth`
- `profile`, `model_id`, `billing_tier`, `escalated_from`, `cost_usd`

Crystallize: `python scripts/crystallize-report.py --since 7d --project tms-automator`

## Error codes (CLI)

| Exit | Meaning |
|------|---------|
| 0 | OK |
| 1 | LLM invoke failed / escalation exhausted |
| 2 | Usage / validation (missing user prompt) |

## Routes (python tier)

When `GREEDY_TOKEN_ROOT` points at automator repo, greedy-token routes match:

- `resolve_testops_project` → `scripts/resolve_testops_project.py`
- `sync_testops_layer_mappings` → `scripts/sync_testops_layer_mappings.py`
- `auth_storage_probe` → `scripts/auth_storage_probe.py` (crystallize target)
