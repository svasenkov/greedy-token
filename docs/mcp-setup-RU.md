# MCP setup (все хосты)

greedy-token поднимает один **stdio MCP-сервер** (`greedy-token-mcp`) — тот же процесс для любого хоста. Отличаются только путь к конфигу и как вы учите агента предпочитать MCP.

**English:** [mcp-setup.md](mcp-setup.md)

## Гайды по хостам

| Хост | Конфиг | Инструкции агенту | Гайд |
|------|--------|-------------------|------|
| **Cursor** | `.cursor/mcp.json` | `.cursor/rules/token-economy.mdc` | [cursor-setup-RU.md](cursor-setup-RU.md) |
| **Claude Desktop** | `claude_desktop_config.json` | Custom instructions проекта | [claude-desktop-setup-RU.md](claude-desktop-setup-RU.md) |
| **Только CLI** | — | — | [README-RU.md](../README-RU.md) |
| **Continue / VS Code** | — | — | 🔜 [#15](https://github.com/svasenkov/greedy-token/issues/15) |

## Установка (один раз)

```bash
pip install "greedy-token[mcp]"
which greedy-token-mcp   # абсолютный путь для конфигов IDE
```

## Ожидаемые tools (все хосты)

После старта сервера хост должен показать **5 tools**:

- `greedy_token_search`
- `greedy_token_rag`
- `greedy_token_route`
- `greedy_token_pipeline`
- `greedy_token_usage`

## Smoke checklist

1. **CLI** (опционально): `GREEDY_TOKEN_ROOT=$PWD greedy-token route "find README"`
2. **MCP route**: промпт → `greedy_token_route` с footer **Token economy**
3. **MCP search**: промпт → `greedy_token_search` находит известную строку в репо
4. **MCP pipeline list**: `pipeline: list` или task `list` на `greedy_token_pipeline`

Автоматически: `tests/test_mcp_stdio.py` (pytest, без GUI).

## Environment

| Var | Назначение |
|-----|------------|
| `GREEDY_TOKEN_ROOT` | Корень workspace для rg / RAG / scripts (**обязателен** в Claude Desktop — нет `${workspaceFolder}`) |
| `GREEDY_TOKEN_LOG` | Путь к логу; `0` — выключить |
| `OLLAMA_URL` / `OLLAMA_MODEL` | Локальный LLM tier (опционально) |

## См. также

- Roadmap: [ROADMAP-RU.md](ROADMAP-RU.md)
- Cursor: [cursor-setup-RU.md](cursor-setup-RU.md)
- Claude Desktop: [claude-desktop-setup-RU.md](claude-desktop-setup-RU.md)
