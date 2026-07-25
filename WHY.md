# Why greedy-token

**Русская версия:** [WHY-RU.md](WHY-RU.md)

## The problem (ELI5)

You pay a smart, expensive assistant by the word.

Most days you ask small things: “where is this flag?”, “does this check pass?”, “what does our docs say about X?”. Today those small questions often go to the **same expensive chat** as real architecture work — so you pay architecture prices for looking up a string.

Some teams add a *second* model to judge the first. That can help quality. It does **not** stop you from paying frontier prices for grep.

## How we solve it

**Ask first: do you need a model at all?**

```text
Skills / Rules / RAG / ADR
  → rg / jq (fast tools) → Python scripts
  → Ollama (local cheap LLM, if needed)
  → only then Claude / Cursor
```

| Step | Plain English |
|------|----------------|
| Tools / scripts | Free and fast. Search the repo, run a check — no model. |
| Local LLM | Offline and cheap, but slower. Use when you need “sort of AI”, not for every lookup. |
| Expensive chat | Keep for the hard ~3%: wiring, design, judgment. |
| Judge model | Use on **product releases**, not on every internal pass. |

That is greedy-token: a router next to your coding agent so routine work does not burn the expensive path.

## Money

Path comparison (1 eng / team ×10, **★ $82 / ★ $820** savings) lives in the main READMEs:

- [README.md § Which path should I use?](README.md#which-path-should-i-use-money)
- [README-RU.md § Какой путь выбрать?](README-RU.md#какой-путь-выбрать-деньги)

More: [README.md](README.md) · [Continue](docs/continue-setup.md) · [Cursor](docs/cursor-setup.md)
