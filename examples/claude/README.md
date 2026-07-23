# Claude Desktop starter kit

Copy these files into place (merge `claude_desktop_config.json` if you already have MCP servers).

| File | Copy to |
|------|---------|
| `claude_desktop_config.json` | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) · `%APPDATA%\Claude\claude_desktop_config.json` (Windows) |
| `CLAUDE.md` | `<your project>/CLAUDE.md` |

Set `GREEDY_TOKEN_ROOT` in the config to the absolute path of your project — Claude Desktop has no workspace-folder variable.

Tell greedy-token to audit Claude context conventions (`CLAUDE.md` + `.claude/rules/*.md`):

```yaml
# <your project>/.greedy-token.yaml
agent_host: claude
```

Full guide: [docs/claude-setup.md](../../docs/claude-setup.md) · [docs/claude-setup-RU.md](../../docs/claude-setup-RU.md)
