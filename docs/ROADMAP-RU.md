# Roadmap

**English:** [ROADMAP.md](ROADMAP.md)

Сейчас greedy-token заточен под **Cursor + Ollama**. CLI и MCP не привязаны к IDE; платные API и альтернативные локальные runtime — в roadmap ниже.

Легенда: ✅ есть · ❌ нет · 🔜 в планах

Прогресс: [GitHub issues с label `roadmap`](https://github.com/svasenkov/greedy-token/issues?q=is%3Aissue+label%3Aroadmap).

## Темы v0.5

| Тема | Цель | Трекинг |
|------|------|---------|
| **local_llm** | `provider: ollama \| openai_compat` — один конфиг для Ollama и OpenAI-compatible серверов | [#2](https://github.com/svasenkov/greedy-token/issues/2) |
| **cloud_llm** | Опциональный дешёвый cloud executor для bulk classify / audit | [#3](https://github.com/svasenkov/greedy-token/issues/3) |
| **mcp_hosts** | Документация и smoke MCP не только в Cursor | [#14](https://github.com/svasenkov/greedy-token/issues/14), [#15](https://github.com/svasenkov/greedy-token/issues/15) |

## Платные / облако

| Провайдер | Роль | CLI | MCP | Дешёвый executor | Статус | Issue |
|-----------|------|:---:|:---:|:----------------:|:------:|-------|
| **Cursor** (Agent / Composer) | IDE-агент, escalation, baseline | ✅ | ✅ | — | ✅ | — |
| **Anthropic** (Claude API) | Bulk classify / audit вместо агента | ✅ route | — | ❌ | ❌ 🔜 | [#9](https://github.com/svasenkov/greedy-token/issues/9) |
| **OpenAI** (GPT / Codex API) | То же | ✅ route | — | ❌ | ❌ 🔜 | [#10](https://github.com/svasenkov/greedy-token/issues/10) |
| **Google** (Gemini API) | То же | ✅ route | — | ❌ | ❌ 🔜 | [#11](https://github.com/svasenkov/greedy-token/issues/11) |
| **Mistral** (Codestral API) | То же | ✅ route | — | ❌ | ❌ 🔜 | [#12](https://github.com/svasenkov/greedy-token/issues/12) |
| **Groq / Together / Fireworks** | Быстрый cloud open-weights | ✅ route | — | ❌ | ❌ 🔜 | [#13](https://github.com/svasenkov/greedy-token/issues/13) |
| **GitHub Copilot** | Интеграция IDE-агента | — | — | — | ❌ | [#16](https://github.com/svasenkov/greedy-token/issues/16) |
| **Windsurf / Codeium** | Интеграция IDE-агента | — | — | — | ❌ | [#17](https://github.com/svasenkov/greedy-token/issues/17) |

`route` / `estimate` рекомендуют escalation; greedy-token **не вызывает** платные API как executor.

### cloud_llm executor — критерии готовности

- Конфиг: `cloud_llm.provider`, `api_key` env, `model`, опционально `base_url`
- Tier `cloud_llm` между `ollama` и `cursor` в `routes.yaml`
- Скрипты audit/classify могут идти в cloud, если Ollama недоступен
- Token footer с оценкой API-токенов
- Только opt-in — без тихих cloud-вызовов

## Бесплатные / локальные

| Runtime / модель | API | CLI tier | Pipeline / scripts | Статус | Issue |
|------------------|-----|:--------:|:------------------:|:------:|-------|
| **Ollama** (localhost) | `/api/chat`, `/api/tags` | ✅ | ✅ | ✅ | — |
| **Ollama** (удалённый `OLLAMA_URL`) | то же | ✅ | ✅ | ✅ | — |
| **Open models через Ollama** | через Ollama | ✅ | ✅ | ✅ | — |
| **LM Studio** | OpenAI `/v1/chat/completions` | — | — | ❌ 🔜 | [#4](https://github.com/svasenkov/greedy-token/issues/4) |
| **llama.cpp server** | OpenAI-compatible | — | — | ❌ 🔜 | [#5](https://github.com/svasenkov/greedy-token/issues/5) |
| **vLLM / TGI** | OpenAI-compatible | — | — | ❌ 🔜 | [#6](https://github.com/svasenkov/greedy-token/issues/6) |
| **MLX** (Apple Silicon) | native / через Ollama | partial | partial | ❌ 🔜 | [#7](https://github.com/svasenkov/greedy-token/issues/7) |
| **GPT4All / Jan** | свой local API | — | — | ❌ | [#8](https://github.com/svasenkov/greedy-token/issues/8) |

### local_llm — критерии готовности

```yaml
# ~/.greedy-token/config.yaml (предложение)
local_llm:
  provider: ollama          # ollama | openai_compat
  url: http://localhost:11434
  model: qwen2.5-coder:7b-instruct-q4_K_M
```

- `get_local_llm_settings()` вместо `get_ollama_settings()` (alias для совместимости)
- Health check и chat для обоих провайдеров
- `OLLAMA_*` env остаются алиасами

## IDE / MCP host

| Host | MCP tools | Token-economy rule | Статус | Issue |
|------|:---------:|:------------------:|:------:|-------|
| **Cursor** | ✅ | ✅ | ✅ | — |
| **Claude Desktop** (MCP) | вероятно ✅ | — | ❌ 🔜 | [#14](https://github.com/svasenkov/greedy-token/issues/14) |
| **VS Code + Continue** | — | — | ❌ | [#15](https://github.com/svasenkov/greedy-token/issues/15) |
| **Только CLI** | — | — | ✅ | — |

## Вне scope (пока)

- Замена Cursor/Claude как основного coding agent
- Hosted greedy-token SaaS
- Fine-tuning моделей

## Changelog

| Версия | Фокус |
|--------|-------|
| **v0.4.2** | Security hardening, MCP dry-run default, CI pytest, log rotation, settings module |
| **v0.4** | MCP pipeline, Ollama config, token economy footer |
| **v0.5** | `local_llm` + `cloud_llm` providers |
| **v0.6** | IDE-интеграции beyond Cursor |
