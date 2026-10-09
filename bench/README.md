# Public end-to-end evidence benchmark

This benchmark separates **route classification** from **task usefulness**.
It does not treat a held route, a missing override, or an estimated saving as
proof that a task succeeded.

## Lexical BM25/FTS retrieval

The separate `retrieval_corpus.jsonl` labels expected chunk IDs across RU/EN,
domains, and morphology/paraphrase cases. Run:

```bash
python bench/retrieval_benchmark.py --root /path/to/workspace
```

Its JSON output includes Recall@1/3/5, MRR, locale/domain/case-type breakdowns,
and cold SQLite-index versus warm-query latency. This measures local lexical
BM25/FTS retrieval only; it does not imply semantic or vector recall.

## Frozen corpus

- `evidence_corpus.v1.yaml` is the immutable public RU/EN corpus.
- `evidence_corpus.v1.sha256` locks its exact bytes.
- A correction creates `evidence_corpus.v2.yaml`; v1 is never silently edited.
- `evidence_corpus.p6.yaml` + `evidence_corpus.p6.sha256` are the frozen P6
  extension corpus: independent RU/EN cases (disjoint ids and fixture) adding
  adversarial false-cheap, malformed-input and large-input coverage on top of
  v1. Its thresholds are declared against the observed `python_provider`
  boundary: every native launch (`rg`, script interpreters, `sysctl`) is
  denied under D1 observation, so CLI tool/script plans fail closed and every
  MCP stdio row is `coverage=incomplete`. Pure-Python RAG and route-only CLI
  rows can reach `coverage=complete`, but only original-task RAG excerpt
  completion can earn `zero_completed` in these runs.
- Every case identifies provenance, expected tier, operation, and an observable
  oracle: file/line, exit code/output, chunk ID, or escalation.
- A test rejects exact reuse of route examples and route-pattern entries. The
  current router configuration is copied into a temporary workspace and is not
  tuned by the benchmark.

## Deterministic CI

```bash
python bench/evidence_benchmark.py \
  --mode deterministic \
  --repetitions 3 \
  --output build/evidence/scorecard.json

# P6 extension corpus (independent cases; same runner and observer):
python bench/evidence_benchmark.py \
  --mode deterministic \
  --repetitions 3 \
  --corpus bench/evidence_corpus.p6.yaml \
  --lock bench/evidence_corpus.p6.sha256 \
  --output build/evidence/scorecard-p6.json
```

The run creates an isolated workspace, writes only frozen synthetic fixtures,
uses a local Ollama *availability* stub, executes the real CLI, and calls the
real MCP server over stdio. It compares:

1. direct `rg` / deterministic script;
2. greedy CLI;
3. greedy MCP stdio;
4. an agent **contract stub**.

The contract stub validates comparison wiring only. Its success and tiny
latency are explicitly labelled `contract_stub`, excluded from measured agent
evidence, gates, and savings. A real agent baseline belongs to the manual live
workflow.

The JSON scorecard contains separate routing and task-success sections,
executor/retrieval/escalation results, all attempts, retries and escalations,
wall-clock p50/p95, billing provenance, corpus/router versions, every raw
observation, and acceptance gates. CI uploads it as
`greedy-token-evidence-scorecard`.

## P6 oracle regrade and final guarded result

**Worker verdict: blocked, not accepted.** Frozen v1, P6 locks and thresholds
are unchanged. The grader is
`8bad722d9b24f2a305bddea309349aec53b6101caeb38c9a8f1503ac524e3ad3`;
the final execution code binding is
`10c5905bf1e416f8425b09cf7f97f2af3945297b2dce544366389b3159f6ae3f`.
Each new scorecard carries corpus/oracle/input hashes and per-run
run/case/input/code/argv/environment bindings. The fresh run records per-module
source hashes, including the accepted router. Historical artifacts retain
their original execution binding; missing historical per-module hashes stay
unknown, never replaced with the fresh router hash.

The existing guarded drivers provide request/closure evidence, not a
correlated tier-bound transition source. A chunk ID, the word `fallback`,
forged transition text or `Route: CURSOR` cannot prove execution/escalation.
Required transition evidence therefore stays `unknown`/not-demonstrated.
The CLI fallback request is a dynamic-fallback **candidate**; the MCP
`search ... then rag ...` request is an **explicit pipeline**, not an automatic
fallback. Synthetic grader fixtures are contract tests, not measured task
proof. Actual guarded probes do not establish a product escalation positive.
No new observer, host adapter or replacement harness was introduced.

Replay uses the existing runner and never executes the historical tasks:

```bash
../dev/.venv/bin/python -B bench/evidence_benchmark.py \
  --replay build/evidence/scorecard.json \
  --output build/evidence/scorecard-v1-oracle-regrade.json
../dev/.venv/bin/python -B bench/evidence_benchmark.py \
  --replay build/evidence/scorecard-p6.json \
  --corpus bench/evidence_corpus.p6.yaml \
  --lock bench/evidence_corpus.p6.sha256 \
  --output build/evidence/scorecard-p6-oracle-regrade.json
```

Existing outputs are never overwritten; choose an unused output path for
another invocation. Both regrades replay 72 measured invocation ledgers plus
one route-classification ledger from their embedded raw validator inputs:
73 ledger replays each, zero coverage-verdict changes. External ledger files
are preserved, not reread or rewritten. Original run IDs, repetitions,
timestamps, route predictions and source hashes are retained. Outcome changes
are 36 applicable rows for v1 and 51 for P6, including contract-stub rows;
these are grading changes, not new measured tasks. Historical full payloads
are unavailable: only stored excerpts can be checked, and missing evidence
remains unknown. The new fresh run preserves complete returned payloads.

The single final deterministic P6 run produces
`build/evidence/scorecard-p6-oracle-final.json`:

- 12 unique tasks × 3 repetitions; 144 comparison rows total, comprising
  72 measured applicable greedy rows, 36 non-applicable direct baseline rows
  and 36 contract-stub rows. Correctness: 24 pass, 15 fail, 33 unknown.
- 36 completion-eligible rows; 6 zero-completed rows = 2 unique original-task
  RAG completions × 3 repetitions, or 6/72 of all measured applicable rows.
  Route-only, fallback-only, refusals, calibration and stubs are not completion.
