# LLM model presets

Copy-paste templates for `~/.greedy-token/config.yaml`. Paid cloud models default to **`turned_on: false`**; enable manually with API keys and `llm.expensive.opt_in`.

```bash
greedy-token config --init --preset local-ollama-3
greedy-token config --init --preset prod-ollama-2
greedy-token config --init --preset cursor-like-catalog --force
greedy-token config --list-presets
```

## Ollama 2 vs 3 models

| | **2 models** | **3 models** |
|---|---|---|
| **Local (dev)** | `local-ollama` | `local-ollama-3` |
| **Prod** | `prod-ollama-2` | `prod-ollama-3` |

| Preset | Use case |
|--------|----------|
| `local-ollama` | Dev: Ollama 7b + 14b, both **on** |
| `local-ollama-3` | Dev high-VRAM: 7b + 14b + 32b, all **on** |
| `prod-ollama-2` | Prod: 7b classify + 14b audit/generate |
| `prod-ollama-3` | Prod: 2 models on + 32b **off** (enable when needed) |
| `cursor-like-catalog` | Full provider catalog (Cursor-like); cloud **off** |
| `selectel-cl21r` | Prod CL21R Ollama (alias of `prod-ollama-2` naming) |
| `tms-automator` | TMS profiles + escalation for automator |

### Profile routing (Ollama presets)

| Profile | 2-model | 3-model |
|---------|---------|---------|
| classify, compress | 7b (fast) | 7b (fast) |
| audit, generate | 14b (smart) | 14b (smart) |
| architecture, pipeline-fallback | — (escalate to smart) | 32b (heavy) |

`local-ollama-3` requires: `ollama pull qwen2.5-coder:32b-instruct-q4_K_M`

## Executors (v0.5.9)

| Provider key | Executor | Base URL | API key env |
|--------------|----------|----------|-------------|
| `ollama` | native | `http://localhost:11434` | — |
| `openai_compat` | OpenAI-compatible chat | see model `url` | per-model `api_key_env` |
| `yandex_gpt` | YandexGPT native | (native API) | `YANDEX_GPT_API_KEY` |

OpenAI, Groq, Mistral, DeepSeek in presets use **`openai_compat`** + provider base URL.

## Pending providers (README only — not in YAML)

These are **not executable** until native adapters ship. Do not add to `llm.*.models[]` with `turned_on: true` before the executor exists.

| id | model slug | api_key_env | Issue | Role |
|----|------------|-------------|-------|------|
| anthropic-sonnet | `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` | [#9](https://github.com/svasenkov/greedy-token/issues/9) | generate |
| gemini-pro | `gemini-2.5-pro` | `GOOGLE_API_KEY` | [#11](https://github.com/svasenkov/greedy-token/issues/11) | generate |

Enable paid models:

1. Set `turned_on: true` (or `enabled: true`) on the model entry
2. Export the matching `api_key_env`
3. For expensive tier: `llm.expensive.opt_in: true` and `GREEDY_EXPENSIVE_LLM=1` / `--allow-expensive`

See [docs/cloud-api.md](../../../docs/greedy-token/cloud-api.md) (monorepo) and [ROADMAP-RU.md](../../docs/ROADMAP-RU.md).
