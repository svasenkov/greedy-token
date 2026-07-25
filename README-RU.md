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

## Деньги: какой путь выбрать

Иллюстрация, USD / месяц. **Классическая LLM** = всё сразу в облачный / frontier-чат. Первый подходящий tier побеждает.

| Путь | Когда | Не для | Путь · 1 инж. | Классич. · 1 инж. | Экономия · 1 | Путь · ×10 | Классич. · ×10 | Экономия · ×10 |
|------|-------|--------|---------------|-------------------|--------------|------------|----------------|----------------|
| **tool** (rg) | найти текст в репо | правки / дизайн | $0 | $30 | $30 | $0 | $300 | $300 |
| **python** | уже есть детерминированный скрипт | «почини всё» / архитектура | $0 | $25 | $25 | $0 | $250 | $250 |
| **rag** | ответ в паттернах / docs | недокументированный код | $0 | $15 | $15 | $0 | $150 | $150 |
| **ollama** | bulk classify / лёгкий audit | точный wiring | $8 | $20 | $12 | $25 | $200 | $175 |
| **cursor** | wiring, рефакторинг, суждение | grep / bulk-copy | $40 | $40 | $0 | $400 | $400 | $0 |
| **классич. LLM** | база: всё в большую модель | — | $130 | $130 | — | $1,300 | $1,300 | — |
| **★ ИТОГО** | с роутером vs без | — | **$48** | **$130** | **★ $82** | **$425** | **$1,300** | **★ $820** |

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

---

## MCP и команды (кратко)

| Tool | Зачем |
|------|--------|
| `greedy_token_search` | поиск по коду |
| `greedy_token_rag` | паттерны / docs |
| `greedy_token_route` | какой tier + почему |
| `greedy_token_pipeline` | дешёвая цепочка |
| `greedy_token_usage` | статистика (по запросу) |
| `greedy_token_crystallize` | draft / promote / reject скрипта |

```bash
greedy-token doctor
greedy-token run "find …" --execute
greedy-token report --since 7d
greedy-token hub serve
```

Повторяющаяся задача → **crystallize** в скрипт → следующий раз **0 LLM**. Подробности: [guide](docs/guide-RU.md) · [roadmap](docs/ROADMAP-RU.md)

**Лицензия:** MIT · **v0.10.0**
