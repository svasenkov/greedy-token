# Roadmap

**English:** [ROADMAP.md](ROADMAP.md)

Сейчас greedy-token заточен под **Cursor + Ollama**. CLI и MCP не привязаны к IDE; платные API и альтернативные локальные runtime — в roadmap ниже.

Легенда: ✅ есть · ❌ нет · 🔜 в планах

Прогресс: [GitHub issues с label `roadmap`](https://github.com/svasenkov/greedy-token/issues?q=is%3Aissue+label%3Aroadmap).

## Темы v0.5

| Тема | Цель | Трекинг |
|------|------|---------|
| **cheap_llm** | `provider: ollama \| openai_compat` — один конфиг для Ollama и OpenAI-compatible серверов | ✅ [#2](https://github.com/svasenkov/greedy-token/issues/2) (v0.5.0) |
| **expensive_llm** | Metered agent / API path (Cursor сегодня; опциональные paid agent APIs) — не bulk classify | [#3](https://github.com/svasenkov/greedy-token/issues/3) |
| **mcp_hosts** | Документация и smoke MCP не только в Cursor | [#14](https://github.com/svasenkov/greedy-token/issues/14), [#15](https://github.com/svasenkov/greedy-token/issues/15) |

## Темы v0.6

| Тема | Цель | Трекинг |
|------|------|---------|
| **mcp_hosts** (продолжение) | Claude Desktop / Continue — полный smoke | [#14](https://github.com/svasenkov/greedy-token/issues/14), [#15](https://github.com/svasenkov/greedy-token/issues/15) |
| **ci_headless** | greedy-token в CI: route/pipeline на self-hosted Ollama вместо «всё в Claude» | [#18](https://github.com/svasenkov/greedy-token/issues/18) |

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

### expensive_llm path — критерии готовности

- В footer/docs: **expensive LLM** = полный agent chat (Cursor) или metered agent API
- Конфиг (опционально, v0.5+): `expensive_llm.provider`, `api_key` env, `model` для non-Cursor agent hosts
- Opt-in only для любых paid API — без silent spend

## Бесплатные / локальные

| Runtime / модель | API | CLI tier | Pipeline / scripts | Статус | Issue |
|------------------|-----|:--------:|:------------------:|:------:|-------|
| **Ollama** (localhost) | `/api/chat`, `/api/tags` | ✅ | ✅ | ✅ | — |
| **Ollama** (удалённый `OLLAMA_URL`) | то же | ✅ | ✅ | ✅ | — |
| **Open models через Ollama** | через Ollama | ✅ | ✅ | ✅ | — |
| **LM Studio** | OpenAI `/v1/chat/completions` | ✅ via `openai_compat` | ✅ | ✅ adapter · 🔜 docs | [#4](https://github.com/svasenkov/greedy-token/issues/4) |
| **llama.cpp server** | OpenAI-compatible | ✅ via `openai_compat` | ✅ | ✅ adapter · 🔜 docs | [#5](https://github.com/svasenkov/greedy-token/issues/5) |
| **vLLM / TGI** | OpenAI-compatible | ✅ via `openai_compat` | ✅ | ✅ adapter · 🔜 docs | [#6](https://github.com/svasenkov/greedy-token/issues/6) |
| **MLX** (Apple Silicon) | native / через Ollama | partial | partial | ❌ 🔜 | [#7](https://github.com/svasenkov/greedy-token/issues/7) |
| **GPT4All / Jan** | свой local API | — | — | ❌ | [#8](https://github.com/svasenkov/greedy-token/issues/8) |

### cheap_llm — критерии готовности

**Сделано в v0.5.0** ([#2](https://github.com/svasenkov/greedy-token/issues/2)). Tier id в routes остаётся `ollama`.

```yaml
# ~/.greedy-token/config.yaml
cheap_llm:
  provider: ollama          # ollama | openai_compat
  url: http://localhost:11434
  model: qwen2.5-coder:7b-instruct-q4_K_M
  # openai_compat:
  # provider: openai_compat
  # url: http://localhost:1234   # /v1 добавится при необходимости
```

- ✅ `get_cheap_llm_settings()` вместо `get_ollama_settings()` (alias для совместимости)
- ✅ Health check и chat для обоих провайдеров
- ✅ `OLLAMA_*` / `ollama:` остаются алиасами; предпочтительно `CHEAP_LLM_*`
- 🔜 Workspace `scripts/ollama/_common.sh` → env-driven / `scripts/cheap-llm/` (не пакет)

## IDE / MCP host

| Host | MCP tools | Token-economy rule | Статус | Issue |
|------|:---------:|:------------------:|:------:|-------|
| **Cursor** | ✅ | ✅ | ✅ | — |
| **Claude Desktop** (MCP) | вероятно ✅ | — | ❌ 🔜 | [#14](https://github.com/svasenkov/greedy-token/issues/14) |
| **VS Code + Continue** | — | — | ❌ | [#15](https://github.com/svasenkov/greedy-token/issues/15) |
| **Только CLI** | — | — | ✅ | — |

## CI / headless

Сценарий: компания уже жжёт Claude/Cursor в пайплайнах; на своих серверах поднимает Ollama (или openai_compat) — greedy-token в job маршрутизирует часть задач на локальную LLM.

```text
CI job → greedy-token CLI → rg | python | cheap_llm (Ollama/internal) | RAG | expensive_llm agent (opt-in)
```

Это **не MCP внутри Actions**, а headless CLI (`route`, `pipeline --execute`, `report`). Ядро уже умеет remote `OLLAMA_URL`; не хватает доков, примерных workflows и явного env-контракта для runner’ов.

| Хост CI | Роль | Статус | Issue |
|---------|------|:------:|-------|
| **Self-hosted / VPN runner** + Ollama внутри сети | Основной целевой паттерн | ❌ 🔜 | [#18](https://github.com/svasenkov/greedy-token/issues/18) |
| **GitHub-hosted ephemeral** без доступа к private Ollama | Только rg/python/rag или expensive_llm agent | вне фокуса | — |
| **Jenkins / GitLab CI** | Тот же CLI-контракт | ❌ 🔜 (примеры) | [#18](https://github.com/svasenkov/greedy-token/issues/18) |

### ci_headless — критерии готовности

- `docs/ci-setup.md`: env (`OLLAMA_URL` / `cheap_llm`, `GREEDY_TOKEN_ROOT`, телеметрия), без Cursor MCP
- Пример workflow: GitHub Actions (self-hosted) + опционально Jenkins snippet
- Smoke: `route` + `pipeline … --execute` с remote Ollama из чистого runner-образа
- Guidance: какие классы задач остаются на cheap_llm, какие escalate в expensive_llm / agent
- Опционально: `greedy-token report` в job summary / artifact

Связано: [#2](https://github.com/svasenkov/greedy-token/issues/2) (`cheap_llm`), [#3](https://github.com/svasenkov/greedy-token/issues/3) (`expensive_llm`).

## Вне scope (пока)

- Замена Cursor/Claude как основного coding agent
- Hosted greedy-token SaaS
- Fine-tuning моделей
- Ephemeral public runners без сети до корпоративного Ollama (без VPN/self-hosted)

## Changelog

| Версия | Фокус |
|--------|-------|
| **v0.5.0** | `cheap_llm` provider (`ollama` \| `openai_compat`); tier id `ollama` без переименования; `OLLAMA_*` compat |
| **v0.4.4** | Cursor-first README, mascot, короче MCP instructions, roadmap CI/headless (#18) |
| **v0.4.3** | Cursor starter kit (`examples/cursor/`) + setup-дока для пользователей PyPI |
| **v0.4.2** | Security hardening, MCP dry-run default, CI pytest, log rotation, settings module |
| **v0.4** | MCP pipeline, Ollama config, token economy footer |
| **v0.5.x** | `expensive_llm` metered agent path ([#3](https://github.com/svasenkov/greedy-token/issues/3)) |
| **v0.6** | IDE-интеграции beyond Cursor + **CI / headless** docs & examples ([#18](https://github.com/svasenkov/greedy-token/issues/18)) |
