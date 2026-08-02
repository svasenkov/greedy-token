# Cut checklist — greedy-token v0.16.0

**Status:** READY FOR RELEASE. Create the tag and GitHub release only after the
exact commit passes the mandatory `required matrix gate`.

This cut contains prompt #2 (Unicode lexical BM25/FTS retrieval), prompt #3
(portable execution plus cross-platform release matrices), and prompt #4
(content-bound trust manifests for workspace scripts). It deliberately excludes
the Hub accumulated-savings work and the host pre-router spike.

## Summary

- **Lexical BM25/FTS:** `greedy_token_rag` uses a local SQLite FTS5 index with
  the `unicode61` tokenizer, Unicode NFKC + casefold normalization, and BM25
  ranking.
- **Confined persistent index:** only manifest-listed chunks are indexed; the
  content-hash-invalidated database lives in the user cache, outside the
  workspace.
- **Compatible fallback:** SQLite builds without FTS5 use the existing overlap
  scorer and expose the selected engine in search output.
- **Measured retrieval corpus:** the public 18-case RU/EN corpus reports
  Recall@1/3/5, MRR, language/domain/case-type slices, and cold/warm latency.
- **Portable execution:** core subprocess paths use validated argv and cwd
  instead of shell-specific command construction, including Windows suffixes,
  absolute-path handling, spaces, backslashes, and Unicode.
- **Mandatory release matrix:** Ubuntu, macOS, and Windows run portability,
  real-tool integration, and wheel/sdist smoke jobs; dependency profiles cover
  minimum, latest, MCP-lowest, and MCP-latest.
- **Content-bound script approval:** `greedy-token trust add/list/verify/revoke`
  stores user-local, workspace-bound approvals containing SHA-256, canonical
  path, script type, approval metadata, and file identity.
- **Execution-time trust check:** approved scripts are revalidated immediately
  before launch. POSIX execution binds the verified descriptor through
  `/dev/fd`; Windows retains a documented narrow verify-to-open window, and
  neither platform snapshots concurrent same-inode writes.
- **Publication integrity:** PyPI publication checks the successful
  `required matrix gate` from the `Test` workflow on the exact tag commit.

## Honest evidence

The local macOS/Python 3.14 release gate on 2026-08-02 passed:

- 130 focused trust, security, BM25, retrieval, and portability tests;
- the full suite twice: 1169 passed, 3 skipped;
- 100% branch coverage across 7243 statements and 2444 branches;
- the explicit `0.16.0` release-version gate.

Against the current zero-design-system RAG manifest, the frozen 18-case
retrieval corpus measured:

- Recall@1: `0.7222`
- Recall@3: `0.8611`
- Recall@5: `0.9167`
- MRR: `0.8472`
- cold index: `18.695 ms`
- warm query median / p95: `10.458 / 11.247 ms`

These numbers are a small local lexical-retrieval measurement, not a semantic
quality claim or evidence of universal recall. The corpus currently reports
metrics; it does not impose a retrieval acceptance threshold.

## Contract evidence

| Contract | Evidence |
| --- | --- |
| Version `0.16.0` | `pyproject.toml`, release-version test |
| BM25 index, confinement, invalidation, fallback | `src/greedy_token/rag_fts.py`, `src/greedy_token/rag_search.py` |
| RU/EN retrieval measurement | `bench/retrieval_corpus.jsonl`, `bench/retrieval_benchmark.py` |
| Portable argv/cwd trust boundary | `src/greedy_token/subprocess_safe.py`, executor/router/tool tests |
| Content-bound script approvals and fail-closed verification | `src/greedy_token/trust.py`, `tests/test_trust.py`, `tests/test_security.py`, `docs/trust-manifest.md` |
| OS and Python matrix | `.github/workflows/test.yml` |
| Dependency profiles | `scripts/ci/install_profile.py`, `scripts/ci/test_profile.py` |
| Wheel/sdist smoke | `scripts/ci/build_smoke.py` |
| Exact-commit publish gate | `scripts/ci/verify_release_matrix.py`, `.github/workflows/publish.yml` |

## Release gates

1. Run `./scripts/release-gate.sh 0.16.0` in a clean environment.
2. Push the cut commit to `main`.
3. Require the exact commit's `Test / required matrix gate` to succeed.
4. Create and push the annotated `v0.16.0` tag.
5. Publish the GitHub release and require `Publish to PyPI` to succeed.
6. Verify the public GitHub release and PyPI `0.16.0` artifact.

## Out of scope

- Embeddings, vector RAG, or production-grade semantic retrieval
- A universal retrieval-quality claim from the 18-case corpus
- The Hub accumulated-savings feature
- A host pre-router that runs before Cursor's host LLM
