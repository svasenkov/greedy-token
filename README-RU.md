# greedy-token

**English version:** [README.md](README.md)

<img src="docs/greedy-cat.gif" alt="талисман greedy-token" width="240" />

Вы работаете в **Cursor** — greedy-token стоит рядом с агентом (CLI + MCP), чтобы повседневные задачи не всегда открывали полный agent chat.

Маршрутизирует задачу на **самый дешёвый подходящий tier** (`tool` → `python` → `ollama` → `rag` → `cursor`; обход `TIER_ORDER`, лучший score паттерна в tier). **Pipeline** — цепочка из нескольких tier’ов в одном вызове. **Cursor agent chat** — только если дешевле маршрута нет. В каждом ответе — footer **Greedy token** относительно наивного полного чата.

[![greedy-token](https://svasenkov.github.io/greedy-token/readme/badge.svg)](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/)

<details>
<summary><strong>Дашборд автотестов</strong> — живые метрики + превью Allure 3</summary>

[![greedy-token stats](https://svasenkov.github.io/greedy-token/readme/stats.svg)](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/)

[![greedy-token metrics](https://svasenkov.github.io/greedy-token/readme/metrics-panel.svg)](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/)

<a href="https://svasenkov.github.io/greedy-token/reports/latest/dashboard/">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://svasenkov.github.io/greedy-token/readme/dashboard-preview-dark.png">
    <img
      src="https://svasenkov.github.io/greedy-token/readme/dashboard-preview.png"
      alt="Дашборд Allure 3 — pytest, динамика статусов"
      width="800"
    />
  </picture>
</a>

Бейджи и PNG дашборда обновляются после каждого прогона CI на `main` (скриншот дашборда Allure 3 через Playwright).

| Ссылка | Описание |
|--------|----------|
| [Dashboard](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/) | pytest MCP/CLI + контрактные тесты |
| [Awesome](https://svasenkov.github.io/greedy-token/reports/latest/awesome/) | Детализация по epic |
| [CI workflow](https://github.com/svasenkov/greedy-token/actions/workflows/test.yml) | pytest + публикация gh-pages |

</details>

```
В Cursor:  задача  →  greedy-token (MCP/CLI)
                 ↓
     route (один tier на задачу):
       tool → python → ollama → rag → cursor
       обход TIER_ORDER; лучший score паттерна в tier; ollama пропускается, если сервер недоступен
                 ↓
     pipeline (опционально, несколько шагов):
       напр. check-meta-sync then audit-skill …
       собирает шаги tool / python / ollama / rag — это не отдельный tier
                 ↓
     эскалация: Cursor agent chat, если дешевле маршрута нет
```

## Зачем

| Слой | Когда | Стоимость LLM |
|------|-------|---------------|
| **tool** (rg) | find / grep / search | ~0 |
| **python** | скрипты, meta-sync | ~0 |
| **ollama** | bulk classify, audit skill | cheap LLM |
| **rag** | lookup в `docs/rag/` | маленький read |
| **cursor** | wiring, refactor | expensive LLM |

### Cheap vs expensive LLM

В footer и доках — **cheap** / **expensive**. Речь о **куда уходит token budget**.

| Метка | Смысл | Примеры |
|-------|--------|---------|
| **Cheap LLM** | Inference на **вашем** runtime (config `cheap_llm`); tier id `ollama` в routes; **0 Cursor/API meter** на этом шаге | [Ollama](https://ollama.com) (native или remote `OLLAMA_URL`), LM Studio, llama.cpp, vLLM, TGI — через `cheap_llm.provider: ollama \| openai_compat` |
| **Expensive LLM** | Полный **agent chat**: rules, skills, overhead, ответ — за что платите Cursor (и аналоги) | **Cursor** agent / Composer сейчас; туда же **Claude**, **GPT**, **Copilot** как основной coding agent или будущий metered API `expensive_llm` |

**Free tier** (`tool`, `python`, `rag`) — без LLM inference: ripgrep, скрипты, чтение chunk’ов `docs/rag/`.

**Порядок tier:** `TIER_ORDER` в `router.py` / `routes.yaml` — обход `tool → python → ollama → rag → cursor`; внутри tier побеждает маршрут с наивысшим score паттерна (при равенстве — первый в config). Не каждый tier выполняется на каждой задаче. Cheap LLM tier пропускается, если runtime из config недоступен.

## Не дообучаем модели

greedy-token **не** дообучает (fine-tune) модели и не отправляет ваш код или usage-данные на обучение.

- Никакого gradient descent на usage data или overrides.
- «Обучение» здесь = новые детерминированные routes/scripts из телеметрии (`crystallize-report`) — читаемый, проверяемый и откатываемый код, а не веса модели.
- Телеметрия (`~/.greedy-token/usage.jsonl`) остаётся локальной и нужна только для отчётов об экономии; отключить — `GREEDY_TOKEN_LOG=0`.

## Охват и roadmap

Сейчас основной сценарий — **Cursor + Ollama + workspace**. CLI и MCP не привязаны к IDE. **v0.6.2** — coverage/CI harden + Allure palette SSOT = design-system tokens; наследует **v0.6.0** crystallize L2 (`script_override`, CLI `override`, `scripts lint`, shadow routes, `hub serve`, budget / llm invoke) и **v0.6.1** раздел «не дообучаем модели». **v0.5.8** — минимальный search: один `greedy_token_search` на find; docstrings MCP и шаблон cursor rule запрещают route/usage вместе с search. **v0.5.7** — SSOT версии из `pyproject.toml` (без hardcode в `__init__`), `./scripts/release-gate.sh TARGET`, auto-sync `minTestsCount` из pytest collection. **v0.5.6** — честный search footer, e2e MCP stdio `pipeline execute=true`, удалён мёртвый `SearchResult.spent_tokens`. **v0.5.5** — `config --init` без workspace (PyPI bootstrap), отказ `run --execute` на cursor tier, telemetry cheap_llm по workspace. **v0.5.3+** — честность pipeline: multi-word `search-rag`, dry-run footer (`saved=0`), RAG через `rag_est_tokens` (`cheap_llm.provider: ollama | openai_compat`). Paid agent APIs (`expensive_llm`) — opt-in / roadmap.

**Полная матрица (✅ / ❌ / 🔜) + критерии + GitHub issues:** [docs/ROADMAP-RU.md](docs/ROADMAP-RU.md) · [docs/ROADMAP.md](docs/ROADMAP.md)

| Зона | ✅ сейчас (v0.6.2) | 🔜 дальше |
|------|-------------------|-----------|
| Executors | `tool`, `python`, `ollama` (через `cheap_llm`), `rag` | paid bulk APIs; Crystal IR store |
| Agent host | Cursor MCP + token baseline | Claude Desktop, Continue |
| Конфиг | `cheap_llm.provider` + алиасы `OLLAMA_*` / `ollama:` | silent L3 auto-codegen (deferred) |

## Установка

**Python 3.12+** (CI и сборки PyPI — 3.12).

```bash
pip install greedy-token
# с MCP для Cursor:
pip install "greedy-token[mcp]"
# editable из этого clone:
pip install -e ".[dev,mcp]"
# monorepo hub (соседний ../dev):
#   cd ../dev && ./scripts/install.sh
```

```bash
export GREEDY_TOKEN_ROOT=/path/to/workspace   # опционально; авто-detect при наличии маркеров
```

## Интеграция с Cursor

**Полная инструкция (любой workspace / PyPI):** [docs/cursor-setup-RU.md](docs/cursor-setup-RU.md) · [docs/cursor-setup.md](docs/cursor-setup.md)

Starter kit в этом репозитории (скопируйте в свой проект):

| Шаблон | Куда |
|--------|------|
| [`examples/cursor/mcp.json`](examples/cursor/mcp.json) | `.cursor/mcp.json` |
| [`examples/cursor/rules/greedy-token.mdc`](examples/cursor/rules/greedy-token.mdc) | `.cursor/rules/greedy-token.mdc` |

```bash
pip install "greedy-token[mcp]"
mkdir -p .cursor/rules
# из клона greedy-token или вставьте из доки:
cp examples/cursor/mcp.json .cursor/mcp.json
cp examples/cursor/rules/greedy-token.mdc .cursor/rules/greedy-token.mdc
```

Далее: **Settings → MCP → greedy-token → Enable → Refresh** → **новый** Agent chat.

Должно быть **5 MCP tools**, включая `greedy_token_pipeline`.

## MCP tools

| Tool | Назначение |
|------|------------|
| `greedy_token_search` | Ripgrep: `query` + опционально `path` |
| `greedy_token_rag` | Поиск по `docs/rag/` |
| `greedy_token_route` | Куда нести задачу + token footer |
| `greedy_token_pipeline` | Цепочка search/tool → python → ollama → rag |
| `greedy_token_usage` | Сводка экономии из `~/.greedy-token/usage.jsonl` |

**Footers:** `route` / `search` / `rag` / `pipeline` — полный блок **Greedy token** (This call → Tier alternatives → Saved). `usage` — **Session totals** (не полный single-tool footer). `pipeline: list` — только список рецептов, без economy footer.

### Pipeline (несколько шагов)

```text
pipeline: meta-audit configurator-boolean
```

или:

```text
pipeline: check-meta-sync then audit-skill configurator-boolean
```

Именованные рецепты (`greedy-token pipeline --list`):

| Рецепт | Шаги | Аргументы |
|--------|------|-----------|
| `meta-audit` | python → ollama | `<skill>` |
| `meta-rag` | python → rag | `<query>` |
| `search-rag` | rg → rag | `<query> <path>` · multi-word query + `path=` · или kwargs `query=` / `path=` |

`search-rag` переиспользует `query` для обоих шагов; `path` только для ripgrep:

```text
pipeline: search-rag baseUrl configurator-option-presets.html
pipeline: search-rag baseUrl path=configurator-option-presets.html
```

Footer с **таблицей экономии по шагам**:

```text
Per-step savings (if each step were a separate naive Cursor chat):
   #  step                   executor     ms   spent  baseline     saved  billing
   1  check-meta-sync        python       83       0     9,487     9,487  script
   2  audit-skill            ollama     2698   2,507     9,499     6,992  cheap LLM

Saved by executor (sum of per-step savings):
  python (script)              steps=1  spent ~0      saved ~9,487
  ollama (cheap LLM)           steps=1  spent ~2,507  saved ~6,992
```

| Колонка | Смысл |
|---------|--------|
| **baseline** | сколько съел бы отдельный naive Cursor-чат для этого шага |
| **spent** | сколько потратили реально |
| **saved** | baseline − spent на шаге |

## CLI

| Команда | Назначение |
|---------|------------|
| `greedy-token route "…"` | Рекомендация tier |
| `greedy-token estimate "…"` | Оценка + tier scan |
| `greedy-token run "…" [--execute]` | Route + dry-run / read-only |
| `greedy-token pipeline "…" [--execute]` | Pipeline |
| `greedy-token pipeline --list` | Список рецептов |
| `greedy-token rag QUERY` | RAG lookup |
| `greedy-token scripts --list` | Workspace script wrappers |
| `greedy-token scripts --run ID [--execute]` | Run wrapper |
| `greedy-token audit-context` | Rules/skills token audit |
| `greedy-token tokens PATH…` | Count tokens in paths |
| `greedy-token compress` | Short prompt (stdin; `--ollama`) |
| `greedy-token report [--since 7d]` | Usage telemetry aggregate |
| `greedy-token config [--init] [--export]` | Ollama URL/model |
| `greedy-token-mcp` | MCP server (stdio) |

Флаг `--no-log` отключает запись в log на один вызов.

**Pipeline execute:** MCP `greedy_token_pipeline` и CLI `greedy-token pipeline` по умолчанию **dry-run**. Для запуска allowlisted шагов: `execute=true` (MCP) или `--execute` (CLI).

## Тесты

Нужен **Python 3.12+** (как в CI). GitHub Actions: job **tests (all)** — полный прогон, Allure 3 quality gate, отчёт на GitHub Pages; upload в TestOps при наличии `ALLURE_TOKEN`.

**CI ethalon:** `.github/_ethalon/` (пины actions в `gha-actions.yaml`) → runnable `.github/workflows/`. Тот же паттерн, что `tests-java/.github/_ethalon/` в workspace. Sync: `./scripts/sync-github-workflows.sh`; в CI перед pytest — `./scripts/check-github-workflows-sync.sh`.

**TestOps:** проект [5276](https://allure.autotests.cloud/project/5276). Секрет `ALLURE_TOKEN` в настройках репо; `ALLURE_PROJECT_ID` по умолчанию `5276`.

```bash
# из этого clone (после pip install -e ".[dev,mcp]"):
python -m coverage run -m pytest tests/ -v --alluredir=build/allure-results
python -m coverage report --include='src/greedy_token/*'
npx --yes allure@3.13.0 quality-gate build/allure-results --config allurerc.mjs
npx --yes allure@3.13.0 generate build/allure-results --config allurerc.mjs -o build/allure-report
# monorepo hub: cd ../dev && ./scripts/install.sh && source .venv/bin/activate && cd ../greedy-token
```

**Coverage:** `branch = true` и `fail_under = 100` для `src/greedy_token/` (`pyproject.toml`). CI: `coverage run` + `coverage report` (lines + branches).

**Слайсы по layer:** модуль → `tests/pyramid_layers.py` → Allure label `layer` + pytest marker (`-m unit|component|integration|e2e`). В CI matrix job `tests` гоняет каждый слой отдельно.

Интеграционные тесты (реальные файлы workspace) запускаются, если в checkout есть `stacks/java-spring/`. `GREEDY_TOKEN_ROOT` переопределяет корень workspace.

Человекочитаемые имена в TestOps — `@allure.title` / `@feature` / `@story` / `@epic` на каждом тесте, `@allure.parent_suite` / `@allure.suite` на модуле (`pytestmark`).

## Примеры

```bash
# Поиск (0 LLM)
greedy-token run "find baseUrl in configurator-option-presets.html" --execute

# RAG
greedy-token rag "какой -D flag для baseUrl"

# Ollama tier
greedy-token route "audit skill configurator-boolean"

# Pipeline dry-run
greedy-token pipeline "pipeline: meta-audit configurator-boolean"

# Pipeline execute
greedy-token pipeline "check-meta-sync then audit-skill configurator-boolean" --execute

# Отчёт
greedy-token report --since 7d
```

## Token economy — что значит «сэкономили»

- **Executor (rg/python/rag)** — free tier, 0 LLM spend на этот шаг (`search` в pipeline → `rg`)
- **Executor (ollama)** — cheap LLM
- **Tier alternatives** — строка `← this call` = фактический Spent этого вызова
- **Saved vs naive Cursor chat** — оценка greedy-token (tiktoken), не биллинг Cursor API
- **Agent chat** — expensive LLM (rules + ваше сообщение + ответ)
- **Исключения footer:** `usage` → Session totals; `pipeline: list` → только рецепты

## Телеметрия

Файл: `~/.greedy-token/usage.jsonl` · отключить: `GREEDY_TOKEN_LOG=0`

Pipeline пишет **одну строку на каждый шаг**. При превышении `GREEDY_TOKEN_LOG_MAX_BYTES` (default 5 MiB) лог ротируется в `usage.jsonl.1`, `.2`, …; `report` читает активный файл и архивы.

## Переменные окружения

| Var | Default |
|-----|---------|
| `GREEDY_TOKEN_ROOT` | auto-detect |
| `CHEAP_LLM_PROVIDER` | из config или `ollama` (`ollama` \| `openai_compat`) |
| `CHEAP_LLM_URL` / `OLLAMA_URL` | из config или `http://localhost:11434` |
| `CHEAP_LLM_MODEL` / `OLLAMA_MODEL` | из config или `qwen2.5-coder:7b-instruct-q4_K_M` |
| `GREEDY_TOKEN_LOG` | `~/.greedy-token/usage.jsonl` |
| `GREEDY_TOKEN_LOG_MAX_BYTES` | `5242880` (5 MiB) |
| `GREEDY_TOKEN_LOG_MAX_FILES` | `5` rotated archives |

## Конфиг cheap LLM

Приоритет (низкий → высокий): defaults → `~/.greedy-token/config.yaml` → `$GREEDY_TOKEN_ROOT/.greedy-token.yaml` → `CHEAP_LLM_*` / `OLLAMA_*` env (`OLLAMA_*` = алиасы url/model). Tier id в routes — по-прежнему `ollama`.

```bash
# Создать пользовательский конфиг
greedy-token config --init
greedy-token config --init --provider openai_compat --url http://localhost:1234 --model local-model

# Показать текущие значения
greedy-token config

# Экспорт для shell / scripts/ollama
eval "$(greedy-token config --export)"
```

Пример `~/.greedy-token/config.yaml`:

```yaml
cheap_llm:
  provider: ollama          # или openai_compat
  url: http://localhost:11434
  model: qwen2.5-coder:7b-instruct-q4_K_M
```

Проектный override (опционально): `.greedy-token.yaml` в корне workspace.

## Конфиг маршрутизации

| Файл | Назначение |
|------|------------|
| `src/greedy_token/config/routes.yaml` | Паттерны маршрутизации |
| `src/greedy_token/config/pipelines.yaml` | Именованные pipeline |

## Безопасность `--execute`

Авто-запуск: read-only шаги (rg, check-meta-sync, pipeline allowlist).

Rsync / migrate / batch-inventory — только dry-run из pipeline.

## Лицензия

MIT