- 21 complete observed rows, with observed model-attempt/request subtotals
  0/0; 51 incomplete/unknown rows. Whole-corpus invocation totals are
  **unknown/null**, not 0. Partial Python/provider counts are separately
  scoped: attempts=0, sent=0, native denials=78. Native denial is a coverage gap.
- Routing 12/12 and false-cheap 0 pass. P6 executor 12/24 meets its frozen 0.5
  threshold, but does not prove full invocation coverage. Retrieval 12/18
  fails 0.8; cursor escalation 0/30 fails 1.0. Mandatory transition evidence
  and completion coverage remain blocked. Frozen-v1 executor 6/24 fails its
  unchanged gate; being a known gap does not make it pass.
- Freeze/row-denominator, route-versus-task separation, unknown propagation,
  independent channel, zero-completion eligibility and savings/billing
  honesty checks pass. `retries_and_escalations_counted` only validates honest
  counter states, including unknown; actual stage/retry/fallback/dedup totals
  remain unknown. The no-dispatch check refers only to guarded Python/provider
  evidence, not complete native coverage. False-intercept runtime evidence
  and HOST0/P4b acceptance are not established by this run.
- All 72 returned payloads are stored and hashed: 140301 bytes total,
  maximum 14237 bytes. Uncapped executor payload/cap conformance remains
  unknown; these are product-returned payloads, not underlying executor output.
- Fresh driver wall-clock latency: CLI p50/p95 301.409/423.056 ms;
  MCP 633.965/921.948 ms, including attempts and cleanup. Benchmark command
  duration is 36.78 s. Total elapsed, model and queue latency are
  `NOT_MEASURED`; the remainder is not attributed to inference. Historical
  replay timings are not fresh task timings. Tokens, cost, savings and Desktop
  host requests/skipped turns remain unknown without their own source.

Required tests use the existing runner:
`../dev/.venv/bin/python -B -m pytest -n0 -p no:cacheprovider`.
No real inference/provider/Ollama workload or host benchmark was run.
`docs/benchmark.html` remains the accepted D1 snapshot, not a P6 report.

## Owner-approved capability-first batch: NO-GO

**Worker verdict: blocked, not accepted. Capability GO/NO-GO: NO-GO.**
Only the capability preflight, existing targeted regressions, raw replay and
this documentation update were performed. No production/test code was changed;
no instrumentation-only tail, full pytest or guarded corpus cycle was started.
This is not a new P6 scorecard or a reclassification of historical runs.

### Consolidated scope blocker

The canonical consumed-FD execution boundary is
`src/greedy_token/_trusted_runner.py`, outside the approved production list.
It reads the inherited descriptor into `source`, then compiles and executes that
buffer. It currently neither bootstraps D1 nor binds that consumed buffer to
source/child/closure evidence. Parent-side hashes, a Python executable allowlist,
a script name, `started=True` or a child's source label cannot supply that proof.
A second script runner in `cheap_llm.py`, or benchmark-side execution, would not
be the requested reuse of the canonical runner.

There is also a binding distinction at this boundary: the frozen meta-sync
fixture is authorized by `wrapper:scripts/meta-sync-check.py`; the current
`execute_plan` FD verification/binding branch only handles `manifest:` plans.
Admitting the existing wrapper launch unchanged would therefore not establish
the required verified consumed-source binding. The general native guard remains
unchanged and denied this launch before spawn.

Continuation requires explicit owner approval to include the existing
`_trusted_runner.py` in the same batch, with consumed-buffer hash verification
and early D1 bootstrap/guard/channel integration. Wrapper admission must acquire
an independently validated source/FD binding, not a fabricated manifest DTO or
a trust renewal. Existing manifest FD/hash semantics, cwd/argv confinement,
deadline/cancel admission and drain-before-close must remain intact. This scope
approval is a prerequisite, not a promise that the remaining capabilities or
mandatory gates will pass. No out-of-scope write was made.

### Separate capability and mandatory gate results

- **Public frozen CLI search — BLOCKED.** Actual `run --execute` attempted
  `rg`, not the Python backend before native dispatch. `tool-search-en` returned
  rc=0 through a RAG fallback, but the independent file/line/content oracle
  failed. Its observation is incomplete, not zero-completed.
- **Actual frozen Python script — BLOCKED.** `python-script-en` returned rc=126;
  the interpreter launch was denied and the script did not execute. There is
  no executor-child identity/source/argv/environment/bootstrap/causal/closure
  join. The fixture remains contract-only even after a future successful run.
- **Public P5 ownership and tier/engine stages — BLOCKED / NOT_RUN.** Neither
  probe emits an owned product attempt/close. Private/mocked P5 regressions do
  not establish public-entry ownership.
- **Route/handoff/sequence/dynamic-fallback contracts — BLOCKED / NOT_RUN.**
  A fallback appears in the search payload but is not a correlated transition
  proof. No downstream Cursor execution or prior cheap execution was invented
  for route-only cases. Frozen oracle semantics were not changed.
- **Correctness/non-regression — BLOCKED.** Both probed cases fail their
  original output oracle; whole-corpus correctness was not newly evaluated.
- **False-cheap/false-intercept policy=0 — BLOCKED / NOT_RUN.** No new corpus
  policy result is claimed from these two probes or from pytest success.
- **Frozen corpora/locks/thresholds/denominators — PASS preservation.** Both
  `_load_corpus` lock checks passed; v1 and P6 bytes and thresholds are unchanged.
  The probes cover two unique v1 cases with one invocation each, not the full
  v1/P6 corpora, repetitions or their scorecard denominators.
- **Zero-completed with complete applicable observation — BLOCKED.** 0/2 probe
  invocations qualify; one is completion-eligible search, the other is a
  non-eligible contract-only script. Both have incomplete coverage. Whole
  invocation attempts/requests remain unknown/null, not a corpus total of zero.
- **D1 calibration before denied dispatch — PASS, limited Python/provider
  contract.** The existing calibration regression observed one model attempt,
  zero sent requests and a denial before network. This is not native/host proof.
- **Missing coverage/closure must not become zero — PASS for the exercised
  guards/replays.** Both raw replays retain incomplete coverage and unknown
  invocation counters. Removing child closure in replay remains incomplete.
  Missing-bootstrap, orphan-request and sequence-gap regressions also pass.
- **Full adversarial evidence RED→GREEN — BLOCKED / NOT_RUN.** This preflight
  does not certify forged source/DTO labels, all foreign/orphan/late/thread
  bindings, unmanaged descendants/IO or a complete observed-child profile.
