/**
 * Remap Allure 3 testing pyramid fills to Palette A (--layer-* in CSS).
 * Allure 3.13 hardcodes active bands to var(--color-intent-primary-bg).
 * Keep hex/vars in sync with allure/pyramid-layer-colors.mjs + tokens.css.
 */
(function () {
  const FUNNEL_TOP_TO_BOTTOM = ["manual", "e2e", "api", "integration", "component", "unit"];
  const PALETTE = {
    light: {
      unit: "#3bc95d",
      component: "#ff8200",
      integration: "#c165c1",
      api: "#ffd833",
      e2e: "#f43f3b",
      manual: "#459bde",
    },
    dark: {
      unit: "#60d87a",
      component: "#ffa833",
      integration: "#dd7edd",
      api: "#ffe04a",
      e2e: "#ff6f67",
      manual: "#61b6fb",
    },
  };

  function findPyramidWidget(root) {
    return [...root.querySelectorAll('[class*="styles_widget"]')].find((el) =>
      /testing pyramid|пирамида тестирования/i.test(el.textContent || ""),
    );
  }

  function safeBBoxY(node) {
    try {
      return node.getBBox?.().y ?? 0;
    } catch {
      return 0;
    }
  }

  /**
   * Normalize a raw "Layer: <name>…" fragment to a known layer key.
   * Allure concatenates tspans ("manualNo tests") and layer names contain
   * digits ("e2e"), so a naive [a-z]+ capture is wrong — match by prefix.
   */
  function normalizeLayer(raw) {
    const text = (raw || "").trim().toLowerCase();
    let best = null;
    for (const layer of FUNNEL_TOP_TO_BOTTOM) {
      if (text.startsWith(layer) && (!best || layer.length > best.length)) {
        best = layer;
      }
    }
    return best;
  }

  /** Unique Layer: <name> labels with Y (Allure annotation tspans). */
  function layerLabelsFromWidget(widget) {
    const seen = new Set();
    const labels = [];
    widget.querySelectorAll("text, tspan").forEach((node) => {
      const match = (node.textContent || "").match(/Layer:\s*(.+)/i);
      if (!match) return;
      const layer = normalizeLayer(match[1]);
      if (!layer || seen.has(layer)) return;
      seen.add(layer);
      const textEl = node.closest("text") || node;
      labels.push({ layer, y: safeBBoxY(textEl) });
    });
    return labels;
  }

  function pyramidShapes(svg) {
    return [...svg.querySelectorAll("path, polygon")]
      .filter((shape) => {
        const d = shape.getAttribute("d") || "";
        const points = shape.getAttribute("points") || "";
        return d.length > 16 || points.length > 8;
      })
      .map((shape) => ({ shape, y: safeBBoxY(shape) }))
      .sort((left, right) => left.y - right.y);
  }

  /**
   * Pair shapes → layer by label Y proximity (not by data length / order tricks).
   * Same algorithm as allure/pyramid-layer-colors.mjs#pairShapesToLayers.
   */
  function pairShapesToLayers(shapeEntries, labels) {
    if (!shapeEntries.length) return [];

    // Full pyramid: shapes are sorted top→bottom, funnel order is deterministic.
    if (shapeEntries.length === FUNNEL_TOP_TO_BOTTOM.length) {
      return [...FUNNEL_TOP_TO_BOTTOM];
    }

    if (labels.length === shapeEntries.length) {
      const sorted = [...labels].sort((a, b) => a.y - b.y);
      return sorted.map((entry) => entry.layer);
    }

    if (labels.length > 0) {
      return shapeEntries.map((entry) => {
        let best = null;
        let bestDist = Infinity;
        for (const label of labels) {
          const dist = Math.abs(label.y - entry.y);
          if (dist < bestDist) {
            bestDist = dist;
            best = label.layer;
          }
        }
        return best;
      });
    }

    return shapeEntries.map(() => null);
  }

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function colorForLayer(layer) {
    if (!layer || !PALETTE.light[layer]) return null;
    const cssVar = getComputedStyle(document.documentElement)
      .getPropertyValue(`--layer-${layer}`)
      .trim();
    return cssVar || PALETTE[currentTheme()][layer] || PALETTE.light[layer];
  }

  function setShapeFill(shape, layer) {
    const color = colorForLayer(layer);
    if (!color) return;
    shape.setAttribute("fill", color);
    shape.style.setProperty("fill", color, "important");
    shape.setAttribute("data-pyramid-layer", layer);
  }

  function paintPyramid(root = document) {
    const widget = findPyramidWidget(root);
    if (!widget) return false;

    const svg = widget.querySelector("svg");
    if (!svg) return false;

    const shapeEntries = pyramidShapes(svg);
    if (!shapeEntries.length) return false;

    const labels = layerLabelsFromWidget(widget);
    const layers = pairShapesToLayers(shapeEntries, labels);

    shapeEntries.forEach((entry, index) => {
      const layer = layers[index];
      if (!layer) return;
      setShapeFill(entry.shape, layer);
    });

    return true;
  }

  function schedulePaint() {
    paintPyramid();
    window.setTimeout(paintPyramid, 200);
    window.setTimeout(paintPyramid, 800);
    window.setTimeout(paintPyramid, 2000);
  }

  let paintQueued = false;
  function queuePaint() {
    if (paintQueued) return;
    paintQueued = true;
    requestAnimationFrame(() => {
      paintQueued = false;
      paintPyramid();
    });
  }

  const observer = new MutationObserver(queuePaint);
  observer.observe(document.documentElement, { childList: true, subtree: true });

  new MutationObserver(queuePaint).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });

  window.addEventListener("storage", queuePaint);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", schedulePaint);
  } else {
    schedulePaint();
  }
})();
