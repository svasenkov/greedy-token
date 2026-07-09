# greedy-token

**English version:** [README.md](README.md)

<img src="docs/greedy-cat.gif" alt="талисман greedy-token" width="240" />

Вы работаете в **Cursor** — greedy-token стоит рядом с агентом (CLI + MCP), чтобы повседневные задачи не всегда открывали полный agent chat.

Маршрутизирует задачу на **самый дешёвый подходящий tier** (`tool` → `python` → `ollama` → `rag` → `cursor`; побеждает первый match по паттерну). **Pipeline** — цепочка из нескольких tier’ов в одном вызове. **Cursor agent chat** — только если дешевле маршрута нет. В каждом ответе — footer **Token economy** относительно наивного полного чата.

```
В Cursor:  задача  →  greedy-token (MCP/CLI)
                 ↓
     route (один tier на задачу):
       tool → python → ollama → rag → cursor
       первый match в routes.yaml; tier ollama пропускается, если сервер недоступен
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
| **ollama** | bulk classify, audit skill | только локально |
| **rag** | lookup в `docs/rag/` | маленький read |
| **cursor** | wiring, refactor | полный agent chat |

**Порядок tier:** `TIER_ORDER` в `router.py` / `routes.yaml` — обход `tool → python → ollama → rag → cursor`, побеждает первый match. Не каждый tier выполняется на каждой задаче. Tier `ollama` пропускается, если сервер недоступен.

## Охват и roadmap

Сейчас основной сценарий — **Cursor + Ollama + monorepo**. CLI и MCP не привязаны к IDE; платные API и альтернативные локальные runtime пока **не подключены**.

**Полная матрица (✅ / ❌ / 🔜) + критерии + GitHub issues:** [docs/ROADMAP-RU.md](docs/ROADMAP-RU.md) · [docs/ROADMAP.md](docs/ROADMAP.md)

| Зона | ✅ сейчас | 🔜 v0.5+ |
|------|-----------|----------|
| Executors | `tool`, `python`, `ollama`, `rag` | `cloud_llm`, `openai_compat` local |
| Agent host | Cursor MCP + token baseline | Claude Desktop, Continue |
| Конфиг | `OLLAMA_URL` / `OLLAMA_MODEL` | `local_llm.provider`, `cloud_llm.provider` |

## Установка

**Python 3.12+** (CI и сборки PyPI — 3.12).

```bash
pip install greedy-token
# с MCP для Cursor:
pip install "greedy-token[mcp]"
# editable (monorepo):
cd projects/greedy-token-home/dev && ./scripts/install.sh
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
| [`examples/cursor/rules/token-economy.mdc`](examples/cursor/rules/token-economy.mdc) | `.cursor/rules/token-economy.mdc` |

```bash
pip install "greedy-token[mcp]"
mkdir -p .cursor/rules
# из клона greedy-token или вставьте из доки:
cp examples/cursor/mcp.json .cursor/mcp.json
cp examples/cursor/rules/token-economy.mdc .cursor/rules/token-economy.mdc
```

Далее: **Settings → MCP → greedy-token → Enable → Refresh** → **новый** Agent chat.

Должно быть **5 MCP tools**, включая `greedy_token_pipeline`.

## MCP tools

| Tool | Назначение |
|------|------------|
| `greedy_token_search` | Ripgrep: `query` + опционально `path` |
| `greedy_token_rag` | Поиск по `docs/rag/` |
| `greedy_token_route` | Куда нести задачу + token footer |
| `greedy_token_pipeline` | Цепочка python → ollama → rag |
| `greedy_token_usage` | Сводка экономии из `~/.greedy-token/usage.jsonl` |

Каждый ответ tool заканчивается блоком **Token economy** — показывай его пользователю.

### Pipeline (несколько шагов)

```text
pipeline: meta-audit configurator-boolean
```

или:

```text
pipeline: check-meta-sync then audit-skill configurator-boolean
```

Именованные рецепты (`greedy-token pipeline --list`):

| Рецепт | Шаги |
|--------|------|
| `meta-audit` | python → ollama |
| `meta-rag` | python → rag |
| `search-rag` | rg → rag |

Footer с **таблицей экономии по шагам**:

