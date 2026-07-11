#!/usr/bin/env bash
# SSOT: zero-design-system generators/ethalon/readme/generate-readme-badge.sh
# Consumer copy → greedy-token .github/scripts/generate-readme-badge.sh (test.yml README assets)
#
# Usage:
#   generate-readme-badge.sh RESULTS_DIR OUTPUT_DIR BRANCH BUILD \
#     [COMPONENT_FILTER] [TITLE] [STACK_FOOTER] [OUTPUT_SUFFIX]
#
# OUTPUT_SUFFIX empty → badge.svg; suffix "selenoid" → badge-selenoid.svg
set -euo pipefail

RESULTS_DIR="${1:?allure-results directory required}"
OUTPUT_DIR="${2:?output directory required}"
BRANCH="${3:-main}"
BUILD="${4:-}"
COMPONENT_FILTER="${5:-}"
TITLE="${6:-Selenoid Tests}"
STACK_FOOTER="${7:-Go 1.26 · Java 21 · Allure 3}"
OUTPUT_SUFFIX="${8:-}"

mkdir -p "${OUTPUT_DIR}"

if [ -n "${OUTPUT_SUFFIX}" ]; then
  badge_file="badge-${OUTPUT_SUFFIX}.svg"
  stats_file="stats-${OUTPUT_SUFFIX}.svg"
  panel_file="metrics-panel-${OUTPUT_SUFFIX}.svg"
  panel_dark_file="metrics-panel-${OUTPUT_SUFFIX}-dark.svg"
else
  badge_file="badge.svg"
  stats_file="stats.svg"
  panel_file="metrics-panel.svg"
  panel_dark_file="metrics-panel-dark.svg"
fi

passed=0
failed=0
broken=0
skipped=0
duration_ms=0

while IFS= read -r line; do
  status="${line%%$'\t'*}"
  test_ms="${line#*$'\t'}"
  case "${status}" in
    passed) passed=$((passed + 1)) ;;
    failed) failed=$((failed + 1)) ;;
    broken) broken=$((broken + 1)) ;;
    skipped) skipped=$((skipped + 1)) ;;
  esac
  if [[ "${test_ms}" =~ ^[0-9]+$ ]]; then
    duration_ms=$((duration_ms + test_ms))
  fi
