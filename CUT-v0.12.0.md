# Cut checklist — greedy-token v0.12.0

**Status:** READY in `main` — awaiting tag/PyPI (explicit user command).

Trust cut execution: stop false-cheap `rg`/tool holds on edit prompts, and
make RAG/routing tokenize Cyrillic-safe.

## Summary

- **Edit-escalation**: tool/rag hits with edit verbs (`fix`, `patch`,
  `исправь`, `почини`, …) hard-escalate to `cursor-edit-escalate`.
- **RU tokenize**: `_tokenize` uses Unicode `\w` so Cyrillic queries are not
  an empty token set (shared by index + search).
- **Tests**: `tests/test_trust_cut.py` — false-cheap corpus + RU RAG smoke.

## CONTRACT — evidence

| Claim | Evidence |
|-------|----------|
| escalate | `router._escalate_edit_from_cheap` / `has_edit_verbs` |
| false-cheap corpus | `test_false_cheap_edit_corpus_escalates` |
| pure search stays tool | `test_pure_search_stays_tool` |
| Cyrillic tokenize | `test_tokenize_cyrillic_not_empty`, `test_rag_search_russian_query` |
| shared tokenize | `rag_index._tokenize` used by `rag_search` |

## Gate (when releasing)

```bash
./scripts/release-gate.sh 0.12.0
```

Bump `pyproject.toml` version to `0.12.0` before the gate if not already.
Shipping as 0.12.0 may fold after or instead of a separate 0.11.2 tag —
operator choice; AC for both cuts are met on `main`.

## Tag / publish (explicit user command only)

```bash
git tag -a v0.12.0 -m "Release v0.12.0: edit-escalation + Cyrillic tokenize"
# push / gh release / PyPI — only when asked
```

## Out of scope (stay on ROADMAP)

- v0.13 routing corpus scorecard / shell harden
- host pre-router / Windows / FTS
