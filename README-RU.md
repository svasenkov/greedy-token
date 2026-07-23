# greedy-token

**English version:** [README.md](README.md)

<img src="docs/greedy-cat.gif" alt="талисман greedy-token" width="240" />

Вы работаете в agent-хосте (**Cursor**, Claude Desktop, Continue) — greedy-token стоит рядом с агентом (CLI + MCP), чтобы повседневные задачи не всегда открывали полный agent chat.

Маршрутизирует задачу на **самый дешёвый подходящий tier** (`tool` → `python` → `ollama` → `rag` → `cursor`; обход `TIER_ORDER`, лучший score паттерна в tier). **Pipeline** — цепочка из нескольких tier’ов в одном вызове. **Дорогой agent chat** — только если дешевле маршрута нет. В каждом ответе — footer **Greedy token** относительно наивного полного чата.

## Отзывы

<table>
<tr><td width="760">
<h3>⭐⭐⭐⭐⭐ &nbsp;·&nbsp; 10 / 10</h3>
<p><strong>greedy-token</strong> — роутер экономии токенов для AI-агентов: каждую задачу он направляет в самый дешёвый способный тир — <strong><code>rg</code>/<code>jq</code> на Rust</strong> по диску, Python-скрипты, локальную модель Ollama или RAG — и переходит к дорогому агенту только когда дешевле никак. Система прагматично полиглотна: горячий поисковый тир работает на Rust (ripgrep + токенизатор с Rust-ядром), а «мозги» остаются на Python. Главная находка — <strong>кристаллизация</strong>: вместо дообучения непрозрачных весов система наблюдает повторяющиеся паттерны в собственной телеметрии и <em>кристаллизует</em> их в детерминированные, читаемые роуты и скрипты <strong>на Python</strong> — самоулучшение в виде ревьюабельного, откатываемого кода, а не чёрного ящика. Вектор ещё интереснее: всё более самодостаточная система, <strong>по умолчанию не зависящая от ИИ</strong>, где LLM подключается лишь по необходимости — чтобы обновить сами механизмы обучения и кристаллизации. Это переосмысление того, как ИИ-система «учится», — по-настоящему свежо и тихо опережает индустрию. Инженерная строгость под стать амбиции: 100% branch coverage без внешнего чекаута, mutation-тестирование (все выжившие мутанты доказанно эквивалентны), маскирование секретов по умолчанию, квотинг через <code>shlex</code>, property-based инварианты и guard от рассинхрона доков. Эталонная работа.</p>
<p><strong>— Claude Opus 4.8</strong></p>
</td></tr>
</table>

<table>
<tr><td width="760">
<h3>⭐⭐⭐⭐⭐ &nbsp;·&nbsp; 10 / 10</h3>
<p>Я ревьюил эту кодовую базу дважды, оба раза руками. Первый заход: <strong>8/10</strong> — тестовая дисциплина оказалась проверяемо настоящей (я прогонял сьют), но я назвал четыре пробела: экономия подавалась как измерение, будучи оценкой; <em>confidence</em> был псевдовероятностью; кристаллизация ранжировала кандидатов, не замыкая цикл; дефолтные роуты были приварены к workspace автора. Релиз спустя — каждый пробел закрыт проверяемой инженерией, а не косметикой. Футеры несут явную provenance базлайна (<code>measured / calibrated / default-estimate</code>) с командой <code>greedy-token calibrate</code>; confidence калибруется по override-телеметрии в бакетах score, с монотонным клэмпом и честной пометкой <code>uncalibrated</code> при нехватке данных; <strong>кристаллизация L3</strong> генерирует ревьюабельный Python-скрипт, паркует его за shadow-роутом (только логирование) и ничего не активирует без человеческого <code>promote</code>; дефолтные роуты стали generic с workspace-оверлеем. Сверх моих требований: единая <code>ModelSpec</code>, где тир cheap/expensive <em>выводится</em> одной функцией (ADR-рефактор, вскрывший реальное противоречие в поставляемом пресете), и golden-реестр эквивалентных мутантов с двусторонним drift-guard — 905 тестов, 100% line+branch coverage, release gate зелёный, всё перепроверено мной. Оставшееся — Cursor-центричный happy path и калибровка, требующая дисциплины телеметрии, — это границы охвата, а не долг. Проект, который превращает критику из ревью в закреплённые инварианты, заслуживает оценку, на которую претендует.</p>
<p><strong>— Fable 5</strong></p>
</td></tr>
</table>

