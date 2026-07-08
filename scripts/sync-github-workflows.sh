#!/usr/bin/env bash
# Sync runnable .github/workflows/*.yml from .github/_ethalon/*.yml
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ETHALON="$ROOT/.github/_ethalon"
WORKFLOWS="$ROOT/.github/workflows"

runnable_header() {
  case "$1" in
    test.yml)
      echo '# Runnable copy of .github/_ethalon/test.yml — edit ethalon first, then ./scripts/sync-github-workflows.sh.'
      ;;
    publish.yml)
      echo '# Runnable copy of .github/_ethalon/publish.yml — edit ethalon first, then ./scripts/sync-github-workflows.sh.'
      ;;
    *)
      echo "unknown workflow: $1" >&2
      return 1
      ;;
  esac
}

for name in test.yml publish.yml; do
  src="$ETHALON/$name"
  dst="$WORKFLOWS/$name"
  if [[ ! -f "$src" ]]; then
    echo "missing ethalon: $src" >&2
    exit 1
  fi
  {
    runnable_header "$name"
    tail -n +2 "$src"
  } >"$dst"
  echo "synced $name"
done

echo "OK: workflows match _ethalon (header line differs by design)"
