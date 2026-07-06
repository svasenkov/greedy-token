# greedy-token

Route dev tasks through **tool → python → ollama → RAG** before escalating to Claude / ChatGPT / Codex / Cursor / etc.

```
Your task  →  greedy-token  →  rg/jq | scripts | Ollama | docs/rag | Cursor
```

## Cursor vs greedy-token (token comparison)

Measured with `greedy-token audit-context` and `greedy-token estimate` against [zero-design-system](https://github.com/svasenkov/zero-design-system) (tiktoken `cl100k_base`; order of magnitude, not API billing).

### Cursor context overhead (every new chat)

| Context | Tokens | Charged when |
|---------|-------:|--------------|
| Always-on rules (`.cursor/rules/*.mdc`) | 2,524 | every chat |
| Skills on disk (`.cursor/skills/*/SKILL.md`) | 26,386 | if agent loads skill |
| Sampled docs (`CONTEXT.md`, `migration-prompts.md`) | 3,349 | if referenced |
| **Sampled set total** | **32,259** | full agent context |
| Naive agent baseline (`rules + 6k overhead + task`) | ~8,530 | default Cursor path |

Rules ≥ 1024 tokens → stable prefix is cache-friendly for Claude API prompt caching.

### Task routing: naive Cursor vs greedy-token

| Task | Naive Cursor | greedy-token route | Est. tokens | Saved vs Cursor | Savings | Command |
|------|-------------:|--------------------|------------:|----------------:|--------:|---------|
| `какой -D flag для baseUrl в e2e config` | ~8,534 | **rag** (95%) | 1,810 | ~6,724 | ~79% | `greedy-token rag "baseUrl -D flag"` |
| `ADR 002 baseUrl pattern` | ~8,530 | **rag** (61%) | 1,806 | ~6,724 | ~79% | `greedy-token rag "ADR 002 baseUrl"` |
| `find baseUrl in e2e properties` | ~8,532 | **tool** (59%) | 0 | ~8,532 | ~100% | `greedy-token run "…" --execute` → `rg` |
| `sync phase-manifest и skills-map` | ~8,532 | **python** (65%) | 0 | ~8,532 | ~100% | `greedy-token scripts --run check-meta-sync --execute` |
| `rsync template-project в monorepo` | ~8,533 | **python** (60%) | 0 | ~8,533 | ~100% | dry-run script; run manually |
| `batch inventory template-project` | ~8,532 | **ollama** (66%) | 0 cloud | ~8,532 | ~100% cloud | `scripts/ollama/batch-inventory.sh` (local LLM) |
| `refactor header layout and wire nav links` | ~8,535 | **cursor** (82%) | 8,535 | 0 | — | new Cursor chat + skill from `docs/skills-map.md` |

**Tier order:** `tool (rg/jq) → python → ollama → rag → cursor` — first match wins. Ollama tier is skipped when unavailable.

**Takeaway:** lookup / search / sync / bulk tasks save **~6.7k–8.5k tokens per request**; wiring and architecture correctly stay on Cursor.

```bash
greedy-token audit-context                    # your workspace overhead
greedy-token estimate "your task here"        # route + savings before opening a chat
```

Install once, point at your workspace root, route every task through the cheapest tier that can handle it.

## Install

```bash
pip install greedy-token
# or editable: pip install -e .
# or from git: pip install git+https://github.com/svasenkov/greedy-token.git
```

`tiktoken` (exact BPE counts via `cl100k_base`) is a required dependency. If install fails
on an unsupported platform, use a Python version with a prebuilt `tiktoken` wheel or install
from source with a Rust toolchain.

### PyPI publish (maintainer)

1. Create project **greedy-token** on [pypi.org](https://pypi.org/manage/projects/)
2. Add trusted publisher: Owner `svasenkov`, repo `greedy-token`, workflow `publish.yml`
3. Publish: GitHub → Releases → re-run workflow or new tag

## Workspace root

`greedy-token` runs against a project directory (monorepo, app repo, etc.):

```bash
export GREEDY_TOKEN_ROOT=/path/to/your-workspace
```

Auto-detect works when the workspace has `docs/phase-manifest.json` and `scripts/check-meta-sync.sh` (e.g. [zero-design-system](https://github.com/svasenkov/zero-design-system)). Otherwise set `GREEDY_TOKEN_ROOT` explicitly.

## Commands

| Command | Purpose |
|---------|---------|
| `greedy-token route "…"` | Recommend: tool \| python \| ollama \| rag \| cursor + scoring |
| `greedy-token estimate "…"` | Token-aware estimate: complexity, est_tokens, tier scan |
| `greedy-token run "…" [--execute]` | Route + dry-run / **read-only** execute |
| `greedy-token scripts --list` | List workspace script wrappers |
| `greedy-token scripts --run ID [--execute]` | Dry-run / execute read-only wrapper |
| `greedy-token audit-context` | Size of always-on rules/skills (tokens) |
| `greedy-token tokens PATH…` | Count tokens in files/directories |
| `greedy-token rag QUERY` | Search chunks in `docs/rag/` |
| `greedy-token compress` | Short prompt version (stdin; `--ollama` for LLM) |

## Tier order

```
tool (rg/jq) → python → ollama → rag → cursor
```

First matching tier wins. Ollama is **optional** — if unavailable, the tier is skipped.

## Examples

```bash
greedy-token route "find baseUrl"
# → tool (rg)

greedy-token estimate "refactor header layout"
# → cursor, complexity=high

greedy-token route "batch inventory template-project"
# → ollama

greedy-token route "sync phase-manifest and skills-map"
# → python

greedy-token route "ADR 002 baseUrl pattern"
# → rag
```

## Environment

| Var | Default |
|-----|---------|
| `GREEDY_TOKEN_ROOT` | auto-detect or required |
| `OLLAMA_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `qwen2.5-coder:14b` |

## `--execute`

Read-only only: `rg`, `jq`, `check-meta-sync.sh`. Rsync/migrate/ollama — dry-run; run manually.

## Route config

`src/greedy_token/config/routes.yaml` — customize patterns and commands for your workspace.

## License

MIT
