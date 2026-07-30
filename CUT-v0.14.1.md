# Cut checklist — greedy-token v0.14.1

**Status:** DRAFT — patch prepared locally; no tag, GitHub release, push, or
PyPI publication performed.

Patch release after the v0.14.0 repeat review: release integrity, fail-safe
false-cheap routing, classifier scorecard, and an enforceable route-command
trust boundary. Windows support, vector RAG, and a host pre-router remain out
of scope.

## Summary

- **Read-only intent grammar:** tool-tier matching requires a recognised,
  complete read-only search/JSON lookup. Ambiguous or mixed search+edit
  requests fail safe to `cursor`; the 14 EN/RU adversarial prompts are pinned.
- **Command trust contract:** `read_only` is metadata, not authorization.
  Route execution accepts internal `rg`/`jq` argv, registered read-only
  wrappers, or local `trusted_script_paths`. Absolute executables,
  `python -c`, shell `-c`, outside-workspace cwd/paths, and arbitrary preset
  commands are rejected.
- **Root consistency:** explicit `root` controls route overlay loading and
  command cwd even when `GREEDY_TOKEN_ROOT` points elsewhere.
- **Corpus v3:** held-out/adversarial corpus separated from route examples;
  exact-match accuracy/micro-precision, confusion matrix, per-target
  precision/recall, family/language accuracy, and false-cheap rate 0.
  Classification accuracy is not execution or retrieval success.
- **Release integrity:** release-triggered PyPI publication requires the latest
  completed `push` run of `Test` for the exact tag commit to be successful.
  The local release gate again runs branch coverage with `fail_under = 100`.
- **Honesty invariants:** MCP does not remove the host LLM; `route` selects one
  tier; `rag` is lexical overlap; confidence is not correctness.

## Contract evidence

| Contract | Evidence |
|----------|----------|
| Version `0.14.1` | `pyproject.toml` |
| Strict tool intent | `src/greedy_token/router.py`, `tests/test_trust_cut.py` |
| Trusted structured argv | `src/greedy_token/subprocess_safe.py`, `src/greedy_token/executors.py` |
| Malicious route has no side effect | `tests/test_security.py` |
| Explicit-root config + cwd | `tests/test_router.py` |
| Corpus v3 gates | `bench/routing_corpus.yaml`, `bench/route_examples.yaml`, `tests/test_routing_corpus.py` |
| Green-commit publish gate | `.github/_ethalon/publish.yml`, `.github/workflows/publish.yml` |
| Coverage gate | `pyproject.toml`, `scripts/release-gate.sh`, `.github/_ethalon/test.yml` |

## Required verification

```bash
python -m pytest -q
python -m pytest tests/ -q -m unit
python -m pytest tests/ -q -m component
python -m pytest tests/ -q -m integration
python -m pytest tests/ -q -m e2e
python -m coverage run -m pytest tests/ -q
python -m coverage report --include='src/greedy_token/*'
python -m pytest -q --release-version=0.14.1 -m release
bash scripts/check-github-workflows-sync.sh
```

## Release actions intentionally not performed

- no push;
- no `v0.14.1` tag;
- no GitHub release;
- no PyPI publication.
