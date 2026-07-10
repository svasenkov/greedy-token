#!/usr/bin/env bash
# SSOT: pytest collection count → allure/quality-gate.mjs + _ethalon/test.yml summary.
# Run after adding/removing tests, or via ./scripts/release-gate.sh (end of gate).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

collect_line="$(python -m pytest tests/ --collect-only -q 2>&1 | tail -1)"
if [[ ! "$collect_line" =~ ^([0-9]+)\ tests\ collected ]]; then
  echo "failed to parse pytest collection: $collect_line" >&2
  exit 1
fi
COUNT="${BASH_REMATCH[1]}"

GATE="$ROOT/allure/quality-gate.mjs"
ETHALON="$ROOT/.github/_ethalon/test.yml"

if [[ "$(uname -s)" == Darwin ]]; then
  sed -i '' "s/minTestsCount: [0-9][0-9]*/minTestsCount: ${COUNT}/" "$GATE"
  sed -i '' "s/minTestsCount: [0-9][0-9]*/minTestsCount: ${COUNT}/g" "$ETHALON"
else
  sed -i "s/minTestsCount: [0-9][0-9]*/minTestsCount: ${COUNT}/" "$GATE"
  sed -i "s/minTestsCount: [0-9][0-9]*/minTestsCount: ${COUNT}/g" "$ETHALON"
fi

bash "$ROOT/scripts/sync-github-workflows.sh"
echo "minTestsCount synced → ${COUNT} (pytest collected)"
