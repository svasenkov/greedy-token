# Настройка Continue

Подключение greedy-token (MCP stdio-сервер) к **Continue** (VS Code / JetBrains) и аудит контекст-конвенций Continue.

**English version:** [continue-setup.md](continue-setup.md)

## Требования

- **Python 3.12+**, `pip install "greedy-token[mcp]"`
- Расширение Continue с поддержкой MCP
- Опционально: [Ollama](https://ollama.com) или любой OpenAI-совместимый сервер для tier **cheap LLM**

## 1. MCP-сервер

Смержите блок `mcpServers` в `~/.continue/config.yaml` (шаблон: [`examples/continue/config.yaml`](../examples/continue/config.yaml)):

```yaml
mcpServers:
  - name: greedy-token
    command: greedy-token-mcp
    env:
      GREEDY_TOKEN_ROOT: /absolute/path/to/your/project
```

Если бинарь не в PATH Continue — абсолютный путь к `greedy-token-mcp` (venv/pyenv). Перезагрузите расширение; инструменты greedy-token появятся в списке tools.

## 2. Always-on правила (.continuerules)

Continue читает `.continuerules` из корня проекта в каждом чате. Скопируйте шаблон
[`examples/continue/continuerules.md`](../examples/continue/continuerules.md) в `<ваш проект>/.continuerules`. Дополнительные правила — `.continue/rules/*.md`.

## 3. Сообщите greedy-token про хост

```yaml
# <ваш проект>/.greedy-token.yaml
agent_host: continue
```

(Либо `agent_host: continue` в `~/.greedy-token/config.yaml`, либо `GREEDY_AGENT_HOST=continue`.)

С `agent_host: continue` команда `greedy-token audit-context` и базлайн наивного чата считают `.continuerules` + `.continue/rules/*.md` как always-on правила — токены, списываемые в каждом чате этого хоста.

## 4. Smoke-тест

```bash
export GREEDY_TOKEN_ROOT="$PWD"
greedy-token audit-context   # заголовок: == Continue context audit ==
greedy-token route "find baseUrl"
```

В чате Continue: `find TODO in README.md` → ожидаем вызов `greedy_token_search` и строку-футер **Greedy token**.

## См. также

- Настройка Cursor: [cursor-setup-RU.md](cursor-setup-RU.md)
- Настройка Claude Desktop: [claude-setup-RU.md](claude-setup-RU.md)
- README пакета: [README-RU.md](../README-RU.md)