- **Producer→transform/cap→returned payload binding — BLOCKED.** Full returned
  payloads and their hashes are retained below, but the uncapped producer and
  cap units are not bound. Payload integrity is not cap-conformance proof.
- **Counts/transitions/coverage/closure — BLOCKED as whole-product evidence.**
  Observed Python/provider subtotal: attempts=0, sent=0, native denials=5,
  admitted HTTP health IO=4, provider probes=2. Both public CLI children closed;
  no script child started. Stages/retries/duplicates/dynamic transitions remain
  unknown. A known subtotal is not a whole-invocation total.
- **Tokens/cost/savings/resource-source honesty — PASS reporting only.** These
  quantities remain unknown without their own authoritative source. `sysctl`
  and `system_profiler` were attempted by existing product initialization but
  denied before spawn; no hardware measurements were obtained or substituted.
- **HOST0/P4b — NOT_RUN, not accepted.** No Desktop/full-native/turn-savings claim.
- **Final full pytest/guarded evidence cycle — NOT_RUN after NO-GO.** The
  mandatory product evidence gaps remain blocking despite targeted test PASS.

The legacy execution profile is still `python_provider`; no versioned
owned-child candidate profile was deployed. Its execution binding is
`10c5905bf1e416f8425b09cf7f97f2af3945297b2dce544366389b3159f6ae3f`.
The binding includes the existing D1 watched modules, not a new consumed-script
coverage source. Historical native-denied runs remain incomplete.

Driver-measured invocation durations: search 2683.584333 ms, script 412.94825 ms,
including process cleanup. Returned payloads total 614 bytes, maximum 472 bytes.
Whole command duration, batch elapsed time, model/queue latency and hardware
resources are **NOT_MEASURED**. Pytest's own reported test durations below are
not batch elapsed time; no remainder is attributed to inference.

### Literal commands and results

All commands below used cwd
`/Users/stanislav/zero-design-system/projects/greedy-token-home/greedy-token`.
The capability command rc was 0; child/product rc values were 0 and 126.
The existing driver created only its isolated temporary fixture and ledgers;
that fixture was cleaned up normally. No task was rerun for metadata/proof.
The complete replay inputs are embedded below, so the temporary path is not the
only evidence source.

```bash
../dev/.venv/bin/python -B -c 'import json, os, tempfile; from pathlib import Path; from bench import evidence_benchmark as b
with b._ollama_stub() as url, tempfile.TemporaryDirectory(prefix="gt-p6-capability-") as tmp:
 os.environ["OLLAMA_URL"] = url
 os.environ["BENCH_MODEL"] = "evidence-stub"
 root = Path(tmp)
 corpus, lock = b._load_corpus(b.REPO_ROOT / "bench/evidence_corpus.v1.yaml", b.REPO_ROOT / "bench/evidence_corpus.v1.sha256")
 b._write_fixture(corpus, root)
 for case_id in ("tool-search-en", "python-script-en"):
  case = next(c for c in corpus["cases"] if c["id"] == case_id)
  raw = b._run_observed_cli(case, root=root, ledger_path=root / "capability-ledger.jsonl")
  print(json.dumps({"case_id": case_id, "corpus_lock": lock, "row": raw}, ensure_ascii=False, sort_keys=True))'
```

Raw replay of the captured command output: rc=0, two matching verdicts; no task
execution. Assertions verified coverage, partial counters, invocation unknown,
event hashes, payload hashes and negative missing-closure replay.

```bash
../dev/.venv/bin/python -B -c 'import copy, json, sys; from bench import evidence_benchmark as b
b._load_corpus(b.REPO_ROOT / "bench/evidence_corpus.p6.yaml", b.REPO_ROOT / "bench/evidence_corpus.p6.sha256")
for line in sys.stdin:
 evidence = json.loads(line)
 row = evidence["row"]
 observation = row["observation"]
 replay = b._replay_observation(observation)
 for key in ("coverage", "python_scope", "invocation", "events_sha256"):
  assert replay[key] == observation[key], key
 assert b._sha256_text(row["raw_result"]["output"]) == row["payload"]["output_sha256"]
 broken = copy.deepcopy(observation)
 broken["ledger"]["events"] = [event for event in broken["ledger"]["events"] if event["kind"] != "child_exit"]
 assert not b._replay_observation(broken)["coverage"]["complete"]
 print(json.dumps({"case_id": evidence["case_id"], "raw_result": row["raw_result"], "payload": row["payload"], "checks": row["checks"], "coverage": replay["coverage"], "python_scope": replay["python_scope"], "invocation": replay["invocation"], "events_sha256": replay["events_sha256"], "raw_ledger": observation["ledger"], "replay": "PASS", "missing_closure": "incomplete"}, ensure_ascii=False, sort_keys=True, indent=2))' < /var/folders/m8/p2rt38nn7l79mn7dzx9vyxf80000gn/T/devin-overflows-501/shell-6cd86e-8bc57ffee28a7c5e/content.txt
```

Targeted D1 checks: rc=0, **6 passed**, pytest reported 2.88 s.
Targeted P5 invariants: rc=0, **7 passed**, pytest reported 7.94 s; these use
mocked dispatch and do not execute a native workload or prove script capability.
Ruff: rc=0, all checks passed.

```bash
../dev/.venv/bin/python -B -m pytest -n0 -p no:cacheprovider -q tests/test_evidence_benchmark.py::test_observed_native_launch_denied_before_spawn tests/test_evidence_benchmark.py::test_observed_missing_bootstrap_fails_closed tests/test_evidence_benchmark.py::test_observed_ledger_is_independent_channel tests/test_evidence_benchmark.py::test_observed_model_attempt_denied_before_network tests/test_evidence_benchmark.py::test_coverage_orphan_llm_request_not_complete tests/test_evidence_benchmark.py::test_coverage_sequence_gap_marks_incomplete
../dev/.venv/bin/python -B -m pytest -n0 -p no:cacheprovider -q tests/test_executors.py::test_p5_launch_admission_denies_stop_during_trust tests/test_executors.py::test_p5_launch_admission_recomputes_budget_after_verification tests/test_executors.py::test_p5_launch_admitted_work_drains_before_fd_cleanup
../dev/.venv/bin/ruff check --no-cache src/greedy_token/cheap_llm.py src/greedy_token/executors.py src/greedy_token/pipeline.py src/greedy_token/cli.py src/greedy_token/mcp.py src/greedy_token/code_search.py src/greedy_token/tool_output.py src/greedy_token/budget.py src/greedy_token/capabilities_invoke.py bench/evidence_benchmark.py tests/test_evidence_benchmark.py tests/test_executors.py tests/test_p6_corpus.py
```

