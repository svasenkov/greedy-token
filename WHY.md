# Why greedy-token

**Русская версия:** [WHY-RU.md](WHY-RU.md)

Most AI coding setups send almost everything to an expensive model — Claude, Cursor, take your pick. Then, to feel safe about quality, teams add another model that *judges* the first one.

That works. It also burns money on tasks that never needed a model: find this string, run this check, look up a flag we already documented.

**Greedy-token asks a simpler question first: do you need a model at all?**

---

## The idea in one breath

Send the easy work to cheap, fast tools. Keep the expensive chat for the hard stuff. Keep LLM-as-a-judge for the product you ship — not for every internal grep.

On a shape that looks like our real traffic, that means roughly **97%** of turns never hit the frontier model, and **~3%** still do. For a small team, the illustrative gap vs “everything expensive + standing judge” is on the order of **~$2k / month** or **~$26k / year** (assumptions at the bottom — swap in your numbers).

---

## How work actually flows

Think of a ladder. The first rung that can handle the task wins. The expensive model is the *last* rung.

1. **Tools** — `rg`, `jq`, and friends. Native **Rust** binaries. Search and parse almost free, and *fast*.
2. **Python** — especially after **crystallize**: you solve something once with an LLM, then turn the repeatable part into a boring script that does it forever.
3. **Cheap / local LLM** — **Ollama** and similar. Fine for classify / audit / bulk when you need language, but you don’t need frontier IQ. Works offline too (e.g. **Continue.dev**). Local models are **slow**, so we try tools and scripts before waking them up.
4. **Expensive LLM** — **Claude / Cursor**. Wiring, architecture, judgment calls. Only when the rungs above weren’t enough.

Sitting under all of that is the **knowledge layer** — not a paid API call, just the canon that keeps the team from reinventing itself every chat:

| | |
|--|--|
| **Skills** | “Here’s how we always do X” (crystallize, sync-meta, …) |
| **Rules** | Hard rails: scope, stands, search via greedy-token before raw Grep |
| **RAG** | Short answers from docs — patterns, flags — without a full agent essay |
| **ADR** | Decisions we already made, so we don’t re-debate them in every session |

In practice:

```text
Skills / Rules / RAG / ADR
  → rg / jq (Rust)
  → crystallize / Python
  → Ollama
  → only then Claude / Cursor
```

---

## Two stacks, same company

Imagine a small AI product shop: **8 engineers**, about **14 000** agent turns a month.

### Stack A — the classic path · ~$2 700 / mo

Everything goes through the expensive model. On top of that, a golden set and an LLM-as-a-judge on every serious run.

Great for **shipping** model quality. Painful as the default for **internal** eng chores.

| | |
|--|--|
| Expensive LLM (Claude / Cursor) for ~100% of turns | $550 |
| Judge + people to keep the eval alive | $2 150 |

### Stack B — with greedy-token · ~$540 / mo

Same expensive LLM card as stack A — Claude / Cursor didn’t disappear. It just sees **~3%** of the traffic instead of everything. Skills / Rules / RAG / ADR steer the rest down the ladder. Judge stays for **releases**, not for every internal pass.

| | |
|--|--|
| Skills · Rules · RAG · ADR | ~$0 |
| Tools + Python crystallize + Ollama (~97%) | ~$14 |
| Expensive LLM (~3%) — same as A, thinner slice | ~$17 |
| Light offline checks + release judge | $515 |

---

## The math, without the fog

All figures are **USD per month**. Change the inputs; keep the shape.

**Agents without routing**

```text
turns × expensive_price
14_000 × $0.04 = $550
```

**Agents with the ladder**

```text
turns × (3% × expensive + 97% × cheap)
14_000 × (~$0.002) ≈ $30
```

**Standing judge**

```text
(cases × runs × $per_score) + maintainer time
(250 × 40 × $0.015) + $2_000 = $2_150
```

**Light eval + release judge**

```text
$500 + $15 ≈ $515
```

**Gap**

```text
$2_700 − $540 ≈ $2_160 / month
≈ $26_000 / year
```

The big line item isn’t the judge API (~$150). It’s the people you don’t need on a permanent “score every answer” treadmill.

---

## What we are *not* claiming

Greedy-token does **not** replace LLM-as-a-judge for the model you sell customers. Keep that judge on **product releases**.

What it *does* replace is the habit of paying frontier prices for search, diffs, and checks your repo already knows how to do.

### Fully local is fine

No Claude key? Use **Continue.dev** + Ollama (or a local hub). Tools and crystallized scripts still run. The local model is a backup, not the front door.

And yes — even when everything is local, the ladder helps: **local models crawl**; `rg` and scripts don’t.

---

## Assumptions (so you can argue with them)

- 8 engineers · ~14k turns / month  
- Expensive turn $0.04 · cheap turn $0.001  
- ~97% cheap share shaped like live greedy-token traffic  
- Judge golden: 250 cases × 40 runs · 0.2 FTE vs 0.05 FTE for offline eval  
- Cursor seats (~$320 / mo) sit in both stacks and are **not** in the savings  

---

More setup: [README.md](README.md) · [Continue](docs/continue-setup.md) · [Cursor](docs/cursor-setup.md)
