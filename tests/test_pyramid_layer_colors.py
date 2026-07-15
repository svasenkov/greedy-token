"""Regression: Allure testing pyramid must keep Palette A per-layer colors."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import allure
import pytest

from tests.allure_reporting import attach_json, attach_text

ROOT = Path(__file__).resolve().parents[1]
COLORS_MJS = ROOT / "allure" / "pyramid-layer-colors.mjs"
OVERRIDES_JS = ROOT / ".github" / "assets" / "dashboard-overrides.js"
OVERRIDES_CSS = ROOT / ".github" / "assets" / "dashboard-overrides.css"
PREPARE_SH = ROOT / ".github" / "scripts" / "prepare-allure-pages-colors.sh"

pytestmark = [
    allure.epic("Test infrastructure"),
    allure.parent_suite("Test infrastructure"),
    allure.feature("Pyramid layer colors"),
    allure.suite("Pyramid layer colors"),
]


def _node_eval(expr: str) -> str:
    script = f"""
import {{
  PYRAMID_LAYERS,
  PYRAMID_COLORS,
  PYRAMID_FUNNEL_TOP_TO_BOTTOM,
  pairShapesToLayers,
  assertPaletteUnique,
  cssVarForLayer,
  colorForLayer,
}} from {COLORS_MJS.as_uri()!r};

const result = (() => {{ {expr} }})();
process.stdout.write(typeof result === "string" ? result : JSON.stringify(result));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


@allure.story("Palette A uniqueness")
@allure.title("Palette A has unique colors per layer in light and dark themes")
def test_palette_a_unique_per_layer() -> None:
    with allure.step("Assert unique hex values for light and dark"):
        raw = _node_eval(
            """
            assertPaletteUnique("light");
            assertPaletteUnique("dark");
            return {
              light: PYRAMID_LAYERS.map((l) => [l, PYRAMID_COLORS.light[l]]),
              dark: PYRAMID_LAYERS.map((l) => [l, PYRAMID_COLORS.dark[l]]),
            };
            """
        )
        attach_json("palette A", json.loads(raw))


@allure.story("Name-based mapping")
@allure.title("Layer→shape pairing does not depend on which layers have tests")
def test_pair_shapes_by_layer_name_not_data_count() -> None:
    with allure.step("Pair with sparse labels (api/manual empty) in shuffled Y order"):
        raw = _node_eval(
            """
            // Only four bands have geometry (empty layers omitted) — old FALLBACK.slice(-n) mis-colored.
            const shapes = [{ y: 10 }, { y: 40 }, { y: 70 }, { y: 100 }];
            const labels = [
              { layer: "e2e", y: 12 },
              { layer: "integration", y: 38 },
              { layer: "component", y: 72 },
              { layer: "unit", y: 98 },
            ];
            return pairShapesToLayers(shapes, labels);
            """
        )
        paired = json.loads(raw)
        attach_json("paired layers", paired)
    with allure.step("Verify stable name mapping"):
        assert paired == ["e2e", "integration", "component", "unit"]


@allure.story("Name-based mapping")
@allure.title("Nearest-Y pairing survives mismatched label/shape counts")
def test_pair_shapes_nearest_y_when_counts_differ() -> None:
    with allure.step("More labels than shapes — still map by proximity"):
        raw = _node_eval(
            """
            const shapes = [{ y: 50 }, { y: 150 }];
            const labels = [
              { layer: "manual", y: 0 },
              { layer: "e2e", y: 45 },
              { layer: "api", y: 90 },
              { layer: "unit", y: 160 },
            ];
            return pairShapesToLayers(shapes, labels);
            """
        )
        paired = json.loads(raw)
        attach_json("nearest-y pairs", paired)
    with allure.step("Verify nearest layers"):
        assert paired == ["e2e", "unit"]


@allure.story("CSS vars")
@allure.title("dashboard-overrides.css defines --layer-* for light and dark")
def test_overrides_css_defines_layer_vars() -> None:
    css = OVERRIDES_CSS.read_text(encoding="utf-8")
    attach_text("dashboard-overrides.css head", css[:500])
    with allure.step("Require Palette A vars in :root and dark theme"):
        for layer in ("unit", "component", "integration", "api", "e2e", "manual"):
            assert f"--layer-{layer}:" in css
        assert 'html[data-theme="dark"]' in css
        assert 'html[data-theme="light"]' in css


