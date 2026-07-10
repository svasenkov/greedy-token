#!/usr/bin/env bash
# CI guard: _ethalon pins + runnable workflows in sync (body from line 2).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ETHALON="$ROOT/.github/_ethalon"
WORKFLOWS="$ROOT/.github/workflows"
PINS="$ETHALON/gha-actions.yaml"

if [[ ! -f "$PINS" ]]; then
  echo "missing $PINS" >&2
  exit 1
fi

check_pair() {
  local name=$1
  local eth="$ETHALON/$name"
  local run="$WORKFLOWS/$name"
  if ! diff -q <(tail -n +2 "$eth") <(tail -n +2 "$run") >/dev/null; then
    echo "OUT OF SYNC: $name — run ./scripts/sync-github-workflows.sh" >&2
    diff -u <(tail -n +2 "$eth") <(tail -n +2 "$run") >&2 || true
    return 1
  fi
  echo "sync OK: $name"
}

fail=0
check_pair test.yml || fail=1
check_pair publish.yml || fail=1

while IFS= read -r action; do
  [[ -z "$action" ]] && continue
  if ! grep -qF "$action" "$PINS"; then
    echo "ethalon uses action not listed in gha-actions.yaml: $action" >&2
    fail=1
  fi
done < <(grep -hoE 'uses: [^ ]+' "$ETHALON"/*.yml | sed 's/uses: //' | sort -u)

gate_count=$(grep -oE 'minTestsCount: [0-9]+' "$ROOT/allure/quality-gate.mjs" | awk '{print $2}')
summary_count=$(grep -oE 'minTestsCount: [0-9]+' "$ETHALON/test.yml" | tail -1 | awk '{print $2}')
collect_line="$(python -m pytest tests/ --collect-only -q 2>&1 | tail -1)"
live_count=""
if [[ "$collect_line" =~ ^([0-9]+)\ tests\ collected ]]; then
  live_count="${BASH_REMATCH[1]}"
fi
if [[ "$gate_count" != "$summary_count" ]]; then
  echo "minTestsCount mismatch: quality-gate.mjs=$gate_count ethalon test.yml summary=$summary_count" >&2
  fail=1
elif [[ -n "$live_count" && "$gate_count" != "$live_count" ]]; then
  echo "minTestsCount stale: configured=$gate_count pytest collected=$live_count — run ./scripts/sync-min-tests-count.sh" >&2
  fail=1
else
  echo "minTestsCount OK: $gate_count"
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "OK: GHA ethalon checks passed"
