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

**License:** MIT · **v0.10.0**
