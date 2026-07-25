# Why greedy-token

**Русская версия:** [WHY-RU.md](WHY-RU.md)

Stop judging every answer. Ask first: **do you need a model at all?**

LLM-as-a-judge scores how good generation is. Greedy-token runs the task down a ladder of cheap executors — and only then calls Claude / Cursor.

| | |
|--|--|
| **~97%** cheap path | **~3%** expensive LLM |
| **~$2.2k** saved / month* | **~$26k** saved / year* |

\*Illustrative P&L for a small AI product company (assumptions at the bottom).

---

## The ladder

First matching tier wins. The expensive model is the **last** step, not the first. Skills, Rules, RAG, and ADRs steer where each task goes.

| Step | What | Cost |
|------|------|------|
| **1. Tool** | `rg` / `jq` and other CLIs — native **Rust** binaries. Search and parse for nearly $0. | ~$0 LLM |
| **2. Python** | Scripts and **crystallize**: extract a repeatable procedure once from an LLM → deterministic Python forever after. | ~$0 LLM |
| **3. Cheap / local LLM** | **Ollama** (and similar) — classify, audit, bulk. Works without Claude (e.g. **Continue.dev**). Local inference is **slow**, so tool / Python / RAG go first. | cheap · slower than frontier |
| **4. Expensive LLM** | Only here: **Claude / Cursor** — wiring, architecture, whatever steps 1–3 did not close. | expensive · ~3% |

### Knowledge layer (not a paid step)

**Skills · Rules · RAG · ADR** — canon that feeds the ladder: where to route, what is already decided, which pattern to read without Claude.

| | |
|--|--|
| **Skills** | Repeatable agent procedures (crystallize, sync-meta, …) |
| **Rules** | Hard constraints: scope, stands, greedy-token before Grep |
| **RAG** | Pattern / flag lookup from docs without a full chat |
| **ADR** | Architecture decisions — do not reinvent in every session |

Typical path:

```text
Skills / Rules / RAG / ADR
  → rg / jq (Rust)
  → crystallize / Python
  → Ollama
  → only then Claude / Cursor
```

---

## Side by side

Small AI product company · 8 engineers · ~14 000 agent turns / month

| Dimension | LLM-as-a-judge | Greedy-token | Judge $ | Greedy-token $ |
|-----------|----------------|--------------|---------|----------------|
| Core question | How good is the answer? | Is a model needed? | — | — |
| Agents | Straight to Claude / Cursor | Skills/Rules/RAG/ADR → rg·jq (Rust) → Python → Ollama → Claude/Cursor | $550 | $30 |
| Quality checks | Second model on every serious run | Hard asserts + judge on releases | $2 150 | $515 |
| Full stack | Expensive agents + standing judge | Routing + light offline eval | **$2 700** | **$540** |

### Stack A — classic · $2 700 / mo

Everything through an expensive LLM, plus a golden dataset and LLM-as-a-judge on every meaningful run. Strong for **product** quality — expensive as the default for **internal** eng work.

| Line | Cost |
|------|------|
| Expensive LLM (Claude / Cursor) · 100% | $550 |
| Judge + people | $2 150 |

### Stack B — greedy-token · $540 / mo

Skills, Rules, RAG, and ADRs set the canon. Then the tier ladder. At the end sits the **same** “Expensive LLM” card (Claude / Cursor) as in stack A — it just sees **~3%** of traffic, not 100%.

| Line | Cost |
|------|------|
| Skills · Rules · RAG · ADR | ~$0 |
| Tool (rg/jq · Rust) + Python crystallize + Ollama · ~97% | ~$14 |
| Expensive LLM (Claude / Cursor) · ~3% — same card as A | ~$17 |
| Offline-eval + release judge | $515 |

---

## Plain equations

All figures are **USD / month**. Swap in your numbers — the formulas stay the same.

### 1. Agent cost

Without routing (everything on the expensive model):

```text
agent_cost = turns × expensive_price
14_000 × $0.04 = $550
```

With greedy-token (tier ladder):

```text
agent_cost = turns × (3% × Claude/Cursor + 97% × tool/Python/Ollama)
14_000 × (~$0.002) ≈ $30
```

Canon = Skills / Rules / RAG / ADR · tool = rg/jq (Rust) · Python = crystallize · cheap LLM = Ollama · expensive = Claude / Cursor.

### 2. Quality-check cost

Standing LLM-as-a-judge:

```text
judge = (cases × runs × $per_score) + maintainer_salary
(250 × 40 × $0.015) + $2_000 = $2_150
```

Greedy-token: asserts + judge only on releases:

```text
eval = light_maintenance + release_judge
$500 + $15 ≈ $515
```

### 3. Savings

```text
savings = stack_A − stack_B
$2_700 − $540 ≈ $2_160 / month
$2_160 × 12 ≈ $26_000 / year
```

Most of the savings is **people**, not judge API (~$150) — you do not staff continuous LLM-judge ops ($2 000).

---

## Honest positioning

Greedy-token does **not** replace LLM-as-a-judge for the model you sell. Keep judge on **product releases**. Inside the team:

```text
Skills / Rules / RAG / ADR → rg/jq (Rust) → Python crystallize → Ollama → only then Claude / Cursor
```

### Fully local is supported

You can work **without access to an expensive LLM** — e.g. **Continue.dev** + Ollama / a local hub. Tool (`rg`/`jq`) and Python crystallize cover search and repeats with no model; a local LLM stays a fallback tier.

The win is not only price: **local models are slow**, while rg / scripts / RAG answer much faster.

---

## Assumptions (illustrative)

- 8 engineers · ~14k turns / month  
- Expensive turn $0.04 · cheap turn $0.001  
- ~97% cheap share (from live greedy-token traffic shape)  
- Judge golden 250 × 40 runs · 0.2 FTE judge maintenance vs 0.05 FTE offline eval  
- Cursor seats (~$320 / mo) are the same in both stacks and are **not** counted in the savings  

---

See also: [README.md](README.md) · [WHY-RU.md](WHY-RU.md) · [docs/continue-setup.md](docs/continue-setup.md) · [docs/cursor-setup.md](docs/cursor-setup.md)
