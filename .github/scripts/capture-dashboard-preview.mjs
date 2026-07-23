import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

// Trailing slash required: Allure sets <base> from pathname; `…/index.html` breaks
// relative assets when served locally (`serve` strips index.html → wrong base).
const baseUrl =
  process.env.PREVIEW_URL ??
  "http://127.0.0.1:8765/reports/latest/dashboard/";
const outputDir = process.env.PREVIEW_OUTPUT_DIR ?? "pages/readme";
const viewportWidth = Number(process.env.PREVIEW_WIDTH ?? "1280");
const suffix = process.env.PREVIEW_SUFFIX ?? "";

const fileBase = suffix ? `dashboard-preview-${suffix}` : "dashboard-preview";

const variants = [
  { theme: "light", file: `${fileBase}.png` },
  { theme: "dark", file: `${fileBase}-dark.png` },
];

await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({ headless: true });

try {
  for (const { theme, file } of variants) {
    const page = await browser.newPage({
      viewport: { width: viewportWidth, height: 900 },
      deviceScaleFactor: 1.5,
    });

    await page.addInitScript((selectedTheme) => {
      localStorage.setItem("theme", selectedTheme);
    }, theme);

    await page.goto(baseUrl, { waitUntil: "networkidle", timeout: 90_000 });

    const layout = page.locator('[data-testid="base-layout"]');
    await layout.waitFor({ timeout: 30_000 });
    // Wait until dashboard-overrides.js finishes pyramid reshape + label realign
    // (SETTLE_MS debounce after nivo animation). Avoids README shots with labels
    // stuck at Allure's pre-reshape positions.
    await page.waitForFunction(
      () => {
        const widget = [...document.querySelectorAll('[class*="styles_widget"]')].find(
          (el) => /testing pyramid|пирамида тестирования/i.test(el.textContent || ""),
        );
        if (!widget) return false;
        const svg = widget.querySelector("svg");
        if (!svg) return false;
        const bands = [...svg.querySelectorAll("path, polygon")].filter((shape) => {
          if (shape.style.display === "none") return false;
          const d = shape.getAttribute("d") || "";
          const points = shape.getAttribute("points") || "";
          return d.length > 16 || points.length > 8;
        });
        return bands.length > 0 && bands.every((s) => s.getAttribute("data-pyramid-shape") === "rounded");
      },
      { timeout: 30_000 },
    );
    await page.waitForTimeout(300);

    const output = join(outputDir, file);
    await layout.screenshot({ path: output, type: "png" });
    console.log(`Saved ${theme} dashboard preview to ${output}`);

    await page.close();
  }
} finally {
  await browser.close();
}