done < <(
  find "${RESULTS_DIR}" -name '*-result.json' -print0 2>/dev/null \
    | while IFS= read -r -d '' file; do
        if [ -n "${COMPONENT_FILTER}" ]; then
          if ! jq -e --arg c "${COMPONENT_FILTER}" '
              ([.labels[]? | select(.name == "component" and .value == $c)] | length) > 0
            ' "${file}" >/dev/null 2>&1; then
            continue
          fi
        fi
        jq -r '
          (.status // "unknown")
          + "\t"
          + (if (.start != null and .stop != null) then (.stop - .start | tostring) else "0" end)
        ' "${file}" 2>/dev/null || true
      done
)

total=$((passed + failed + broken + skipped))

format_duration() {
  local ms="${1:-0}"
  if [ "${ms}" -lt 1000 ]; then
    echo "${ms}ms"
    return
  fi
  local sec=$((ms / 1000))
  if [ "${sec}" -lt 60 ]; then
    echo "${sec}s"
    return
  fi
  local min=$((sec / 60))
  local rem=$((sec % 60))
  echo "${min}m ${rem}s"
}

duration_label="$(format_duration "${duration_ms}")"

if [ "${total}" -eq 0 ]; then
  status_label="no data"
  status_label_upper="NO DATA"
  status_bg="#64748b"
  badge_right="awaiting run"
  pass_rate="—"
else
  pass_rate="$((passed * 100 / total))%"
  if [ "${failed}" -gt 0 ] || [ "${broken}" -gt 0 ]; then
    status_label="failing"
    status_label_upper="FAILING"
    status_bg="#dc2626"
    badge_right="${failed} failed"
    if [ "${broken}" -gt 0 ]; then
      badge_right="${badge_right}, ${broken} broken"
    fi
  else
    status_label="passing"
    status_label_upper="PASSING"
    status_bg="#008a56"
    badge_right="${passed} passed"
  fi
fi

build_line=""
build_meta=""
if [ -n "${BUILD}" ]; then
  build_line=" · build ${BUILD}"
  build_meta="${BUILD}"
else
  build_meta="—"
fi

title_width=$(( ${#TITLE} * 7 + 40 ))
[ "${title_width}" -lt 92 ] && title_width=92
badge_label_width=$(( title_width < 120 ? title_width : 120 ))
badge_total_width=$(( badge_label_width + 128 ))

cat > "${OUTPUT_DIR}/${badge_file}" <<EOF
<svg xmlns="http://www.w3.org/2000/svg" width="${badge_total_width}" height="20" role="img" aria-label="${TITLE}: ${badge_right}">
  <title>${TITLE}: ${badge_right}</title>
  <linearGradient id="b" x2="0" y2="100%">
    <stop offset="0" stop-color="#f8fafc" stop-opacity=".7"/>
    <stop offset=".1" stop-color="#eef2f7" stop-opacity=".3"/>
    <stop offset=".9" stop-color="#eef2f7" stop-opacity=".3"/>
    <stop offset="1" stop-color="#f8fafc" stop-opacity=".7"/>
  </linearGradient>
  <mask id="m"><rect width="${badge_total_width}" height="20" rx="3" fill="#fff"/></mask>
  <g mask="url(#m)">
    <rect width="${badge_label_width}" height="20" fill="#0b4f6c"/>
    <rect x="${badge_label_width}" width="128" height="20" fill="${status_bg}"/>
    <rect width="${badge_total_width}" height="20" fill="url(#b)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="ui-sans-serif,system-ui,sans-serif" font-size="11">
    <text x="$((badge_label_width / 2))" y="14" font-weight="600">${TITLE}</text>
    <text x="$((badge_label_width + 64))" y="14">${badge_right}</text>
  </g>
</svg>
EOF

bar_width=280
segment_total="${total}"
[ "${segment_total}" -gt 0 ] || segment_total=1

passed_w=$((passed * bar_width / segment_total))
failed_w=$((failed * bar_width / segment_total))
broken_w=$((broken * bar_width / segment_total))
skipped_w=$((skipped * bar_width / segment_total))

x=40
passed_x="${x}"
failed_x=$((x + passed_w))
broken_x=$((failed_x + failed_w))
skipped_x=$((broken_x + broken_w))

panel_bar_width=720
panel_passed_w=$((passed * panel_bar_width / segment_total))
panel_failed_w=$((failed * panel_bar_width / segment_total))
panel_broken_w=$((broken * panel_bar_width / segment_total))
panel_skipped_w=$((skipped * panel_bar_width / segment_total))

panel_x=40
panel_passed_x="${panel_x}"
panel_failed_x=$((panel_x + panel_passed_w))
panel_broken_x=$((panel_failed_x + panel_failed_w))
panel_skipped_x=$((panel_broken_x + panel_broken_w))

subtitle="${TITLE}"
if [ -n "${COMPONENT_FILTER}" ]; then
  subtitle="${TITLE} · @Component(${COMPONENT_FILTER})"
fi

cat > "${OUTPUT_DIR}/${stats_file}" <<EOF
<svg xmlns="http://www.w3.org/2000/svg" width="360" height="92" viewBox="0 0 360 92" role="img" aria-label="${TITLE} stats: ${passed} passed, ${failed} failed">
  <title>${subtitle} on ${BRANCH}${build_line}</title>
  <rect width="360" height="92" rx="12" fill="#ffffff" stroke="rgba(11,48,86,0.12)"/>
  <text x="20" y="28" fill="rgba(1,10,24,0.83)" font-family="ui-sans-serif,system-ui,sans-serif" font-size="14" font-weight="700">${TITLE}</text>
  <text x="20" y="46" fill="rgba(2,19,44,0.6)" font-family="ui-sans-serif,system-ui,sans-serif" font-size="11">${BRANCH}${build_line} · ${status_label}</text>
  <rect x="40" y="58" width="${bar_width}" height="10" rx="5" fill="#e2e8f0"/>
EOF

if [ "${passed_w}" -gt 0 ]; then
  echo "  <rect x=\"${passed_x}\" y=\"58\" width=\"${passed_w}\" height=\"10\" rx=\"5\" fill=\"#008a56\"/>" >> "${OUTPUT_DIR}/${stats_file}"
fi
if [ "${failed_w}" -gt 0 ]; then
  echo "  <rect x=\"${failed_x}\" y=\"58\" width=\"${failed_w}\" height=\"10\" fill=\"#dc2626\"/>" >> "${OUTPUT_DIR}/${stats_file}"
fi
if [ "${broken_w}" -gt 0 ]; then
  echo "  <rect x=\"${broken_x}\" y=\"58\" width=\"${broken_w}\" height=\"10\" fill=\"#ea580c\"/>" >> "${OUTPUT_DIR}/${stats_file}"
fi
if [ "${skipped_w}" -gt 0 ]; then
  echo "  <rect x=\"${skipped_x}\" y=\"58\" width=\"${skipped_w}\" height=\"10\" fill=\"#94a3b8\"/>" >> "${OUTPUT_DIR}/${stats_file}"
fi

cat >> "${OUTPUT_DIR}/${stats_file}" <<EOF
  <g font-family="ui-sans-serif,system-ui,sans-serif" font-size="11" fill="rgba(1,18,40,0.68)">
    <circle cx="52" cy="82" r="4" fill="#008a56"/>
    <text x="62" y="86">${passed} passed</text>
    <circle cx="132" cy="82" r="4" fill="#dc2626"/>
    <text x="142" y="86">${failed} failed</text>
    <circle cx="210" cy="82" r="4" fill="#ea580c"/>
    <text x="220" y="86">${broken} broken</text>
    <circle cx="296" cy="82" r="4" fill="#94a3b8"/>
    <text x="306" y="86">${skipped} skipped</text>
  </g>
</svg>
EOF

write_metrics_panel() {
  local output_file="$1"
  local bg0="$2"
  local bg1="$3"
  local stroke="$4"
  local title_fill="$5"
  local meta_fill="$6"
  local value_fill="$7"
  local label_fill="$8"
  local legend_fill="$9"
  local stack_fill="${10}"
  local bar_track="${11}"
  local status_opacity="${12}"
  local gradient_id="${13}"

  cat > "${output_file}" <<EOF
<svg xmlns="http://www.w3.org/2000/svg" width="800" height="168" viewBox="0 0 800 168" role="img" aria-label="${TITLE} metrics: ${total} total, ${pass_rate} pass rate">
  <title>${subtitle} on ${BRANCH}${build_line} · ${status_label}</title>
  <defs>
    <linearGradient id="${gradient_id}" x1="0" y1="0" x2="800" y2="168" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="${bg0}"/>
      <stop offset="1" stop-color="${bg1}"/>
    </linearGradient>
  </defs>
  <rect width="800" height="168" rx="14" fill="url(#${gradient_id})" stroke="${stroke}"/>
  <rect x="20" y="20" width="88" height="88" rx="12" fill="${status_bg}" opacity="${status_opacity}"/>
  <rect x="20" y="20" width="88" height="88" rx="12" fill="none" stroke="${status_bg}" stroke-width="1.5"/>
  <text x="64" y="58" text-anchor="middle" fill="${status_bg}" font-family="ui-sans-serif,system-ui,sans-serif" font-size="13" font-weight="800">${status_label_upper}</text>
  <text x="64" y="78" text-anchor="middle" fill="${status_bg}" font-family="ui-sans-serif,system-ui,sans-serif" font-size="11" font-weight="600">${total} tests</text>
  <text x="128" y="36" fill="${title_fill}" font-family="ui-sans-serif,system-ui,sans-serif" font-size="18" font-weight="700">${TITLE}</text>
  <text x="128" y="56" fill="${meta_fill}" font-family="ui-sans-serif,system-ui,sans-serif" font-size="12">${BRANCH} · build ${build_meta} · ${status_label}</text>
  <text x="128" y="88" fill="${value_fill}" font-family="ui-sans-serif,system-ui,sans-serif" font-size="28" font-weight="700">${total}</text>
  <text x="188" y="88" fill="${label_fill}" font-family="ui-sans-serif,system-ui,sans-serif" font-size="12">total</text>
  <text x="248" y="88" fill="#008a56" font-family="ui-sans-serif,system-ui,sans-serif" font-size="28" font-weight="700">${passed}</text>
  <text x="286" y="88" fill="${label_fill}" font-family="ui-sans-serif,system-ui,sans-serif" font-size="12">passed</text>
  <text x="360" y="88" fill="#dc2626" font-family="ui-sans-serif,system-ui,sans-serif" font-size="28" font-weight="700">${failed}</text>
  <text x="394" y="88" fill="${label_fill}" font-family="ui-sans-serif,system-ui,sans-serif" font-size="12">failed</text>
  <text x="560" y="72" fill="${value_fill}" font-family="ui-sans-serif,system-ui,sans-serif" font-size="22" font-weight="700">${pass_rate}</text>
  <text x="560" y="92" fill="${label_fill}" font-family="ui-sans-serif,system-ui,sans-serif" font-size="12">pass rate</text>
  <text x="680" y="72" fill="${value_fill}" font-family="ui-sans-serif,system-ui,sans-serif" font-size="22" font-weight="700">${duration_label}</text>
  <text x="680" y="92" fill="${label_fill}" font-family="ui-sans-serif,system-ui,sans-serif" font-size="12">duration</text>
  <rect x="40" y="118" width="${panel_bar_width}" height="12" rx="6" fill="${bar_track}"/>
EOF

  if [ "${panel_passed_w}" -gt 0 ]; then
    echo "  <rect x=\"${panel_passed_x}\" y=\"118\" width=\"${panel_passed_w}\" height=\"12\" rx=\"6\" fill=\"#008a56\"/>" >> "${output_file}"
  fi
  if [ "${panel_failed_w}" -gt 0 ]; then
    echo "  <rect x=\"${panel_failed_x}\" y=\"118\" width=\"${panel_failed_w}\" height=\"12\" rx=\"6\" fill=\"#dc2626\"/>" >> "${output_file}"
  fi
  if [ "${panel_broken_w}" -gt 0 ]; then
    echo "  <rect x=\"${panel_broken_x}\" y=\"118\" width=\"${panel_broken_w}\" height=\"12\" rx=\"6\" fill=\"#ea580c\"/>" >> "${output_file}"
  fi
  if [ "${panel_skipped_w}" -gt 0 ]; then
    echo "  <rect x=\"${panel_skipped_x}\" y=\"118\" width=\"${panel_skipped_w}\" height=\"12\" rx=\"6\" fill=\"#94a3b8\"/>" >> "${output_file}"
  fi

  cat >> "${output_file}" <<EOF
  <g font-family="ui-sans-serif,system-ui,sans-serif" font-size="11" fill="${legend_fill}">
    <circle cx="52" cy="150" r="4" fill="#008a56"/>
    <text x="62" y="154">${passed} passed</text>
    <circle cx="152" cy="150" r="4" fill="#dc2626"/>
    <text x="162" y="154">${failed} failed</text>
    <circle cx="242" cy="150" r="4" fill="#ea580c"/>
    <text x="252" y="154">${broken} broken</text>
    <circle cx="338" cy="150" r="4" fill="#94a3b8"/>
    <text x="348" y="154">${skipped} skipped</text>
    <text x="560" y="154" fill="${stack_fill}">${STACK_FOOTER}</text>
  </g>
</svg>
EOF
}

gradient_suffix="${OUTPUT_SUFFIX:-full}"
write_metrics_panel "${OUTPUT_DIR}/${panel_file}" \
  "#ffffff" "#f8fafc" "rgba(11,48,86,0.14)" \
  "rgba(1,10,24,0.88)" "rgba(2,19,44,0.58)" "rgba(1,10,24,0.78)" "rgba(2,19,44,0.55)" \
  "rgba(1,18,40,0.68)" "rgba(2,19,44,0.45)" "#e2e8f0" "0.12" "panel-bg-${gradient_suffix}"

write_metrics_panel "${OUTPUT_DIR}/${panel_dark_file}" \
  "#242830" "#1f2329" "rgba(255,255,255,0.08)" \
  "rgba(232,234,237,0.92)" "rgba(154,160,166,0.88)" "rgba(232,234,237,0.88)" "rgba(154,160,166,0.85)" \
  "rgba(232,234,237,0.72)" "rgba(154,160,166,0.65)" "#3a4049" "0.16" "panel-bg-dark-${gradient_suffix}"

echo "Generated ${OUTPUT_DIR}/${badge_file}, ${stats_file}, ${panel_file} (${passed}/${total} passed, ${duration_label})"
