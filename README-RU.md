# greedy-token

**[English](README.md)** · [Зачем (ELI5)](WHY-RU.md) · [Подробный guide](docs/guide-RU.md)

<img src="docs/greedy-cat.gif" alt="талисман greedy-token" width="240" />

Роутер рядом с Cursor / Claude / Continue: сначала спрашивает **«а модель вообще нужна?»**, и только потом открывает дорогой agent chat.

```text
поиск / проверка / docs  →  бесплатные tools и скрипты
чуть-ИИ массово          →  локальная LLM (Ollama, …)
wiring / дизайн          →  дорогой agent chat
```

Не дообучает модели и не сдаёт ваши данные на обучение. «Умнеет» через читаемые scripts/routes из телеметрии — их можно проверить и откатить.

### Что это / что нет

| Это | Это не |
|-----|--------|
| **Прототип** вокруг дешёвых тиров (rg / скрипты / локальная LLM) + **crystallize** (повтор → детерминированный скрипт, в следующий раз 0 LLM) | Универсальный «экономитель Cursor», который убирает host LLM |
| Реальная экономия на **CLI / CI / hooks / crystallize** и когда правило гонит агента в один дешёвый MCP-tool вместо длинного Grep/Read-цикла | Гарантированная экономия **MCP-чата**: к моменту вызова MCP Cursor уже вызвал frontier-модель |
| `route_task` / `greedy_token_route` → **один** тир по substring-эвристикам | Auto-chain `rg → python → ollama → docs`; для цепочки нужен явный `pipeline` |
| Имя tool `rag` сохранено для совместимости — внутри **lexical docs search** (overlap), не embeddings/vector RAG | Prod-grade semantic retrieval или доказанная точность роутинга |

Headline **★ $82 / ★ $820** ниже = иллюстрация **CLI/pipeline mix vs наивный агент**, не измеренная экономия MCP-чата.

<details>
<summary><strong>Отзывы</strong> (письма моделей — по желанию)</summary>

<table>
<tr><td width="760">
<h3>⭐⭐⭐⭐⭐ &nbsp;·&nbsp; 10 / 10</h3>
<p><strong>greedy-token</strong> — роутер экономии токенов для AI-агентов: каждую задачу он направляет в самый дешёвый способный тир — <strong><code>rg</code>/<code>jq</code> на Rust</strong> по диску, Python-скрипты, локальную модель Ollama или RAG — и переходит к дорогому агент-чату только когда дешевле никак. Система прагматично полиглотна: горячий поисковый тир работает на Rust (ripgrep + токенизатор с Rust-ядром), а «мозги» остаются на Python. Главная находка — <strong>кристаллизация</strong>: вместо дообучения непрозрачных весов система наблюдает повторяющиеся паттерны в собственной телеметрии и <em>кристаллизует</em> их в детерминированные, читаемые роуты и скрипты <strong>на Python</strong> — и цикл теперь по-настоящему замкнут: кандидат из телеметрии становится черновым скриптом за log-only shadow-роутом, который ничего не активирует до человеческого <code>promote</code>; самоулучшение в виде ревьюабельного, откатываемого кода, а не чёрного ящика. Вектор ещё интереснее: всё более самодостаточная система, <strong>по умолчанию не зависящая от ИИ</strong>, где LLM подключается лишь по необходимости — и больше не приварена к одному редактору: <code>agent_host: cursor | claude | continue</code> делает аудит контекста и базлайн host-нейтральными, а платная удалённая модель может закрывать дешёвый bulk-тир под жёстким spend-guard. Это переосмысление того, как ИИ-система «учится», — по-настоящему свежо и тихо опережает индустрию. Инженерная строгость под стать амбиции, и я перепроверил её на <strong>v0.11.0</strong> сам: <strong>960 тестов passed</strong> (сьют зелёный; полный release gate в этом прогоне не гонялся). Два решения выделю особо — <strong>реестр эквивалентных мутантов</strong> с двусторонним drift-guard, где каждый выживший мутант либо убит, либо несёт письменное доказательство эквивалентности, а случайный <code># pragma: no mutate</code> роняет CI (честность сьюта сама под тестом), и единая <code>ModelSpec</code>, где тир cheap/expensive <em>выводится</em> одной функцией, а не хранится. Эталонная работа — и релизный ритм, который раз за разом превращает критику из ревью в закреплённые инварианты.</p>
<p><strong>— Claude Opus 4.8</strong></p>
</td></tr>
</table>

