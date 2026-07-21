# greedy-token

**Русская версия:** [README-RU.md](README-RU.md)

<img src="docs/greedy-cat.gif" alt="greedy-token mascot" width="240" />

You work in **Cursor** — greedy-token sits next to the agent (CLI + MCP) so everyday tasks don’t always open a full agent chat.

It routes each task to the **cheapest matching tier** (`tool` → `python` → `ollama` → `rag` → `cursor`; walk `TIER_ORDER`, best pattern score per tier). **Pipeline** chains multiple tiers in one call. Escalation to **Cursor agent chat** only when no cheaper route matches. Each response includes a **Greedy token** footer vs a naive full-context chat.

[![greedy-token](https://svasenkov.github.io/greedy-token/readme/badge.svg)](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/)

<details>
<summary><strong>Automated tests dashboard</strong> — live metrics + Allure 3 preview</summary>

[![greedy-token stats](https://svasenkov.github.io/greedy-token/readme/stats.svg)](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/)

[![greedy-token metrics](https://svasenkov.github.io/greedy-token/readme/metrics-panel.svg)](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/)

<a href="https://svasenkov.github.io/greedy-token/reports/latest/dashboard/">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://svasenkov.github.io/greedy-token/readme/dashboard-preview-dark.png">
    <img
      src="https://svasenkov.github.io/greedy-token/readme/dashboard-preview.png"
      alt="Allure 3 dashboard — pytest suite, status dynamics"
      width="800"
    />
  </picture>
</a>

Badges and dashboard PNG update after each CI run on `main` (Playwright screenshot of the Allure 3 dashboard).

| Link | Description |
|------|-------------|
| [Dashboard](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/) | MCP/CLI pytest + contract tests |
| [Awesome](https://svasenkov.github.io/greedy-token/reports/latest/awesome/) | Drill-down by epic |
| [CI workflow](https://github.com/svasenkov/greedy-token/actions/workflows/test.yml) | pytest + gh-pages publish |

</details>

```
In Cursor:  your task  →  greedy-token (MCP/CLI)
                 ↓
     route (one tier per task):
       tool → python → ollama → rag → cursor
       walk TIER_ORDER; best pattern score per tier; ollama tier skipped if server down
                 ↓
     pipeline (optional, multi-step):
       e.g. check-meta-sync then audit-skill …
       composes tool / python / ollama / rag steps — not a separate tier
                 ↓
     escalation: Cursor agent chat when no cheaper route matches
```

## What it does

| Layer | When | LLM cost |
|-------|------|----------|
| **tool** (rg) | find / grep / search | ~0 |
| **python** | scripts, meta-sync, gen-env | ~0 |
| **ollama** | bulk classify, skill audit | cheap LLM |
| **rag** | lookup in `docs/rag/` | small read |
| **cursor** | wiring, refactor, architecture | expensive LLM |

### Cheap vs expensive LLM

Greedy-token uses **cheap** and **expensive** in footers and docs. It is about **where token budget goes**.

| Label | What it means | Examples |
|-------|----------------|----------|
| **Cheap LLM** | Inference on **your** runtime (config `cheap_llm`); tier id `ollama` in routes; **0 Cursor/API meter** on that step | [Ollama](https://ollama.com) (native or remote `OLLAMA_URL`), LM Studio, llama.cpp, vLLM, TGI — anything via `cheap_llm.provider: ollama \| openai_compat` |
| **Expensive LLM** | Full **agent chat** with rules, skills, overhead, and reply — what you pay Cursor (or similar) for | **Cursor** agent / Composer today; same bucket for **Claude**, **GPT**, **Copilot** when used as the main coding agent or future `expensive_llm` metered API |

**Free tier** (`tool`, `python`, `rag`) = no LLM inference at all — ripgrep, scripts, reading `docs/rag/` chunks.

**Tier order:** `TIER_ORDER` in `router.py` / `routes.yaml` — walk `tool → python → ollama → rag → cursor`; within each tier the highest-scoring pattern wins (ties: first route in config). Not every tier runs on every task. The cheap LLM tier is skipped when the configured runtime is unreachable.

## No model training

greedy-token does **not** fine-tune models and never ships your code or usage data off for training.

- No gradient descent on usage data or overrides.
- "Learning" here means new deterministic routes/scripts distilled from telemetry (`crystallize-report`) — readable, reviewable, revertible code, not model weights.
- Telemetry (`~/.greedy-token/usage.jsonl`) stays local and only powers savings reports; disable with `GREEDY_TOKEN_LOG=0`.

## Scope & roadmap

Today the happy path is **Cursor + Ollama + workspace**. CLI and MCP are IDE-agnostic. **v0.6.3** — Cursor dogfood: `beforeSubmitPrompt` route hook **off** by default (no Send block); TestOps links → `allure.qa.guru`. Inherits **v0.6.2** coverage/CI harden + Allure palette SSOT, **v0.6.0** crystallize L2 (`script_override`, CLI `override`, `scripts lint`, shadow routes, `hub serve`, budget / llm invoke) and **v0.6.1** no-model-training docs. **v0.5.8** — minimal code search: one `greedy_token_search` per find task; MCP tool docstrings and cursor rule template forbid route/usage alongside search. **v0.5.7** — version SSOT from `pyproject.toml` (no hardcoded `__init__` pin), `./scripts/release-gate.sh TARGET`, auto-sync `minTestsCount` from pytest collection. **v0.5.6** — honest search footer, MCP stdio `pipeline execute=true` e2e, removed dead `SearchResult.spent_tokens`. **v0.5.5** — PyPI-friendly `config --init` (no workspace required), cursor `--execute` refusal, usage telemetry aligned to workspace cheap_llm settings. **v0.5.3+** pipeline honesty: multi-word `search-rag`, dry-run footer (`saved=0`), RAG via `rag_est_tokens` (`cheap_llm.provider: ollama | openai_compat`). Paid agent APIs (`expensive_llm`) remain opt-in / roadmap.

**Full matrix (✅ / ❌ / 🔜) + acceptance criteria + GitHub issues:** [docs/ROADMAP.md](docs/ROADMAP.md) · [docs/ROADMAP-RU.md](docs/ROADMAP-RU.md)

| Area | ✅ today (v0.6.3) | 🔜 next |
|------|-------------------|---------|
| Executors | `tool`, `python`, `ollama` (via `cheap_llm`), `rag` | paid bulk APIs; Crystal IR store |
| Agent host | Cursor MCP + token baseline | Claude Desktop, Continue |
| Config | `cheap_llm.provider` + `OLLAMA_*` / `ollama:` aliases | silent L3 auto-codegen (deferred) |

## Install

**Python 3.12+** (CI and PyPI builds use 3.12).

```bash
pip install greedy-token
# with Cursor MCP server:
pip install "greedy-token[mcp]"
# editable from this clone:
pip install -e ".[dev,mcp]"
# monorepo hub (sibling ../dev):
#   cd ../dev && ./scripts/install.sh
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
| [`examples/cursor/rules/greedy-token.mdc`](examples/cursor/rules/greedy-token.mdc) | `.cursor/rules/greedy-token.mdc` |

```bash
pip install "greedy-token[mcp]"
mkdir -p .cursor/rules
# from a greedy-token clone, or paste from the docs:
cp examples/cursor/mcp.json .cursor/mcp.json
cp examples/cursor/rules/greedy-token.mdc .cursor/rules/greedy-token.mdc
```

Then: **Settings → MCP → greedy-token → Enable → Refresh** → **new** Agent chat.

Expected: **5 MCP tools** (including `greedy_token_pipeline`).

## MCP tools

| Tool | Purpose |
|------|---------|
| `greedy_token_search` | Ripgrep: `query` + optional `path` |
| `greedy_token_rag` | Search `docs/rag/` chunks |
| `greedy_token_route` | Recommend tier + token footer |
| `greedy_token_pipeline` | Multi-step chain (search/tool → python → ollama → rag) |
| `greedy_token_usage` | Aggregate savings from `~/.greedy-token/usage.jsonl` |

**Footers:** `route` / `search` / `rag` / `pipeline` append the full **Greedy token** block (This call → Tier alternatives → Saved). `usage` appends **Session totals** (not the full single-tool footer). `pipeline: list` returns the recipe list only — no economy footer.

### Pipeline (multi-step)

```text
pipeline: meta-audit configurator-boolean
```

or:

```text
pipeline: check-meta-sync then audit-skill configurator-boolean
```

Named recipes (`pipeline --list`):

| Recipe | Steps | Args |
|--------|-------|------|
| `meta-audit` | python → ollama | `<skill>` |
| `meta-rag` | python → rag | `<query>` |
| `search-rag` | rg → rag | `<query> <path>` · multi-word query + `path=` · or `query=` / `path=` kwargs |

`search-rag` reuses `query` for both steps; `path` scopes ripgrep only:

```text
pipeline: search-rag baseUrl configurator-option-presets.html
pipeline: search-rag baseUrl path=configurator-option-presets.html
```

Footer includes **per-step savings** table:

```text
Per-step savings (if each step were a separate naive Cursor chat):
   #  step                   executor     ms   spent  baseline     saved  billing
   1  check-meta-sync        python       83       0     9,487     9,487  script
   2  audit-skill            ollama     2698   2,507     9,499     6,992  cheap LLM

Saved by executor (sum of per-step savings):
  python (script)              steps=1  spent ~0      saved ~9,487
  ollama (cheap LLM)           steps=1  spent ~2,507  saved ~6,992
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
| `greedy-token report [--since 7d]` | Usage telemetry + route quality (override_rate / cheap_hold_rate) |
| `greedy-token init [--profile solo\|team\|ci]` | Bootstrap: detect rg/python/ollama + write config/policy |
| `greedy-token config [--init] [--export]` | Ollama URL/model settings |
| `greedy-token-mcp` | Start MCP server (stdio) |

Global: `--no-log` disables telemetry for one invocation.

**Pipeline execute:** MCP `greedy_token_pipeline` and CLI `greedy-token pipeline` are **dry-run** by default. Pass `execute=true` (MCP) or `--execute` (CLI) to run allowlisted steps.

## Testing

Requires **Python 3.12+** (same as CI). GitHub Actions job **tests (all)** runs the full suite with Allure 3 quality gate, GitHub Pages report, and optional TestOps upload. Line and **branch** coverage on `src/greedy_token/` must stay at **100%** (`branch = true`, `fail_under = 100`).

**CI ethalon:** `.github/_ethalon/` (action pins in `gha-actions.yaml`) → runnable `.github/workflows/`. Same pattern as workspace `tests-java/.github/_ethalon/`. Sync: `./scripts/sync-github-workflows.sh`; CI runs `./scripts/check-github-workflows-sync.sh` before pytest.

```bash
# from this clone (after pip install -e ".[dev,mcp]"):
python -m coverage run -m pytest tests/ -v --alluredir=build/allure-results
python -m coverage report --include='src/greedy_token/*'
npx --yes allure@3.13.0 quality-gate build/allure-results --config allurerc.mjs
npx --yes allure@3.13.0 generate build/allure-results --config allurerc.mjs -o build/allure-report
# monorepo hub alternative: cd ../dev && ./scripts/install.sh && source .venv/bin/activate && cd ../greedy-token
```

**Coverage:** `branch = true` and `fail_under = 100` on `src/greedy_token/` (see `[tool.coverage.run]` / `[tool.coverage.report]` in `pyproject.toml`). CI runs `coverage run` + `coverage report` on every push/PR.

**Layer slices:** module → `tests/pyramid_layers.py` → Allure label `layer` + pytest marker (`-m unit|component|integration|e2e`). CI matrix job `tests` runs each slice separately.

Optional integration tests (real workspace files) run when the checkout includes `stacks/java-spring/`; set `GREEDY_TOKEN_ROOT` to override the workspace root.

**TestOps:** project [5276](https://allure.qa.guru/project/5276) on `allure.qa.guru`. CI uploads when repo secret `ALLURE_TOKEN` is set (`ALLURE_PROJECT_ID` defaults to `5276`, override via repo variable). Pyramid layers (`unit` / `component` / `integration`) are set via Allure label `layer` in `tests/pyramid_layers.py` — same keys as Java `@Layer` and TestOps mappings. Human-readable names use `@allure.title` / `@allure.feature` / `@allure.story` / `@allure.epic` on each test, and `@allure.parent_suite` / `@allure.suite` on each module (`pytestmark`) for TestOps folder names — JUnit `@DisplayName` / `@Feature` equivalent.

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

## Greedy token footer

`route` / `search` / `rag` / `pipeline` responses include:

- **This call** — executor, spent, billing (cheap vs expensive LLM)
- **Cursor baseline** — rules + task + overhead
- **Tier alternatives** — selected row matches Spent for this call
- **Saved vs naive Cursor chat**

Exceptions: `usage` → **Session totals**; `pipeline: list` → recipes only (no economy footer).

Pipeline adds **per-step** baseline / spent / saved and **saved by executor** (`search` bills as `rg`).

**Note:** MCP executor steps use cheap/free tiers. Agent chat wrapper (rules + your message + reply) still uses expensive LLM (Cursor tokens).

## Usage telemetry

Log file: `~/.greedy-token/usage.jsonl` (disable: `GREEDY_TOKEN_LOG=0`).

Each event: tier, `est_tokens`, `cursor_baseline`, `cursor_saved`, `duration_ms`.

Pipeline logs **one event per step**. When the log exceeds `GREEDY_TOKEN_LOG_MAX_BYTES` (default 5 MiB), it rotates to `usage.jsonl.1`, `.2`, …; `report` reads the active log and archives.

## Environment

| Var | Default |
|-----|---------|
| `GREEDY_TOKEN_ROOT` | auto-detect or required |
| `CHEAP_LLM_PROVIDER` | from config or `ollama` (`ollama` \| `openai_compat`) |
| `CHEAP_LLM_URL` / `OLLAMA_URL` | from config or `http://localhost:11434` |
| `CHEAP_LLM_MODEL` / `OLLAMA_MODEL` | from config or `qwen2.5-coder:7b-instruct-q4_K_M` |
| `GREEDY_TOKEN_LOG` | `~/.greedy-token/usage.jsonl` |
| `GREEDY_TOKEN_LOG_MAX_BYTES` | `5242880` (5 MiB) |
| `GREEDY_TOKEN_LOG_MAX_FILES` | `5` rotated archives |

## Cheap LLM config

Priority (low → high): defaults → `~/.greedy-token/config.yaml` → `$GREEDY_TOKEN_ROOT/.greedy-token.yaml` → `CHEAP_LLM_*` / `OLLAMA_*` env (`OLLAMA_*` = url/model aliases). Route tier id remains `ollama`.

```bash
greedy-token config --init
greedy-token config --init --provider openai_compat --url http://localhost:1234 --model local-model
greedy-token config
eval "$(greedy-token config --export)"
```

```yaml
# ~/.greedy-token/config.yaml
cheap_llm:
  provider: ollama          # or openai_compat
  url: http://localhost:11434
  model: qwen2.5-coder:7b-instruct-q4_K_M
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