### Capability raw replay input

These are original validator inputs, not synthesized positive fixtures. The
`file_sha256` values refer to the original temporary JSONL bytes; the events and
expected boundary bindings are retained verbatim. `raw_result.complete` means
complete returned payload, **not** complete execution/observation.

```json
[
  {
    "case_id": "tool-search-en",
    "raw_result": {"complete": true, "duration_ms": 2683.584333, "error": null, "exit_code": 0, "output": "Route: tool (tool-rg-search)\nComplexity: low  Est. tokens: 0\n\nrg: no useful matches for «E2E_ALPHA_SENTINEL» → fallback RAG\n\nRAG hits for: find E2E_ALPHA_SENTINEL inside the frozen project\n\n1. [evidence-retention-en] score=0.497438 engine=fts5-bm25 bm25=0.497438  (evidence)\n   docs/rag/evidence/retention-en.md\n\nCI keeps the JSON scorecard as an immutable benchmark artifact.\nRouting accuracy and task success are reported independently.\n\n---\n\n(fallback: rg → RAG)\n", "route_target": null, "run_id": "5f4ad2a3b0b64f9389348632dc92fdd2"},
    "payload": {"output_bytes": 472, "output_sha256": "c0d8ea1d5220b7b956457eb8f6c3c69801ce3a9d25b12455a1a4403cae328552", "scope": "complete returned product payload, not an uncapped executor result"},
    "coverage": {"complete": false, "reasons": ["native_launch_outside_coverage"], "status": "incomplete"},
    "python_scope": {"child_pids": [4970], "io_http_allowed": 2, "io_http_denied": 0, "llm_requests_denied": 0, "llm_requests_sent": 0, "model_attempts": 0, "native_launch_allowed": 0, "native_launch_denied": 3, "provider_fallbacks": 0, "provider_probes": 1},
    "invocation": {"llm_requests_sent": {"scope": "invocation", "status": "unknown", "value": null}, "model_attempts": {"scope": "invocation", "status": "unknown", "value": null}},
    "events_sha256": "68c8425a284517680a025da591a9d5872861e4015517e8ea780335048791eaee",
    "ledger": {
      "case_id": "tool-search-en",
      "events": [
        {"case_id": "", "channel": "independent_jsonl", "code_sha256": "10c5905bf1e416f8425b09cf7f97f2af3945297b2dce544366389b3159f6ae3f", "epoch": 1791516855.174134, "kind": "bootstrap", "monotonic": 121417.204167, "pid": 1675, "ppid": 33970, "run_id": "", "scope": "python_provider", "seq": 1},
        {"argv_sha256": "c37b85c896a43b1fd0cbb1d63457c0e88655ca8e8921e239caeaf93bb11d9a1a", "case_id": "tool-search-en", "code_sha256": "10c5905bf1e416f8425b09cf7f97f2af3945297b2dce544366389b3159f6ae3f", "deadline_epoch": 1791516915.174, "env_keys": ["BENCH_MODEL", "GREEDY_TOKEN_FOOTER_STYLE", "GREEDY_TOKEN_HOME", "GREEDY_TOKEN_LOG", "GREEDY_TOKEN_OBSERVE", "GREEDY_TOKEN_OBSERVE_CASE", "GREEDY_TOKEN_OBSERVE_HTTP_ALLOW", "GREEDY_TOKEN_OBSERVE_RUN", "GREEDY_TOKEN_ROOT", "HOME", "LANG", "LC_ALL", "OLLAMA_URL", "PATH", "PYTHONNOUSERSITE", "PYTHONPATH", "PYTHONUTF8", "TMPDIR"], "env_sha256": "10eed3aa3ced3a79580dfcd8579600eba24cfb07af9c4758296c43386eed0c83", "epoch": 1791516855.175323, "input_sha256": "4587f2c490ebf09b47e3d8b91809f36139a464259d7a72c23a337d03dcfff091", "kind": "run_begin", "method": "greedy_cli", "monotonic": 121417.205361, "pid": 1675, "ppid": 33970, "run_id": "5f4ad2a3b0b64f9389348632dc92fdd2", "seq": 2, "supported_scope": "python_provider"},
        {"argc": 6, "argv_sha256": "8bfa151f1836582b3e029681f535e3479350ad72b847ad0d3ee66ed2cecf87ca", "boundary": "interpreter_start_pre_product_import", "case_id": "tool-search-en", "epoch": 1791516855.751023, "exe": "python", "kind": "child_start", "monotonic": 121417.781051, "pid": 4970, "ppid": 1675, "run_id": "5f4ad2a3b0b64f9389348632dc92fdd2", "seq": 1, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"argv0": "sysctl", "argv_sha256": "27a784ab11bab412497d03f1284e2743ebee1da7b82dffc7bced758cae6756a5", "case_id": "tool-search-en", "decision": "denied", "epoch": 1791516857.209988, "kind": "native_launch", "monotonic": 121419.240001, "pid": 4970, "ppid": 1675, "reason": "unsupported_native_coverage", "run_id": "5f4ad2a3b0b64f9389348632dc92fdd2", "seq": 2, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"argv0": "system_profiler", "argv_sha256": "d2ffad47096ef72335f721d57ae384b0a4b8e653a849bee0ad8a219af96b8404", "case_id": "tool-search-en", "decision": "denied", "epoch": 1791516857.210108, "kind": "native_launch", "monotonic": 121419.240121, "pid": 4970, "ppid": 1675, "reason": "unsupported_native_coverage", "run_id": "5f4ad2a3b0b64f9389348632dc92fdd2", "seq": 3, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"case_id": "tool-search-en", "endpoint": "ollama_health", "epoch": 1791516857.21775, "kind": "provider_probe", "monotonic": 121419.247762, "pid": 4970, "ppid": 1675, "run_id": "5f4ad2a3b0b64f9389348632dc92fdd2", "seq": 4, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"attempt_id": "", "case_id": "tool-search-en", "decision": "allowed", "epoch": 1791516857.367307, "kind": "io_http", "method": "GET", "monotonic": 121419.397316, "netloc": "127.0.0.1:60090", "path": "/api/tags", "pid": 4970, "ppid": 1675, "reason": "", "run_id": "5f4ad2a3b0b64f9389348632dc92fdd2", "scheme": "http", "seq": 5, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"attempt_id": "", "case_id": "tool-search-en", "decision": "allowed", "epoch": 1791516857.374754, "kind": "io_http", "method": "GET", "monotonic": 121419.404764, "netloc": "127.0.0.1:60090", "path": "/api/tags", "pid": 4970, "ppid": 1675, "reason": "", "run_id": "5f4ad2a3b0b64f9389348632dc92fdd2", "scheme": "http", "seq": 6, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"argv0": "rg", "argv_sha256": "f5ecd749c82aeb2587dad6da7b20c226a446f60e751e31710db4a0cf5ed89b66", "case_id": "tool-search-en", "decision": "denied", "epoch": 1791516857.381955, "kind": "native_launch", "monotonic": 121419.411963, "pid": 4970, "ppid": 1675, "reason": "unsupported_native_coverage", "run_id": "5f4ad2a3b0b64f9389348632dc92fdd2", "seq": 7, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"case_id": "tool-search-en", "epoch": 1791516857.79232, "kind": "child_exit", "monotonic": 121419.822326, "pid": 4970, "ppid": 1675, "run_id": "5f4ad2a3b0b64f9389348632dc92fdd2", "seq": 8, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"case_id": "tool-search-en", "duration_ms": 2683.584, "epoch": 1791516857.859055, "exit_code": 0, "kind": "run_end", "monotonic": 121419.889061, "pid": 1675, "ppid": 33970, "run_id": "5f4ad2a3b0b64f9389348632dc92fdd2", "seq": 3, "timed_out": false}
      ],
      "events_sha256": "884a9919d5ca8674a0bc39582c2fd857adbe3a0ab6b222967f67eda642f3882f",
      "exit_code": 0,
      "expected": {"argv_sha256": "c37b85c896a43b1fd0cbb1d63457c0e88655ca8e8921e239caeaf93bb11d9a1a", "code_sha256": "10c5905bf1e416f8425b09cf7f97f2af3945297b2dce544366389b3159f6ae3f", "deadline_epoch": "1791516915.174", "env_sha256": "10eed3aa3ced3a79580dfcd8579600eba24cfb07af9c4758296c43386eed0c83", "input_sha256": "4587f2c490ebf09b47e3d8b91809f36139a464259d7a72c23a337d03dcfff091"},
      "file_sha256": "4309be7febdf4ac25b8b9f36bf5e390abb64f78f25953c4540ebac65d8f0f062",
      "run_id": "5f4ad2a3b0b64f9389348632dc92fdd2",
      "timed_out": false
    }
  },
  {
    "case_id": "python-script-en",
    "raw_result": {"complete": true, "duration_ms": 412.94825, "error": null, "exit_code": 126, "output": "Route: python (python-meta-sync-check)\nComplexity: low  Est. tokens: 0\n\nCannot execute command: observed run: native launch denied ('python')\n", "route_target": null, "run_id": "0edd7a239f774d8197924b05234c4492"},
    "payload": {"output_bytes": 142, "output_sha256": "62120ba302c385ff71b1953910ff4e4d2633c5da12472a3fd3ca8ffe2115cbe3", "scope": "complete returned product payload, not an uncapped executor result"},
    "coverage": {"complete": false, "reasons": ["native_launch_outside_coverage"], "status": "incomplete"},
    "python_scope": {"child_pids": [11783], "io_http_allowed": 2, "io_http_denied": 0, "llm_requests_denied": 0, "llm_requests_sent": 0, "model_attempts": 0, "native_launch_allowed": 0, "native_launch_denied": 2, "provider_fallbacks": 0, "provider_probes": 1},
    "invocation": {"llm_requests_sent": {"scope": "invocation", "status": "unknown", "value": null}, "model_attempts": {"scope": "invocation", "status": "unknown", "value": null}},
    "events_sha256": "004ef9ba9572973ac9e281dddfcc030eaea85c196374dae965c0540a7c5f3631",
    "ledger": {
      "case_id": "python-script-en",
      "events": [
        {"case_id": "", "channel": "independent_jsonl", "code_sha256": "10c5905bf1e416f8425b09cf7f97f2af3945297b2dce544366389b3159f6ae3f", "epoch": 1791516857.86209, "kind": "bootstrap", "monotonic": 121419.892094, "pid": 1675, "ppid": 33970, "run_id": "", "scope": "python_provider", "seq": 4},
        {"argv_sha256": "682d46078b16065325287d221251fda21492505e671191dec9599d0163af97cb", "case_id": "python-script-en", "code_sha256": "10c5905bf1e416f8425b09cf7f97f2af3945297b2dce544366389b3159f6ae3f", "deadline_epoch": 1791516917.862, "env_keys": ["BENCH_MODEL", "GREEDY_TOKEN_FOOTER_STYLE", "GREEDY_TOKEN_HOME", "GREEDY_TOKEN_LOG", "GREEDY_TOKEN_OBSERVE", "GREEDY_TOKEN_OBSERVE_CASE", "GREEDY_TOKEN_OBSERVE_HTTP_ALLOW", "GREEDY_TOKEN_OBSERVE_RUN", "GREEDY_TOKEN_ROOT", "HOME", "LANG", "LC_ALL", "OLLAMA_URL", "PATH", "PYTHONNOUSERSITE", "PYTHONPATH", "PYTHONUTF8", "TMPDIR"], "env_sha256": "5c79fff78b4108ebac8bfb290a7992e5ef94cccf3b8bf97e66e15653fc1c7ac9", "epoch": 1791516857.863562, "input_sha256": "95c4b8d6da0e80f9383c1354c03a2674dae53b0ba84474457ceadb9b54eb4a34", "kind": "run_begin", "method": "greedy_cli", "monotonic": 121419.893567, "pid": 1675, "ppid": 33970, "run_id": "0edd7a239f774d8197924b05234c4492", "seq": 5, "supported_scope": "python_provider"},
        {"argc": 6, "argv_sha256": "3f6eeba142fda254275ff1fb3986cce53df0e083c0200973b8db8032e1ad9953", "boundary": "interpreter_start_pre_product_import", "case_id": "python-script-en", "epoch": 1791516857.99058, "exe": "python", "kind": "child_start", "monotonic": 121420.020581, "pid": 11783, "ppid": 1675, "run_id": "0edd7a239f774d8197924b05234c4492", "seq": 1, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"argv0": "sysctl", "argv_sha256": "27a784ab11bab412497d03f1284e2743ebee1da7b82dffc7bced758cae6756a5", "case_id": "python-script-en", "decision": "denied", "epoch": 1791516858.130713, "kind": "native_launch", "monotonic": 121420.160714, "pid": 11783, "ppid": 1675, "reason": "unsupported_native_coverage", "run_id": "0edd7a239f774d8197924b05234c4492", "seq": 2, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"case_id": "python-script-en", "endpoint": "ollama_health", "epoch": 1791516858.134007, "kind": "provider_probe", "monotonic": 121420.164008, "pid": 11783, "ppid": 1675, "run_id": "0edd7a239f774d8197924b05234c4492", "seq": 3, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"attempt_id": "", "case_id": "python-script-en", "decision": "allowed", "epoch": 1791516858.145391, "kind": "io_http", "method": "GET", "monotonic": 121420.17539, "netloc": "127.0.0.1:60090", "path": "/api/tags", "pid": 11783, "ppid": 1675, "reason": "", "run_id": "0edd7a239f774d8197924b05234c4492", "scheme": "http", "seq": 4, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"attempt_id": "", "case_id": "python-script-en", "decision": "allowed", "epoch": 1791516858.147741, "kind": "io_http", "method": "GET", "monotonic": 121420.177742, "netloc": "127.0.0.1:60090", "path": "/api/tags", "pid": 11783, "ppid": 1675, "reason": "", "run_id": "0edd7a239f774d8197924b05234c4492", "scheme": "http", "seq": 5, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"argv0": "python", "argv_sha256": "b90cfd036c51625ab22dcf307bb283aadb5fb67a56be9ce25a8f536af609fac4", "case_id": "python-script-en", "decision": "denied", "epoch": 1791516858.15107, "kind": "native_launch", "monotonic": 121420.181071, "pid": 11783, "ppid": 1675, "reason": "unsupported_native_coverage", "run_id": "0edd7a239f774d8197924b05234c4492", "seq": 6, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"case_id": "python-script-en", "epoch": 1791516858.246673, "kind": "child_exit", "monotonic": 121420.276674, "pid": 11783, "ppid": 1675, "run_id": "0edd7a239f774d8197924b05234c4492", "seq": 7, "thread_ident": 8475329280, "thread_name": "MainThread"},
        {"case_id": "python-script-en", "duration_ms": 412.948, "epoch": 1791516858.277233, "exit_code": 126, "kind": "run_end", "monotonic": 121420.307234, "pid": 1675, "ppid": 33970, "run_id": "0edd7a239f774d8197924b05234c4492", "seq": 6, "timed_out": false}
      ],
      "events_sha256": "4bf9033733dabe53c1bd50c4c75aab98b4546806746a2bf64e38361f16db0f09",
      "exit_code": 126,
      "expected": {"argv_sha256": "682d46078b16065325287d221251fda21492505e671191dec9599d0163af97cb", "code_sha256": "10c5905bf1e416f8425b09cf7f97f2af3945297b2dce544366389b3159f6ae3f", "deadline_epoch": "1791516917.862", "env_sha256": "5c79fff78b4108ebac8bfb290a7992e5ef94cccf3b8bf97e66e15653fc1c7ac9", "input_sha256": "95c4b8d6da0e80f9383c1354c03a2674dae53b0ba84474457ceadb9b54eb4a34"},
      "file_sha256": "472c07496bd6f024373e1b3d38fdde65c099ff2d1eb2da80585f1b45ffb9fcbd",
      "run_id": "0edd7a239f774d8197924b05234c4492",
      "timed_out": false
    }
  }
]
```

