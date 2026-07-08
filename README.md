# greedy-token

**Русская версия:** [README-RU.md](README-RU.md)

<img src="docs/greedy-cat.gif" alt="greedy-token mascot" width="240" />

You work in **Cursor** — greedy-token sits next to the agent (CLI + MCP) so everyday tasks don’t always open a full agent chat.

It tries cheaper executors first — **ripgrep → scripts → local Ollama → docs/rag** — and only escalates to a **Cursor agent** turn when those aren’t enough. Each call shows a **Token economy** estimate of what you saved vs a naive full-context chat.

```
In Cursor:  your task  →  greedy-token (MCP/CLI)
                 ↓
           cheapest first:  rg | scripts | Ollama | docs/rag | pipeline
                 ↓
           only if needed:  Cursor agent chat
```

## What it does

| Layer | When | LLM cost |
|-------|------|----------|
| **tool** (rg) | find / grep / search | ~0 |
| **python** | scripts, meta-sync, gen-env | ~0 |
| **ollama** | bulk classify, skill audit | local only |
| **rag** | lookup in `docs/rag/` | small read |
| **cursor** | wiring, refactor, architecture | full agent chat |

**Tier order:** first match wins. Ollama is skipped when unavailable.

## Scope & roadmap

Today the happy path is **Cursor + Ollama + monorepo**. CLI and MCP are IDE-agnostic; paid APIs and alternate local runtimes are **not** wired yet.

**Full matrix (✅ / ❌ / 🔜) + acceptance criteria + GitHub issues:** [docs/ROADMAP.md](docs/ROADMAP.md) · [docs/ROADMAP-RU.md](docs/ROADMAP-RU.md)

| Area | ✅ today | 🔜 v0.5+ |
|------|----------|----------|
| Executors | `tool`, `python`, `ollama`, `rag` | `cloud_llm`, `openai_compat` local |
| Agent host | Cursor MCP + token baseline | Claude Desktop, Continue |
| Config | `OLLAMA_URL` / `OLLAMA_MODEL` | `local_llm.provider`, `cloud_llm.provider` |

## Install

**Python 3.12+** (CI and PyPI builds use 3.12).

```bash
pip install greedy-token
# with Cursor MCP server:
pip install "greedy-token[mcp]"
# editable (monorepo):
cd projects/greedy-token-home/dev && ./scripts/install.sh
```

```bash
export GREEDY_TOKEN_ROOT=/path/to/workspace   # optional; auto-detect when markers exist
```

## Cursor integration (recommended)

**Full guide (any workspace / PyPI):** [docs/cursor-setup.md](docs/cursor-setup.md) · [docs/cursor-setup-RU.md](docs/cursor-setup-RU.md)

Starter kit in this repo (copy into your project):

| Template | Copy to |
|----------|---------|
| [`examples/cursor/mcp.json`](examples/cursor/mcp.json) | `.cursor/mcp.json` |
| [`examples/cursor/rules/token-economy.mdc`](examples/cursor/rules/token-economy.mdc) | `.cursor/rules/token-economy.mdc` |

```bash
pip install "greedy-token[mcp]"
mkdir -p .cursor/rules
# from a greedy-token clone, or paste from the docs:
cp examples/cursor/mcp.json .cursor/mcp.json
cp examples/cursor/rules/token-economy.mdc .cursor/rules/token-economy.mdc
```

Then: **Settings → MCP → greedy-token → Enable → Refresh** → **new** Agent chat.

Expected: **5 MCP tools** (including `greedy_token_pipeline`).

## MCP tools

| Tool | Purpose |
|------|---------|
| `greedy_token_search` | Ripgrep: `query` + optional `path` |
| `greedy_token_rag` | Search `docs/rag/` chunks |
| `greedy_token_route` | Recommend tier + token footer |
| `greedy_token_pipeline` | Multi-step chain (python → ollama → rag) |
| `greedy_token_usage` | Aggregate savings from `~/.greedy-token/usage.jsonl` |

Every tool response ends with a **Token economy** block — show it to the user.

### Pipeline (multi-step)

```text
pipeline: meta-audit configurator-boolean
```

or:

```text
pipeline: check-meta-sync then audit-skill configurator-boolean
```

Named recipes (`pipeline --list`):

| Recipe | Steps |
|--------|-------|
| `meta-audit` | python → ollama |
| `meta-rag` | python → rag |
| `search-rag` | rg → rag |

Footer includes **per-step savings** table:

```text
Per-step savings (if each step were a separate naive Cursor chat):
   #  step                   executor     ms   spent  baseline     saved  billing
   1  check-meta-sync        python       83       0     9,487     9,487  local script
   2  audit-skill            ollama     2698   2,507     9,499     6,992  local Ollama

Saved by executor (sum of per-step savings):
  python (script)              steps=1  spent ~0      saved ~9,487
  ollama (local LLM)           steps=1  spent ~2,507  saved ~6,992
```

