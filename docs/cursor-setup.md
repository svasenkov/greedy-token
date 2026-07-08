# Cursor setup (PyPI / any workspace)

Use this when you install **greedy-token from PyPI** and wire it into **your own** Cursor project.  
For the zero-design-system monorepo launcher (`greedy-token.sh` + hooks), see that repo’s `docs/token-economy/cursor.md`.

**Русская версия:** [cursor-setup-RU.md](cursor-setup-RU.md)

## Requirements

- **Python 3.12+**
- Cursor with MCP enabled
- Optional: [Ollama](https://ollama.com) for the local LLM tier

## 1. Install

```bash
pip install "greedy-token[mcp]"
which greedy-token-mcp   # must be on PATH for Cursor
```

If Cursor cannot find the binary (common with pyenv / venv), use an absolute path in `mcp.json` (see Troubleshooting).

Optional Ollama defaults:

```bash
greedy-token config init --model qwen2.5-coder:7b-instruct-q4_K_M
```

## 2. Copy the starter kit

Templates live in the package repo / sdist under `examples/cursor/`:

| Template | Destination in your project |
|----------|-----------------------------|
| [`examples/cursor/mcp.json`](../examples/cursor/mcp.json) | `.cursor/mcp.json` |
| [`examples/cursor/rules/token-economy.mdc`](../examples/cursor/rules/token-economy.mdc) | `.cursor/rules/token-economy.mdc` |

```bash
# from a git clone of greedy-token:
mkdir -p .cursor/rules
cp examples/cursor/mcp.json .cursor/mcp.json
cp examples/cursor/rules/token-economy.mdc .cursor/rules/token-economy.mdc
```

Or create the files by hand:

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

`GREEDY_TOKEN_ROOT` should be the workspace root (where you want `rg` / RAG / scripts to run). `${workspaceFolder}` is expanded by Cursor.

**`.cursor/rules/token-economy.mdc`** — copy from [`examples/cursor/rules/token-economy.mdc`](../examples/cursor/rules/token-economy.mdc). Without this rule, Cursor often uses built-in Grep instead of MCP.

Merge rule: if `.cursor/mcp.json` already exists, add only the `"greedy-token"` server entry under `mcpServers`.

## 3. Enable in Cursor

1. **Settings → MCP**
2. Find **greedy-token** → **Enable** → **Refresh**
3. You should see **5 tools**:
   - `greedy_token_search`
   - `greedy_token_rag`
   - `greedy_token_route`
   - `greedy_token_pipeline`
   - `greedy_token_usage`
4. Start a **new** Agent chat (old chats keep previous tool set)

## 4. Smoke test

**CLI** (optional):

```bash
export GREEDY_TOKEN_ROOT="$PWD"
greedy-token route "find baseUrl"
greedy-token rag "your topic"
greedy-token pipeline "pipeline: list"
```

**Agent chat** prompts:

```text
find TODO in README.md
```

Expect: `greedy_token_search`, then a **Token economy** footer in the reply.

```text
pipeline: list
```

Expect: `greedy_token_pipeline` (dry-run by default — pass `execute=true` to run allowlisted steps).

## What the rule does

`token-economy.mdc` is `alwaysApply: true`. It tells the agent to:

- prefer MCP search/RAG/route/pipeline for lookups
- show the **Token economy** block to you
- fall back to Grep only when MCP is off or you opt out (`cursor:`, “without greedy-token”)

## Optional: Ollama

Without Ollama, `tool` / `python` / `rag` still work; the `ollama` tier is skipped.

```bash
curl -s http://localhost:11434/api/tags
greedy-token config
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| MCP red / tools missing | `which greedy-token-mcp`; reinstall `"greedy-token[mcp]"`; Refresh MCP |
| `command not found` | Put absolute path in `mcp.json`: `"command": "/path/to/venv/bin/greedy-token-mcp"` |
| Agent still uses Grep | New chat; confirm rule is listed under Settings → Rules; MCP enabled |
| Wrong workspace for search | Set `GREEDY_TOKEN_ROOT` in mcp `env` to the correct root |
| `rg` missing | `brew install ripgrep` (or ensure `rg` is on PATH for the MCP process) |
| Ollama tier skipped | Start Ollama; `greedy-token config init` |

### Absolute-path `mcp.json` example

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

## Not included in this starter kit

| Item | Why |
|------|-----|
| Monorepo `greedy-token.sh` launcher | Tied to `projects/greedy-token-home/dev/.venv` |
| `sessionStart` hooks | Optional; monorepo-specific |

Hooks / custom launchers can be added later; MCP + rule are enough for beta testing.

## Related

- Package README: [README.md](../README.md)
- Roadmap (Claude Desktop / Continue): [ROADMAP.md](ROADMAP.md)