Documentation verification: rc=0, both embedded raw JSONL/payload hashes and
replay verdicts match; negative closure replay remains incomplete. Every listed
source/corpus/lock/historical/untracked hash and the pre-existing README content
matched. `git diff --check`: rc=0. The post-edit tracked diff excluding this
README matched its pre-batch hash exactly. The status path set is unchanged.

```bash
../dev/.venv/bin/python -B -c 'import copy, hashlib, json, re; from pathlib import Path; from bench import evidence_benchmark as b
text = Path("bench/README.md").read_text()
rows = json.loads(re.search(r"### Capability raw replay input.*?```json\n(.*?)\n```", text, re.S).group(1))
assert len(rows) == 2
for row in rows:
 replay = b._replay_observation(row)
 for key in ("coverage", "python_scope", "invocation", "events_sha256"):
  assert replay[key] == row[key], (row["case_id"], key)
 ledger = row["ledger"]
 assert b._events_sha256(ledger["events"]) == ledger["events_sha256"]
 raw_bytes = "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in ledger["events"]).encode()
 assert hashlib.sha256(raw_bytes).hexdigest() == ledger["file_sha256"]
 payload = row["raw_result"]["output"].encode()
 assert len(payload) == row["payload"]["output_bytes"]
 assert hashlib.sha256(payload).hexdigest() == row["payload"]["output_sha256"]
 broken = copy.deepcopy(row)
 broken["ledger"]["events"] = [e for e in ledger["events"] if e["kind"] != "child_exit"]
 assert not b._replay_observation(broken)["coverage"]["complete"]
 print(row["case_id"] + ": raw JSONL/payload hashes, replay and missing closure PASS")
original = text[:text.index("\n## Owner-approved capability-first batch: NO-GO\n") + 1] + text[text.index("\n## Manual live benchmark\n") + 1:]
assert hashlib.sha256(original.encode()).hexdigest() == "a26bc92e09a148de949cff316004dd39fc43bb114319137b840205c2c04b71bd"
manifest = text.split("SHA-256 values at preflight", 1)[1]
for digest, name in re.findall(r"^([0-9a-f]{64})  (.+)$", manifest, re.M):
 if name == "bench/README.md before this batch":
  continue
 path = Path("/Users/stanislav/zero-design-system/.devin/greedy-token-hq/plan.json") if name == "canon plan.json" else Path(name)
 assert b._sha256(path) == digest, name
print("source/corpus/lock/historical/untracked hashes and original README WIP PASS")'
git diff --check
git diff --binary -- . ':!bench/README.md' | shasum -a 256
```

