# Настройка Claude Desktop

Подключение greedy-token (MCP stdio-сервер) к **Claude Desktop** и аудит контекст-конвенций Claude.

**English version:** [claude-setup.md](claude-setup.md)

## Требования

- **Python 3.12+**, `pip install "greedy-token[mcp]"`
- Claude Desktop с поддержкой MCP
- Опционально: [Ollama](https://ollama.com) или любой OpenAI-совместимый сервер для tier **cheap LLM**

## 1. MCP-сервер

Добавьте сервер в конфиг Claude Desktop (шаблон: [`examples/claude/claude_desktop_config.json`](../examples/claude/claude_desktop_config.json)):

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "greedy-token": {
      "command": "greedy-token-mcp",
      "env": {
        "GREEDY_TOKEN_ROOT": "/absolute/path/to/your/project"
      }
    }
  }
}
```

В Claude Desktop **нет переменной workspace-папки** — укажите в `GREEDY_TOKEN_ROOT` абсолютный путь к проекту (один конфиг на проект, либо меняйте путь при смене проекта). Если бинарь не в PATH Claude — абсолютный путь к `greedy-token-mcp` (venv/pyenv).

Перезапустите Claude Desktop; инструменты greedy-token появятся в списке MCP tools.

## 2. Always-on правила (CLAUDE.md)

Claude читает `CLAUDE.md` из корня проекта в каждом чате. Скопируйте шаблон
[`examples/claude/CLAUDE.md`](../examples/claude/CLAUDE.md) в проект (или смержите с существующим `CLAUDE.md`). Дополнительные правила — `.claude/rules/*.md`.

## 3. Сообщите greedy-token про хост

```yaml
# <ваш проект>/.greedy-token.yaml
agent_host: claude
```

(Либо `agent_host: claude` в `~/.greedy-token/config.yaml`, либо `GREEDY_AGENT_HOST=claude`.)

С `agent_host: claude` команда `greedy-token audit-context` и базлайн наивного чата считают `CLAUDE.md` + `.claude/rules/*.md` как always-on правила — токены, списываемые в каждом чате этого хоста.

## 4. Smoke-тест

```bash
export GREEDY_TOKEN_ROOT="$PWD"
greedy-token audit-context   # заголовок: == Claude context audit ==
greedy-token route "find baseUrl"
```

В чате Claude: `find TODO in README.md` → ожидаем вызов `greedy_token_search` и строку-футер **Greedy token**.

## См. также

- Настройка Cursor: [cursor-setup-RU.md](cursor-setup-RU.md)
- Настройка Continue: [continue-setup-RU.md](continue-setup-RU.md)
- README пакета: [README-RU.md](../README-RU.md)
