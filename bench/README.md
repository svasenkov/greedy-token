# Public end-to-end evidence benchmark

This benchmark separates **route classification** from **task usefulness**.
It does not treat a held route, a missing override, or an estimated saving as
proof that a task succeeded.

## Frozen corpus

- `evidence_corpus.v1.yaml` is the immutable public RU/EN corpus.
- `evidence_corpus.v1.sha256` locks its exact bytes.
- A correction creates `evidence_corpus.v2.yaml`; v1 is never silently edited.
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
