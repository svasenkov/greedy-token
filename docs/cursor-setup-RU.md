# Настройка Cursor (PyPI / любой workspace)

Инструкция для установки **greedy-token с PyPI** и подключения к **вашему** проекту в Cursor.  
Если host-monorepo уже поставляет свой launcher (`greedy-token.sh` + hooks) — смотрите документацию того репозитория.

**English:** [cursor-setup.md](cursor-setup.md)

## Требования

- **Python 3.12+**
- Cursor с включённым MCP
- Опционально: [Ollama](https://ollama.com) или OpenAI-compatible сервер для **cheap LLM** tier (config `cheap_llm`). См. [README-RU § Cheap vs expensive LLM](../README-RU.md#cheap-vs-expensive-llm).

## 1. Установка

```bash
pip install "greedy-token[mcp]"
which greedy-token-mcp   # должен быть в PATH для Cursor
```

Если Cursor не находит бинарь (часто pyenv / venv) — укажите абсолютный путь в `mcp.json` (см. Troubleshooting).

Ollama (опционально):

```bash
greedy-token config init --model qwen2.5-coder:7b-instruct-q4_K_M
```

## 2. Скопировать starter kit

Шаблоны в репозитории / sdist: `examples/cursor/`

| Шаблон | Куда в проекте |
|--------|----------------|
| [`examples/cursor/mcp.json`](../examples/cursor/mcp.json) | `.cursor/mcp.json` |
| [`examples/cursor/rules/token-economy.mdc`](../examples/cursor/rules/token-economy.mdc) | `.cursor/rules/token-economy.mdc` |

```bash
# из git-клона greedy-token:
mkdir -p .cursor/rules
cp examples/cursor/mcp.json .cursor/mcp.json
cp examples/cursor/rules/token-economy.mdc .cursor/rules/token-economy.mdc
```

Или вручную:

**`.cursor/mcp.json`**

```json
{
  "mcpServers": {
    "greedy-token": {
      "command": "greedy-token-mcp",
      "env": {
        "GREEDY_TOKEN_ROOT": "${workspaceFolder}"
      }
    }
  }
}
```

`GREEDY_TOKEN_ROOT` — корень workspace (где крутятся `rg` / RAG / scripts). Cursor подставляет `${workspaceFolder}`.

**`.cursor/rules/token-economy.mdc`** — из [`examples/cursor/rules/token-economy.mdc`](../examples/cursor/rules/token-economy.mdc). Без rule агент часто игнорирует MCP и идёт в встроенный Grep.

Если `.cursor/mcp.json` уже есть — добавьте только сервер `"greedy-token"` в `mcpServers`.

## 3. Включить в Cursor

1. **Settings → MCP**
2. **greedy-token** → **Enable** → **Refresh**
3. Должно быть **5 tools**:
   - `greedy_token_search`
   - `greedy_token_rag`
   - `greedy_token_route`
   - `greedy_token_pipeline`
   - `greedy_token_usage`
4. Откройте **новый** Agent chat (старые чаты не подхватят новые tools)

## 4. Smoke

**CLI** (опционально):

```bash
export GREEDY_TOKEN_ROOT="$PWD"
greedy-token route "find baseUrl"
greedy-token rag "ваша тема"
greedy-token pipeline "pipeline: list"
```

**Agent chat:**

```text
find TODO in README.md
```

Ожидание: `greedy_token_search` + footer **Token economy**.

```text
pipeline: list
```

Ожидание: `greedy_token_pipeline` (по умолчанию dry-run; для запуска шагов — `execute=true`).

## Что делает rule

`token-economy.mdc` с `alwaysApply: true` — **канон** поведения агента (таблица tools + исключения).  
MCP server instructions остаются короткими (footer + pipeline); таблицу tools туда не дублировать.

Rule говорит агенту:

- для lookup предпочитает MCP (search / rag / route / pipeline)
- показывает вам блок **Token economy**
- fallback на Grep только если MCP выключен или вы явно отказались (`cursor:`, «без greedy-token»)

## Ollama (опционально)

Без Ollama работают `tool` / `python` / `rag`; tier `ollama` пропускается.

```bash
curl -s http://localhost:11434/api/tags
greedy-token config
```

## Troubleshooting

| Симптом | Что сделать |
|---------|-------------|
| MCP красный / нет tools | `which greedy-token-mcp`; переустановить `"greedy-token[mcp]"`; Refresh |
| `command not found` | Абсолютный путь: `"command": "/path/to/venv/bin/greedy-token-mcp"` |
| Агент всё равно Grep | Новый chat; rule в Settings → Rules; MCP enabled |
| Поиск не там | `GREEDY_TOKEN_ROOT` в `env` mcp.json |
| Нет `rg` | `brew install ripgrep` |
| Ollama skipped | Запустить Ollama; `greedy-token config init` |

### Пример с абсолютным путём

```json
{
  "mcpServers": {
    "greedy-token": {
      "command": "/Users/you/.venv/bin/greedy-token-mcp",
      "env": {
        "GREEDY_TOKEN_ROOT": "${workspaceFolder}"
      }
    }
  }
}
```

## Что не входит в starter kit

| Что | Почему |
|-----|--------|
| Monorepo launcher `greedy-token.sh` | Привязан к `projects/greedy-token-home/dev/.venv` |
| `sessionStart` hooks | Опционально; монорепо-специфика |

Для beta достаточно MCP + rule.

## См. также

- README пакета: [README-RU.md](../README-RU.md)
- Roadmap (Claude Desktop / Continue): [ROADMAP-RU.md](ROADMAP-RU.md)
