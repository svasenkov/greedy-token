#!/usr/bin/env bash
# Mutation testing (anti-false-green) for the "hot" modules.
#
# Config lives in pyproject.toml [tool.mutmut] (source_paths + only_mutate).
# Runs the full pytest suite against each mutant and reports survivors.
#
# Usage:
#   ./scripts/mutation.sh            # run + summary
#   ./scripts/mutation.sh results    # show survivors from the last run
#
# Requires mutmut (pip install -e ".[dev]"). Uses the active interpreter, so
# run it with the project's venv, e.g. ../dev/.venv/bin/python -m ... or after
# `source ../dev/.venv/bin/activate`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v mutmut >/dev/null 2>&1; then
  echo "mutmut not found. Install dev extras: pip install -e '.[dev]'" >&2
  exit 1
fi

if [[ "${1:-run}" == "results" ]]; then
  mutmut results
  exit 0
fi

# Clean any stale working tree so results are reproducible.
rm -rf mutants

mutmut run || true

echo
echo "=== Mutation results (survivors, if any) ==="
mutmut results
