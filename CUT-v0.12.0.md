# Cut checklist — greedy-token v0.12.0

**Status:** FOLDED into **v0.13.0** (SHIPPED 2026-07-30) — no separate tag.

Trust cut: stop false-cheap `rg`/tool holds on edit prompts, and make
RAG/routing tokenize Cyrillic-safe.

## Summary

- **Edit-escalation**: tool/rag hits with edit verbs hard-escalate to
  `cursor-edit-escalate`.
- **RU tokenize**: `_tokenize` uses Unicode `\w`.
- **Tests**: `tests/test_trust_cut.py`.

Shipped as part of `v0.13.0`. See `CUT-v0.13.0.md`.
