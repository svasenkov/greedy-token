# Claude Desktop setup

Wire **greedy-token** MCP into [Claude Desktop](https://claude.ai/download) for token-aware search, RAG, and routing in your workspace.

**Русская версия:** [claude-desktop-setup-RU.md](claude-desktop-setup-RU.md)  
**All MCP hosts:** [mcp-setup.md](mcp-setup.md)

> Same stdio server as Cursor (`greedy-token-mcp`). No Claude-specific code in the package — only config and agent instructions differ.

## Requirements

- **Python 3.12+**
- Claude Desktop (recent build with MCP support)
- Optional: [Ollama](https://ollama.com) for the local LLM tier
- Optional: [ripgrep](https://github.com/BurntSushi/ripgrep) on PATH for the MCP process (`brew install ripgrep`)

## 1. Install

```bash
pip install "greedy-token[mcp]"
which greedy-token-mcp
```

Claude Desktop **does not inherit your shell PATH**. Copy the **absolute path** from `which` into the config (see below).

Alternative if the script is not on PATH:

```json
"command": "/absolute/path/to/python",
"args": ["-m", "greedy_token.mcp"]
```

Optional Ollama defaults:

```bash
greedy-token config init --model qwen2.5-coder:7b-instruct-q4_K_M
```

## 2. MCP config

### Config file location

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` (if supported by your build) |

Create the file if it does not exist. Merge into existing `mcpServers` — do not replace other servers.

Template: [`examples/claude/claude_desktop_config.fragment.json`](../examples/claude/claude_desktop_config.fragment.json)

**Replace both absolute paths** before saving:

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

### Important: `GREEDY_TOKEN_ROOT`

Unlike Cursor’s `${workspaceFolder}`, Claude Desktop has **no workspace variable**. Set `GREEDY_TOKEN_ROOT` to the **git root** where you want `rg`, `docs/rag/`, and `scripts/` to run.

When you switch projects, update this path (or maintain separate Claude **Projects** with different configs — see below).

All `env` values must be JSON **strings**.

## 3. Agent instructions

Claude has no `.cursor/rules/`. Paste [`examples/claude/instructions.md`](../examples/claude/instructions.md) into:

1. **Recommended:** Claude **Project → Set custom instructions** for each codebase you work on  
2. **Global fallback:** Settings → Profile → Custom instructions (applies to all chats)

Without instructions, Claude may ignore MCP and read files manually.

## 4. Restart Claude Desktop

MCP servers load at startup.

1. Quit Claude Desktop completely (not just close the window)
2. Relaunch
3. Open a chat → **tools / hammer icon** → confirm **greedy-token** and **5 tools**

Expected tools:

- `greedy_token_search`
- `greedy_token_rag`
- `greedy_token_route`
- `greedy_token_pipeline`
- `greedy_token_usage`

Start a **new conversation** after enabling MCP.

## 5. Smoke test

**CLI** (same workspace as `GREEDY_TOKEN_ROOT`):

```bash
export GREEDY_TOKEN_ROOT="/Users/you/projects/your-monorepo"
greedy-token route "find baseUrl"
greedy-token rag "your topic"
greedy-token pipeline "pipeline: list"
```

**Chat prompts:**

```text
Use greedy_token_search to find baseUrl in README.md and show the Token economy footer.
```

```text
pipeline: list
```

Expect: tool call to `greedy_token_pipeline` (dry-run by default — `execute=true` only when you want allowlisted steps to run).

Full checklist: [mcp-setup.md#smoke-checklist](mcp-setup.md#smoke-checklist)

## Multiple projects

| Approach | When |
|----------|------|
| One Claude **Project** per repo + custom instructions | Best — instructions match each codebase |
| Single global `GREEDY_TOKEN_ROOT` | Only if you always work in one monorepo |
| Edit `claude_desktop_config.json` when switching | Works but easy to forget |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| No MCP tools / server missing | Full quit + relaunch; validate JSON (no trailing commas) |
| `command not found` | Use absolute `command` path; or `python` + `args: ["-m", "greedy_token.mcp"]` |
| Search hits wrong tree | Fix `GREEDY_TOKEN_ROOT` — must be repo root with `docs/`, `scripts/`, etc. |
| Claude ignores MCP | New chat; Project custom instructions pasted; ask explicitly to use greedy-token tools |
| `rg` missing | Install ripgrep; ensure it is on PATH for the MCP subprocess |
| Ollama tier skipped | Start Ollama; `greedy-token config init` |
| Config wiped after edit | Do not use HTTP `url`-only MCP entries — Claude Desktop expects stdio `command` servers |

### macOS log (debug)

```bash
tail -f ~/Library/Logs/Claude/mcp*.log
```

## Differences from Cursor

| | Cursor | Claude Desktop |
|---|--------|----------------|
| MCP config | `.cursor/mcp.json` in project | OS app config file |
| Workspace root | `${workspaceFolder}` | Manual absolute `GREEDY_TOKEN_ROOT` |
| Agent rules | `.cursor/rules/token-economy.mdc` | Project custom instructions |
| Enable UI | Settings → MCP | Restart app; tools menu in chat |

## Related

- Cursor setup: [cursor-setup.md](cursor-setup.md)
- Package README: [README.md](../README.md)
- Roadmap: [ROADMAP.md](ROADMAP.md) · issue [#14](https://github.com/svasenkov/greedy-token/issues/14)
