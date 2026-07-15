#!/usr/bin/env bash
# Copy Palette A overrides to GitHub Pages root and inject into Allure report HTML.
set -euo pipefail

PAGES_DIR="${1:-pages}"
REPORT_DIR="${2:-pages/reports/latest}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ASSETS_DIR="${ASSETS_DIR:-$SCRIPT_DIR/../assets}"

mkdir -p "$PAGES_DIR"

for f in dashboard-overrides.css dashboard-overrides.js; do
  if [[ ! -f "$ASSETS_DIR/$f" ]]; then
    echo "prepare-allure-pages-colors: missing $ASSETS_DIR/$f" >&2
    exit 1
  fi
  cp "$ASSETS_DIR/$f" "$PAGES_DIR/$f"
done

if [[ -d "$REPORT_DIR" ]]; then
  node "$SCRIPT_DIR/inject-allure-pyramid-colors.mjs" "$PAGES_DIR" "$REPORT_DIR"
fi

echo "prepare-allure-pages-colors: assets → $PAGES_DIR, inject → $REPORT_DIR"
