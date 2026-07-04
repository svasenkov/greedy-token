# llm-opt

Route dev tasks through **tool → python → ollama → RAG** before escalating to Cursor/Claude.

```
Your task  →  llm-opt  →  rg/jq | scripts | Ollama | docs/rag | Cursor
```

Install once, point at your workspace root, route every task through the cheapest tier that can handle it.

## Install

```bash
pip install llm-opt-cli
# or editable: pip install -e .
# or from git: pip install git+https://github.com/svasenkov/llm-opt.git
```

PyPI package name is **`llm-opt-cli`** (`llm-opt` on PyPI is taken by another project). CLI command: `llm-opt`.

## Workspace root

`llm-opt` runs against a project directory (monorepo, app repo, etc.):

```bash
export LLM_OPT_ROOT=/path/to/your-workspace
```

Auto-detect works when the workspace has `docs/phase-manifest.json` and `scripts/check-meta-sync.sh` (e.g. [zero-design-system](https://github.com/svasenkov/zero-design-system)). Otherwise set `LLM_OPT_ROOT` explicitly.

## Commands

| Command | Purpose |
|---------|---------|
| `llm-opt route "…"` | Recommend: tool \| python \| ollama \| rag \| cursor + scoring |
| `llm-opt estimate "…"` | Token-aware estimate: complexity, est_tokens, tier scan |
| `llm-opt run "…" [--execute]` | Route + dry-run / **read-only** execute |
| `llm-opt scripts --list` | List workspace script wrappers |
| `llm-opt scripts --run ID [--execute]` | Dry-run / execute read-only wrapper |
| `llm-opt audit-context` | Size of always-on rules/skills (tokens) |
| `llm-opt tokens PATH…` | Count tokens in files/directories |
| `llm-opt rag QUERY` | Search chunks in `docs/rag/` |
| `llm-opt compress` | Short prompt version (stdin; `--ollama` for LLM) |

## Tier order

```
tool (rg/jq) → python → ollama → rag → cursor
```

First matching tier wins. Ollama is **optional** — if unavailable, the tier is skipped.

## Examples

```bash
llm-opt route "find baseUrl"
# → tool (rg)

llm-opt estimate "refactor header layout"
# → cursor, complexity=high

llm-opt route "batch inventory template-project"
# → ollama

llm-opt route "sync phase-manifest and skills-map"
# → python

llm-opt route "ADR 002 baseUrl pattern"
# → rag
```

## Environment

| Var | Default |
|-----|---------|
| `LLM_OPT_ROOT` | auto-detect or required |
| `OLLAMA_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `qwen2.5-coder:14b` |

## `--execute`

Read-only only: `rg`, `jq`, `check-meta-sync.sh`. Rsync/migrate/ollama — dry-run; run manually.

## Route config

`src/llm_optimizer/config/routes.yaml` — customize patterns and commands for your workspace.

## License

MIT
