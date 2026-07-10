#!/usr/bin/env bash
# Release gate: pass TARGET semver (no v prefix). Example: ./scripts/release-gate.sh 0.5.7
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:?usage: release-gate.sh X.Y.Z}"

export GREEDY_TOKEN_RELEASE_VERSION="$TARGET"
cd "$ROOT"

python -m compileall -q src/greedy_token
python -m pytest -q
python -m pytest -q --release-version="$TARGET" -m release
bash "$ROOT/scripts/sync-min-tests-count.sh"

echo "release gate OK: $TARGET"