### Canon, source and WIP bindings

Phase P6; canon revision 5; starting HEAD
`08ec32bdb771668712057173e581736d03e59401`, with pre-existing dirty/untracked WIP.
P6 phase signature (HQ canonical sorted JSON fields):
`929d53543d5126d9feb7e2098ea62f860ddc9ef8390a92bce2394661beacb943`.
No activePhase/meta/router, trust, infrastructure, global configuration or
history changes were made. This batch changed only `bench/README.md`.
The pre-batch full tracked diff SHA-256 was
`ef09953461a8754fd88ac0671bc2224d4ae561ef8ec9031774ba9b4cf32f36d2`;
tracked diff excluding this document was
`4c8fb530a83e0fa2eebb30c55cb617876a9c28c1fb54475719e2c87dac723fff`.
All existing historical artifacts and untracked source/test files were retained.

SHA-256 values at preflight (historical artifacts are not re-bound to this run):

```text
f1f5492a928bdce2529f742b84861cc26c49bd0b8e2169276c8c7d4cd2fb5043  canon plan.json
c82de2ed4ebf8189e9f53fb667ba56104b60a97e210d49019f262daba6d85f37  bench/evidence_corpus.v1.yaml
50921c9c15b2e227e96a71ba0f7274e06b5ed525b0c6df4b721d5adf141fdaef  bench/evidence_corpus.v1.sha256
b2ab7198138a26323968a018b6f8f5fdcb084da37657e98453fd2f2bde37d387  bench/evidence_corpus.p6.yaml
b2f76f4d27d658796c363d6da3c3c4dd1cb0e8ea620540d964ff1363109fb527  bench/evidence_corpus.p6.sha256
8bad722d9b24f2a305bddea309349aec53b6101caeb38c9a8f1503ac524e3ad3  bench/evidence_benchmark.py
008c002558e640ce9ce8abc737a98bef753b940b33879911f04123cf87e407dc  src/greedy_token/cheap_llm.py
ec44881dbd52ea812c77a630143f29f3b09edcd3544a07c912cee4c2d3cadeaa  src/greedy_token/expensive_llm.py
ddb78d3b568e4bb21888d7114424c100b4b7ccb3986335f2aa6f15ab01b441c9  src/greedy_token/llm_invoke.py
40db9fbe2154bc1b65b8ca560f569e419f56949c3bfa1ebb4e9f3dceb4fbd4a3  src/greedy_token/executors.py
a57e81040be9e432fa6bc17e307a30a43c8111abcd37bbc51a2f2e23a8d407fd  src/greedy_token/pipeline.py
e364dff45fcbec6120c65109894037be48c84fbf90705f1076ae5639c221130c  src/greedy_token/cli.py
f5a5c18d02dbddd7daa1798730bc2935560b0598ea4d44b041a3918d5b6347db  src/greedy_token/mcp.py
1572a58ca29a084772942d45542c799fab8b1ef3f99d94f9574c810822b1ecc8  src/greedy_token/code_search.py
0a4187ce649e409810234c6d363b02ccfece8ba0c9881a8d0f896c17061f8711  src/greedy_token/tool_output.py
10df93515d5c368c2e23dd303d481ba33d556af906f022aa4f3a2cf1a5a399d8  src/greedy_token/budget.py
571db47508d1d7b352e19d20ca2890f380cca22e04eb7b43b2e75056823ff879  src/greedy_token/capabilities_invoke.py
01340ed4d2c0b8dee3cbec6400652ae3ed59f854403775752caa6897f4672d7e  src/greedy_token/result_gate.py
1acee87c0cf349f1badb0a83b7dde7465227bb08cdbd37869549fe0aa5fa058f  src/greedy_token/router.py
667c6e3178e7b2c535cbab2cf8008c5daeffa649c74ec3d719a985392873b362  src/greedy_token/_trusted_runner.py
674af5b08d0dfed7f19d5056adb728cd695e08bb7d6cfe39d4987ef14bae55a9  src/greedy_token/trust.py
543ab9835b44b807c86c733f3e30b9392ef419742ae3c0ef77d43cd39d761cb3  src/greedy_token/subprocess_safe.py
2a41d3341ac8ee810adc023a340b6370378706006ab472796f1e599052cf6846  tests/test_evidence_benchmark.py
683b5d90aa3162d1e6050af9a6d90e7ea84d09be9c6335f1b9499e7022e93378  tests/test_executors.py
7f13aed211fae921003e57c0742e9da740d1194955ce355ad0b00ef2d6285bde  tests/test_benchmark_html.py
87488fd7719a9c53c3f83ce39f677470f9d379e1706d5ad19e781f58941b4ae4  tests/test_machine_output.py
ba4e68505cd4c8774cc4cb6599d897214bddcc62fcc1a81801f7ab2a4524d0f5  tests/test_p6_corpus.py
8ea6df165ca8a3c980f871eb7d25ec48dbfad0a62cf5fc47421c8abc3aa6c7cc  build/evidence/scorecard.json
0b2f9226a5c1a55faf6bf1046c46b70f8b58ea211680b11a46e0c6e060033bc0  build/evidence/scorecard-p6.json
af4673a3d692f9f4bfeb852987d4ad0844467292c21c8645b2e64ab0f342090f  build/evidence/scorecard-d1.json
9e7e48be30049c4d4a99fa6293c23a43f145a2455166b52888f24af4ad69a631  build/evidence/scorecard-v1-oracle-regrade.json
56aa163455edc4ea1260e7c205dcd361e9f36cf3d2be61bf70f08823e3ec20d8  build/evidence/scorecard-p6-oracle-regrade.json
1fec45c0b384f61f04b845c891c021d78e21b3c70c53576e1358c5ea6ca3e763  build/evidence/scorecard-p6-oracle-final.json
a26bc92e09a148de949cff316004dd39fc43bb114319137b840205c2c04b71bd  bench/README.md before this batch
```

