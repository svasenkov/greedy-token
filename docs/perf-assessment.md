# Performance assessment: should greedy-token leave Python?

**Date:** 2026-07-06 · **Branch:** `perf-assessment` · **Machine:** Apple M2 Max (12 cores), macOS 26.5, Python 3.14.0, tiktoken 0.13.0
**Workspace:** zero-design-system monorepo (3,223 text files, ~98 MB, 64.8M tokens)
**Repro:** `python bench/bench.py --runs 5 [--stress]`

## Question

A language-migration discussion (Rust / C++23 / Zig, SIMD, tries, PGO) assumed greedy-token
is a CPU-bound tokenizer. This assessment measures what the CLI actually spends time on.

## Baseline (median of 5 runs)

| Command | Median | Notes |
|---|---:|---|
| `--help` (cold start) | 0.083 s | interpreter 0.02 s + imports ~0.05 s |
| `route "find baseUrl"` | 0.202 s | regex routing, no heavy work |
| `estimate "refactor header layout"` | 0.281 s | includes tiktoken encoder init (~0.13 s) |
| `rag "e2e baseUrl healthCheck"` | 0.087 s | file scan over `docs/rag/` |
| `tokens .cursor/rules` | 0.198 s | tiktoken init dominates |
| `audit-context` | 0.212 s | tiktoken init dominates |

One-off effects (excluded from medians): the very first `tokens`/`estimate` after install
downloads the cl100k_base BPE file (~3 s, network, cached afterwards).

`python -X importtime`: importing `greedy_token.cli` costs ~51 ms cumulative; the biggest
chunk is `urllib.request` (~19 ms) pulled in by `wrappers`, the rest is stdlib
(`dataclasses`, `inspect`, `argparse`).

## Stress case: `tokens .` over the whole monorepo

Total: **19.8 s**. cProfile breakdown:

| Phase | Time | Share | Where |
|---|---:|---:|---|
| `CoreBPE.encode` (tiktoken) | 15.2 s | 77% | **already Rust** |
| `collect_paths` (rglob + per-dir `sorted`) | 3.3 s | 17% | Python |
| `read_text` | 0.9 s | 4% | I/O |
| everything else | ~0.4 s | 2% | Python |

## Experiment: parallel encode without changing language

tiktoken ships `encode_ordinary_batch(texts, num_threads=N)` — the Rust core releases
the GIL and encodes in parallel:

| Variant | Time |
|---|---:|
| current sequential `tokens .` | 19.8 s |
| collect 2.0 s + read 0.7 s + `encode_ordinary_batch(num_threads=12)` 6.8 s | **9.5 s (2.1x)** |

Remaining Python-only cost is path collection (~2 s), fixable with `os.walk` + one final
sort instead of `rglob` with per-directory `sorted` on `Path` objects.

## Amdahl analysis

- **Stress path:** 77% of wall time is already inside Rust (tiktoken). A full rewrite of
  the remaining Python to Rust caps the theoretical gain at ~1.3x — less than the 2.1x
  already available via the batch API, and far less than batch + faster path collection
  (~3x, est. ≤7 s).
- **Interactive commands (route/rag/estimate):** 0.08–0.28 s, dominated by interpreter
  start (~0.08 s) and one-time tiktoken encoder init (~0.13 s). A native Rust binary would
  reduce cold start from ~80 ms to ~5 ms. That is the *only* place a language change wins,
  and it saves ~75 ms per invocation.
- The workload is I/O + FFI bound, not CPU bound. Tries, SIMD, arena allocators, PGO/LTO
  from the migration discussion target a hot loop this codebase does not have — its hot
  loop (BPE encode) already lives in Rust.

## Verdict

**Stay on Python.** A Rust/C++ rewrite of ~1,300 lines of routing/orchestration code buys
~75 ms of cold start and nothing else measurable. Cheaper wins, in order:

1. `tokens`/`audit-context`: switch to `encode_ordinary_batch(num_threads=os.cpu_count())`
   → 2x on large corpora (measured).
2. `collect_paths`: `os.walk` + single sort → additional ~2 s on large corpora (estimated
   from profile).
3. Lazy-import `urllib.request` in `wrappers` → ~19 ms off cold start (optional).

Revisit the language question only if greedy-token grows an actual hot loop in Python
(e.g. its own tokenizer or fuzzy matcher over large corpora). In that case the sensible
shape is the hybrid from the discussion: Python API + Rust core via PyO3 — not C++23/SIMD,
which targets a problem this tool does not have.
