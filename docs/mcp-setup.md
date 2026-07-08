# MCP setup (all hosts)

greedy-token exposes one **stdio MCP server** (`greedy-token-mcp`) — the same process for every host. Host-specific steps are only config path and how you teach the agent to prefer MCP.

**Русская версия:** [mcp-setup-RU.md](mcp-setup-RU.md)

## Host guides

| Host | Config | Agent instructions | Guide |
|------|--------|-------------------|-------|
| **Cursor** | `.cursor/mcp.json` | `.cursor/rules/token-economy.mdc` | [cursor-setup.md](cursor-setup.md) |
| **Claude Desktop** | `claude_desktop_config.json` | Project custom instructions | [claude-desktop-setup.md](claude-desktop-setup.md) |
| **CLI only** | — | — | [README.md](../README.md) |
| **Continue / VS Code** | — | — | 🔜 [#15](https://github.com/svasenkov/greedy-token/issues/15) |

## Install (once)

```bash
pip install "greedy-token[mcp]"
which greedy-token-mcp   # note absolute path for IDE configs
```

## Expected tools (all hosts)

After the server starts, the host should list **5 tools**:

- `greedy_token_search`
- `greedy_token_rag`
- `greedy_token_route`
- `greedy_token_pipeline`
- `greedy_token_usage`

## Smoke checklist

1. **CLI** (optional): `GREEDY_TOKEN_ROOT=$PWD greedy-token route "find README"`
2. **MCP route**: prompt → `greedy_token_route` with **Token economy** footer
3. **MCP search**: prompt → `greedy_token_search` finds a known string in the repo
4. **MCP pipeline list**: `pipeline: list` or task `list` on `greedy_token_pipeline`

Automated stdio coverage: `tests/test_mcp_stdio.py` (pytest, no GUI).

## Environment

| Var | Purpose |
|-----|---------|
| `GREEDY_TOKEN_ROOT` | Workspace root for rg / RAG / scripts (**required** in Claude Desktop — no `${workspaceFolder}`) |
| `GREEDY_TOKEN_LOG` | Usage log path; `0` to disable |
| `OLLAMA_URL` / `OLLAMA_MODEL` | Local LLM tier (optional) |

## Related

- Roadmap: [ROADMAP.md](ROADMAP.md)
- Cursor: [cursor-setup.md](cursor-setup.md)
- Claude Desktop: [claude-desktop-setup.md](claude-desktop-setup.md)