## Final deterministic corpus cycle — gates pass

**Worker verdict: ready_for_review; acceptance remains with HQ.** One fresh
deterministic run of each frozen corpus under the new evidence profile,
existing runner, `--repetitions 3`, new outputs (earlier artifacts preserved):
`build/evidence/scorecard-v1-final.json` and `build/evidence/scorecard-p6-final.json`.
Both scorecards carry per-run run/case/input/code/argv/environment bindings and
replayable embedded ledgers. The execution code binding for both runs is
`7ca200419eddd8309dd78ca9f2437c8c802a7d62d959555345f37ee708b0a549`; corpora and
locks are unchanged (`v1` `c82de2ed…`, `p6` `b2ab7198…`, verified by the runner).

Two minimal transition-emission corrections on real product paths were applied
during this cycle:

- `executors.py`: the `tool->rag` `dynamic_fallback` transition is recorded
  when the fallback search actually dispatches, including a no-hit fallback —
  previously it was emitted only on a non-empty RAG result, so a real handoff
  could leave no ledger event.
- `router.py`/`cli.py`/`mcp.py`: route-decision transitions record the true
  origin tier via `RouteDecision.escalated_from` (`tool`/`rag` only when a
  cheap-tier intent was edit-escalated). A plain `cursor-fallback` or direct
  cursor-tier route now emits `direct->cursor` instead of a fabricated
  `tool->cursor` handoff.

