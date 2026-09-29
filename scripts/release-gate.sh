#!/usr/bin/env bash
# Release gate: pass TARGET semver (no v prefix). Example: ./scripts/release-gate.sh 0.5.7
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:?usage: release-gate.sh X.Y.Z}"

export GREEDY_TOKEN_RELEASE_VERSION="$TARGET"
cd "$ROOT"

# sdist policy: MANIFEST.in can't see git status, so an untracked tests/*.py
# would silently ship — refuse a dirty tests/ tree at the gate.
if command -v git >/dev/null 2>&1 \
  && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  UNTRACKED="$(git -C "$ROOT" status --porcelain -uall -- 'tests/*.py' | grep '^??' || true)"
  if [ -n "$UNTRACKED" ]; then
    printf 'untracked tests/*.py would silently enter the sdist:\n%s\n' "$UNTRACKED" >&2
    exit 1
  fi
fi

python -m compileall -q src/greedy_token
python -m pytest -q
python -m coverage erase
# pytest-cov, not bare `coverage run`: addopts forces -n auto, and coverage
# cannot see xdist worker processes — the report would measure ~0%.
python -m pytest tests/ -q --cov
python -m coverage report --include='src/greedy_token/*'
python -m pytest -q --release-version="$TARGET" -m release
bash "$ROOT/scripts/sync-min-tests-count.sh"

echo "release gate OK: $TARGET"