```text
Per-step savings (if each step were a separate naive Cursor chat):
   #  step                   executor     ms   spent  baseline     saved  billing
   1  check-meta-sync        python       83       0     9,487     9,487  local script
   2  audit-skill            ollama     2698   2,507     9,499     6,992  local Ollama

Saved by executor (sum of per-step savings):
  python (script)              steps=1  spent ~0      saved ~9,487
  ollama (local LLM)           steps=1  spent ~2,507  saved ~6,992
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

> **Pipeline execute:** MCP `greedy_token_pipeline` и CLI `greedy-token pipeline` по умолчанию **dry-run**. Для запуска allowlisted шагов: `execute=true` (MCP) или `--execute` (CLI).

## Тесты

Нужен **Python 3.12+** (как в CI). GitHub Actions: **pytest + Allure 3** (quality gate, отчёт на GitHub Pages; upload в TestOps при наличии `ALLURE_TOKEN`).

**CI ethalon:** `.github/_ethalon/` (пины actions в `gha-actions.yaml`) → runnable `.github/workflows/`. Тот же паттерн, что `tests-java/.github/_ethalon/` в monorepo. Sync: `./scripts/sync-github-workflows.sh`; в CI перед pytest — `./scripts/check-github-workflows-sync.sh`.

**TestOps:** проект [5276](https://allure.autotests.cloud/project/5276). Секрет `ALLURE_TOKEN` в настройках репо; `ALLURE_PROJECT_ID` по умолчанию `5276`.

```bash
cd projects/greedy-token-home/dev && ./scripts/install.sh
source .venv/bin/activate
cd ../greedy-token
python -m coverage run -m pytest tests/ -v --alluredir=build/allure-results
python -m coverage report --include='src/greedy_token/*'
npx --yes allure@3.13.0 quality-gate build/allure-results --config allurerc.mjs
npx --yes allure@3.13.0 generate build/allure-results --config allurerc.mjs -o build/allure-report
```

**Coverage:** `branch = true` и `fail_under = 100` для `src/greedy_token/` (`pyproject.toml`). CI: `coverage run` + `coverage report` (lines + branches).

**Слайсы пирамиды:** модуль → `tests/pyramid_layers.py` → Allure label `layer` + pytest marker (`-m unit|component|integration|e2e`). В CI matrix job `pyramid` гоняет каждый слой отдельно.

Интеграционные тесты (реальные файлы monorepo) запускаются, если в checkout есть `stacks/java-spring/`. `GREEDY_TOKEN_ROOT` переопределяет корень workspace.

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

- **Executor (rg/python/ollama)** — локально, ~0 cloud LLM на этот шаг
- **Saved vs naive Cursor chat** — оценка greedy-token (tiktoken), не биллинг Cursor API
- **Agent chat** (rules + ваше сообщение + ответ) — всё равно тратит Cursor tokens

## Телеметрия

Файл: `~/.greedy-token/usage.jsonl` · отключить: `GREEDY_TOKEN_LOG=0`

Pipeline пишет **одну строку на каждый шаг**. При превышении `GREEDY_TOKEN_LOG_MAX_BYTES` (default 5 MiB) лог ротируется в `usage.jsonl.1`, `.2`, …; `report` читает активный файл и архивы.

## Переменные окружения

| Var | Default |
|-----|---------|
| `GREEDY_TOKEN_ROOT` | auto-detect |
| `OLLAMA_URL` | из config или `http://localhost:11434` |
| `OLLAMA_MODEL` | из config или `qwen2.5-coder:7b-instruct-q4_K_M` |
| `GREEDY_TOKEN_LOG` | `~/.greedy-token/usage.jsonl` |
| `GREEDY_TOKEN_LOG_MAX_BYTES` | `5242880` (5 MiB) |
| `GREEDY_TOKEN_LOG_MAX_FILES` | `5` rotated archives |

## Конфиг Ollama

Приоритет (низкий → высокий): defaults → `~/.greedy-token/config.yaml` → `$GREEDY_TOKEN_ROOT/.greedy-token.yaml` → `OLLAMA_*` env.

```bash
# Создать пользовательский конфиг
greedy-token config init
greedy-token config init --model llama3.2 --url http://192.168.1.10:11434

# Показать текущие значения
greedy-token config

# Экспорт для shell / scripts/ollama
eval "$(greedy-token config --export)"
```

Пример `~/.greedy-token/config.yaml`:

```yaml
ollama:
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
