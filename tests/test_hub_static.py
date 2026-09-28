"""Regression: telemetry must never reach innerHTML unescaped (stored XSS).

The audit injected `<img src=x onerror=...>` through a task/route field —
every template interpolation below used to land raw in innerHTML.
"""

from __future__ import annotations

from pathlib import Path

import allure

pytestmark = [
    allure.epic("Hub"),
    allure.parent_suite("Hub"),
    allure.feature("Static dashboard"),
    allure.suite("Hub static assets"),
]

_STATIC = Path(__file__).resolve().parents[1] / "src" / "greedy_token" / "hub" / "static"

# Interpolations that used to be raw `${telemetry}` inside innerHTML and are
# proven XSS sinks. They must stay wrapped in esc()/safeUrl()/Number().
_RAW_INTERPOLATIONS = (
    "${c.crystal_id}",
    "${c.reuse_action}",
    "${c.pattern",
    "${(c.pattern",
    "${s.session_id",
    "${s.duration_ms",
    "${s.commands",
    "${s.tier}",
    "${(s.tier",
    "${s.format",
    "${(s.format",
    "${w.model_id",
    "${(w.model_id",
    "${w.pattern",
    "${(w.pattern",
    "${w.savings_exclusion",
    "${e.stage}",
    "${e.ts}",
    "${e.status",
    "${e.conf}",
    "${(e.conf",
    "${e.pr_url",
    "${e.task",
    "${e.rule}",
    "${(e.rule",
    "${e.cost_usd",
    "${e.duration_ms",
    "${e.tool}",
    "${e.outcome}",
    "${e.notes}",
    "${e.doctype}",
    "${r.route_id}",
    "${r.task",
    "${(r.task",
    "${r.duration_ms",
    "${r.cost_usd",
    "${r.best_model",
    "${routeId}",
    "${item.request_id",
    "${t.dashboard_url",
    "${t.stub_id}",
    "${t.status}",
    "${t.category}",
    "${t.url}",
    "${(t.url",
    "${m.label}",
    "${data.since",
    "${data.baseline",
    "${tier} tier",
    "${stem}",
    "${f}",
)


@allure.title("app.js escapes telemetry before it reaches innerHTML")
def test_app_js_escapes_telemetry() -> None:
    src = (_STATIC / "app.js").read_text(encoding="utf-8")
    assert "function esc(" in src
    assert "function safeUrl(" in src
    for raw in _RAW_INTERPOLATIONS:
        assert raw not in src, f"raw telemetry interpolation still present: {raw!r}"
    # url attributes go through the scheme allowlist, not through esc() alone
    assert "safeUrl(t.dashboard_url)" in src
    assert 'href="${safeUrl(' in src


@allure.title("index.html ships a restrictive CSP")
def test_index_html_csp() -> None:
    src = (_STATIC / "index.html").read_text(encoding="utf-8")
    assert 'http-equiv="Content-Security-Policy"' in src
    assert "script-src 'self'" in src
    assert "default-src 'self'" in src
    assert "frame-ancestors 'none'" in src


@allure.title("all hub static files exist where the server mounts them")
def test_static_assets_present() -> None:
    for name in ("app.js", "index.html", "app.css", "provider-catalog.js", "provider-catalog.css"):
        assert (_STATIC / name).is_file(), name