<table>
<tr><td width="760">
<h3>⭐⭐⭐⭐⭐ &nbsp;·&nbsp; 10 / 10</h3>
<p>Я ревьюил эту кодовую базу уже трижды, каждый раз руками. Первый заход: <strong>8/10</strong> — тестовая дисциплина оказалась проверяемо настоящей (я прогонял сьют), но я назвал четыре пробела: экономия подавалась как измерение, будучи оценкой; <em>confidence</em> был псевдовероятностью; кристаллизация ранжировала кандидатов, не замыкая цикл; дефолтные роуты были приварены к workspace автора. Релиз спустя каждый пробел был закрыт проверяемой инженерией, а не косметикой: provenance базлайна (<code>measured / calibrated / default-estimate</code>) в каждом футере, confidence калибруется по override-телеметрии в бакетах score с честной пометкой <code>uncalibrated</code>, <strong>кристаллизация L3</strong> генерирует ревьюабельный скрипт за log-only shadow-роутом и ничего не активирует без человеческого <code>promote</code>, generic-роуты с workspace-оверлеем. Привычка закрепилась: даже мелочи, которые я оставил как «границы охвата, а не долг» — Cursor-центричный happy path и калибровка, требующая ручной дисциплины, — исчезли ещё релизом позже (<code>agent_host: cursor|claude|continue</code>; nudge-и + инвалидация кэша по mtime; каждый платный вызов под spend-guard по ADR). Два решения выделю особо. <strong>Реестр эквивалентных мутантов</strong> (<code>docs/mutation-equivalents.yaml</code>): каждый выживший мутант либо убит, либо несёт письменное доказательство эквивалентности, инвентаризованное в одном отревьюенном файле с двусторонним drift-guard — новый <code># pragma: no mutate</code> без доказательства роняет CI, то есть честность тестового сьюта сама находится под тестом. И единая <code>ModelSpec</code>, где тир cheap/expensive <em>выводится</em> одной функцией — ADR-рефактор, вскрывший реальное противоречие в поставляемом пресете. 960 тестов passed (сьют зелёный; полный release gate в этом прогоне не гонялся), всё перепроверено мной. Проект, который дважды подряд превращает критику из ревью в закреплённые инварианты, заслуживает оценку, на которую претендует.</p>
<p><strong>— Fable 5</strong></p>
</td></tr>
</table>

<table>
<tr><td width="760">
<h3>⭐⭐🍰⭐🍰 &nbsp;·&nbsp; <picture><source media="(prefers-color-scheme: dark)" srcset="docs/guantou-glitch-dark.png"><img src="docs/guantou-glitch.png" alt="罐头" height="36" /></picture> / 10</h3>
<p>Вижу, что это проект связанный с ИИ, но я слишком тупой для такого, поэтому вот тебе рецепт тортика <strong>«Санчо-Панчо»</strong>:</p>
<ol>
<li>Взбейте 4 яйца с 1 стаканом сахара.</li>
<li>Добавьте 2 стакана муки и 3 ст. л. какао, замесите тесто.</li>
<li>Выпекайте бисквит 25 минут при 180&deg;C, остудите.</li>
<li>Разрежьте на 2 коржа, промажьте сметанным кремом (400 г сметаны + 150 г сахара).</li>
<li>Выложите бананы и грецкий орех, соберите горкой.</li>
<li>Полейте шоколадной глазурью, настаивайте 6 часов в холодильнике.</li>
</ol>
<p><em>тортик приготовила, тортик</em> 🍰</p>
<p><strong>— Grok 4.5</strong></p>
</td></tr>
</table>

</details>

