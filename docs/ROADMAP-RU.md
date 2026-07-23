# Roadmap

**English:** [ROADMAP.md](ROADMAP.md)

Начиная с **v0.10.0** greedy-token работает в любом agent-хосте с поддержкой MCP (`agent_host: cursor | claude | continue`, по умолчанию Cursor); cheap tier — Ollama или любой OpenAI-совместимый runtime. CLI и MCP не привязаны к IDE; платные bulk-API поддерживаются opt-in ([ADR-0002](adr/0002-metered-bulk-cheap-tier.md)). Оставшиеся пробелы — ниже.

Легенда: ✅ есть · ❌ нет · 🔜 в планах

Прогресс: [GitHub issues с label `roadmap`](https://github.com/svasenkov/greedy-token/issues?q=is%3Aissue+label%3Aroadmap).

## Темы

| Тема | Цель | Статус |
|------|------|--------|
| **cheap_llm** | `provider: ollama \| openai_compat` — один конфиг для Ollama и OpenAI-compatible серверов | ✅ v0.5.0 ([#2](https://github.com/svasenkov/greedy-token/issues/2)) |
| **multi_model** | `llm.models[]` + profiles (`tms-classify`, `tms-generate`), `greedy-token llm invoke` | ✅ v0.5.9; единый пул — v0.9.0 ([ADR-0001](adr/0001-unified-model-spec-derived-tier.md)) |
| **expensive_llm** | YandexGPT Lite opt-in, daily cap, escalation fast→smart→paid | ✅ MVP v0.5.9; metered bulk path — v0.10.0 ([ADR-0002](adr/0002-metered-bulk-cheap-tier.md)) ([#3](https://github.com/svasenkov/greedy-token/issues/3)) |
| **mcp_hosts** | Документация и smoke MCP не только в Cursor | ✅ v0.10.0 конфиг + доки (`agent_host`); 🔜 live smoke ([#14](https://github.com/svasenkov/greedy-token/issues/14), [#15](https://github.com/svasenkov/greedy-token/issues/15)) |
| **ci_headless** | greedy-token в CI: route/pipeline на self-hosted Ollama вместо «всё в Claude» | ✅ доки + примеры ([ci-setup-RU.md](ci-setup-RU.md)); 🔜 live smoke ([#18](https://github.com/svasenkov/greedy-token/issues/18)) |

## Платные / облако

| Провайдер | Роль | CLI | MCP | Дешёвый executor | Статус | Issue |
|-----------|------|:---:|:---:|:----------------:|:------:|-------|
| **Cursor** (Agent / Composer) | IDE-агент, escalation, baseline | ✅ | ✅ | — | ✅ | — |
| **Anthropic** (Claude API) | Bulk classify / audit вместо агента | ✅ route | — | ✅ через `openai_compat` + ADR-0002 | ✅ | [#9](https://github.com/svasenkov/greedy-token/issues/9) |
| **OpenAI** (GPT / Codex API) | То же | ✅ route | — | ✅ через `openai_compat` + ADR-0002 | ✅ | [#10](https://github.com/svasenkov/greedy-token/issues/10) |
| **Google** (Gemini API) | То же | ✅ route | — | ✅ через `openai_compat` + ADR-0002 | ✅ | [#11](https://github.com/svasenkov/greedy-token/issues/11) |
| **Mistral** (Codestral API) | То же | ✅ route | — | ✅ через `openai_compat` + ADR-0002 | ✅ | [#12](https://github.com/svasenkov/greedy-token/issues/12) |
| **Groq / Together / Fireworks** | Быстрый cloud open-weights | ✅ route | — | ✅ через `openai_compat` + ADR-0002 | ✅ | [#13](https://github.com/svasenkov/greedy-token/issues/13) |
| **GitHub Copilot** | Интеграция IDE-агента | — | — | — | ❌ | [#16](https://github.com/svasenkov/greedy-token/issues/16) |
| **Windsurf / Codeium** | Интеграция IDE-агента | — | — | — | ❌ | [#17](https://github.com/svasenkov/greedy-token/issues/17) |

Metered bulk APIs обслуживают дешёвый executor tier через `llm.models[]` (`billing: metered`, выведенный tier cheap) — opt-in `llm.metered.opt_in` + spend guard, см. [ADR-0002](adr/0002-metered-bulk-cheap-tier.md). Escalation к платному *агенту* — по-прежнему только рекомендация.

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
- ✅ **v0.5.9:** multi-model registry `llm.cheap.models[]`, profiles, `greedy-token llm invoke --profile`
- 🔜 Workspace `scripts/ollama/_common.sh` → env-driven / `scripts/cheap-llm/` (не пакет)

### multi_model — v0.5.9

```yaml
llm:
  policy: auto              # cheap_only | expensive_only | auto
  cheap:
    default_id: fast
    models:
      - id: fast
        enabled: true
        provider: ollama
        model: qwen2.5-coder:7b-instruct-q4_K_M
        profiles: [tms-classify, classify]
      - id: smart
        enabled: true
        model: qwen2.5-coder:14b-instruct-q4_K_M
        profiles: [tms-generate, generate]
  expensive:
    opt_in: false
    models:
      - id: yandex-lite
        provider: yandex_gpt
        enabled: false
        profiles: [escalate, crystallize-verify]
  escalation:
    chain: [fast, smart, yandex-lite]
```

- ✅ `resolve_model(profile)` · `greedy-token llm list` · pipeline `--profile`
- ✅ Telemetry: `model_id`, `profile`, `tags.project`, `billing_tier`, `cost_usd`
- ✅ Backward compat: одиночный `cheap_llm.model` → synthetic `default` model

### Model presets (v0.5.9+)

Шаблоны в [examples/presets/](../examples/presets/README.md) — не runtime SSOT, а старт для `~/.greedy-token/config.yaml`:

```bash
greedy-token config --list-presets
greedy-token config --init --preset local-ollama
greedy-token config --init --preset cursor-like-catalog
```

| Preset | Назначение |
|--------|------------|
| `local-ollama` | Dev **2 models**: 7b + 14b, обе **on** |
| `local-ollama-3` | Dev **3 models**: 7b + 14b + 32b, все **on** |
| `prod-ollama-2` | Prod **2 models**: 7b classify + 14b generate |
| `prod-ollama-3` | Prod **3 models**: 2 on + 32b **off** по умолчанию |
| `cursor-like-catalog` | Полный каталог провайдеров; paid **off** |
| `selectel-cl21r` | CL21R prod (alias `prod-ollama-2`) |
| `tms-automator` | TMS automator + escalation |

**Anthropic / Gemini:** только в README пресетов как pending ([#9](https://github.com/svasenkov/greedy-token/issues/9), [#11](https://github.com/svasenkov/greedy-token/issues/11)) — native executor ещё не реализован; в YAML не добавлять (чтобы случайный `enabled: true` не ломал `resolve_model`).

OpenAI / Groq / Mistral / DeepSeek в пресетах — через `openai_compat` + `url` + `api_key_env`.

## IDE / MCP host

| Host | MCP tools | Token-economy rule | Статус | Issue |
|------|:---------:|:------------------:|:------:|-------|
| **Cursor** | ✅ | ✅ | ✅ | — |
| **Claude Desktop** (MCP) | ✅ | ✅ `examples/claude/CLAUDE.md` | ✅ конфиг + доки (`agent_host: claude`) | [#14](https://github.com/svasenkov/greedy-token/issues/14) |
| **VS Code + Continue** | ✅ | ✅ `examples/continue/continuerules.md` | ✅ конфиг + доки (`agent_host: continue`) | [#15](https://github.com/svasenkov/greedy-token/issues/15) |
| **Только CLI** | — | — | ✅ | — |

Acceptance: гайды `docs/claude-setup(-RU).md` / `docs/continue-setup(-RU).md`; конфиг `agent_host: cursor|claude|continue` — `audit-context` и базлайн наивного чата считают always-on правила хоста (`CLAUDE.md`, `.continuerules`); live-smoke на реальных хостах — 🔜 (ручной чеклист).

## CI / headless

Сценарий: компания уже жжёт Claude/Cursor в пайплайнах; на своих серверах поднимает Ollama (или openai_compat) — greedy-token в job маршрутизирует часть задач на локальную LLM.

```text
CI job → greedy-token CLI → rg | python | cheap_llm (Ollama/internal) | RAG | expensive_llm agent (opt-in)
```

Это **не MCP внутри Actions**, а headless CLI (`route`, `pipeline --execute`, `report`). Remote `OLLAMA_URL` работает; доки, примерные workflows и env-контракт runner’а — в [ci-setup-RU.md](ci-setup-RU.md).

| Хост CI | Роль | Статус | Issue |
|---------|------|:------:|-------|
| **Self-hosted / VPN runner** + Ollama внутри сети | Основной целевой паттерн | ✅ доки + env-контракт · 🔜 live smoke | [#18](https://github.com/svasenkov/greedy-token/issues/18) |
| **GitHub-hosted ephemeral** без доступа к private Ollama | Только rg/python/rag или expensive_llm agent | вне фокуса | — |
| **Jenkins / GitLab CI** | Тот же CLI-контракт | ✅ сниппеты в [ci-setup-RU.md](ci-setup-RU.md) | [#18](https://github.com/svasenkov/greedy-token/issues/18) |

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

Детали по релизам — чеклисты `CUT-v*.md` в корне репозитория.

| Версия | Фокус |
|--------|-------|
| **v0.10.0** | Beyond-Cursor: `agent_host: cursor \| claude \| continue`, metered bulk APIs под spend guard ([ADR-0002](adr/0002-metered-bulk-cheap-tier.md)), nudge-и калибровки, team route presets (`init --preset`) |
| **v0.9.0** | Единый реестр моделей ([ADR-0001](adr/0001-unified-model-spec-derived-tier.md), выводимый tier cheap/expensive), реестр эквивалентных мутантов с drift-guard, MCP `greedy_token_crystallize` |
| **v0.8.0** | Кристаллизация L3 safe mode (`draft` → shadow → `promote` / `reject`), portable routes, `calibrate` (provenance базлайна), confidence по телеметрии |
| **v0.7.x** | Качество маршрутов: `explain_route`, атрибуция override, `init --profile`, метрики hub; v0.7.2 — mutation testing, маскирование секретов, doc-drift guard |
| **v0.6.x** | Crystallize L2 (`override`, `scripts lint`, shadow routes), `hub serve`, `doctor`, split budget, usage.jsonl v2 `billing` |
| **v0.5.9** | Multi-model registry, profiles, `llm invoke`, YandexGPT opt-in MVP, escalation, model presets (`config --init --preset`) |
| **v0.5.0** | `cheap_llm` provider (`ollama` \| `openai_compat`); tier id `ollama` без переименования; `OLLAMA_*` compat |
| **v0.4.x** | MCP pipeline, token economy footer, security hardening, Cursor starter kit + setup-доки |
