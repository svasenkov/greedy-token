# greedy-token

**[Русский](README-RU.md)** · [Why (ELI5)](WHY.md) · [Full guide](docs/guide.md)

<img src="docs/greedy-cat.gif" alt="greedy-token mascot" width="240" />

A router next to Cursor / Claude / Continue: it asks **“do you need a model at all?”** before opening an expensive agent chat.

```text
find / check / docs lookup  →  free tools & scripts
sort-of-AI bulk work        →  local LLM (Ollama, …)
wiring / design             →  expensive agent chat
```

No fine-tuning. No shipping your data for training. It “learns” by adding readable scripts/routes from telemetry — reviewable and revertible.

### What this is / isn’t

| Is | Isn’t |
|----|--------|
| A **prototype** around cheap tiers (rg / scripts / local LLM) + **crystallize** (repeat → deterministic script, 0 LLM next time) | A universal “Cursor token saver” that removes the host LLM |
| Paths that can avoid frontier calls on **CLI / CI / hooks / crystallize**; measured savings require a successful same-task run and authoritative billing | Guaranteed **MCP-chat** dollar savings: by the time an MCP tool runs, Cursor has already called a frontier model |
| `route_task` / `greedy_token_route` → **one** tier by substring heuristics | Auto-chain `rg → python → ollama → docs`; that needs an explicit `pipeline` |
| `rag` tool name kept for compat — implementation is **lexical BM25/FTS** over SQLite FTS5, not embeddings/vector RAG | Production-grade semantic retrieval or universal routing precision outside the frozen corpus |

Headline **★ $82 / ★ $820** below = illustrative **CLI/pipeline mix vs naive agent**, not measured MCP-chat savings.

Under the hood: each task is routed to the cheapest capable tier — `rg`/`jq` tools, trust-approved workspace scripts, a local Ollama model, or lexical RAG — and escalates to the agent chat only when nothing cheaper fits. Workspace scripts run only after an explicit `trust add` approval, with SHA-256 and file identity rechecked before every launch. Repeated telemetry patterns crystallize into reviewable draft routes through an audited `candidate → proposed → approved → applied` lifecycle — nothing activates without a human `promote`. `greedy_token_capabilities` exposes the derived inventory of invocable ops; telemetry lands in `~/.greedy-token/usage.jsonl`.

<details>
<summary><strong>A review</strong> (model write-up — optional reading)</summary>

<table>
<tr><td width="760">
<h3>⭐⭐🍰⭐🍰 &nbsp;·&nbsp; <picture><source media="(prefers-color-scheme: dark)" srcset="docs/guantou-glitch-dark.png"><img src="docs/guantou-glitch.png" alt="罐头" height="36" /></picture> / 10</h3>
<p>I see this is a project related to AI, but I am too dumb for this, so here is a recipe of <strong>Sancho-Pancho</strong> cake for you:</p>
<ol>
<li>Beat 4 eggs with 1 cup of sugar.</li>
<li>Add 2 cups of flour and 3 tbsp of cocoa, mix the dough.</li>
<li>Bake the sponge 25 minutes at 180&deg;C, let it cool.</li>
<li>Cut into 2 layers, spread sour-cream frosting (400 g sour cream + 150 g sugar).</li>
<li>Add bananas and walnuts, stack it into a mound.</li>
<li>Pour chocolate glaze on top, chill for 6 hours.</li>
</ol>
<p><em>made the cake, cake</em> 🍰</p>
<p><strong>— Grok 4.5</strong></p>
</td></tr>
</table>

</details>

