# Claude Desktop — установка

Подключите MCP **greedy-token** в [Claude Desktop](https://claude.ai/download) для поиска, RAG и маршрутизации с учётом token economy.

**English:** [claude-desktop-setup.md](claude-desktop-setup.md)  
**Все MCP-хосты:** [mcp-setup-RU.md](mcp-setup-RU.md)

> Тот же stdio-сервер, что и в Cursor (`greedy-token-mcp`). Отдельного Claude-кода в пакете нет — только конфиг и инструкции агенту.

## Требования

- **Python 3.12+**
- Claude Desktop (с поддержкой MCP)
- Опционально: [Ollama](https://ollama.com)
- Опционально: [ripgrep](https://github.com/BurntSushi/ripgrep) в PATH для MCP-процесса

## 1. Установка

```bash
pip install "greedy-token[mcp]"
which greedy-token-mcp
```

Claude Desktop **не наследует PATH из shell**. Скопируйте **абсолютный путь** из `which` в конфиг.

Альтернатива, если скрипта нет в PATH:

```json
"command": "/absolute/path/to/python",
"args": ["-m", "greedy_token.mcp"]
```

Ollama (опционально):

```bash
greedy-token config init --model qwen2.5-coder:7b-instruct-q4_K_M
```

## 2. Конфиг MCP

### Где лежит файл

| OS | Путь |
|----|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` (если поддерживается вашей сборкой) |

Создайте файл при необходимости. **Добавьте** сервер в `mcpServers`, не затирайте другие.

Шаблон: [`examples/claude/claude_desktop_config.fragment.json`](../examples/claude/claude_desktop_config.fragment.json)

**Замените оба абсолютных пути:**

```json
{
  "mcpServers": {
    "greedy-token": {
      "command": "/Users/you/.venv/bin/greedy-token-mcp",
      "env": {
        "GREEDY_TOKEN_ROOT": "/Users/you/projects/your-monorepo"
      }
    }
  }
}
```

### Важно: `GREEDY_TOKEN_ROOT`

В отличие от Cursor (`${workspaceFolder}`), в Claude Desktop **нет переменной workspace**. Укажите **корень git-репозитория**, где должны работать `rg`, `docs/rag/` и `scripts/`.

При смене проекта обновляйте путь (или заведите отдельные **Projects** в Claude).

Все значения в `env` — только JSON-**строки**.

## 3. Инструкции агенту

У Claude нет `.cursor/rules/`. Вставьте [`examples/claude/instructions.md`](../examples/claude/instructions.md) в:

1. **Рекомендуется:** Claude **Project → Custom instructions** для каждой кодовой базы  
2. **Глобально:** Settings → Profile → Custom instructions

Без инструкций Claude часто читает файлы вручную, минуя MCP.

## 4. Перезапуск Claude Desktop

MCP поднимается при старте приложения.

1. Полностью выйти из Claude Desktop  
2. Запустить снова  
3. В чате → иконка tools → **greedy-token** и **5 tools**

Ожидаемые tools: `greedy_token_search`, `greedy_token_rag`, `greedy_token_route`, `greedy_token_pipeline`, `greedy_token_usage`.

После включения MCP — **новый** диалог.

## 5. Smoke test

**CLI** (тот же корень, что в `GREEDY_TOKEN_ROOT`):

```bash
export GREEDY_TOKEN_ROOT="/Users/you/projects/your-monorepo"
greedy-token route "find baseUrl"
greedy-token rag "your topic"
greedy-token pipeline "pipeline: list"
```

**Промпты в чате:**

```text
Use greedy_token_search to find baseUrl in README.md and show the Token economy footer.
```

```text
pipeline: list
```

Чеклист: [mcp-setup-RU.md](mcp-setup-RU.md)

## Несколько проектов

| Подход | Когда |
|--------|-------|
| Отдельный Claude **Project** на репо + свои instructions | Лучший вариант |
| Один глобальный `GREEDY_TOKEN_ROOT` | Если всегда один monorepo |
| Править `claude_desktop_config.json` при смене репо | Работает, но легко забыть |

## Troubleshooting

| Симптом | Решение |
|---------|---------|
| Нет MCP tools | Полный quit + relaunch; проверить JSON |
| `command not found` | Абсолютный `command` или `python` + `-m greedy_token.mcp` |
| Поиск не в том дереве | Исправить `GREEDY_TOKEN_ROOT` |
| Claude игнорирует MCP | Новый чат; instructions; явно попросить greedy-token tools |
| Нет `rg` | Установить ripgrep |
| Ollama tier пропускается | Запустить Ollama; `greedy-token config init` |
| Конфиг пропал после правки | Не использовать только HTTP `url` — нужен stdio `command` |

### Лог macOS

```bash
tail -f ~/Library/Logs/Claude/mcp*.log
```

## Отличия от Cursor

| | Cursor | Claude Desktop |
|---|--------|----------------|
| MCP config | `.cursor/mcp.json` в проекте | системный config приложения |
| Корень workspace | `${workspaceFolder}` | абсолютный `GREEDY_TOKEN_ROOT` |
| Правила агента | `token-economy.mdc` | Custom instructions проекта |
| Включение | Settings → MCP | перезапуск + tools в чате |

## См. также

- Cursor: [cursor-setup-RU.md](cursor-setup-RU.md)
- README: [README-RU.md](../README-RU.md)
- Roadmap: [ROADMAP-RU.md](ROADMAP-RU.md) · [#14](https://github.com/svasenkov/greedy-token/issues/14)
