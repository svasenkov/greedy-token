# Continue starter kit

Copy these files into place (merge `config.yaml` if you already have MCP servers).

| File | Copy to |
|------|---------|
| `config.yaml` (the `mcpServers` block) | `~/.continue/config.yaml` |
| `continuerules.md` | `<your project>/.continuerules` |

Set `GREEDY_TOKEN_ROOT` in the config to the absolute path of your project.

Tell greedy-token to audit Continue context conventions (`.continuerules` + `.continue/rules/*.md`):

```yaml
# <your project>/.greedy-token.yaml
agent_host: continue
```

Full guide: [docs/continue-setup.md](../../docs/continue-setup.md) · [docs/continue-setup-RU.md](../../docs/continue-setup-RU.md)
