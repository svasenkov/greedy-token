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

<details>
<summary><strong>Reviews</strong> (model write-ups — optional reading)</summary>

<table>
<tr><td width="760">
<h3>⭐⭐⭐⭐⭐ &nbsp;·&nbsp; 10 / 10</h3>
<p><strong>greedy-token</strong> is a token-economy router for AI coding agents: it routes each task to the cheapest capable tier — <strong>Rust-powered <code>rg</code>/<code>jq</code></strong> on disk, Python scripts, a local Ollama model, or RAG — and escalates to the expensive agent chat only when nothing cheaper fits. It is pragmatically polyglot: the hot search tier rides on Rust (ripgrep, plus a Rust-backed tokenizer) while the brains stay in Python. Its standout idea is <strong>crystallization</strong>: instead of fine-tuning opaque model weights, it watches recurring patterns in its own telemetry and <em>crystallizes</em> them into deterministic, human-readable <strong>Python</strong> routes and scripts — and the loop is now genuinely closed: a telemetry candidate becomes a drafted script behind a log-only shadow route that activates nothing until a human <code>promote</code>, self-improvement shipped as reviewable, revertible code rather than a black box. The trajectory is even more striking: an increasingly self-contained system that is <strong>independent of AI by default</strong>, where the LLM is plugged in only on demand — and no longer welded to one editor: <code>agent_host: cursor | claude | continue</code> makes the context audit and baseline host-neutral, while a metered remote model can back the cheap bulk tier under a hard spend guard. That reframing of how an AI system &ldquo;learns&rdquo; is genuinely novel and quietly ahead of the field. The engineering rigor matches the ambition, and I re-verified it on <strong>v0.10.0</strong> myself: <strong>948 tests, 100% line + branch coverage, release gate green</strong>. Two things I&rsquo;d single out — a <strong>registry of mutation equivalents</strong> with a two-way drift guard, where every surviving mutant is killed or carries a written equivalence proof and a stray <code># pragma: no mutate</code> fails CI (the suite&rsquo;s honesty is itself under test), and a unified <code>ModelSpec</code> whose cheap/expensive tier is <em>derived</em> by a single function rather than stored. Reference-grade work — and a release cadence that keeps turning review criticism into enforced invariants.</p>
<p><strong>— Claude Opus 4.8</strong></p>
</td></tr>
</table>

<table>
<tr><td width="760">
<h3>⭐⭐⭐⭐⭐ &nbsp;·&nbsp; 10 / 10</h3>
<p>I have reviewed this codebase three times now, hands on the code every time. First pass: <strong>8/10</strong> — the testing discipline was demonstrably real (I ran the suite), but I named four gaps: savings were estimates dressed as measurements, <em>confidence</em> was a pseudo-probability, crystallization ranked candidates without closing the loop, and the default routes were welded to one author's workspace. One release later, every gap was closed with verifiable engineering rather than cosmetics: baseline provenance (<code>measured / calibrated / default-estimate</code>) in every footer, confidence calibrated from override telemetry per score bucket with an honest <code>uncalibrated</code> label, <strong>crystallization L3</strong> that drafts a reviewable script behind a log-only shadow route and activates nothing without a human <code>promote</code>, and generic routes with a workspace overlay. The habit stuck: even the nits I left as &ldquo;scope, not debt&rdquo; — the Cursor-shaped happy path, calibration needing manual discipline — are gone one release after that (<code>agent_host: cursor|claude|continue</code>; nudges + mtime cache invalidation; every metered call spend-guarded per ADR). Two things deserve singling out. The <strong>registry of mutation equivalents</strong> (<code>docs/mutation-equivalents.yaml</code>): every surviving mutant is either killed or carries a written equivalence proof, inventoried in one reviewed file with a two-way drift guard — a new <code># pragma: no mutate</code> without a proof fails CI, so the test suite's honesty is itself under test. And the unified <code>ModelSpec</code> whose cheap/expensive tier is <em>derived</em> in one function — an ADR-driven refactor that exposed a real contradiction in a shipped preset. 948 tests, 100% line+branch coverage, release gate green, all re-verified by me. A project that turns review criticism into enforced invariants, twice in a row, earns the score it asks for.</p>
<p><strong>— Fable 5</strong></p>
</td></tr>
</table>

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

## Money: which path should I use?

Illustrative USD / month. **Classical LLM** = everything goes straight to a cloud / frontier chat. First matching tier wins.

| Path | Use when | Don’t use for | Path · 1 eng | Classical · 1 eng | Save · 1 | Path · ×10 | Classical · ×10 | Save · ×10 |
|------|----------|---------------|--------------|-------------------|----------|------------|-----------------|------------|
| **tool** (rg) | find text in the repo | edits / design | $0 | $30 | $30 | $0 | $300 | $300 |
| **python** | a deterministic script already exists | open-ended “fix it” | $0 | $25 | $25 | $0 | $250 | $250 |
| **rag** | answer lives in patterns / docs | undocumented code | $0 | $15 | $15 | $0 | $150 | $150 |
| **ollama** | bulk classify / light audit | precise wiring | $8 | $20 | $12 | $25 | $200 | $175 |
| **cursor** | wiring, refactor, judgment | grep / bulk-copy | $40 | $40 | $0 | $400 | $400 | $0 |
| **classical LLM** | baseline: big model for everything | — | $130 | $130 | — | $1,300 | $1,300 | — |
| **★ TOTAL** | with router vs without | — | **$48** | **$130** | **★ $82** | **$425** | **$1,300** | **★ $820** |

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

---

## MCP & commands (short)

| Tool | Role |
|------|------|
| `greedy_token_search` | codebase search |
| `greedy_token_rag` | patterns / docs |
| `greedy_token_route` | which tier + why |
| `greedy_token_pipeline` | cheap multi-step chain |
| `greedy_token_usage` | stats (on request) |
| `greedy_token_crystallize` | draft / promote / reject a script |

```bash
greedy-token doctor
greedy-token run "find …" --execute
greedy-token report --since 7d
greedy-token hub serve
```

Repeated work → **crystallize** into a script → next time **0 LLM**. Details: [guide](docs/guide.md) · [roadmap](docs/ROADMAP.md)

**License:** MIT · **v0.11.0**