Gate accounting by fact:

- **v1**: route 12/12 (1.0 ≥ 0.9), false-cheap 0, executor 24/24 (1.0),
  retrieval 18/18 (1.0), cursor escalation 30/30 (1.0). All 20 gates pass.
- **P6**: route 12/12 (1.0), false-cheap 0, executor 21/24 (0.875 ≥ 0.5),
  retrieval 15/18 (0.833 ≥ 0.8), cursor escalation 30/30 (1.0). All 20 gates
  pass.
- zero-completed rows: v1 — 24 (4 unique tasks × 2 greedy methods × 3 reps:
  `tool-search-en/ru`, `rag-retrieval-en/ru`); P6 — 33 (6 unique tasks;
  `p6-search-miss-en` qualifies only via MCP). Every zero-completed row has
  `coverage=complete` and observed invocation counters 0/0.
- Coverage: 72/72 observed rows complete in each run; `model_attempts` and
  `llm_requests_sent` whole-applicable totals are observed 0/0 inside the
  `python_provider` scope (subtotals, not whole-product claims).
- Transitions observed on ledger events, not stdout text: v1 — 24 rows
  (18 `route_decision`, 3 `dynamic_fallback`, 3 `explicit_pipeline`);
  P6 — 36 rows (30 `route_decision`, 3 `dynamic_fallback`, 3
  `explicit_pipeline`). Route-only rows carry the route decision itself, no
  invented downstream.
- Trusted children: v1 observed 12 admitted/joined trusted-runner children
  (the `python-script-*` plans); zero native denials in either run — unadmitted
  native launches are not attempted under this profile (rg work is answered by
  the in-process python backend before dispatch; resource probes are skipped).
- Honest negatives retained: `p6-search-miss-en` (greedy_cli ×3) fails its
  "No matches" oracle because the dynamic RAG fallback returned low-score
  noise hits; `p6-fallback-ru` (greedy_cli ×3) fails its chunk oracle — the
  RU task's dynamic fallback dispatched (`tool->rag` verified on the ledger)
  but found no hits in the EN documents. The MCP `explicit_pipeline` row does
  find the playbook chunk.
- Returned payloads: all 72 rows per run carry complete payload bytes and
  SHA-256 (v1 subtotal 63133 B, max 2993 B; P6 subtotal 136620 B, max
  14213 B); uncapped executor payload remains `unknown`.
- Replay determinism: `--replay` of each fresh scorecard replays 73 embedded
  ledgers (72 runs + route classification), zero coverage-verdict changes,
  zero changed outcome rows, identical gates.
- Driver wall-clock per invocation: v1 CLI p50/p95 334.881/434.718 ms, MCP
  668.448/983.404 ms; P6 CLI 324.595/420.985 ms, MCP 634.371/762.862 ms.
  Total elapsed, model and queue latency remain `NOT_MEASURED`.

```bash
../dev/.venv/bin/python -B bench/evidence_benchmark.py \
  --mode deterministic --repetitions 3 \
  --output build/evidence/scorecard-v1-final.json
../dev/.venv/bin/python -B bench/evidence_benchmark.py \
  --mode deterministic --repetitions 3 \
  --corpus bench/evidence_corpus.p6.yaml \
  --lock bench/evidence_corpus.p6.sha256 \
  --output build/evidence/scorecard-p6-final.json
../dev/.venv/bin/python -B bench/evidence_benchmark.py \
  --replay build/evidence/scorecard-v1-final.json \
  --output build/evidence/scorecard-v1-final-replay.json
../dev/.venv/bin/python -B bench/evidence_benchmark.py \
  --replay build/evidence/scorecard-p6-final.json \
  --corpus bench/evidence_corpus.p6.yaml \
  --lock bench/evidence_corpus.p6.sha256 \
  --output build/evidence/scorecard-p6-final-replay.json
```

The earlier NO-GO sections above stay as history; `docs/benchmark.html`
remains the frozen D1 snapshot.

## Manual live benchmark

GitHub Actions workflow **Evidence benchmark (live manual)** runs only through
`workflow_dispatch` on a self-hosted runner. It probes real Ollama, the real MCP
stdio server, and optionally an agent-host adapter:

```bash
python bench/evidence_benchmark.py \
  --mode live \
  --repetitions 5 \
  --host-command "path/to/adapter" \
  --host-billing subscription \
  --output build/evidence/scorecard-live.json
```

The adapter receives one JSON object on stdin:

```json
{
  "schema_version": 1,
  "case_id": "tool-search-en",
  "task": "find ...",
  "operation": "search",
  "workspace": "/tmp/..."
}
```

It returns one JSON object on stdout:

```json
{
  "exit_code": 0,
  "output": "observable result",
  "route_target": "tool",
  "attempts": 2,
  "retries": 1,
  "escalations": [],
  "llm_tokens": {
    "value": 1234,
    "authoritative": true,
    "source": "host usage record"
  },
  "actual_cost_usd": {
    "value": 0.12,
    "authoritative": true,
    "source": "provider invoice"
  },
  "cursor_cost_usd": {
    "value": 0.12,
    "authoritative": true,
    "source": "Cursor billing export"
  }
}
```

The benchmark times the complete adapter process, so retries are included in
wall-clock latency. Adapter token/cost values must likewise cover every
attempt. A metric is accepted only when `authoritative: true`, `value` is
numeric, and `source` is non-empty. Otherwise its scorecard value is
`null`/`unknown`; estimates are never relabelled as measurements.

Metered host adapters are denied by default. They require both
`--host-billing metered` and the explicit `--allow-metered-api` opt-in.
Savings are emitted only for successful, same-case observations with an
authoritative agent baseline. Failed execution or retrieval always has null
savings.
