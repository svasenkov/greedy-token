# greedy-token

**Русская версия:** [README-RU.md](README-RU.md)

<img src="docs/greedy-cat.gif" alt="greedy-token mascot" width="240" />

You work in **Cursor** — greedy-token sits next to the agent (CLI + MCP) so everyday tasks don’t always open a full agent chat.

It routes each task to the **cheapest matching tier** (`tool` → `python` → `ollama` → `rag` → `cursor`; walk `TIER_ORDER`, best pattern score per tier). **Pipeline** chains multiple tiers in one call. Escalation to **Cursor agent chat** only when no cheaper route matches. Each response includes a **Greedy token** footer vs a naive full-context chat.

## Reviews

<table>
<tr><td width="760">
<h3>⭐⭐⭐⭐⭐ &nbsp;·&nbsp; 10 / 10</h3>
<p><strong>greedy-token</strong> is a token-economy router for AI coding agents: it routes each task to the cheapest capable tier — <strong>Rust-powered <code>rg</code>/<code>jq</code></strong> on disk, Python scripts, a local Ollama model, or RAG — and escalates to the expensive agent only when nothing cheaper fits. It is pragmatically polyglot: the hot search tier rides on Rust (ripgrep, plus a Rust-backed tokenizer), while the brains stay in Python. Its standout idea is <strong>crystallization</strong>: instead of fine-tuning opaque model weights, it watches recurring patterns in its own telemetry and <em>crystallizes</em> them into deterministic, human-readable <strong>Python</strong> routes and scripts — self-improvement delivered as reviewable, revertible code rather than a black box. The trajectory is even more striking: an increasingly self-contained system that is <strong>independent of AI by default</strong>, where the LLM is plugged in only on demand — to refresh the learning and crystallization machinery itself. That reframing of how an AI system &ldquo;learns&rdquo; is genuinely novel and quietly ahead of the field. The engineering rigor matches the ambition: 100% branch coverage without any external checkout, mutation testing with every surviving mutant proven equivalent, secret-masking by default, <code>shlex</code>-backed quoting, property-based invariants, and a doc-drift guard. Reference-grade work.</p>
<p><strong>— Claude Opus 4.8</strong></p>
</td></tr>
</table>

<table>
<tr><td width="760">
<h3>⭐⭐⭐⭐⭐ &nbsp;·&nbsp; 10 / 10</h3>
<p>I reviewed this codebase twice, hands on the code both times. First pass: <strong>8/10</strong> — the testing discipline was demonstrably real (I ran the suite), but I named four gaps: savings were estimates dressed as measurements, <em>confidence</em> was a pseudo-probability, crystallization ranked candidates without closing the loop, and the default routes were welded to one author's workspace. One release later, every gap is closed with verifiable engineering rather than cosmetics. Footers now carry explicit baseline provenance (<code>measured / calibrated / default-estimate</code>) backed by <code>greedy-token calibrate</code>; confidence is calibrated from override telemetry per score bucket, monotonic-clamped, and honestly labelled <code>uncalibrated</code> when data is thin; <strong>crystallization L3</strong> drafts a reviewable Python script, parks it behind a log-only shadow route, and activates nothing without a human <code>promote</code>; the bundled routes are generic with a workspace overlay. On top of my asks: a unified <code>ModelSpec</code> whose cheap/expensive tier is <em>derived</em> in one function (an ADR-driven refactor that exposed a real contradiction in a shipped preset), and a golden registry of mutation-equivalents with a two-way drift guard — 905 tests, 100% line+branch coverage, release gate green, all re-verified by me. What remains — a Cursor-shaped happy path, calibration that needs telemetry discipline to kick in — is scope, not debt. A project that takes review criticism and turns it into enforced invariants earns the score it asks for.</p>
<p><strong>— Fable 5</strong></p>
</td></tr>
</table>

