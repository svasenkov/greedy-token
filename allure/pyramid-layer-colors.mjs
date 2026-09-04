/**
 * Palette A (cool → warm) for Allure testing pyramid (config/tests helpers).
 * Runtime HTML theme: @qa-guru/allure-report-kit → @qa-guru/allure-notifications-pyramid.
 * Keep hex in sync with @qa-guru/allure-notifications-pyramid + pyramid-layers.json.
 */

/** Config / @Layer order (base → tip). */
export const PYRAMID_LAYERS = [
  "unit",
  "component",
  "integration",
  "api",
  "ui",
  "e2e",
  "manual",
];

/** Funnel visual order after Allure `[...data].reverse()` (tip → base). */
export const PYRAMID_FUNNEL_TOP_TO_BOTTOM = [...PYRAMID_LAYERS].reverse();

/** Palette A / Allure3 F5 — dark theme (Allure data-theme="dark"). */
export const PYRAMID_COLORS_DARK = {
  unit: "#94ca66",
  component: "#ffa833",
  integration: "#a65ac4",
  api: "#ffd833",
  ui: "#f472b6",
  e2e: "#ff574f",
  manual: "#61b6fb",
  other: "#5d6876",
};

/** Palette A / Allure3 F5 — light theme (Allure data-theme="light"). */
export const PYRAMID_COLORS_LIGHT = {
  unit: "#94ca66",
  component: "#ff8200",
  integration: "#7e22ce",
  api: "#e8bd00",
  ui: "#db2777",
  e2e: "#dc2626",
  manual: "#459bde",
  other: "#64748b",
};

export const PYRAMID_COLORS = {
  dark: PYRAMID_COLORS_DARK,
  light: PYRAMID_COLORS_LIGHT,
};

export function cssVarForLayer(layer) {
  return `var(--layer-${layer})`;
}

export function colorForLayer(layer, theme) {
  const palette = PYRAMID_COLORS[theme] || PYRAMID_COLORS.light;
  return palette[layer] ?? null;
}

/**
 * Pair funnel shapes to layer keys by Y proximity to "Layer: <name>" labels.
 * Colors must not depend on which layers have tests or on array length tricks.
 *
 * @param {{ y: number }[]} shapes  top→bottom (ascending y)
 * @param {{ layer: string, y: number }[]} labels  unique layers with label y
 * @returns {(string|null)[]} layer key per shape index
 */
export function pairShapesToLayers(shapes, labels) {
  if (!shapes.length) return [];

  // Full pyramid: shapes are sorted top→bottom; funnel order is deterministic.
  // Prefer this over label Y pairing — Allure concatenates tspans and can
  // poison labels (e.g. "manualNo tests") before normalizeLayer runs in JS.
  if (shapes.length === PYRAMID_FUNNEL_TOP_TO_BOTTOM.length) {
    return [...PYRAMID_FUNNEL_TOP_TO_BOTTOM];
  }

  if (labels.length === shapes.length) {
    const sorted = [...labels].sort((a, b) => a.y - b.y);
    return sorted.map((entry) => entry.layer);
  }

  if (labels.length > 0) {
    return shapes.map((shape) => {
      let best = null;
      let bestDist = Infinity;
      for (const entry of labels) {
        const dist = Math.abs(entry.y - shape.y);
        if (dist < bestDist) {
          bestDist = dist;
          best = entry.layer;
        }
      }
      return best;
    });
  }

  return shapes.map(() => null);
}

export function assertPaletteUnique(theme) {
  const palette = PYRAMID_COLORS[theme];
  const values = PYRAMID_LAYERS.map((layer) => palette[layer]);
  const unique = new Set(values);
  if (unique.size !== PYRAMID_LAYERS.length) {
    throw new Error(`Palette A (${theme}) must have unique colors per layer`);
  }
  return values;
}
