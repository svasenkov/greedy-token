# Claude Desktop setup

Wire greedy-token (MCP stdio server) into **Claude Desktop** and audit Claude context conventions.

**Русская версия:** [claude-setup-RU.md](claude-setup-RU.md)

## Requirements

- **Python 3.12+**, `pip install "greedy-token[mcp]"`
- Claude Desktop with MCP support
- Optional: [Ollama](https://ollama.com) or any OpenAI-compatible server for the **cheap LLM** tier

## 1. MCP server

Add the server to the Claude Desktop config (template: [`examples/claude/claude_desktop_config.json`](../examples/claude/claude_desktop_config.json)):

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

Claude Desktop has **no workspace-folder variable** — set `GREEDY_TOKEN_ROOT` to the absolute project path (one config per project, or switch the path when you switch projects). If the binary is not on Claude's PATH, use an absolute path to `greedy-token-mcp` (venv/pyenv installs).

Restart Claude Desktop; the greedy-token tools appear under the MCP tools list.

## 2. Always-on rules (CLAUDE.md)

Claude reads `CLAUDE.md` from the project root on every chat. Copy the template
[`examples/claude/CLAUDE.md`](../examples/claude/CLAUDE.md) into your project (or merge into an existing `CLAUDE.md`). Extra rule files can live in `.claude/rules/*.md`.

## 3. Tell greedy-token about the host

```yaml
# <your project>/.greedy-token.yaml
agent_host: claude
```

(Or `agent_host: claude` in `~/.greedy-token/config.yaml`, or `GREEDY_AGENT_HOST=claude`.)

With `agent_host: claude`, `greedy-token audit-context` and the naive-chat baseline count `CLAUDE.md` + `.claude/rules/*.md` as the always-on rules — the token cost charged on every chat in this host.

## 4. Smoke test

```bash
export GREEDY_TOKEN_ROOT="$PWD"
greedy-token audit-context   # header: == Claude context audit ==
greedy-token route "find baseUrl"
```

In a Claude chat: `find TODO in README.md` → expect a `greedy_token_search` call and the **Greedy token** footer line.

## Related

- Cursor setup: [cursor-setup.md](cursor-setup.md)
- Continue setup: [continue-setup.md](continue-setup.md)
- Package README: [README.md](../README.md)