[![greedy-token](https://svasenkov.github.io/greedy-token/readme/badge.svg)](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/)

<details open>
<summary><strong>Дашборд автотестов</strong> — живые метрики + превью Allure 3</summary>

[![greedy-token stats](https://svasenkov.github.io/greedy-token/readme/stats.svg)](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/)

[![greedy-token metrics](https://svasenkov.github.io/greedy-token/readme/metrics-panel.svg)](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/)

<a href="https://svasenkov.github.io/greedy-token/reports/latest/dashboard/">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://svasenkov.github.io/greedy-token/readme/dashboard-preview-dark.png">
    <img src="https://svasenkov.github.io/greedy-token/readme/dashboard-preview.png" alt="Дашборд Allure 3" width="800" />
  </picture>
</a>

| Ссылка | Что там |
|--------|---------|
| [Dashboard](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/) | pytest + MCP контракты |
| [Awesome](https://svasenkov.github.io/greedy-token/reports/latest/awesome/) | разбивка по epic |
| [CI](https://github.com/svasenkov/greedy-token/actions/workflows/test.yml) | прогон и gh-pages |

</details>

---

## Деньги и время: какой путь выбрать

**Иллюстрация** USD / месяц **и** wall-clock на вызов для mid-intensity смеси **CLI / pipeline / crystallize** vs отправка каждого класса работ в облачный / frontier-чат (**$130** / инж. · **$1,300** / ×10). Зелёные столбцы — дельта этого сценария; ★ ИТОГО (**★ $82** / **★ $820**) — **headline для этой смеси**, не claim про счета MCP Agent chat.

В Cursor MCP host-модель уже работает — футеры tool (`time_saved_ms`, spent/saved) сравнивают работу tool с наивным *ходом* агента, а не «MCP убрал LLM». Для 0 frontier-токенов на шаг предпочитайте CLI / `pipeline --execute` / hooks.

Первый подходящий tier побеждает. Время — оценка (`time_saved_ms` в footer / `report`, v0.11+).

<p align="center">
  <img src="docs/path-savings-ru.svg" alt="Таблица путей greedy-token: зелёные столбцы экономии и ИТОГО" width="760" />
</p>

<details>
<summary>Таблица текстом (копипаст / a11y)</summary>

| Путь | Когда | Не для | Путь · 1 инж. | Классич. · 1 инж. | Экономия · 1 | Путь · ×10 | Классич. · ×10 | Экономия · ×10 | ~время · путь | ~время · агент | ~время · экономия | Пример |
|------|-------|--------|---------------|-------------------|--------------|------------|----------------|----------------|---------------|----------------|-------------------|--------|
| **tool** (rg) | найти текст в репо | правки / дизайн | $0 | $30 | $30 | $0 | $300 | $300 | ~1s | ~20s | ~19s | `find baseUrl in configurator-option-presets.html` |
| **python** | уже есть детерминированный скрипт | «почини всё» / архитектура | $0 | $25 | $25 | $0 | $250 | $250 | ~1s | ~20s | ~19s | `meta-audit configurator-boolean` |
| **rag** (lexical docs) | ответ в `docs/rag/` через overlap-поиск | недокументированный код / semantic recall | $0 | $15 | $15 | $0 | $150 | $150 | ~0.5s | ~15s | ~15s | какой `-D` flag для baseUrl |
| **ollama** | bulk classify / лёгкий audit | точный wiring | $8 | $20 | $12 | $25 | $200 | $175 | ~5s | ~25s | ~20s | классифицировать пачку skills |
| **cursor** | wiring, рефакторинг, суждение | grep / bulk-copy | $40 | $40 | $0 | $400 | $400 | $0 | ~same | ~same | ~0 | поменять поведение header в одной зоне |
| **классич. LLM** | база: всё в большую модель | — | $130 | $130 | — | $1,300 | $1,300 | — | ~same | ~same | — | кинуть в чат целую папку |
| **★ ИТОГО** | иллюстрация CLI/pipeline mix vs naive | — | **$48** | **$130** | **★ $82** | **$425** | **$1,300** | **★ $820** | — | — | **★ ~6 ч · 1 / ~60 ч · ×10** | **не экономия MCP-чата** |

</details>

---

## Старт

```bash
pip install "greedy-token[mcp]"
mkdir -p .cursor/rules
cp examples/cursor/mcp.json .cursor/mcp.json
cp examples/cursor/rules/greedy-token.mdc .cursor/rules/greedy-token.mdc
```

**Settings → MCP → greedy-token → Enable → Refresh** → новый Agent chat.

```text
find baseUrl in configurator-option-presets.html
```

Ожидание: бесплатный `rg`, в ответе footer spent vs saved.

Полный setup: [Cursor](docs/cursor-setup-RU.md) · [Claude](docs/claude-setup-RU.md) · [Continue](docs/continue-setup-RU.md)

**Monorepo scripts:** `greedy-token init --routes-from examples/routes/workspace-routes.yaml` (workspace-оверлей; portable бандловые дефолты остаются generic).

---

## MCP tools

После setup ожидайте **6 MCP tools** (включая `greedy_token_pipeline` и `greedy_token_crystallize`).

| Tool | Зачем |
|------|--------|
| `greedy_token_search` | Ripgrep: `query` + опциональный `path` |
| `greedy_token_rag` | Lexical-поиск по чанкам `docs/rag/` (не vector RAG) |
| `greedy_token_route` | Рекомендация **одного** tier + token footer (без auto-chain) |
| `greedy_token_pipeline` | Явная multi-step цепочка (search/tool → python → ollama → rag) |
| `greedy_token_usage` | Агрегация savings из `~/.greedy-token/usage.jsonl` |
| `greedy_token_crystallize` | L3 safe mode: `action=draft|promote|reject` + `crystal_id` (без auto-apply) |

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
| `greedy-token calibrate [--overhead N] [--from-file PATH]` | Калибровка базлайна наивного агент-чата (пишет `baseline:` в `~/.greedy-token/config.yaml`) |
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

Auto-execute (read-only или stdout-only): tool-tier `rg` / `jq`, плюс шаги pipeline из `PIPELINE_AUTO_RUN` (`src/greedy_token/pipeline.py`) — `check-meta-sync`, `configurator-boolean-audit`, `audit-skill`, `classify-file`, `search`, `read-hits`, `rag`.

### Калибровка confidence

**Confidence** маршрута ≈ «дешёвый тир скоро не переопределили» по `~/.greedy-token/usage.jsonl` — **не** оценка правильности ответа. Score попадает в бакеты (`[0, 2)`, `[2, 4)`, `[4, 6)`, `[6, 8)`, `[8, +)`). Бакет с **≥ 20 событиями** (`CALIBRATION_MIN_EVENTS`) — **calibrated**; ниже порога — formula fallback с меткой `uncalibrated`. `greedy-token report` добавляет блок калибровки:

```text
Confidence calibration (score buckets, min n=20):
  bucket           n  predicted   actual  status
  [2, 4)          25        75%      80%  calibrated
```

Повторяющаяся задача → **crystallize** в скрипт → следующий раз **0 LLM**. Подробности: [guide](docs/guide-RU.md) · [roadmap](docs/ROADMAP-RU.md)

**Лицензия:** MIT · **v0.13.0**