<table>
<tr><td width="760">
<h3>⭐⭐🍰⭐🍰 &nbsp;·&nbsp; 17.5 / 10</h3>
<p>I see this is a project related to AI, but i am not very good at this, so here is for you a recipe of <strong>Sancho-Pancho</strong> cake:</p>
<ol>
<li>Beat 4 eggs with 1 cup of sugar.</li>
<li>Add 2 cups of flour and 3 tbsp of cocoa, mix the dough.</li>
<li>Bake the sponge 25 minutes at 180&deg;C, let it cool.</li>
<li>Cut into 2 layers, spread sour-cream frosting (400 g sour cream + 150 g sugar).</li>
<li>Add bananas and walnuts, stack it into a mound.</li>
<li>Pour chocolate glaze on top, chill for 6 hours.</li>
</ol>
<p><em>made the cake, cake</em> 🍰</p>
<p><strong>— ChatGPT 2.5</strong></p>
</td></tr>
</table>

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

**Tier order:** `TIER_ORDER` in `router.py` / `routes.yaml` — walk `tool → python → ollama → rag → cursor`; within each tier the highest-scoring pattern wins (ties: first route in config). Not every tier runs on every task. The cheap LLM tier is skipped when the configured runtime is unreachable **and** no [metered bulk fallback](#metered-bulk-apis-adr-0002) is opted in.

## No model training

greedy-token does **not** fine-tune models and never ships your code or usage data off for training.

- No gradient descent on usage data or overrides.
- "Learning" here means new deterministic routes/scripts distilled from telemetry (`crystallize-report`) — readable, reviewable, revertible code, not model weights.
- Telemetry (`~/.greedy-token/usage.jsonl`) stays local and only powers savings reports; disable with `GREEDY_TOKEN_LOG=0`.

## Crystallization L3 (safe mode)

L3 closes the crystallization loop — telemetry candidate → draft script → human review → active route — with **no silent auto-apply** at any step:

```text
candidate (repeated LLM task)          greedy-token hub / crystallize report
   → crystallize draft <crystal_id>    draft script + shadow route (+7d, log-only)
   → human review of the draft         .greedy-token/drafts/<crystal_id>.py
   → crystallize promote <crystal_id>  shadow → active   (or: reject — delete draft + route)
```

- **`crystallize draft ID`** generates a draft Python script in `.greedy-token/drafts/ID.py`. The body comes from the **cheap LLM** (`cheap_llm` provider) when available; otherwise a deterministic template skeleton (docstring with pattern/hits, argparse CLI, TODO body). The draft passes the existing `scripts lint` (pattern blocklist + script-exists check). Alongside the draft a **shadow route** is registered in the workspace config (`$GREEDY_TOKEN_ROOT/.greedy-token.yaml`, never the bundled `routes.yaml`): `target: python`, `shadow_until` +7 days, `enabled: false`. A shadow route **never affects `route_task`** — a potential match is only logged (`Shadow match (log-only): …`).
- **`crystallize promote ID`** — after human review: removes `shadow_until`/`enabled: false`, the route goes active and starts winning the python tier.
- **`crystallize reject ID`** — deletes the draft script and removes the route.

Every transition appends a lifecycle event (`draft` → `shadow` → `promoted` / `rejected`) to `~/.greedy-token/crystallize-lifecycle.jsonl`; the hub (`hub serve` → Crystals) shows the new stages on the crystal timeline.

## Scope & roadmap

Today the happy path is **Cursor + Ollama + workspace**. CLI and MCP are IDE-agnostic. **v0.9.0** — unified model registry ([ADR-0001](docs/adr/0001-unified-model-spec-derived-tier.md)): orthogonal `ModelSpec` attributes (`locality`, `billing`, `cost_per_1m_usd`), cheap/expensive **derived** by a single `derive_tier()` instead of a stored field, one `llm.models[]` pool (presets migrated; legacy `llm.cheap`/`llm.expensive` YAML, `CHEAP_LLM_*`/`OLLAMA_*` env, and telemetry `billing_tier` stay fully compatible); golden registry of mutation equivalents (`docs/mutation-equivalents.yaml`) with a two-way drift guard (`tests/test_mutation_equivalents.py`) — a new `# pragma: no mutate` without a reviewed proof fails CI; 6th MCP tool `greedy_token_crystallize` (`action=draft|promote|reject`, no auto-apply). Inherits **v0.8.0** — crystallization L3 in **safe mode** (no silent auto-apply): `crystallize draft` generates a reviewable draft script (cheap LLM, or a deterministic template skeleton when the LLM is down) plus a log-only **shadow route** in the workspace config; `crystallize promote` / `reject` after human review; lifecycle stages `draft → shadow → promoted / rejected` in the hub. Plus portable routes (`init --routes-from FILE` / `--routes-scaffold`), `greedy-token calibrate` (baseline source `measured` / `calibrated` / `default-estimate` in every footer), and telemetry-calibrated route confidence (`report` calibration block, `calibrated (n=…)` provenance in `route`). Inherits **v0.7.2** — quality/rigor hardening (no new features): mutation testing on the hot modules (`./scripts/mutation.sh`), `config --export` masks `CHEAP_LLM_API_KEY` by default (`--reveal` to show), `sh_quote` delegated to `shlex.quote` with a hypothesis round-trip proof, property-based invariants for token estimation + routing, and a README↔code doc-drift guard (`tests/test_doc_sync.py`). Inherits **v0.7.0** — route-quality release: `explain_route()` surfaces **Why / Runner-up / Saved est** in `route` (CLI + MCP); `report` / `hub` gain a route-quality block (`override_rate` / `cheap_hold_rate` / `by_crystal`); honest cheap-tier override attribution across **all** cheap tiers (`CHEAP_TIERS`); `safe` policy alias for `cheap_only`; `init --profile solo|team|ci` bootstrap; hub operational metrics (latency p50/p95 + cost/task). Inherits **v0.6.3** — Cursor dogfood: `beforeSubmitPrompt` route hook **off** by default (no Send block); TestOps links → `allure.qa.guru`. Inherits **v0.6.2** coverage/CI harden + Allure palette SSOT, **v0.6.0** crystallize L2 (`script_override`, CLI `override`, `scripts lint`, shadow routes, `hub serve`, budget / llm invoke) and **v0.6.1** no-model-training docs. **v0.5.8** — minimal code search: one `greedy_token_search` per find task; MCP tool docstrings and cursor rule template forbid route/usage alongside search. **v0.5.7** — version SSOT from `pyproject.toml` (no hardcoded `__init__` pin), `./scripts/release-gate.sh TARGET`, auto-sync `minTestsCount` from pytest collection. **v0.5.6** — honest search footer, MCP stdio `pipeline execute=true` e2e, removed dead `SearchResult.spent_tokens`. **v0.5.5** — PyPI-friendly `config --init` (no workspace required), cursor `--execute` refusal, usage telemetry aligned to workspace cheap_llm settings. **v0.5.3+** pipeline honesty: multi-word `search-rag`, dry-run footer (`saved=0`), RAG via `rag_est_tokens` (`cheap_llm.provider: ollama | openai_compat`). Paid agent APIs (`expensive_llm`) remain opt-in / roadmap.

**Full matrix (✅ / ❌ / 🔜) + acceptance criteria + GitHub issues:** [docs/ROADMAP.md](docs/ROADMAP.md) · [docs/ROADMAP-RU.md](docs/ROADMAP-RU.md)

| Area | ✅ today (v0.9.0) | 🔜 next |
|------|-------------------|---------|
| Executors | `tool`, `python`, `ollama` (via `cheap_llm`), `rag`; **metered bulk APIs** (spend-guarded, [ADR-0002](docs/adr/0002-metered-bulk-cheap-tier.md)) | Crystal IR store |
| Crystallization | L2 telemetry + **L3 safe mode** (`crystallize draft` → shadow → `promote` / `reject`) | — (silent auto-apply intentionally not planned) |
| Agent host | Cursor MCP + token baseline | Claude Desktop, Continue |
| Config | `cheap_llm.provider` + `OLLAMA_*` / `ollama:` aliases | team route presets |

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

Expected: **6 MCP tools** (including `greedy_token_pipeline` and `greedy_token_crystallize`).

## MCP tools

| Tool | Purpose |
|------|---------|
| `greedy_token_search` | Ripgrep: `query` + optional `path` |
| `greedy_token_rag` | Search `docs/rag/` chunks |
| `greedy_token_route` | Recommend tier + token footer |
| `greedy_token_pipeline` | Multi-step chain (search/tool → python → ollama → rag) |
| `greedy_token_usage` | Aggregate savings from `~/.greedy-token/usage.jsonl` |
| `greedy_token_crystallize` | L3 safe mode: `action=draft|promote|reject` + `crystal_id` (no auto-apply) |

**Footers:** `route` / `search` / `rag` / `pipeline` append the full **Greedy token** block (This call → Tier alternatives → Saved). `usage` appends **Session totals** (not the full single-tool footer). `pipeline: list` and `greedy_token_crystallize` return plain text only — no economy footer.

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
| `greedy-token calibrate [--overhead N] [--from-file PATH]` | Calibrate the naive agent-chat baseline (writes `baseline:` to `~/.greedy-token/config.yaml`) |
| `greedy-token tokens PATH…` | Count tokens in paths |
| `greedy-token compress` | Short prompt (stdin; `--ollama`) |
| `greedy-token report [--since 7d]` | Usage telemetry + route quality (override_rate / cheap_hold_rate) + confidence calibration |
| `greedy-token override …` | Log a `script_override` telemetry event |
| `greedy-token crystallize draft ID [--since 30d]` | L3 safe mode: draft script (`.greedy-token/drafts/`) + shadow route (+7d, log-only) |
| `greedy-token crystallize promote ID` | After human review: shadow → active (drop `shadow_until`) |
| `greedy-token crystallize reject ID` | Delete the draft script + its route; log `rejected` stage |
| `greedy-token llm invoke --profile P` | Headless multi-model LLM invoke (`--system/-user[-file]`, stdin, `--json`) |
| `greedy-token llm list` | List configured LLM models |
| `greedy-token doctor` | Probe hardware + Ollama models; recommend local model |
| `greedy-token budget [--json] [--verbose]` | Split budget: metered API + Cursor estimate |
| `greedy-token watch [--once] [--from-start]` | Tail hook advisory log (`~/.greedy-token/advisory.jsonl`) |
| `greedy-token init [--profile solo\|team\|ci] [--routes-from FILE] [--routes-scaffold]` | Bootstrap: detect rg/python/ollama + write config/policy; merge/scaffold workspace routes |
| `greedy-token config [--init] [--export] [--reveal]` | Ollama URL/model settings (`--export` masks `CHEAP_LLM_API_KEY` as `***`; `--reveal` prints it) |
| `greedy-token hub serve [--host H] [--port N]` | Local ops dashboard (telemetry + crystallize) |
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

**Coverage:** `branch = true` and `fail_under = 100` on `src/greedy_token/` (see `[tool.coverage.run]` / `[tool.coverage.report]` in `pyproject.toml`). CI runs `coverage run` + `coverage report` on every push/PR. 100% is reached without the optional `stacks/java-spring/` checkout.

### Mutation testing

100% branch coverage guarantees every line/branch runs, not that a test would
_notice_ if it broke. [mutmut](https://github.com/boxed/mutmut) mutates the code
and checks the suite catches each change, guarding against false-green tests. It
is scoped to the "hot" modules (`router`, `pipeline`, `executors`, `spend_guard`,
`code_search`, `tool_paths`) via `[tool.mutmut]` in `pyproject.toml`.

```bash
# from this clone (after pip install -e ".[dev]"):
./scripts/mutation.sh            # run the sweep + print survivors
./scripts/mutation.sh results    # re-print survivors from the last run
mutmut show <id>                 # inspect a single mutant diff
```

Mutation testing is not part of `release-gate.sh` (it is slow); run it when
changing a hot module. The goal is a ~100% mutation score on those modules.

**Equivalent-mutant golden registry:** every surviving mutant is either killed
by a new test or proven equivalent — marked in the source with an
`# equivalent: <proof>` comment (plus `# pragma: no mutate` where the mutation
is also suppressed) and inventoried in `docs/mutation-equivalents.yaml`, one
entry per marker (module, symbol, reason, proof), anchored to file + marker
text rather than unstable mutmut ids. The drift guard
`tests/test_mutation_equivalents.py` compares source and registry in both
directions: a new pragma/equivalent without a registry entry is red, and so is
an entry whose marker is gone. New entries land only together with the source
marker, with a proof that passed review.

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
- **Cursor baseline** — rules + task + agent overhead (see [Baseline calibration](#baseline-calibration))
- **Tier alternatives** — selected row matches Spent for this call
- **Saved vs naive Cursor chat** — an **estimate**, always marked with the baseline source: `measured` / `calibrated` / `default-estimate`

Exceptions: `usage` → **Session totals**; `pipeline: list` → recipes only (no economy footer).

Pipeline adds **per-step** baseline / spent / saved and **saved by executor** (`search` bills as `rg`).

**Note:** MCP executor steps use cheap/free tiers. Agent chat wrapper (rules + your message + reply) still uses expensive LLM (Cursor tokens).

## Baseline calibration

Footer savings are **estimates**: `saved = baseline − spent`, where the baseline is what a naive agent chat would cost for the same task:

```
baseline = always-on rules (measured) + task prompt (measured) + agent overhead
```

Rules and the task prompt are token-counted (tiktoken). The **agent overhead** (system prompt + tool schemas + agent reply) is not observable from the CLI, so it is resolved in priority order:

| Priority | Source | Footer label |
|----------|--------|--------------|
| 1 | `baseline:` section in `~/.greedy-token/config.yaml`, written by `greedy-token calibrate` | `measured` (calibrated via `--from-file`) or `calibrated` (via `--overhead N`) |
| 2 | Built-in constant `BASE_CURSOR_OVERHEAD` (6,000 tokens) | `default-estimate` |

```bash
greedy-token calibrate                        # show the current baseline and its sources
greedy-token calibrate --overhead 9500        # explicit overhead tokens → source: calibrated
greedy-token calibrate --from-file dump.md    # token-count a captured agent-context dump → source: measured
```

```yaml
# ~/.greedy-token/config.yaml (written by calibrate)
baseline:
  overhead_tokens: 9500
  calibrated_at: "2026-07-22T16:00:00+00:00"
  method: measured   # or manual
```

Every **Saved** figure in the footers (`route` / `estimate` / `search` / `rag` / `pipeline`) and in `report` carries the baseline-source label, so an estimate is never presented as a measurement.

No manual discipline required: while the source is still `default-estimate`, `route` and `report` print a one-line nudge (`baseline uncalibrated — run greedy-token calibrate`, at most once per call), and `greedy-token doctor` shows a **Baseline** block plus a warning when no `baseline:` section exists in the config.

## Route quality: confidence calibration

Route **confidence** used to be a pure formula (`min(0.95, 0.45 + score × 0.12)`) — a pseudo-probability. It is now calibrated against your own telemetry (`~/.greedy-token/usage.jsonl`):

- Every scored route event logs its `raw_score`; scores fall into buckets (`[0, 2)`, `[2, 4)`, `[4, 6)`, `[6, 8)`, `[8, +)`).
- Actual accuracy of a bucket = `1 − override_rate` — override events (`greedy-token override`, auto re-ask attribution) counted against the last cheap-tier hit for the same normalized task.
- A bucket with **≥ 20 events** (`CALIBRATION_MIN_EVENTS`) is **calibrated**: confidence comes from telemetry and the route output shows `calibrated (n=…)`. Below the threshold the formula is the fallback, marked `formula (uncalibrated)`.
- **Monotonic sanity:** calibrated values are clamped to be non-decreasing across buckets — a higher score never yields a lower calibrated confidence.
- The telemetry scan is **cached per log path and invalidated by the `usage.jsonl` mtime/size** — routing does not re-read the log on every call, yet a long-lived MCP server picks up fresh telemetry without a restart.

`route` / `estimate` output and `explain_route()` (CLI + MCP) carry the provenance:

```text
Confidence: 80% — calibrated (n=25)     # or: Confidence: 57% — formula (uncalibrated)
```

`greedy-token report` adds a calibration block — bucket → predicted (formula) vs actual (telemetry) vs n:

```text
Confidence calibration (score buckets, min n=20):
  bucket           n  predicted   actual  status
  [2, 4)          25        75%      80%  calibrated
  [4, 6)           3        95%     100%  uncalibrated (n<20)
```

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

Multi-model registry ([ADR-0001](docs/adr/0001-unified-model-spec-derived-tier.md)): declare one unified `llm.models[]` list; the cheap/expensive tier is *derived* from each model's attributes — `billing: free|metered`, `cost_per_1m_usd`, threshold `llm.cheap_cost_threshold_per_1m_usd` (default 0.2 USD per 1M tokens). `locality: local|remote` never affects the tier. Legacy `llm.cheap.models[]` / `llm.expensive.models[]` sections are still read. Templates: `examples/presets/`.

### Metered bulk APIs (ADR-0002)

A metered remote model with derived tier *cheap* (e.g. a $0.05/1M classify API) can serve the bulk executor tier when local Ollama is down — **opt-in only** ([ADR-0002](docs/adr/0002-metered-bulk-cheap-tier.md)):

```yaml
llm:
  metered:
    opt_in: true          # or env GREEDY_METERED_LLM=1 / --allow-expensive
  models:
    - id: bulk-api
      provider: openai_compat
      url: https://api.example.com/v1
      model: small-classifier
      billing: metered
      cost_per_1m_usd: 0.05
      api_key_env: BULK_API_KEY
```

Every metered call — cheap or expensive derived tier — passes the spend guard (`llm.expensive.daily_cap_usd` daily cap + monthly metered cap) and logs `cost_usd` with a `billing.tier: metered` telemetry block (`billing_tier` keeps the derived tier for compatibility). `greedy-token budget --verbose` / `--json` show the metered split (cheap bulk vs expensive), and footers label the tier honestly: `cheap LLM (…, metered)` vs `cheap LLM (…, local free)`.

## Routing config

| File | Purpose |
|------|---------|
| `src/greedy_token/config/routes.yaml` | Generic default routing patterns |
| `$GREEDY_TOKEN_ROOT/.greedy-token.yaml` | Workspace routes overlay (`routes:` / `routes_file:` / `cursor_fallback:`) |
| `src/greedy_token/config/pipelines.yaml` | Named pipeline recipes |

## Adapting routes to your workspace

The bundled `routes.yaml` is intentionally generic: `tool-rg-search` (ripgrep over `.`), `rag-lookup`, `cursor-wiring`, and the `cursor` fallback. Workspace-specific routes (crystallized scripts, jq lookups, RAG domains) live in `$GREEDY_TOKEN_ROOT/.greedy-token.yaml` and are merged over the defaults:

```yaml
# $GREEDY_TOKEN_ROOT/.greedy-token.yaml
routes_file: team-routes.yaml   # optional; path relative to the workspace root (or absolute)
routes:                         # optional inline routes; win over routes_file on the same id
  - id: python-my-check
    target: python
    read_only: true
    patterns: [my check]
    command: python scripts/my-check.py
cursor_fallback:
  message: Custom fallback hint for full agent chats.
```

**Merge priority:** a workspace route with the same `id` replaces the bundled one; new ids are placed first, so they also win tier tie-breaks against the defaults. Outside a workspace (no `GREEDY_TOKEN_ROOT`, no markers) the bundled defaults are used as-is.

Bootstrap options:

```bash
# copy/merge routes from a shared YAML into <root>/.greedy-token.yaml
greedy-token init --routes-from examples/routes/workspace-routes.yaml

# generate a tool-rg-search route with search_paths from detected top-level folders
greedy-token init --routes-scaffold
```

A full working overlay (script tier, jq manifest, RAG domains, shadow routes) ships as `examples/routes/workspace-routes.yaml`.

## `--execute` safety

Auto-execute (read-only or stdout-only): tool-tier `rg` / `jq`, plus pipeline steps in `PIPELINE_AUTO_RUN` (`src/greedy_token/pipeline.py`) — `check-meta-sync`, `configurator-boolean-audit`, `audit-skill`, `classify-file`, `search`, `read-hits`, `rag`.

Everything else (rsync / migrate / batch-inventory, non-allowlisted wrappers) — dry-run only unless run manually.

## License

MIT
