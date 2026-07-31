# Cut checklist — greedy-token v0.15.0

**Status:** READY — NOT RELEASED. No tag, GitHub release, PyPI
publish, or monorepo push is part of this cut.

This cut adds public end-to-end evidence for whether a selected tier produces
the expected result. It does not turn a finite synthetic corpus into a claim
of universal routing quality or measured Cursor savings.

## Summary

- **Explicit outcomes:** `route_outcome` records success/failure/escalation,
  executor or retrieval layer, complete attempts/retries/escalations, exit
  code, and wall-clock duration.
- **Honest confidence:** override telemetry is named
  **override/hold confidence** and is not correctness. Router confidence uses
  explicit success/failure only, selecting route → tier → language → global
  calibration when the bucket has `n >= 20`; otherwise it stays formula
  (`uncalibrated`).
- **Failed work is not saved:** failed CLI/MCP execution or retrieval gets
  zero/null savings in telemetry, footers, and benchmark observations.
- **Frozen public corpus:** immutable SHA-256-locked RU/EN synthetic fixture,
  provenance, no route-example/pattern case reuse, and file/line, exit-code,
  chunk-ID, or escalation oracle per task.
- **Separate score layers:** route classification; tool/python executor;
  lexical retrieval; cursor escalation; attempts/retries/escalations;
  wall-clock p50/p95; authoritative tokens/cost.
- **Four comparison paths:** direct `rg`/script, greedy CLI, greedy MCP stdio,
  and agent baseline. Deterministic CI labels the agent as a contract stub,
  not measured agent evidence.
- **Billing honesty:** no metered API by default. Cursor/host cost is
  `null`/`unknown` without authoritative data. Savings need a successful
  same-task run with an authoritative live agent baseline.
- **CI evidence:** deterministic benchmark uploads
  `greedy-token-evidence-scorecard`; a separate manual self-hosted workflow
  probes live Ollama/MCP/optional host adapter.

## Contract evidence

| Contract | Evidence |
| --- | --- |
| Version `0.15.0` | `pyproject.toml` |
| Explicit outcomes + split reporting | `src/greedy_token/usage.py`, `tests/test_usage.py` |
| Outcome-only confidence | `src/greedy_token/outcome_calibration.py`, `tests/test_calibration.py` |
| Failed work excluded from savings | `src/greedy_token/budget.py`, `src/greedy_token/pipeline.py`, benchmark tests |
| Frozen corpus + provenance + oracles | `bench/evidence_corpus.v1.yaml`, `.sha256` lock |
| Direct/CLI/MCP/agent scorecard | `bench/evidence_benchmark.py`, `bench/README.md` |
| Deterministic artifact | `.github/_ethalon/test.yml`, `.github/workflows/test.yml` |
| Manual live benchmark | `.github/_ethalon/evidence-live.yml`, runnable copy |
| Full suite + branch gate | `pyproject.toml`, `scripts/release-gate.sh` |

## Evidence interpretation

The local one-repetition smoke on the frozen v1 corpus passed 12/12 route
classification, 0% false-cheap, and 100% task success for greedy CLI and MCP.
The direct baseline passed 4/5 applicable tasks because direct `rg` cannot
satisfy the intentional search→RAG fallback oracle.

That smoke proves runner wiring and observable usefulness on this corpus only.
It does **not** prove:

- universal precision outside the frozen corpus;
- live Ollama quality (deterministic CI stubs availability only);
- real agent success or latency (the deterministic baseline is
  `contract_stub`);
- Cursor token or dollar savings (no authoritative Cursor billing was
  available).

The canonical repeatable result is the JSON CI artifact, which keeps routing
accuracy and task success in different fields.

## Cut gates

- [x] deterministic benchmark ×3: 12/12 routing, false-cheap 0%, greedy
  CLI/MCP task success 100%, all JSON gates passed
- [x] full suite: 1060 passed + 1 release-only skipped; release metadata test
  passed separately
- [x] branch coverage: 6570 statements, 2246 branches, 100%
- [x] workflow ethalon/runnable sync
- [x] frozen corpus SHA-256 verified
- [x] false-cheap threshold fixed at `0.0`
- [x] no metered API without explicit opt-in
- [x] no release or publish action

Release-gate command after all checkboxes are green:

```bash
./scripts/release-gate.sh 0.15.0
```

## Out of scope

- Router tuning against the evidence prompts
- Vector/embedding RAG
- A host pre-router that avoids the already-running host LLM
- Automatic use of any metered API
- Replacing unavailable Cursor billing with an estimate
- Windows CI matrix

## Tag / publish

**Blocked for this task.** Do not create `v0.15.0`, a GitHub release, or a PyPI
publication until the user explicitly requests release and the deterministic
CI scorecard is available.