@allure.story("Override script")
@allure.title("dashboard-overrides.js paints SVG with resolved colors, not raw CSS vars")
def test_overrides_js_uses_css_vars_and_name_pairing() -> None:
    js = OVERRIDES_JS.read_text(encoding="utf-8")
    attach_text("overrides js markers", "checked")
    with allure.step("Reject regressive patterns"):
        assert "--layer-${layer}" in js
        assert "getComputedStyle(document.documentElement)" in js
        assert "const PALETTE" in js
        assert "data-pyramid-layer" in js
        assert "FALLBACK_LAYER_ORDER.slice" not in js
        assert "setAttribute(\"fill\", cssColor)" not in js
        assert "setProperty(\"fill\", cssColor" not in js
        # Must not assign Allure's single primary fill as the paint source.
        assert 'fill", "var(--color-intent-primary-bg)' not in js
        assert "setProperty(\"fill\", \"var(--color-intent-primary-bg)" not in js
    with allure.step("Robust label parsing (digits + concatenated tspans)"):
        # Naive [a-z]+ turned "manualNo tests" -> "manualno" and "e2e" -> "e",
        # producing fill="undefined" (black). normalizeLayer must map by prefix.
        assert "function normalizeLayer" in js
        assert r"([a-z]+)" not in js
        # Unknown layers must never reach setAttribute (guard returns null).
        assert "if (!color) return;" in js
        assert "if (!layer || !PALETTE.light[layer]) return null;" in js


@allure.story("CI inject")
@allure.title("prepare-allure-pages-colors injects relative overrides into report HTML")
def test_inject_pyramid_colors_into_report_html() -> None:
    with allure.step("Build minimal pages + report fixture and inject"):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pages = tmp_path / "pages"
            report = pages / "reports" / "latest"
            dashboard = report / "dashboard"
            awesome = report / "awesome"
            dashboard.mkdir(parents=True)
            awesome.mkdir(parents=True)
            stub = "<!DOCTYPE html><html><head><title>t</title></head><body></body></html>\n"
            (dashboard / "index.html").write_text(stub, encoding="utf-8")
            (awesome / "index.html").write_text(stub, encoding="utf-8")

            proc = subprocess.run(
                ["bash", str(PREPARE_SH), str(pages), str(report)],
                check=True,
                capture_output=True,
                text=True,
            )
            attach_text("prepare stdout", proc.stdout)

            dash_html = (dashboard / "index.html").read_text(encoding="utf-8")
            awesome_html = (awesome / "index.html").read_text(encoding="utf-8")
            attach_text("dashboard head", dash_html)

            assert (pages / "dashboard-overrides.css").is_file()
            assert (pages / "dashboard-overrides.js").is_file()
            assert 'href="../../../dashboard-overrides.css"' in dash_html
            assert 'src="../../../dashboard-overrides.js"' in dash_html
            assert 'href="../../../dashboard-overrides.css"' in awesome_html
            assert "data-dashboard-overrides" in dash_html


@allure.story("SSOT sync")
@allure.title("Overrides JS palette hex matches allure/pyramid-layer-colors.mjs")
def test_overrides_js_hex_matches_ssot_module() -> None:
    with allure.step("Extract SSOT hex map"):
        raw = _node_eval("return PYRAMID_COLORS;")
        colors = json.loads(raw)
        js = OVERRIDES_JS.read_text(encoding="utf-8")
        css = OVERRIDES_CSS.read_text(encoding="utf-8")
    with allure.step("Every SSOT hex appears in CSS (theme vars)"):
        missing = []
        for theme, palette in colors.items():
            for layer, hex_color in palette.items():
                if hex_color.lower() not in css.lower():
                    missing.append(f"{theme}/{layer}={hex_color}")
        attach_json("missing from css", missing)
        assert not missing, missing
    with allure.step("Override script keeps CSS var + hex fallback remapping"):
        assert "--layer-${layer}" in js
        assert "PALETTE" in js
        assert "pairShapesToLayers" in js