## CLI commands

| Command | Purpose |
|---------|---------|
| `greedy-token route "…"` | Recommend tier + scoring |
| `greedy-token estimate "…"` | Token-aware estimate + tier scan |
| `greedy-token run "…" [--execute]` | Route + dry-run / read-only execute |
| `greedy-token pipeline "…" [--execute]` | Multi-step pipeline |
| `greedy-token pipeline --list` | Named pipeline recipes |
| `greedy-token rag QUERY` | Search `docs/rag/` |
| `greedy-token scripts --list` | Workspace script wrappers |
| `greedy-token scripts --run ID [--execute]` | Run wrapper |
| `greedy-token audit-context` | Rules/skills token audit |
| `greedy-token tokens PATH…` | Count tokens in paths |
| `greedy-token compress` | Short prompt (stdin; `--ollama`) |
| `greedy-token report [--since 7d]` | Usage telemetry aggregate |
| `greedy-token config [--init] [--export]` | Ollama URL/model settings |
| `greedy-token-mcp` | Start MCP server (stdio) |

Global: `--no-log` disables telemetry for one invocation.

> **Pipeline execute:** MCP `greedy_token_pipeline` and CLI `greedy-token pipeline` are **dry-run** by default. Pass `execute=true` (MCP) or `--execute` (CLI) to run allowlisted steps.

## Testing

Requires **Python 3.12+** (same as CI).

```bash
cd projects/greedy-token-home/dev && ./scripts/install.sh
source .venv/bin/activate
cd ../greedy-token
python -m pytest tests/ -v
```

Optional integration tests (real monorepo files) run when the checkout includes `stacks/java-spring/`; set `GREEDY_TOKEN_ROOT` to override the workspace root.

## Examples

```bash
# Search (0 LLM tokens)
greedy-token run "find baseUrl in configurator-option-presets.html" --execute

# RAG lookup
greedy-token rag "baseUrl -D flag"

# Ollama tier
greedy-token route "audit skill configurator-boolean"

# Pipeline dry-run
greedy-token pipeline "pipeline: meta-audit configurator-boolean"

# Pipeline execute (python + ollama)
greedy-token pipeline "check-meta-sync then audit-skill configurator-boolean" --execute

# Savings report
greedy-token report --since 7d
```

## Token economy footer

Single-tool responses include:

- **This call** — executor, spent, billing (local vs cloud)
- **Cursor baseline** — rules + task + overhead
- **Saved vs naive Cursor chat**

Pipeline adds **per-step** baseline / spent / saved and **saved by executor**.

> **Note:** MCP executor steps are local/cheap. Agent chat wrapper (rules + your message + reply) still uses Cursor tokens.

## Usage telemetry

Log file: `~/.greedy-token/usage.jsonl` (disable: `GREEDY_TOKEN_LOG=0`).

Each event: tier, `est_tokens`, `cursor_baseline`, `cursor_saved`, `duration_ms`.

Pipeline logs **one event per step**. When the log exceeds `GREEDY_TOKEN_LOG_MAX_BYTES` (default 5 MiB), it rotates to `usage.jsonl.1`, `.2`, …; `report` reads the active log and archives.

## Environment

| Var | Default |
|-----|---------|
| `GREEDY_TOKEN_ROOT` | auto-detect or required |
| `OLLAMA_URL` | from config or `http://localhost:11434` |
| `OLLAMA_MODEL` | from config or `qwen2.5-coder:7b-instruct-q4_K_M` |
| `GREEDY_TOKEN_LOG` | `~/.greedy-token/usage.jsonl` |
| `GREEDY_TOKEN_LOG_MAX_BYTES` | `5242880` (5 MiB) |
| `GREEDY_TOKEN_LOG_MAX_FILES` | `5` rotated archives |

## Ollama config

Priority (low → high): defaults → `~/.greedy-token/config.yaml` → `$GREEDY_TOKEN_ROOT/.greedy-token.yaml` → `OLLAMA_*` env.

```bash
greedy-token config init
greedy-token config init --model llama3.2 --url http://192.168.1.10:11434
greedy-token config
eval "$(greedy-token config --export)"
```

## Routing config

| File | Purpose |
|------|---------|
| `src/greedy_token/config/routes.yaml` | Task routing patterns |
| `src/greedy_token/config/pipelines.yaml` | Named pipeline recipes |

## `--execute` safety

Auto-execute (read-only or stdout-only): `rg`, `jq`, `check-meta-sync`, pipeline steps in allowlist.

Rsync / migrate / batch-inventory — dry-run only unless run manually.

## License

MIT