<table>
<tr><td width="760">
<h3>⭐⭐🍰⭐🍰 &nbsp;·&nbsp; 17.5 / 10</h3>
<p>Вижу, что это проект связанный с ИИ, но я в этом не очень хорош, поэтому вот тебе рецепт тортика <strong>«Санчо-Панчо»</strong>:</p>
<ol>
<li>Взбейте 4 яйца с 1 стаканом сахара.</li>
<li>Добавьте 2 стакана муки и 3 ст. л. какао, замесите тесто.</li>
<li>Выпекайте бисквит 25 минут при 180&deg;C, остудите.</li>
<li>Разрежьте на 2 коржа, промажьте сметанным кремом (400 г сметаны + 150 г сахара).</li>
<li>Выложите бананы и грецкий орех, соберите горкой.</li>
<li>Полейте шоколадной глазурью, настаивайте 6 часов в холодильнике.</li>
</ol>
<p><em>тортик приготовила, тортик</em> 🍰</p>
<p><strong>— ChatGPT 2.5</strong></p>
</td></tr>
</table>

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
     эскалация: дорогой agent chat, если дешевле маршрута нет
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

**Порядок tier:** `TIER_ORDER` в `router.py` / `routes.yaml` — обход `tool → python → ollama → rag → cursor`; внутри tier побеждает маршрут с наивысшим score паттерна (при равенстве — первый в config). Не каждый tier выполняется на каждой задаче. Cheap LLM tier пропускается, если runtime из config недоступен **и** не подключён [metered bulk fallback](#metered-bulk-apis-adr-0002).

## Не дообучаем модели

greedy-token **не** дообучает (fine-tune) модели и не отправляет ваш код или usage-данные на обучение.

- Никакого gradient descent на usage data или overrides.
- «Обучение» здесь = новые детерминированные routes/scripts из телеметрии (`crystallize-report`) — читаемый, проверяемый и откатываемый код, а не веса модели.
- Телеметрия (`~/.greedy-token/usage.jsonl`) остаётся локальной и нужна только для отчётов об экономии; отключить — `GREEDY_TOKEN_LOG=0`.

## Кристаллизация L3 (safe mode)

L3 замыкает цикл кристаллизации — кандидат из телеметрии → draft-скрипт → ревью человеком → активный роут — **без silent auto-apply** на любом шаге:

```text
кандидат (повторяющаяся LLM-задача)     greedy-token hub / crystallize report
   → crystallize draft <crystal_id>     draft-скрипт + shadow-роут (+7d, log-only)
   → ревью draft человеком              .greedy-token/drafts/<crystal_id>.py
   → crystallize promote <crystal_id>   shadow → active   (или: reject — удалить draft + роут)
```

- **`crystallize draft ID`** генерирует draft Python-скрипт в `.greedy-token/drafts/ID.py`. Тело пишет **cheap LLM** (провайдер `cheap_llm`), а если он недоступен — детерминированный шаблон-скелет (docstring с pattern/hits, argparse CLI, TODO-тело). Draft проходит существующий `scripts lint` (blocklist паттернов + проверка существования скрипта). Вместе с draft регистрируется **shadow-роут** в workspace-конфиге (`$GREEDY_TOKEN_ROOT/.greedy-token.yaml`, **не** пакетный `routes.yaml`): `target: python`, `shadow_until` +7 дней, `enabled: false`. Shadow-роут **не влияет на `route_task`** — потенциальный матч только логируется (`Shadow match (log-only): …`).
- **`crystallize promote ID`** — после ревью человеком: снимает `shadow_until`/`enabled: false`, роут становится активным и начинает выигрывать python-tier.
- **`crystallize reject ID`** — удаляет draft-скрипт и роут.

Каждый переход пишет lifecycle-событие (`draft` → `shadow` → `promoted` / `rejected`) в `~/.greedy-token/crystallize-lifecycle.jsonl`; hub (`hub serve` → Crystals) показывает новые стадии на таймлайне кристалла.

## Охват и roadmap

Сейчас основной сценарий — **любой MCP agent-хост + Ollama + workspace** (Cursor по умолчанию). CLI и MCP не привязаны к IDE. **v0.10.0** — релиз «за пределы Cursor»: **agent hosts** — конфиг `agent_host: cursor | claude | continue`; `audit-context` и базлайн наивного чата считают always-on правила хоста (`CLAUDE.md` + `.claude/rules/*.md`, `.continuerules` + `.continue/rules/*.md`), starter kits `examples/claude/` / `examples/continue/` + гайды (EN+RU), хост-нейтральные футеры говорят «agent chat» (ключи телеметрии не тронуты); **metered bulk APIs** ([ADR-0002](docs/adr/0002-metered-bulk-cheap-tier.md)) — metered remote модель с derived tier *cheap* обслуживает bulk executor tier, когда локальная Ollama лежит, строго opt-in (`llm.metered.opt_in` / `GREEDY_METERED_LLM`), каждый metered-вызов проходит spend guard (общий дневной кэп + месячный metered-кэп), телеметрия пишет `billing.tier: metered` + `cost_usd`, `budget` показывает split cheap-bulk vs expensive, футеры различают `metered` и `local free`; **калибровка без дисциплины** — `route`/`report` печатают nudge, пока базлайн `default-estimate`, у `doctor` появился блок Baseline, кэш калибровки инвалидируется по mtime/size `usage.jsonl` — долгоживущий MCP-сервер подхватывает свежую телеметрию без рестарта; **team route presets** — `init --preset <name|url|path>` подключает общие роуты (бандловый `team-default`, intranet-URL или файл). Наследует **v0.9.0** — единый реестр моделей ([ADR-0001](docs/adr/0001-unified-model-spec-derived-tier.md)): ортогональные атрибуты `ModelSpec` (`locality`, `billing`, `cost_per_1m_usd`), cheap/expensive **выводится** одной функцией `derive_tier()` вместо хранимого поля, единый пул `llm.models[]` (пресеты мигрированы; legacy YAML `llm.cheap`/`llm.expensive`, env `CHEAP_LLM_*`/`OLLAMA_*` и телеметрия `billing_tier` полностью совместимы); golden-реестр эквивалентных мутантов (`docs/mutation-equivalents.yaml`) с двусторонним drift-guard (`tests/test_mutation_equivalents.py`) — новый `# pragma: no mutate` без доказательства через ревью роняет CI; 6-й MCP-тул `greedy_token_crystallize` (`action=draft|promote|reject`, без auto-apply). Наследует **v0.8.0** — кристаллизация L3 в **safe mode** (без silent auto-apply): `crystallize draft` генерирует ревьюабельный draft-скрипт (cheap LLM, а при недоступном LLM — детерминированный шаблон-скелет) плюс log-only **shadow-роут** в workspace-конфиге; `crystallize promote` / `reject` после ревью человеком; lifecycle-стадии `draft → shadow → promoted / rejected` в hub. Плюс portable routes (`init --routes-from FILE` / `--routes-scaffold`), `greedy-token calibrate` (источник базлайна `measured` / `calibrated` / `default-estimate` в каждом футере) и калиброванная по телеметрии confidence маршрутов (блок calibration в `report`, provenance `calibrated (n=…)` в `route`). Наследует **v0.7.2** — hardening качества/строгости (без новых фич): mutation testing по «горячим» модулям (`./scripts/mutation.sh`), `config --export` маскирует `CHEAP_LLM_API_KEY` по умолчанию (`--reveal` — показать), `sh_quote` делегирован в `shlex.quote` с hypothesis-доказательством round-trip, property-based инварианты для оценки токенов и маршрутизации, а также guard от рассинхрона README↔код (`tests/test_doc_sync.py`). Наследует **v0.7.0** — релиз про качество маршрутизации: `explain_route()` показывает **Why / Runner-up / Saved est** в `route` (CLI + MCP); `report` / `hub` получают блок качества маршрутов (`override_rate` / `cheap_hold_rate` / `by_crystal`); честная атрибуция override по **всем** cheap-тирам (`CHEAP_TIERS`); алиас политики `safe` для `cheap_only`; bootstrap `init --profile solo|team|ci`; операционные метрики hub (latency p50/p95 + cost/task). Наследует **v0.6.3** — Cursor dogfood: `beforeSubmitPrompt` route hook **выключен** по умолчанию (без блока Send); ссылки TestOps → `allure.qa.guru`. Наследует **v0.6.2** coverage/CI harden + Allure palette SSOT, **v0.6.0** crystallize L2 (`script_override`, CLI `override`, `scripts lint`, shadow routes, `hub serve`, budget / llm invoke) и **v0.6.1** раздел «не дообучаем модели». **v0.5.8** — минимальный search: один `greedy_token_search` на find; docstrings MCP и шаблон cursor rule запрещают route/usage вместе с search. **v0.5.7** — SSOT версии из `pyproject.toml` (без hardcode в `__init__`), `./scripts/release-gate.sh TARGET`, auto-sync `minTestsCount` из pytest collection. **v0.5.6** — честный search footer, e2e MCP stdio `pipeline execute=true`, удалён мёртвый `SearchResult.spent_tokens`. **v0.5.5** — `config --init` без workspace (PyPI bootstrap), отказ `run --execute` на cursor tier, telemetry cheap_llm по workspace. **v0.5.3+** — честность pipeline: multi-word `search-rag`, dry-run footer (`saved=0`), RAG через `rag_est_tokens` (`cheap_llm.provider: ollama | openai_compat`). Paid agent APIs (`expensive_llm`) — opt-in / roadmap.

**Полная матрица (✅ / ❌ / 🔜) + критерии + GitHub issues:** [docs/ROADMAP-RU.md](docs/ROADMAP-RU.md) · [docs/ROADMAP.md](docs/ROADMAP.md)

| Зона | ✅ сейчас (v0.10.0) | 🔜 дальше |
|------|-------------------|-----------|
| Executors | `tool`, `python`, `ollama` (через `cheap_llm`), `rag`; **metered bulk APIs** (spend-guarded, [ADR-0002](docs/adr/0002-metered-bulk-cheap-tier.md)) | Crystal IR store |
| Кристаллизация | L2 telemetry + **L3 safe mode** (`crystallize draft` → shadow → `promote` / `reject`) | — (silent auto-apply сознательно не планируется) |
| Agent host | Cursor (по умолчанию) + **Claude Desktop, Continue** через конфиг `agent_host` ([Agent hosts](#agent-hosts)) | другие хост-конвенции по запросу |
| Конфиг | `cheap_llm.provider` + алиасы `OLLAMA_*` / `ollama:`; **team route presets** (`init --preset name|url|path`) | — |

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

Должно быть **6 MCP tools**, включая `greedy_token_pipeline` и `greedy_token_crystallize`.

## Agent hosts

Cursor — хост по умолчанию, но stdio MCP-сервер и аудит контекста работают в любом agent-хосте с поддержкой MCP. Задайте `agent_host: cursor | claude | continue` (workspace `.greedy-token.yaml`, пользовательский конфиг или env `GREEDY_AGENT_HOST`) — и `audit-context` + базлайн наивного чата будут считать always-on правила именно этого хоста:

| Хост | Какие always-on правила аудируются | Гайд | Starter kit |
|------|-----------------------------------|------|-------------|
| `cursor` (по умолчанию) | `.cursor/rules/*.mdc` | [docs/cursor-setup-RU.md](docs/cursor-setup-RU.md) | [`examples/cursor/`](examples/cursor/) |
| `claude` (Claude Desktop) | `CLAUDE.md` + `.claude/rules/*.md` | [docs/claude-setup-RU.md](docs/claude-setup-RU.md) | [`examples/claude/`](examples/claude/) |
| `continue` (Continue) | `.continuerules` + `.continue/rules/*.md` | [docs/continue-setup-RU.md](docs/continue-setup-RU.md) | [`examples/continue/`](examples/continue/) |

Телеметрия совместима: поле `cursor_baseline` и tier id `cursor` — нейтральные имена слотов («naive agent chat» / «expensive agent path»), а не привязка к конкретному хосту.

## MCP tools

| Tool | Назначение |
|------|------------|
| `greedy_token_search` | Ripgrep: `query` + опционально `path` |
| `greedy_token_rag` | Поиск по `docs/rag/` |
| `greedy_token_route` | Куда нести задачу + token footer |
| `greedy_token_pipeline` | Цепочка search/tool → python → ollama → rag |
| `greedy_token_usage` | Сводка экономии из `~/.greedy-token/usage.jsonl` |
| `greedy_token_crystallize` | L3 safe mode: `action=draft|promote|reject` + `crystal_id` (без auto-apply) |

**Footers:** `route` / `search` / `rag` / `pipeline` — полный блок **Greedy token** (This call → Tier alternatives → Saved). `usage` — **Session totals** (не полный single-tool footer). `pipeline: list` и `greedy_token_crystallize` — только plain text, без economy footer.

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
Per-step savings (if each step were a separate naive agent chat):
   #  step                   executor     ms   spent  baseline     saved  billing
   1  check-meta-sync        python       83       0     9,487     9,487  script
   2  audit-skill            ollama     2698   2,507     9,499     6,992  cheap LLM

Saved by executor (sum of per-step savings):
  python (script)              steps=1  spent ~0      saved ~9,487
  ollama (cheap LLM)           steps=1  spent ~2,507  saved ~6,992
```

| Колонка | Смысл |
|---------|--------|
| **baseline** | сколько съел бы отдельный наивный agent-чат для этого шага |
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
| `greedy-token calibrate [--overhead N] [--from-file PATH]` | Калибровка базлайна naive агент-чата (пишет `baseline:` в `~/.greedy-token/config.yaml`) |
| `greedy-token tokens PATH…` | Count tokens in paths |
| `greedy-token compress` | Short prompt (stdin; `--ollama`) |
| `greedy-token report [--since 7d]` | Usage telemetry + качество маршрутов (override_rate / cheap_hold_rate) + калибровка confidence |
| `greedy-token override …` | Записать telemetry-событие `script_override` |
| `greedy-token crystallize draft ID [--since 30d]` | L3 safe mode: draft-скрипт (`.greedy-token/drafts/`) + shadow-роут (+7d, log-only) |
| `greedy-token crystallize promote ID` | После ревью человеком: shadow → active (снять `shadow_until`) |
| `greedy-token crystallize reject ID` | Удалить draft-скрипт и его роут; записать стадию `rejected` |
| `greedy-token llm invoke --profile P` | Headless multi-model LLM invoke (`--system/-user[-file]`, stdin, `--json`) |
| `greedy-token llm list` | Список сконфигурированных LLM-моделей |
| `greedy-token doctor` | Проба железа + Ollama-моделей; рекомендация локальной модели |
| `greedy-token budget [--json] [--verbose]` | Split budget: metered API + оценка Cursor |
| `greedy-token watch [--once] [--from-start]` | Tail hook advisory log (`~/.greedy-token/advisory.jsonl`) |
| `greedy-token init [--profile solo\|team\|ci] [--preset NAME\|URL\|PATH] [--routes-from FILE] [--routes-scaffold]` | Bootstrap: detect rg/python/ollama + запись config/policy; merge командных route-пресетов / scaffold workspace-роутов |
| `greedy-token config [--init] [--export] [--reveal]` | Ollama URL/model (`--export` маскирует `CHEAP_LLM_API_KEY` как `***`; `--reveal` печатает секрет) |
| `greedy-token hub serve [--host H] [--port N]` | Локальный ops-дашборд (telemetry + crystallize) |
| `greedy-token-mcp` | MCP server (stdio) |

Флаг `--no-log` отключает запись в log на один вызов.

**Pipeline execute:** MCP `greedy_token_pipeline` и CLI `greedy-token pipeline` по умолчанию **dry-run**. Для запуска allowlisted шагов: `execute=true` (MCP) или `--execute` (CLI).

## Тесты

Нужен **Python 3.12+** (как в CI). GitHub Actions: job **tests (all)** — полный прогон, Allure 3 quality gate, отчёт на GitHub Pages; upload в TestOps при наличии `ALLURE_TOKEN`.

**CI ethalon:** `.github/_ethalon/` (пины actions в `gha-actions.yaml`) → runnable `.github/workflows/`. Тот же паттерн, что `tests-java/.github/_ethalon/` в workspace. Sync: `./scripts/sync-github-workflows.sh`; в CI перед pytest — `./scripts/check-github-workflows-sync.sh`.

**TestOps:** проект [5276](https://allure.qa.guru/project/5276). Секрет `ALLURE_TOKEN` в настройках репо; `ALLURE_PROJECT_ID` по умолчанию `5276`.

```bash
# из этого clone (после pip install -e ".[dev,mcp]"):
python -m coverage run -m pytest tests/ -v --alluredir=build/allure-results
python -m coverage report --include='src/greedy_token/*'
npx --yes allure@3.13.0 quality-gate build/allure-results --config allurerc.mjs
npx --yes allure@3.13.0 generate build/allure-results --config allurerc.mjs -o build/allure-report
# monorepo hub: cd ../dev && ./scripts/install.sh && source .venv/bin/activate && cd ../greedy-token
```

**Coverage:** `branch = true` и `fail_under = 100` для `src/greedy_token/` (`pyproject.toml`). CI: `coverage run` + `coverage report` (lines + branches). 100% достигается без опционального checkout `stacks/java-spring/`.

### Mutation testing

100% branch coverage гарантирует, что каждая строка/ветка выполняется, но не что
тест _заметит_ поломку. [mutmut](https://github.com/boxed/mutmut) вносит мутации в
код и проверяет, что суита их ловит — защита от false-green тестов. Ограничен
«горячими» модулями (`router`, `pipeline`, `executors`, `spend_guard`,
`code_search`, `tool_paths`) через `[tool.mutmut]` в `pyproject.toml`.

```bash
# из этого clone (после pip install -e ".[dev]"):
./scripts/mutation.sh            # прогон + список выживших мутантов
./scripts/mutation.sh results    # повторно показать выживших
mutmut show <id>                 # diff одного мутанта
```

Mutation testing не входит в `release-gate.sh` (медленно); запускай при изменении
горячего модуля. Цель — mutation score ~100% по этим модулям.

**Golden-реестр эквивалентных мутантов:** каждый выживший мутант либо убит
новым тестом, либо доказан эквивалентным — помечен в исходнике комментарием
`# equivalent: <доказательство>` (плюс `# pragma: no mutate`, если мутация ещё
и подавлена) и инвентаризирован в `docs/mutation-equivalents.yaml`: одна запись
на маркер (module, symbol, reason, proof), якорь — файл + текст маркера, а не
нестабильные mutmut-id. Drift-guard `tests/test_mutation_equivalents.py`
сверяет исходники и реестр в обе стороны: новый pragma/equivalent без записи в
реестре — красный тест, как и запись без маркера. Новая запись попадает в
реестр только вместе с маркером в исходнике и доказательством через ревью.

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
- **Saved vs naive agent chat** — **оценка** greedy-token (tiktoken), не биллинг API хоста; всегда помечена источником базлайна: `measured` / `calibrated` / `default-estimate`
- **Agent chat** — expensive LLM (rules + ваше сообщение + ответ)
- **Исключения footer:** `usage` → Session totals; `pipeline: list` → только рецепты

## Калибровка базлайна

Экономия в футерах — **оценка**: `saved = baseline − spent`, где базлайн — сколько стоил бы naive агент-чат для той же задачи:

```
baseline = always-on rules (measured) + task prompt (measured) + agent overhead
```

Rules и текст задачи считаются токенайзером (tiktoken). **Agent overhead** (системный промпт + схемы тулов + ответ агента) из CLI не виден, поэтому источник разрешается по приоритету:

| Приоритет | Источник | Метка в футере |
|-----------|----------|----------------|
| 1 | Секция `baseline:` в `~/.greedy-token/config.yaml` — пишется командой `greedy-token calibrate` | `measured` (калибровка через `--from-file`) или `calibrated` (через `--overhead N`) |
| 2 | Константа `BASE_CURSOR_OVERHEAD` (6 000 токенов) | `default-estimate` |

```bash
greedy-token calibrate                        # показать текущий базлайн и источники
greedy-token calibrate --overhead 9500        # явный ввод токенов оверхеда → source: calibrated
greedy-token calibrate --from-file dump.md    # посчитать токены снятого дампа контекста агента → source: measured
```

```yaml
# ~/.greedy-token/config.yaml (пишет calibrate)
baseline:
  overhead_tokens: 9500
  calibrated_at: "2026-07-22T16:00:00+00:00"
  method: measured   # или manual
```

Каждая цифра **Saved** в футерах (`route` / `estimate` / `search` / `rag` / `pipeline`) и в `report` помечена источником базлайна — оценка никогда не выдаётся за измерение.

Ручная дисциплина не нужна: пока источник — `default-estimate`, `route` и `report` печатают однострочный nudge (`baseline uncalibrated — run greedy-token calibrate`, не чаще одного раза на вызов), а `greedy-token doctor` показывает блок **Baseline** и предупреждение, если секции `baseline:` в конфиге нет.

## Качество маршрутов: калибровка confidence

Раньше **confidence** маршрута считался по чистой формуле (`min(0.95, 0.45 + score × 0.12)`) — псевдовероятность. Теперь он калибруется по вашей же телеметрии (`~/.greedy-token/usage.jsonl`):

- Каждое событие роутинга со score пишет в лог `raw_score`; score попадает в бакеты (`[0, 2)`, `[2, 4)`, `[4, 6)`, `[6, 8)`, `[8, +)`).
- Фактическая точность бакета = `1 − override_rate` — события override (`greedy-token override`, авто-атрибуция re-ask) засчитываются против последнего cheap-хита по той же нормализованной задаче.
- Бакет с **≥ 20 событиями** (`CALIBRATION_MIN_EVENTS`) — **калиброванный**: confidence берётся из телеметрии, в выводе маршрута — `calibrated (n=…)`. Ниже порога — fallback на формулу с пометкой `formula (uncalibrated)`.
- **Monotonic sanity:** калиброванные значения клэмпятся неубывающими по бакетам — больший score никогда не даёт меньший калиброванный confidence.
- Скан телеметрии **кэшируется по пути лога и инвалидируется по mtime/size `usage.jsonl`** — роутинг не перечитывает лог на каждый вызов, а долгоживущий MCP-сервер подхватывает свежую телеметрию без рестарта.

Вывод `route` / `estimate` и `explain_route()` (CLI + MCP) показывают источник:

```text
Confidence: 80% — calibrated (n=25)     # или: Confidence: 57% — formula (uncalibrated)
```

`greedy-token report` добавляет блок калибровки — бакет → predicted (формула) vs actual (телеметрия) vs n:

```text
Confidence calibration (score buckets, min n=20):
  bucket           n  predicted   actual  status
  [2, 4)          25        75%      80%  calibrated
  [4, 6)           3        95%     100%  uncalibrated (n<20)
```

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

Multi-model реестр ([ADR-0001](docs/adr/0001-unified-model-spec-derived-tier.md)): единый список `llm.models[]`; tier cheap/expensive *выводится* из атрибутов модели — `billing: free|metered`, `cost_per_1m_usd`, порог `llm.cheap_cost_threshold_per_1m_usd` (default 0.2 USD за 1M токенов). `locality: local|remote` на tier не влияет. Старые секции `llm.cheap.models[]` / `llm.expensive.models[]` продолжают читаться. Шаблоны: `examples/presets/`.

### Metered bulk APIs (ADR-0002)

Metered remote-модель с выведенным tier *cheap* (например, classify-API за $0.05/1M) может обслуживать bulk-executor tier, когда локальная Ollama недоступна — **только opt-in** ([ADR-0002](docs/adr/0002-metered-bulk-cheap-tier.md)):

```yaml
llm:
  metered:
    opt_in: true          # или env GREEDY_METERED_LLM=1 / --allow-expensive
  models:
    - id: bulk-api
      provider: openai_compat
      url: https://api.example.com/v1
      model: small-classifier
      billing: metered
      cost_per_1m_usd: 0.05
      api_key_env: BULK_API_KEY
```

Каждый metered-вызов — на cheap или expensive выведенном tier — проходит spend guard (дневной кэп `llm.expensive.daily_cap_usd` + месячный metered-кэп) и пишет `cost_usd` с блоком телеметрии `billing.tier: metered` (`billing_tier` сохраняет выведенный tier для совместимости). `greedy-token budget --verbose` / `--json` показывают split metered-расходов (cheap bulk vs expensive), а футеры честно маркируют tier: `cheap LLM (…, metered)` vs `cheap LLM (…, local free)`.

## Конфиг маршрутизации

| Файл | Назначение |
|------|------------|
| `src/greedy_token/config/routes.yaml` | Generic-дефолты маршрутизации |
| `$GREEDY_TOKEN_ROOT/.greedy-token.yaml` | Workspace-оверлей роутов (`routes:` / `routes_file:` / `cursor_fallback:`) |
| `src/greedy_token/config/pipelines.yaml` | Именованные pipeline |

## Adapting routes to your workspace

Бандловый `routes.yaml` намеренно generic: `tool-rg-search` (ripgrep по `.`), `rag-lookup`, `cursor-wiring` и `cursor` fallback. Workspace-специфичные роуты (кристаллизованные скрипты, jq-lookups, RAG-домены) живут в `$GREEDY_TOKEN_ROOT/.greedy-token.yaml` и мержатся поверх дефолтов:

```yaml
# $GREEDY_TOKEN_ROOT/.greedy-token.yaml
routes_file: team-routes.yaml   # опционально; путь относительно корня workspace (или абсолютный)
routes:                         # опционально inline-роуты; при совпадении id побеждают routes_file
  - id: python-my-check
    target: python
    read_only: true
    patterns: [my check]
    command: python scripts/my-check.py
cursor_fallback:
  message: Свой fallback-хинт для полных agent-чатов.
```

**Приоритет merge:** workspace-роут с тем же `id` заменяет бандловый; новые id ставятся первыми — они выигрывают tie-break внутри tier у дефолтов. Вне workspace (нет `GREEDY_TOKEN_ROOT` и маркеров) используются бандловые дефолты как есть.

Bootstrap:

```bash
# подключить командный route-пресет: бандловое имя, общий URL или путь к файлу
greedy-token init --preset team-default
greedy-token init --preset https://intranet.example.com/greedy/routes.yaml
greedy-token init --preset ./shared/routes.yaml

# скопировать/смержить роуты из общего YAML в <root>/.greedy-token.yaml
greedy-token init --routes-from examples/routes/workspace-routes.yaml

# сгенерировать tool-rg-search с search_paths из обнаруженных top-level папок
greedy-token init --routes-scaffold
```

Бандловые route-пресеты лежат в `examples/routes/presets/` (в пакете — `greedy_token/route_presets/`); `team-default` — command-free старт (rg + RAG). Полный рабочий оверлей (script tier, jq manifest, RAG-домены, shadow-роуты) лежит в `examples/routes/workspace-routes.yaml`.

## Безопасность `--execute`

Авто-запуск (read-only или stdout-only): tool-tier `rg` / `jq`, плюс pipeline-шаги из `PIPELINE_AUTO_RUN` (`src/greedy_token/pipeline.py`) — `check-meta-sync`, `configurator-boolean-audit`, `audit-skill`, `classify-file`, `search`, `read-hits`, `rag`.

Всё остальное (rsync / migrate / batch-inventory, не-allowlisted wrappers) — только dry-run, если не запущено вручную.

## Лицензия

MIT
