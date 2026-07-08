# Token economy (greedy-token MCP)

When **greedy-token** MCP is enabled for this workspace, prefer MCP tools for lookup and search — not manual file greps or repeated reads of large trees.

| Task | Tool |
|------|------|
| find in code | `greedy_token_search(query, path?)` |
| patterns / flags / docs | `greedy_token_rag(query, domain?)` |
| route a task | `greedy_token_route(task)` |
| multi-step chain | `greedy_token_pipeline(task)` |
| savings report | `greedy_token_usage(since?)` |

**path** — a file or directory name, not the whole user prompt.

When relaying MCP output, show the **Token economy** footer to the user.

**Exceptions:** wiring/refactor after search is done; user says "without greedy-token"; MCP disabled → one built-in search is OK.

**Pipeline execute:** `greedy_token_pipeline` is **dry-run** by default. Pass `execute=true` only when the user wants allowlisted steps to run.
