from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import json
import os
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import allure

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "benchmark.html"
D1_SHA256 = "a9fb7b1e7757e925f6d71823501c76f86ac437c6dd025c906d4caf4cfcffc3e0"
CORPUS_SHA256 = "c82de2ed4ebf8189e9f53fb667ba56104b60a97e210d49019f262daba6d85f37"
pytestmark = [pytest.mark.component, allure.epic("Evidence benchmark"), allure.feature("Benchmark presentation")]

NODE_RENDER = r"""
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const elements = new Map();
const attrs = text => Object.fromEntries([...text.matchAll(/([\w-]+)="([^"]*)"/g)].map(m => [m[1], m[2]]));
function element(id, attributes = {}) {
  if (!elements.has(id)) elements.set(id, {
    value: '', textContent: '', dataset: {}, attributes: {}, markup: '',
    get innerHTML() { return this.markup; },
    set innerHTML(value) {
      this.markup = value;
      for (const match of value.matchAll(/<input\b([^>]*)>/g)) {
        const a = attrs(match[1]);
        if (a.id) element(a.id, a);
      }
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name]; },
    addEventListener() {},
  });
  const e = elements.get(id);
  Object.assign(e.attributes, attributes);
  if (attributes.value !== undefined) e.value = attributes.value;
  for (const [key, value] of Object.entries(attributes)) {
    if (key.startsWith('data-')) e.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
  }
  return e;
}
for (const match of input.html.matchAll(/<[^>]+\bid="[^"]+"[^>]*>/g)) {
  const a = attrs(match[0]); element(a.id, a);
}
const scripts = [...input.html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
for (const match of scripts) {
  const a = attrs(match[1]); if (a.id) element(a.id, a).textContent = match[2];
}
const context = vm.createContext({
  document: {getElementById: id => element(id)},
  console, Response, Blob, DecompressionStream, Uint8Array, TextDecoder,
  crypto: webcrypto, atob, SYNTHETIC: input.fixture,
  fetch: () => { throw Error('Network is forbidden in render fixtures'); },
});
(async () => {
  for (const match of scripts) {
    const a = attrs(match[1]);
    if (!a.type || ['text/javascript', 'application/javascript'].includes(a.type)) vm.runInContext(match[2], context);
  }
  await vm.runInContext('typeof benchmarkReady === "undefined" ? Promise.resolve() : benchmarkReady', context);
  const result = await vm.runInContext(input.expression, context);
  process.stdout.write(JSON.stringify(result));
})().catch(error => { console.error(error.message); process.exitCode = 1; });
"""

SYNTHETIC_COMPLETION = {
    "provenance": "synthetic-render-fixture-only-not-new-measured-data",
    "case_id": "rag-retrieval-en",
    "method": "greedy_cli",
    "evidence_level": "measured",
    "applicable": True,
    "success": True,
    "completion_eligible": True,
    "completion_evidence": {
        "category": "rag_excerpt",
        "satisfied": True,
        "matched_windows": [["routing", "accuracy", "and", "task", "success", "are"]],
    },
    "zero_completed": True,
    "observation": {
        "scope": "python_provider",
        "run_id": "SYNTHETIC_ONLY",
        "coverage": {"status": "complete", "complete": True, "reasons": []},
        "invocation": {
            "model_attempts": {"value": 0, "status": "observed", "scope": "invocation"},
            "llm_requests_sent": {"value": 0, "status": "observed", "scope": "invocation"},
        },
        "ledger": {"run_id": "SYNTHETIC_ONLY", "events": ["SYNTHETIC_ONLY"]},
    },
}


