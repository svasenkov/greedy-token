# Continue setup

Wire greedy-token (MCP stdio server) into **Continue** (VS Code / JetBrains) and audit Continue context conventions.

**Русская версия:** [continue-setup-RU.md](continue-setup-RU.md)

## Requirements

- **Python 3.12+**, `pip install "greedy-token[mcp]"`
- Continue extension with MCP support
- Optional: [Ollama](https://ollama.com) or any OpenAI-compatible server for the **cheap LLM** tier

## 1. MCP server

Merge the `mcpServers` block into `~/.continue/config.yaml` (template: [`examples/continue/config.yaml`](../examples/continue/config.yaml)):

```yaml
mcpServers:
  - name: greedy-token
    command: greedy-token-mcp
    env:
      GREEDY_TOKEN_ROOT: /absolute/path/to/your/project
```

If the binary is not on Continue's PATH, use an absolute path to `greedy-token-mcp` (venv/pyenv installs). Reload the extension; the greedy-token tools appear in the tools list.

## 2. Always-on rules (.continuerules)

Continue reads `.continuerules` from the project root on every chat. Copy the template
[`examples/continue/continuerules.md`](../examples/continue/continuerules.md) to `<your project>/.continuerules`. Extra rule files can live in `.continue/rules/*.md`.

## 3. Tell greedy-token about the host

```yaml
# <your project>/.greedy-token.yaml
agent_host: continue
```

(Or `agent_host: continue` in `~/.greedy-token/config.yaml`, or `GREEDY_AGENT_HOST=continue`.)

With `agent_host: continue`, `greedy-token audit-context` and the naive-chat baseline count `.continuerules` + `.continue/rules/*.md` as the always-on rules — the token cost charged on every chat in this host.

## 4. Smoke test

```bash
export GREEDY_TOKEN_ROOT="$PWD"
greedy-token audit-context   # header: == Continue context audit ==
greedy-token route "find baseUrl"
```

In a Continue chat: `find TODO in README.md` → expect a `greedy_token_search` call and the **Greedy token** footer line.

## Related

- Cursor setup: [cursor-setup.md](cursor-setup.md)
- Claude Desktop setup: [claude-setup.md](claude-setup.md)
- Package README: [README.md](../README.md)