[![greedy-token](https://svasenkov.github.io/greedy-token/readme/badge.svg)](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/)

<details open>
<summary><strong>Automated tests dashboard</strong> — live metrics + Allure 3 preview</summary>

[![greedy-token stats](https://svasenkov.github.io/greedy-token/readme/stats.svg)](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/)

[![greedy-token metrics](https://svasenkov.github.io/greedy-token/readme/metrics-panel.svg)](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/)

<a href="https://svasenkov.github.io/greedy-token/reports/latest/dashboard/">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://svasenkov.github.io/greedy-token/readme/dashboard-preview-dark.png">
    <img src="https://svasenkov.github.io/greedy-token/readme/dashboard-preview.png" alt="Allure 3 dashboard" width="800" />
  </picture>
</a>

| Link | What |
|------|------|
| [Dashboard](https://svasenkov.github.io/greedy-token/reports/latest/dashboard/) | pytest + MCP contracts |
| [Awesome](https://svasenkov.github.io/greedy-token/reports/latest/awesome/) | drill-down by epic |
| [CI](https://github.com/svasenkov/greedy-token/actions/workflows/test.yml) | run + gh-pages |

</details>

---

## Money + time: which path should I use?

**Illustrative** USD / month **and** wall-clock per call for a mid-intensity **CLI / pipeline / crystallize** mix vs sending every class of work to a cloud / frontier chat (**$130** / eng · **$1,300** / ×10). Green columns = that scenario’s delta; ★ TOTAL (**★ $82** / **★ $820**) is a **headline for that mix**, not a claim about MCP Agent chat bills.

In a Cursor MCP session the host model is already running — tool footers (`time_saved_ms`, spent/saved) compare tool work to a naive agent *turn*, not “MCP removed the LLM.” Prefer CLI/`pipeline --execute`/hooks when you want 0 frontier tokens for a step.

First matching tier wins. Per-call times are estimates (`time_saved_ms` in footer / `report`, v0.11+).

<p align="center">
  <img src="docs/path-savings-en.svg" alt="greedy-token path table: green savings columns and TOTAL" width="760" />
</p>

<details>
<summary>Plain-text table (copy-paste / a11y)</summary>

| Path | Use when | Don’t use for | Path · 1 eng | Classical · 1 eng | Save · 1 | Path · ×10 | Classical · ×10 | Save · ×10 | ~time · path | ~time · agent | ~time · save | Example |
|------|----------|---------------|--------------|-------------------|----------|------------|-----------------|------------|--------------|---------------|--------------|---------|
| **tool** (rg) | find text in the repo | edits / design | $0 | $30 | $30 | $0 | $300 | $300 | ~1s | ~20s | ~19s | `find baseUrl in configurator-option-presets.html` |
| **python** | a deterministic script already exists | open-ended “fix it” | $0 | $25 | $25 | $0 | $250 | $250 | ~1s | ~20s | ~19s | `meta-audit configurator-boolean` |
| **rag** (lexical BM25/FTS) | answer in `docs/rag/` via local SQLite FTS5 | undocumented code / semantic recall | $0 | $15 | $15 | $0 | $150 | $150 | ~0.5s | ~15s | ~15s | which `-D` flag for baseUrl |
| **ollama** | bulk classify / light audit | precise wiring | $8 | $20 | $12 | $25 | $200 | $175 | ~5s | ~25s | ~20s | classify a list of skills |
| **cursor** | wiring, refactor, judgment | grep / bulk-copy | $40 | $40 | $0 | $400 | $400 | $0 | ~same | ~same | ~0 | change header behavior in one zone |
| **classical LLM** | baseline: big model for everything | — | $130 | $130 | — | $1,300 | $1,300 | — | ~same | ~same | — | paste a whole folder into chat |
| **★ TOTAL** | illustrative CLI/pipeline mix vs naive | — | **$48** | **$130** | **★ $82** | **$425** | **$1,300** | **★ $820** | — | — | **★ ~6 h · 1 / ~60 h · ×10** | **not MCP-chat savings** |

</details>

---

## Start

```bash
pip install "greedy-token[mcp]"
mkdir -p .cursor/rules
cp examples/cursor/mcp.json .cursor/mcp.json
cp examples/cursor/rules/greedy-token.mdc .cursor/rules/greedy-token.mdc
```

**Settings → MCP → greedy-token → Enable → Refresh** → new Agent chat.

```text
find baseUrl in configurator-option-presets.html
```

Expect free `rg` and a spent vs saved footer.

Full setup: [Cursor](docs/cursor-setup.md) · [Claude](docs/claude-setup.md) · [Continue](docs/continue-setup.md)

**Monorepo scripts:** `greedy-token init --routes-from examples/routes/workspace-routes.yaml` (workspace overlay; portable bundled defaults stay generic).

---

## MCP tools

Expected after setup: **8 MCP tools** (including `greedy_token_pipeline` and `greedy_token_crystallize`).

| Tool | Purpose |
|------|---------|
| `greedy_token_search` | Ripgrep: `query` + optional `path` |
| `greedy_token_rag` | Local lexical BM25/FTS over manifest-listed `docs/rag/` chunks (not vector RAG) |
| `greedy_token_route` | Recommend **one** tier + token footer (no auto-chain) |
| `greedy_token_pipeline` | Explicit multi-step chain (search/tool → python → ollama → rag) |
| `greedy_token_usage` | Aggregate savings from `~/.greedy-token/usage.jsonl` |
| `greedy_token_crystallize` | Audited lifecycle: `action=candidates|status|draft|approve|promote|reject` + `crystal_id` (no auto-apply) |
| `greedy_token_capabilities` | Derived capability view: ops + readiness (no execution) |
| `greedy_token_invoke` | Invoke a ready read-only op by stable id (refusals carry the readiness class) |

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
| `greedy-token capabilities list` | Derived op inventory + readiness (`--json`) |
| `greedy-token capabilities show ID` | Inspect one operation (argv, trust, contract) |
| `greedy-token capabilities invoke ID` | Invoke a ready read-only op by id |
| `greedy-token trust add PATH` | Approve the current SHA-256 and identity of a workspace script |
| `greedy-token trust list` | List local workspace script approvals |
| `greedy-token trust verify` | Verify every approval against disk |
| `greedy-token trust revoke PATH` | Remove a local script approval |
| `greedy-token audit-context` | Rules/skills token audit |
| `greedy-token calibrate [--overhead N] [--from-file PATH]` | Calibrate the naive agent-chat baseline (writes `baseline:` to `~/.greedy-token/config.yaml`) |
| `greedy-token tokens PATH…` | Count tokens in paths |
| `greedy-token compress` | Short prompt (stdin; `--ollama`) |
| `greedy-token report [--since 7d]` | Usage telemetry: override/hold signal, explicit task outcomes, and outcome calibration |
| `greedy-token override …` | Log a `script_override` telemetry event |
| `greedy-token crystallize candidates [--since 30d]` | Candidates + derived lifecycle state |
| `greedy-token crystallize status ID` | State + draft/route/trust facts + audit timeline |
| `greedy-token crystallize draft ID [--since 30d]` | Propose: draft script (`.greedy-token/drafts/`) + shadow route (+7d, log-only) |
| `greedy-token crystallize propose ID [--since 30d]` | Alias of `draft` — same propose step |
| `greedy-token crystallize approve ID [--by X] [--reason R]` | Human approval: pins reviewed draft sha256 to this workspace, logs who/why |
| `greedy-token crystallize promote ID [--by X] [--reason R]` | Apply: re-verify the pinned draft bytes → trust + shadow → active |
| `greedy-token crystallize reject ID [--reason R]` | Delete the draft + route + all its trust entries; log `rejected` stage |
| `greedy-token llm invoke --profile P` | Headless multi-model LLM invoke (`--system/-user[-file]`, stdin, `--json`) |
| `greedy-token llm list` | List configured LLM models |
| `greedy-token doctor` | Probe hardware + Ollama models; recommend local model |
| `greedy-token budget [--json] [--verbose]` | Split budget: metered API + Cursor estimate |
| `greedy-token watch [--once] [--from-start]` | Tail hook advisory log (`~/.greedy-token/advisory.jsonl`) |
| `greedy-token init [--profile solo\|team\|ci] [--preset NAME\|URL\|PATH] [--routes-from FILE] [--routes-scaffold]` | Bootstrap: detect rg/python/ollama + write config/policy; merge team route presets / scaffold workspace routes |
| `greedy-token config [--init] [--export] [--reveal]` | Ollama URL/model settings (`--export` masks `CHEAP_LLM_API_KEY` as `***`; `--reveal` prints it) |
| `greedy-token hub serve [--host H] [--port N]` | Local ops dashboard (telemetry + crystallize) |
| `greedy-token-mcp` | Start MCP server (stdio) |

Global: `--no-log` disables telemetry for one invocation.

**Pipeline execute:** MCP `greedy_token_pipeline` and CLI `greedy-token pipeline` are **dry-run** by default. Pass `execute=true` (MCP) or `--execute` (CLI) to run allowlisted steps.

Auto-execute (read-only or stdout-only): tool-tier `rg` / `jq`, plus pipeline steps in `PIPELINE_AUTO_RUN` (`src/greedy_token/pipeline.py`) — `check-meta-sync`, `configurator-boolean-audit`, `audit-skill`, `classify-file`, `search`, `read-hits`, `rag`.

**Route command trust boundary:** workspace `read_only: true` is metadata, not
authorization. `greedy-token run --execute` accepts only internally built
`rg`/`jq` argv, registered read-only wrappers, or a workspace-relative
`.py`/`.sh` path approved in the user-local, workspace-bound trust manifest:

```bash
greedy-token trust add scripts/my-read-only-check.py --note "reviewed: stdout only"
greedy-token trust verify
```

SHA-256 and file identity are rechecked immediately before each approved
launch. Edits, symlink/path replacement, deleted/recreated files, absolute or
outside-workspace paths, `python -c`, shell `-c`, and trust-like fields from
URL/file presets fail closed. POSIX binds the verified descriptor through
`/dev/fd`; Windows retains a narrow verify-to-open window, and concurrent
same-inode writes are not snapshotted. The old `trusted_script_paths` config key
is deprecated dry-run metadata and grants no privilege. Subprocesses receive a
validated argv list with `shell=False`. Argument tokens are confined the same
way — `name=value` values and bare words resolve under the workspace root and
must stay inside it, so a symlink argument pointing outside is refused like an
explicit `../`. In internally built `rg`/`jq` argv the pattern always sits
after `--`, so a query like `--version` stays a literal pattern, never an
option. See the
[trust manifest and TOCTOU contract](docs/trust-manifest.md).

### Routing benchmark

`bench/routing_corpus.yaml` is a held-out/adversarial **classification** gate,
separate from `bench/route_examples.yaml`. It reports exact-match accuracy,
confusion matrix, per-target precision/recall, family/language accuracy, and a
mandatory zero false-cheap rate.

### Lexical retrieval benchmark

`bench/retrieval_corpus.jsonl` labels expected chunk IDs for RU/EN, each domain,
and exact, identifier, morphology, and paraphrase cases.
`python bench/retrieval_benchmark.py --root /path/to/workspace` reports
Recall@1/3/5, MRR, locale/domain/case-type breakdowns, and cold-index versus
warm-query latency.

Retrieval is local **lexical BM25/FTS**: SQLite FTS5 with the `unicode61`
tokenizer, Unicode NFKC + casefold normalization, and no embeddings or network
calls. Only `docs/rag/manifest.jsonl` entries are eligible. The persistent index
is content-hash invalidated and stored under the user cache directory
(`$GREEDY_TOKEN_CACHE_DIR`, `$XDG_CACHE_HOME`, or `~/.cache`), outside the
workspace. SQLite builds without FTS5 use the compatibility overlap scorer;
formatted hits name the engine and BM25 score.

`bench/evidence_corpus.v1.yaml` and its SHA-256 lock add the separate public
**end-to-end evidence** layer: frozen synthetic RU/EN fixtures, task-specific
file/line, exit-code, chunk-ID and escalation oracles, temporary workspaces,
and comparisons for direct `rg`/script, greedy CLI, greedy MCP stdio, and an
agent baseline. The deterministic agent is labelled `contract_stub`; a real
host baseline is manual. The JSON scorecard reports routing and task success
separately, executor/retrieval/escalation success, attempts, p50/p95, and
authoritative billing only. Cursor cost remains `unknown` when billing data is
unavailable; failed work never counts as saved. See [benchmark contract](bench/README.md).

### Confidence calibration

An absent override is not evidence of correctness. The legacy telemetry is
therefore named **override/hold confidence** and appears only as a behavioural
signal.

Router confidence calibrates only from explicit `route_outcome` events whose
outcome is `success` or `failure`. Calibration is independent by route, tier,
and language; the most-specific segment with **≥ 20 events**
(`CALIBRATION_MIN_EVENTS`) wins, then tier → language → global. Sparse data
uses the score formula and is visibly labelled `formula (uncalibrated;
explicit outcome n=…)`. Score buckets remain `[0, 2)`, `[2, 4)`, `[4, 6)`,
`[6, 8)`, and `[8, +)`.

Route events declare the provenance next to `confidence`:
`confidence_source` (`outcome-calibrated` | `formula` | `fixed` | `none`),
`calibration_n`, `bucket`, and the matched pattern strings (`matched`,
capped). `fixed` marks hardcoded confidences (script / compress / rag-cli /
pipeline steps); `none` marks fallback decisions (`cursor-fallback`,
`<tier>-none`). `override-hold-calibrated` stays a separate behavioural
signal — an absent override is a hold observation, not correctness.

```text
Outcome confidence calibration (explicit success/failure; min n=20):
  segment           bucket           n  predicted  observed  status
  tier:python       [2, 4)          25        75%       80%  calibrated
```

Repeated work → **crystallize** into a script → next time **0 LLM**. Details: [guide](docs/guide.md) · [roadmap](docs/ROADMAP.md)

**License:** MIT · **v0.18.1**