def node_eval(expression: str, fixture: dict | None = None, *, html_override: str | None = None) -> object:
    proc = subprocess.run(
        ["node", "-e", NODE_RENDER],
        input=json.dumps({"html": PAGE.read_text() if html_override is None else html_override, "expression": expression, "fixture": fixture}),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def embedded_source() -> dict:
    match = re.search(r'<script\b([^>]*\bid="d1-evidence"[^>]*)>(.*?)</script>', PAGE.read_text(), re.S)
    assert match, "A /tmp path alone is not a durable evidence artifact"
    raw = gzip.decompress(base64.b64decode(re.sub(r"\s+", "", match[2]), validate=True))
    digest = re.search(r'data-sha256="([a-f0-9]{64})"', match[1])
    assert digest and hashlib.sha256(raw).hexdigest() == digest[1]
    return json.loads(raw)


class VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag in ("script", "style"):
            self.hidden_depth += 1

    def handle_endtag(self, tag) -> None:
        if tag in ("script", "style"):
            self.hidden_depth -= 1

    def handle_data(self, data) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def test_unknown_and_zero_are_distinct() -> None:
    assert node_eval('[num(null), num(undefined), num(NaN), fmt$(null), fmt$(NaN), fmt$(0)]') == [
        "unknown", "unknown", "unknown", "unknown", "unknown", "$0.0000",
    ]


@pytest.mark.parametrize("value, expected", [(0.0123, "$0.0123"), (-0.0246, "-$0.0246"), (-0.0004, "-$0.0004")])
def test_signed_usd_is_not_clipped(value: float, expected: str) -> None:
    assert node_eval(f"fmt$({value})") == expected


def test_unknown_is_excluded_without_fabricating_a_total() -> None:
    assert node_eval("knownSum([10, -3, null])") == {
        "value": None, "known_subtotal": 7, "known": 2, "unknown": 1,
    }
    assert node_eval("knownSum([null, undefined])") == {
        "value": None, "known_subtotal": None, "known": 0, "unknown": 2,
    }


def test_positive_and_negative_potential_stay_synthetic() -> None:
    positive = node_eval("potentialResult(100, 30, 2)")
    negative = node_eval("potentialResult(100, 130, 2)")
    assert positive == {
        "evidence_level": "synthetic_potential", "measurement_status": "not_applicable",
        "tokens_saved": 70, "cost_saved_usd": 0.00014,
    }
    assert negative["tokens_saved"] == -30
    assert negative["cost_saved_usd"] == -0.00006
    assert negative["measurement_status"] != "observed"
    assert node_eval("potentialResult(null, 30, 2).tokens_saved") is None


@pytest.mark.parametrize("category", ["routing_only", "contract_only", "fallback_only", "unknown"])
def test_routing_and_contract_success_are_not_task_completion(category: str) -> None:
    fixture = copy.deepcopy(SYNTHETIC_COMPLETION)
    fixture["completion_evidence"]["category"] = category
    assert node_eval("[taskCompletion(SYNTHETIC), zeroCompleted(SYNTHETIC)]", fixture) == [False, False]


def test_partial_ledger_is_not_whole_invocation_zero() -> None:
    fixture = copy.deepcopy(SYNTHETIC_COMPLETION)
    assert node_eval("[taskCompletion(SYNTHETIC), zeroCompleted(SYNTHETIC)]", fixture) == [True, True]
    fixture["observation"]["coverage"] = {"status": "incomplete", "complete": False, "reasons": ["native_launch_outside_coverage"]}
    fixture["observation"]["invocation"]["model_attempts"] = {"status": "unknown", "value": None}
    assert node_eval("[taskCompletion(SYNTHETIC), zeroCompleted(SYNTHETIC)]", fixture) == [True, False]
    fixture["observation"]["coverage"] = {"status": "complete", "complete": True, "reasons": []}
    fixture["observation"]["invocation"]["model_attempts"] = {"status": "observed", "value": 1}
    assert node_eval("zeroCompleted(SYNTHETIC)", fixture) is False


def test_historical_and_desktop_claims_are_explicit_unknown() -> None:
    parser = VisibleText()
    parser.feed(PAGE.read_text())
    visible = " ".join(" ".join(parser.parts).split()).lower()
    assert "гарантированная экономия — intercept" not in visible
    assert "measured baseline ~17,043" not in visible
    assert "таблица 3 — измеренный факт" not in visible
    assert "desktop" in visible and "skipped turn" in visible and "unknown" in visible
    live = node_eval("document.getElementById('live-summary').innerHTML")
    assert "unknown" in live and "0" not in live
    baseline = node_eval("document.getElementById('baseline-summary').innerHTML")
    assert "unknown" in baseline and "not_applicable" in baseline


def test_stored_d1_counts_do_not_use_task_success_summary_as_completion() -> None:
    source = embedded_source()
    assert source["source"]["sha256"] == D1_SHA256
    assert source["benchmark"]["corpus_lock"]["sha256"] == CORPUS_SHA256
    assert source["gates"]["greedy_executor_success"] is False
    assert source["gates"]["all_passed"] is False
    assert node_eval("summarizeProduct(evidence)") == {
        "runs": 24, "tasks": 12, "eligible": 8, "terminal_completed": 6,
        "zero_completed": 2, "oracle_pass": 18, "oracle_fail": 6,
        "complete": 5, "incomplete": 19, "unobserved": 0, "native_denied": 33,
        "python_model_attempts": 0, "python_sent": 0, "invocation_unknown": 19,
    }
    assert node_eval("evidence.route_classification.filter(r => r.ok).length") == 12
    assert node_eval("document.getElementById('gates').innerHTML").count('data-status="FAIL"') == 2
    assert "unknown" in node_eval("document.getElementById('money-summary').innerHTML")


def test_every_observed_row_preserves_source_oracle_and_raw_ledger() -> None:
    source = embedded_source()
    corpus_bytes = (ROOT / "bench" / "evidence_corpus.v1.yaml").read_bytes()
    assert hashlib.sha256(corpus_bytes).hexdigest() == CORPUS_SHA256
    corpus = yaml.safe_load(corpus_bytes)
    assert source["corpus"] == corpus
    cases = {case["id"]: case for case in corpus["cases"]}
    files = corpus["fixture"]["files"]
    chunks = {row["id"]: row["path"] for row in map(json.loads, files["docs/rag/manifest.jsonl"].splitlines())}
    rows = [r for r in source["observations"] if r["method"] in ("greedy_cli", "greedy_mcp_stdio")]
    assert len(rows) == 24 and {r["case_id"] for r in rows} == set(cases)
    zero_ids = {(r["case_id"], r["method"]) for r in rows if r["zero_completed"]}
    assert zero_ids == {("rag-retrieval-en", "greedy_cli"), ("rag-retrieval-ru", "greedy_cli")}
    observations = [r["observation"] for r in rows] + [source["route_observation"]]
    for observation in observations:
        ledger = observation["ledger"]
        assert ledger and ledger["run_id"] == observation["run_id"]
        assert ledger["expected"]["input_sha256"]
        assert ledger["file_sha256"]
        raw = json.dumps(ledger["events"], sort_keys=True, ensure_ascii=False).encode()
        assert hashlib.sha256(raw).hexdigest() == ledger["events_sha256"]
        assert any(e["kind"] == "bootstrap" and e["channel"] == "independent_jsonl" for e in ledger["events"])
        assert any(e["kind"] == "run_begin" for e in ledger["events"])
        assert any(e["kind"] == "run_end" for e in ledger["events"])
    for row in rows:
        if not row["completion_evidence"]["satisfied"]:
            continue
        oracle = cases[row["case_id"]]["oracle"]
        if "expected_chunk_ids" in oracle and row["completion_evidence"]["category"] == "rag_excerpt":
            assert all(chunk in row["output_excerpt"] for chunk in oracle["expected_chunk_ids"])
            expected = " ".join(re.findall(r"\w+", " ".join(files[chunks[chunk]] for chunk in oracle["expected_chunk_ids"]).casefold()))
            actual = " ".join(re.findall(r"\w+", row["output_excerpt"].casefold()))
            assert any(
                len(window) >= 6 and " ".join(window) in expected and " ".join(window) in actual
                for window in row["completion_evidence"]["matched_windows"]
            )
        elif "expected_lines" in oracle:
            for line in oracle["expected_lines"]:
                assert f"{line['path']}:{line['line']}:" in row["output_excerpt"]
                assert line["contains"] in row["output_excerpt"]


def test_self_contained_ledger_replay_does_not_run_a_workload() -> None:
    source = embedded_source()
    observations = [row["observation"] for row in source["observations"] if row["method"] in ("greedy_cli", "greedy_mcp_stdio")]
    observations.append(source["route_observation"])
    with (
        patch("subprocess.Popen", side_effect=AssertionError("No product or agent spawn")),
        patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No network or provider")),
        patch("socket.socket", side_effect=AssertionError("No fixture server")),
    ):
        from bench import evidence_benchmark as benchmark

        for observation in observations:
            replay = benchmark._replay_observation(observation)
            for key in ("coverage", "python_scope", "invocation", "events_sha256"):
                assert replay[key] == observation[key]


@pytest.mark.parametrize("value, expected", [(0.0123, "$0.0123"), (-0.0246, "-$0.0246")])
def test_signed_known_billing_subtotal_does_not_hide_unknown_total(value: float, expected: str) -> None:
    fixture = copy.deepcopy(embedded_source())
    fixture["source"]["path"] = "SYNTHETIC_RENDER_FIXTURE_NOT_MEASURED"
    first = next(row for row in fixture["observations"] if row["method"] == "greedy_cli")
    first["actual_cost_usd"] = {
        "value": value, "status": "observed", "authoritative": True,
        "source": "SYNTHETIC_RENDER_BILLING_ONLY",
    }
    rendered = node_eval("renderProduct(SYNTHETIC); document.getElementById('money-summary').innerHTML", fixture)
    assert "USD: unknown" in rendered
    assert f"Known billing subtotal: {expected}" in rendered
    assert "unknown: 23/24" in rendered


def test_billing_requires_numeric_authoritative_source_not_potential() -> None:
    assert node_eval("""(() => {
      const known = {value:0,status:'observed',authoritative:true,source:'SYNTHETIC_ONLY'};
      return [moneyValue(known), moneyValue({...known,status:'unknown'}),
        moneyValue({...known,status:'potential'}), moneyValue({...known,value:null}),
        moneyValue({...known,value:'0'}), moneyValue({...known,authoritative:false}),
        moneyValue({...known,source:' '})];
    })()""") == [0, None, None, None, None, None, None]


@pytest.mark.parametrize("tamper", ["missing_source", "invalid_gzip", "mismatched_hash"])
def test_invalid_snapshot_fails_closed_without_archival_number_recovery(tamper: str) -> None:
    html = PAGE.read_text()
    match = re.search(r'<script\b([^>]*\bid="d1-evidence"[^>]*)>(.*?)</script>', html, re.S)
    assert match
    if tamper == "missing_source":
        replacement = match[0].replace(match[2], "")
    elif tamper == "invalid_gzip":
        replacement = match[0].replace(match[2], re.sub(r"\S", "!", match[2], count=1))
    else:
        replacement = re.sub(r'data-sha256="[a-f0-9]{64}"', 'data-sha256="' + "0" * 64 + '"', match[0])
    changed = html[:match.start()] + replacement + html[match.end():]
    assert node_eval(
        "[evidence, $('product-status').dataset.status, $('stats').innerHTML, $('product-summary').textContent]",
        html_override=changed,
    ) == [None, "unknown", "", ""]


def test_render_escapes_evidence_labels() -> None:
    assert node_eval("escapeHtml('<script> & \"fixture\"')") == "&lt;script&gt; &amp; &quot;fixture&quot;"


def test_local_browser_render() -> None:
    url = os.environ.get("BENCHMARK_URL")
    if not url and os.environ.get("BENCHMARK_UI") == "1":
        url = PAGE.as_uri()
    if not url:
        pytest.skip("Set BENCHMARK_UI=1 for the self-contained local file, or BENCHMARK_URL from ensure.py")
    module = ROOT.parents[1] / "selenoid-home" / "selenoid-ui" / "ui" / "node_modules" / "playwright"
    assert module.is_dir(), "Use existing Playwright; do not install dependencies"
    script = r"""
const assert = require('node:assert/strict');
const {chromium} = require(process.argv[1]);
const url = process.argv[2], target = new URL(url);
assert.ok(target.protocol==='file:' || ['localhost', '127.0.0.1'].includes(target.hostname));
const errors = [], external = [], checks = [];
async function isolated(browser, options = {}) {
  const context = await browser.newContext({serviceWorkers:'block',...options});
  await context.addInitScript(()=>{
    window.__clipboardWrites=[];
    Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.__clipboardWrites.push(text)}});
  });
  await context.route('**/*',route=>{
    const request = new URL(route.request().url());
    const allowed = target.protocol==='file:' ? request.href===target.href : request.origin===target.origin;
    if (!allowed) {external.push('Unexpected resource request');return route.abort();}
    return route.continue();
  });
  context.on('page',page=>{
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error') errors.push(m.text());});
  });
  return context;
}
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    for (const width of [1280, 768, 390, 320]) {
      const context = await isolated(browser,{viewport:{width,height:1000},colorScheme:width===320?'dark':'light'});
      const page = await context.newPage();
      const response = await page.goto(url);
      assert.equal(response.status(),200);
      await page.waitForFunction(() => document.getElementById('product-status').dataset.status === 'observed');
      assert.equal(await page.locator('#tbody tr').count(),24);
      assert.equal(await page.locator('[data-metric="zero_completed"] .v').textContent(),'2 / 24');
      assert.equal(await page.locator('[data-metric="terminal_completed"] .v').textContent(),'6 / 24');
      assert.equal(await page.locator('#gates tr[data-status="FAIL"]').count(),2);
      assert.equal(await page.locator('#stats [data-source][data-scope][data-oracle][data-coverage]').count(),7);
      assert.ok(await page.locator('#live-summary').textContent().then(t=>t.includes('unknown')));
      assert.ok(await page.locator('#host-summary').textContent().then(t=>t.includes('unknown')));
      await page.locator('#potential-naive').fill('100');
      await page.locator('#potential-direct').fill('130');
      await page.locator('#potential-rate').fill('2');
      assert.ok((await page.locator('#potential-summary').textContent()).includes('-30'));
      assert.ok((await page.locator('#potential-summary').textContent()).includes('-$0.00006'));
      await page.locator('#potential-naive').fill('');
      assert.ok((await page.locator('#potential-summary').textContent()).includes('unknown'));
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      assert.equal(await page.evaluate(()=>localStorage.length),0);
      assert.equal(await page.evaluate(()=>window.__clipboardWrites.length),0);
      checks.push({width,status:'PASS'});
      await context.close();
    }
    const context = await isolated(browser);
    await context.addInitScript(()=>Object.defineProperty(window,'DecompressionStream',{value:undefined}));
    const page = await context.newPage();
    await page.goto(url);
    await page.waitForFunction(()=>document.getElementById('product-status').dataset.status==='unknown');
    assert.equal(await page.locator('#stats [data-status="observed"]').count(),0);
    checks.push({source_unavailable:'PASS'});
    await context.close();
    assert.deepEqual(errors,[]); assert.deepEqual(external,[]);
    process.stdout.write(JSON.stringify({checks,console_page_errors:errors.length,external_requests:external.length,isolated_storage:true,mock_clipboard:true,source:target.protocol==='file:'?'self_contained_local_file':'ensure_stand'}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
"""
    proc = subprocess.run(
        ["node", "-e", script, str(module), url], capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["console_page_errors"] == report["external_requests"] == 0
    print(json.dumps(report, ensure_ascii=False))
