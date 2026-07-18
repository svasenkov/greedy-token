/**
 * Remap Allure 3 testing pyramid fills to Palette A / Allure3 F5 (--layer-* in CSS)
 * and reshape the funnel bands to the canon "rounded tiers" geometry (gallery #071).
 * Allure 3.13 hardcodes active bands to var(--color-intent-primary-bg).
 * Keep hex/vars in sync with allure/pyramid-layer-colors.mjs + tokens.css.
 */
(function () {
  const FUNNEL_TOP_TO_BOTTOM = ["other", "manual", "e2e", "api", "integration", "component", "unit"];
  // Canon shape (variants page: rounded tiers). Set to "funnel" to disable reshaping.
  const SHAPE_MODE = "steps";
  // Corner radius as a fraction of a band's inner height (0 = sharp tiers).
  const CORNER_RATIO = 0.15;
  const PALETTE = {
    light: {
      unit: "#15803d",
      component: "#ff8200",
      integration: "#7e22ce",
      api: "#e8bd00",
      e2e: "#dc2626",
      manual: "#459bde",
      other: "#64748b",
    },
    dark: {
      unit: "#31bd58",
      component: "#ffa833",
      integration: "#a65ac4",
      api: "#ffd833",
      e2e: "#ff574f",
      manual: "#61b6fb",
      other: "#5d6876",
    },
  };

  /**
   * Geometry WE last wrote per node+attribute. The geometry observer uses this
   * to tell our reshape writes apart from Allure's re-renders by VALUE, not by
   * timing — resize triggers several layout passes, and a timing-based guard
   * (painting flag / takeRecords) can swallow a genuine Allure re-render, which
   * is exactly how the old funnel leaked back on resolution change.
   */
  const ownWrites = new WeakMap();
  function writeOwn(node, attr, value) {
    node.setAttribute(attr, value);
    let map = ownWrites.get(node);
    if (!map) {
      map = Object.create(null);
      ownWrites.set(node, map);
    }
    map[attr] = value;
    return value;
  }

  /**
   * A mutation is Allure's (foreign) when the attribute's current value differs
   * from what we last wrote — or we never wrote that attribute at all (e.g.
   * width/height/viewBox on <svg>). Deterministic, so interleaved layout passes
   * can never be mistaken for our own reshape.
   */
  function isForeignMutation(record) {
    const attr = record.attributeName;
    const map = ownWrites.get(record.target);
    if (!map || !(attr in map)) return true;
    return map[attr] !== record.target.getAttribute(attr);
  }

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

  // A layer with zero tests renders its callout as "Layer: <name>" + "No tests"
  // (Allure i18n), never a "Number of tests:" line — so match the empty phrase.
  const EMPTY_LAYER_RE = /no tests|нет тестов/i;

  /**
   * Layers whose callout says "No tests" — dropped from the funnel entirely so
   * only tested layers get a tier. The callout <text> concatenates its tspans
   * ("manualNo tests"), so read the whole node, not a single tspan.
   */
  function emptyLayersFromWidget(widget) {
    const empty = new Set();
    widget.querySelectorAll("text").forEach((node) => {
      const text = node.textContent || "";
      const match = text.match(/Layer:\s*(.+)/i);
      if (!match) return;
      const layer = normalizeLayer(match[1]);
      if (layer && EMPTY_LAYER_RE.test(text)) empty.add(layer);
    });
    return empty;
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

  /** Collapse or restore a band. Uses style so Allure's geometry stays intact. */
  function setShapeHidden(shape, hidden) {
    if (hidden) shape.style.setProperty("display", "none", "important");
    else shape.style.removeProperty("display");
  }

  /** Hide the callout <text> of empty layers, restore the rest. */
  function setEmptyLabelVisibility(widget, emptyLayers) {
    widget.querySelectorAll("text").forEach((node) => {
      const match = (node.textContent || "").match(/Layer:\s*(.+)/i);
      if (!match) return;
      const layer = normalizeLayer(match[1]);
      if (!layer) return;
      node.style.display = emptyLayers.has(layer) ? "none" : "";
    });
  }

  /**
   * Original (pre-reshape) bbox, cached on the node so re-paints stay idempotent
   * — reshaping mutates geometry, so we must always derive from the first read.
   */
  function originalBox(shape) {
    const raw = shape.getAttribute("data-orig-box");
    if (raw) {
      const parts = raw.split(",").map(Number);
      return { x: parts[0], y: parts[1], width: parts[2], height: parts[3] };
    }
    let box;
    try {
      box = shape.getBBox();
    } catch {
      return null;
    }
    if (!box || !box.width || !box.height) return null;
    shape.setAttribute("data-orig-box", [box.x, box.y, box.width, box.height].join(","));
    return box;
  }

  /** Rounded-rect path (arced corners) — used for path bands. */
  function roundedRectPath(left, top, right, bottom, r) {
    return (
      "M" + (left + r).toFixed(2) + "," + top.toFixed(2) +
      " L" + (right - r).toFixed(2) + "," + top.toFixed(2) +
      " Q" + right.toFixed(2) + "," + top.toFixed(2) + " " + right.toFixed(2) + "," + (top + r).toFixed(2) +
      " L" + right.toFixed(2) + "," + (bottom - r).toFixed(2) +
      " Q" + right.toFixed(2) + "," + bottom.toFixed(2) + " " + (right - r).toFixed(2) + "," + bottom.toFixed(2) +
      " L" + (left + r).toFixed(2) + "," + bottom.toFixed(2) +
      " Q" + left.toFixed(2) + "," + bottom.toFixed(2) + " " + left.toFixed(2) + "," + (bottom - r).toFixed(2) +
      " L" + left.toFixed(2) + "," + (top + r).toFixed(2) +
      " Q" + left.toFixed(2) + "," + top.toFixed(2) + " " + (left + r).toFixed(2) + "," + top.toFixed(2) +
      " Z"
    );
  }

  /** Rounded-rect sampled as a polygon `points` list — corners can't use arcs. */
  function roundedRectPoints(left, top, right, bottom, r) {
    const SEG = 5;
    const corners = [
      { cx: right - r, cy: top + r, from: -Math.PI / 2, to: 0 },
      { cx: right - r, cy: bottom - r, from: 0, to: Math.PI / 2 },
      { cx: left + r, cy: bottom - r, from: Math.PI / 2, to: Math.PI },
      { cx: left + r, cy: top + r, from: Math.PI, to: (3 * Math.PI) / 2 },
    ];
    const pts = [];
    corners.forEach((corner) => {
      for (let step = 0; step <= SEG; step++) {
        const angle = corner.from + (corner.to - corner.from) * (step / SEG);
        pts.push(
          (corner.cx + r * Math.cos(angle)).toFixed(2) + "," +
            (corner.cy + r * Math.sin(angle)).toFixed(2),
        );
      }
    });
    return pts.join(" ");
  }

  /**
   * Rewrite one band into a centered rounded stepped-tier (canon: rounded tiers).
   * All tiers share the SAME height — the vertical slot [slotTop, slotBottom] is
   * split equally across bands, so thickness never depends on test counts. Only
   * the width steps per layer (kept from the band's original box width).
   */
  function reshapeStepped(shape, axisX, slotTop, slotBottom, width) {
    const box = originalBox(shape);
    if (!box) return;
    const slotHeight = slotBottom - slotTop;
    // Thin inter-tier gap, matching the status donut's inter-segment gap.
    const gap = Math.max(1.5, slotHeight * 0.07);
    const top = slotTop + gap / 2;
    const bottom = slotBottom - gap / 2;
    const halfW = (width ?? box.width) / 2;
    const left = axisX - halfW;
    const right = axisX + halfW;
    const radius = Math.max(0, Math.min(halfW, (bottom - top) / 2, (bottom - top) * CORNER_RATIO));
    const tag = (shape.tagName || "").toLowerCase();
    if (tag === "polygon") {
      writeOwn(shape, "points", roundedRectPoints(left, top, right, bottom, radius));
    } else {
      writeOwn(shape, "d", roundedRectPath(left, top, right, bottom, radius));
    }
    shape.setAttribute("data-pyramid-shape", "rounded");
  }

  /** Shared vertical axis (center) from the union of original band boxes. */
  function pyramidAxisX(shapeEntries) {
    let minX = Infinity;
    let maxX = -Infinity;
    shapeEntries.forEach((entry) => {
      const box = originalBox(entry.shape);
      if (!box) return;
      minX = Math.min(minX, box.x);
      maxX = Math.max(maxX, box.x + box.width);
    });
    if (minX === Infinity) return null;
    return (minX + maxX) / 2;
  }

  /**
   * Clean funnel step width by tier rank (0 = top/narrowest … N-1 = bottom/widest).
   * Allure's own band widths saturate (the two largest layers render identically
   * wide), so derive an even ramp from the widest original band instead — the
   * canon "rounded tiers" is a decorative stepped pyramid; counts live in labels.
   */
  const MIN_WIDTH_FRACTION = 0.22;
  function tierWidth(index, count, maxWidth) {
    if (count <= 1) return maxWidth;
    const frac = MIN_WIDTH_FRACTION + (1 - MIN_WIDTH_FRACTION) * (index / (count - 1));
    return maxWidth * frac;
  }

  /** Widest original band width — the pyramid base used to scale the ramp. */
  function pyramidMaxWidth(shapeEntries) {
    let maxWidth = 0;
    shapeEntries.forEach((entry) => {
      const box = originalBox(entry.shape);
      if (box) maxWidth = Math.max(maxWidth, box.width);
    });
    return maxWidth;
  }

  /** Union vertical extent of the original band boxes (for equal-height slots). */
  function pyramidBoundsY(shapeEntries) {
    let minY = Infinity;
    let maxY = -Infinity;
    shapeEntries.forEach((entry) => {
      const box = originalBox(entry.shape);
      if (!box) return;
      minY = Math.min(minY, box.y);
      maxY = Math.max(maxY, box.y + box.height);
    });
    if (minY === Infinity) return null;
    return { minY, maxY };
  }

  /**
   * Equal-height reshape moves the bands but not Allure's labels, so each label
   * must follow its band to the new slot center. Allure renders every layer's
   * callout as a single <text> (3 tspans) positioned by its parent band <g>, so
   * `getBBox()` reports the SAME local Y for all labels — matching by geometry
   * is impossible. Instead match each label to its layer via the "Layer: <name>"
   * text and shift it by that layer's `newCenter - originalCenter`. The shift is
   * a pure translation, so it stays correct in the label's local group space.
   */
  function realignTexts(widget, layers, origCenters, bounds, slotHeight) {
    const deltaByLayer = new Map();
    layers.forEach((layer, index) => {
      const origCenter = origCenters[index];
      if (!layer || origCenter == null) return;
      const newCenter = bounds.minY + (index + 0.5) * slotHeight;
      deltaByLayer.set(layer, newCenter - origCenter);
    });
    if (!deltaByLayer.size) return;

    widget.querySelectorAll("text").forEach((node) => {
      const match = (node.textContent || "").match(/Layer:\s*(.+)/i);
      if (!match) return;
      const layer = normalizeLayer(match[1]);
      if (!layer || !deltaByLayer.has(layer)) return;

      if (node.getAttribute("data-orig-transform") == null) {
        node.setAttribute("data-orig-transform", node.getAttribute("transform") || "");
      }
      const base = node.getAttribute("data-orig-transform") || "";
      const delta = deltaByLayer.get(layer);
      writeOwn(node, "transform", (base + " translate(0," + delta.toFixed(2) + ")").trim());
    });
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
    const emptyLayers = emptyLayersFromWidget(widget);
    setEmptyLabelVisibility(widget, emptyLayers);

    // Drop empty ("No tests") layers: hide their band and exclude them from the
    // slot / width / label math so the tested layers redistribute into clean,
    // evenly-sized tiers instead of leaving degenerate slivers at the tip.
    const visible = [];
    shapeEntries.forEach((entry, index) => {
      const layer = layers[index];
      const hidden = layer != null && emptyLayers.has(layer);
      setShapeHidden(entry.shape, hidden);
      if (!hidden) visible.push({ entry, layer });
    });
    if (!visible.length) return true;

    const visibleEntries = visible.map((v) => v.entry);
    const visibleLayers = visible.map((v) => v.layer);

    const axisX = SHAPE_MODE === "steps" ? pyramidAxisX(visibleEntries) : null;
    const bounds = axisX != null ? pyramidBoundsY(visibleEntries) : null;
    const slotHeight = bounds ? (bounds.maxY - bounds.minY) / visibleEntries.length : 0;
    const maxWidth = bounds ? pyramidMaxWidth(visibleEntries) : 0;

    const origCenters = visibleEntries.map((entry) => {
      const box = originalBox(entry.shape);
      return box ? box.y + box.height / 2 : null;
    });

    visibleEntries.forEach((entry, index) => {
      const layer = visibleLayers[index];
      if (!layer) return;
      setShapeFill(entry.shape, layer);
      if (bounds) {
        const slotTop = bounds.minY + index * slotHeight;
        const width = tierWidth(index, visibleEntries.length, maxWidth);
        reshapeStepped(entry.shape, axisX, slotTop, slotTop + slotHeight, width);
      }
    });

    if (bounds) {
      realignTexts(widget, visibleLayers, origCenters, bounds, slotHeight);
    }

    return true;
  }

  /**
   * Drop the cached original geometry so the next paint re-derives it from the
   * band's CURRENT shape. Only safe when the bands still hold Allure's own
   * funnel geometry (i.e. Allure just re-rendered) — never call this while the
   * bands are our reshaped rounded tiers, or the pyramid would shrink each pass.
   */
  function clearShapeCaches(widget) {
    widget
      .querySelectorAll("[data-orig-box], [data-orig-transform]")
      .forEach((node) => {
        node.removeAttribute("data-orig-box");
        node.removeAttribute("data-orig-transform");
      });
  }

  // ---- Unified status indicators (widget header dots) ----
  // Every dashboard widget gets macOS-window dots in its header showing ONLY the
  // status-colour families actually present in that widget's chart, in a fixed
  // priority order. Colours are read "по факту" from rendered SVG fills/strokes
  // and matched to the nearest known palette anchor; noise (white, borders, text)
  // is rejected by a distance threshold.
  const INDICATOR_ORDER = ["red", "orange", "yellow", "purple", "gray", "green", "blue"];
  const INDICATOR_ANCHORS = {
    red: [[244, 63, 59], [255, 90, 80], [255, 100, 100], [153, 0, 24], [220, 38, 38], [244, 99, 134], [192, 57, 43], [255, 87, 68]],
    orange: [[255, 130, 0], [255, 140, 66], [255, 168, 51]],
    yellow: [[255, 216, 51], [255, 206, 87], [255, 224, 74], [201, 159, 0], [112, 93, 0], [255, 208, 80]],
    green: [[59, 201, 93], [148, 202, 102], [0, 111, 45], [0, 185, 151], [86, 214, 111], [137, 190, 62], [144, 187, 56], [163, 177, 37], [105, 167, 85]],
    blue: [[102, 186, 254], [84, 168, 237], [69, 155, 222], [97, 182, 251]],
    purple: [[161, 129, 255], [120, 85, 208], [216, 97, 190], [142, 68, 173]],
    gray: [[165, 183, 209], [170, 170, 170]],
  };
  const INDICATOR_THRESHOLD = 90;

  function parseRgbColor(value) {
    if (!value) return null;
    const match = String(value).match(/rgba?\(([^)]+)\)/i);
    if (!match) return null;
    const parts = match[1].split(",").map((piece) => parseFloat(piece));
    const alpha = parts.length > 3 ? parts[3] : 1;
    if (!(alpha > 0.3)) return null;
    if ([parts[0], parts[1], parts[2]].some((n) => Number.isNaN(n))) return null;
    return [parts[0], parts[1], parts[2]];
  }

  function classifyIndicator(rgb) {
    let best = null;
    let bestDist = Infinity;
    for (const family in INDICATOR_ANCHORS) {
      const anchors = INDICATOR_ANCHORS[family];
      for (let i = 0; i < anchors.length; i++) {
        const anchor = anchors[i];
        const dr = rgb[0] - anchor[0];
        const dg = rgb[1] - anchor[1];
        const db = rgb[2] - anchor[2];
        const dist = Math.sqrt(dr * dr + dg * dg + db * db);
        if (dist < bestDist) {
          bestDist = dist;
          best = family;
        }
      }
    }
    return bestDist <= INDICATOR_THRESHOLD ? best : null;
  }

  function widgetIndicatorFamilies(widget) {
    const present = Object.create(null);
    const shapes = widget.querySelectorAll(
      "svg path, svg polyline, svg polygon, svg rect, svg circle, svg ellipse, svg line",
    );
    shapes.forEach((node) => {
      const cs = getComputedStyle(node);
      [cs.fill, cs.stroke].forEach((raw) => {
        const rgb = parseRgbColor(raw);
        if (!rgb) return;
        const family = classifyIndicator(rgb);
        if (family) present[family] = true;
      });
    });
    return INDICATOR_ORDER.filter((family) => present[family]);
  }

  function widgetHeader(widget) {
    return (
      widget.querySelector('[class*="styles_header__"]') ||
      widget.firstElementChild ||
      widget
    );
  }

  // Idempotent: only mutates when a widget's family set changes, so the childList
  // observer that watches the whole document never enters a repaint loop.
  function paintWidgetIndicators(root) {
    const scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll('[class*="styles_widget__"]').forEach((widget) => {
      const header = widgetHeader(widget);
      if (!header) return;
      const families = widgetIndicatorFamilies(widget);
      const key = families.join(",");
      let dots = header.querySelector(":scope > .zds-widget-dots");
      if (!families.length) {
        if (dots) dots.remove();
        return;
      }
      if (dots && dots.getAttribute("data-zds-fams") === key) return;
      if (!dots) {
        dots = document.createElement("span");
        dots.className = "zds-widget-dots";
        header.insertBefore(dots, header.firstChild);
      }
      dots.setAttribute("data-zds-fams", key);
      dots.textContent = "";
      families.forEach((family) => {
        const dot = document.createElement("span");
        dot.className = "zds-widget-dot zds-widget-dot--" + family;
        dots.appendChild(dot);
      });
    });
  }

  // ---- Coverage diff treemap: brighten the neutral "unchanged" blue ----
  // Allure paints the coverage-diff map on a red -> info-blue -> green scale
  // (colorValue 0..1; 0.5 = "unchanged"). That neutral middle resolves to
  // var(--color-intent-info-bg) from :root at render time — a muted sky tone
  // that reads washed-out next to the all-green success-rate treemap. Repaint
  // only the neutral cells a brighter sky-blue so the coverage-changes map is
  // unmistakably blue; red / green diff cells are left untouched. Idempotent:
  // the target blue is far enough from the neutral anchor that a re-run skips
  // already-painted cells, and an Allure re-render (resize/theme) restores the
  // neutral fill which we then re-brighten.
  const COVERAGE_DIFF_RE = /coverage diff|карта изменений покрытия/i;
  const COVERAGE_BLUE = { light: "#2f8ff0", dark: "#4aa8ff" };
  const COVERAGE_NEUTRAL_DIST = 42;

  function colorStringToRgb(value) {
    if (!value) return null;
    const parsed = parseRgbColor(value);
    if (parsed) return parsed;
    let hex = String(value).trim().replace(/^#/, "");
    if (hex.length === 8) hex = hex.slice(0, 6);
    if (hex.length === 3) hex = hex.split("").map((c) => c + c).join("");
    if (!/^[0-9a-f]{6}$/i.test(hex)) return null;
    return [
      parseInt(hex.slice(0, 2), 16),
      parseInt(hex.slice(2, 4), 16),
      parseInt(hex.slice(4, 6), 16),
    ];
  }

  function coverageNeutralRgb() {
    return colorStringToRgb(
      getComputedStyle(document.documentElement)
        .getPropertyValue("--color-intent-info-bg")
        .trim(),
    );
  }

  function paintCoverageDiff(root = document) {
    const scope = root && root.querySelectorAll ? root : document;
    const widget = [...scope.querySelectorAll('[class*="styles_widget"]')].find(
      (el) => COVERAGE_DIFF_RE.test(el.textContent || ""),
    );
    const svg = widget && widget.querySelector("svg");
    if (!svg) return;
    const neutral = coverageNeutralRgb();
    if (!neutral) return;
    const target = COVERAGE_BLUE[currentTheme()] || COVERAGE_BLUE.light;
    svg.querySelectorAll("rect, path, polygon").forEach((node) => {
      const rgb = parseRgbColor(getComputedStyle(node).fill);
      if (!rgb) return;
      const dist = Math.hypot(rgb[0] - neutral[0], rgb[1] - neutral[1], rgb[2] - neutral[2]);
      if (dist > COVERAGE_NEUTRAL_DIST) return;
      node.setAttribute("fill", target);
      node.style.setProperty("fill", target, "important");
    });
  }

  function paint(fromScratch) {
    if (fromScratch) {
      const widget = findPyramidWidget(document);
      if (widget) clearShapeCaches(widget);
    }
    paintPyramid();
    paintCoverageDiff();
    paintWidgetIndicators();
    ensureGeomObserver();
  }

  /**
   * Watch the funnel band geometry itself. On rescale Allure re-lays-out the
   * pyramid by rewriting `d` / `points` in place — which the childList/theme
   * observers miss, so the original (pre-reshape) funnel leaks back through.
   * A FOREIGN geometry change (value ≠ what we last wrote) means Allure
   * re-rendered, so repaint from scratch: the bands currently hold Allure's
   * fresh geometry, which is exactly what reshapeStepped must measure. Our own
   * reshape writes are recognized by value and ignored, with no timing race.
   */
  let geomObserver = null;
  let observedSvg = null;
  function ensureGeomObserver() {
    const widget = findPyramidWidget(document);
    const svg = widget && widget.querySelector("svg");
    if (!svg || svg === observedSvg) return;
    if (geomObserver) geomObserver.disconnect();
    observedSvg = svg;
    geomObserver = new MutationObserver((records) => {
      if (records.some(isForeignMutation)) queueScratchPaint();
    });
    geomObserver.observe(svg, {
      attributes: true,
      attributeFilter: ["d", "points", "transform", "width", "height", "viewBox"],
      subtree: true,
    });
  }

  /**
   * True when the pyramid bands currently hold geometry we did NOT write — i.e.
   * Allure re-rendered its funnel and our rounded tiers are gone. Used by the
   * resize net to decide between a scratch repaint (re-measure Allure's fresh
   * funnel) and a no-op (our tiers are still on screen, so re-deriving from
   * them would shrink the pyramid each pass).
   */
  function pyramidHoldsForeignGeometry() {
    const widget = findPyramidWidget(document);
    const svg = widget && widget.querySelector("svg");
    if (!svg) return false;
    return pyramidShapes(svg).some((entry) => {
      const shape = entry.shape;
      // Empty layers we collapsed keep Allure's raw geometry by design — ignore
      // them, or the resize net would scratch-repaint (and shrink) every pass.
      if (shape.style.display === "none") return false;
      const attr = (shape.tagName || "").toLowerCase() === "polygon" ? "points" : "d";
      const map = ownWrites.get(shape);
      if (!map || !(attr in map)) return true;
      return map[attr] !== shape.getAttribute(attr);
    });
  }

  let scratchQueued = false;
  function queueScratchPaint() {
    if (scratchQueued) return;
    scratchQueued = true;
    requestAnimationFrame(() => {
      scratchQueued = false;
      paint(true);
    });
  }

  function schedulePaint() {
    paint(false);
    window.setTimeout(() => paint(false), 200);
    window.setTimeout(() => paint(false), 800);
    window.setTimeout(() => paint(false), 2000);
  }

  /**
   * Belt-and-suspenders for resolution changes: some Allure re-renders debounce
   * or replace the SVG in ways the observers can miss. After the resize settles,
   * scratch-repaint only if Allure's raw funnel is back — otherwise re-apply the
   * cached tiers idempotently.
   */
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      if (pyramidHoldsForeignGeometry()) queueScratchPaint();
      else queuePaint();
    }, 150);
  });

  let paintQueued = false;
  function queuePaint() {
    if (paintQueued) return;
    paintQueued = true;
    requestAnimationFrame(() => {
      paintQueued = false;
      paint(false);
    });
  }

  const observer = new MutationObserver(queuePaint);
  observer.observe(document.documentElement, { childList: true, subtree: true });

  // Theme swap only recolors — reuse cached geometry (never from scratch, or
  // repeated toggles would shrink the tiers).
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
