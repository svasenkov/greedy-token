# Why greedy-token

**Русская версия:** [WHY-RU.md](WHY-RU.md)

Most AI setups send almost everything to Claude / Cursor, then add another model to *judge* the first. That works — and it pays frontier prices for grep, checks, and lookups your repo already knows.

**Greedy-token asks first: do you need a model at all?**

```text
Skills / Rules / RAG / ADR
  → rg / jq (Rust) → Python crystallize → Ollama
  → only then Claude / Cursor
```

Tools and scripts are cheap **and** fast. Local LLMs (Ollama, Continue.dev) work offline but are slow — so they sit above tools, not instead of them. Expensive chat stays for the hard ~3%. LLM-as-a-judge stays for **product releases**, not every internal pass.

## One table

Illustrative · 8 engineers · ~14k turns/mo · USD/month

| | Classic (judge everywhere) | Greedy-token |
|--|----------------------------|--------------|
| Core question | How good is the answer? | Is a model needed? |
| Path | Straight to Claude / Cursor | Canon → Rust tools → Python → Ollama → Claude / Cursor |
| Expensive LLM share | ~100% | ~3% (same card, thinner slice) |
| Agents | $550 | ~$30 |
| Quality | Standing judge + people · $2 150 | Offline asserts + release judge · $515 |
| **Total** | **~$2 700** | **~$540** |
| **Gap** | | **~$2 160 / mo · ~$26k / yr** |

Assumptions: expensive turn $0.04 · cheap $0.001 · ~97% cheap · Cursor seats (~$320) not in the gap. Swap your numbers; keep the shape.

More: [README.md](README.md) · [Continue](docs/continue-setup.md) · [Cursor](docs/cursor-setup.md)
