const PAGES = [
  { id: "home", label: "Overview", path: "#/" },
  { id: "sessions", label: "Sessions", path: "#/sessions" },
  { id: "crystals", label: "Crystals", path: "#/crystals" },
  { id: "routes", label: "Routes", path: "#/routes" },
  { id: "providers", label: "Providers", path: "#/providers" },
  { id: "tests", label: "Tests", path: "#/tests" },
];

const STAGES = ["report", "watch", "draft", "shadow", "extract", "register", "route", "smoke", "promote", "promoted", "rejected"];
const SINCE_OPTIONS = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "90d", label: "90d" },
  { value: "365d", label: "1y" },
];
const SINCE_STORAGE_KEY = "gt-hub-since";

function getSince() {
  const saved = localStorage.getItem(SINCE_STORAGE_KEY);
  if (saved && SINCE_OPTIONS.some((o) => o.value === saved)) return saved;
  return "7d";
}

function setSince(value) {
  if (!SINCE_OPTIONS.some((o) => o.value === value)) return;
  localStorage.setItem(SINCE_STORAGE_KEY, value);
}

function fmt(n) {
  n = Number(n) || 0;
  if (n >= 1_000_000) return `~${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `~${Math.round(n / 1000)}K`;
  if (n >= 1000) return `~${(n / 1000).toFixed(1)}K`;
  return n.toLocaleString();
}

async function api(path) {
  const res = await fetch(path);
  return res.json();
}

function renderNav() {
  const hash = location.hash || "#/";
  const since = getSince();
  document.getElementById("nav").innerHTML =
    PAGES.map(
      (p) => `<a href="${p.path}" class="${hash === p.path ? "active" : ""}">${p.label}</a>`
    ).join("") +
    `<label class="since-picker" title="Usage window">
      <span class="muted">since</span>
      <select id="since-select" aria-label="Usage window">
        ${SINCE_OPTIONS.map(
          (o) => `<option value="${o.value}" ${o.value === since ? "selected" : ""}>${o.label}</option>`
        ).join("")}
      </select>
    </label>`;
  const sel = document.getElementById("since-select");
  if (sel) {
    sel.addEventListener("change", () => {
      setSince(sel.value);
      route().catch(console.error);
    });
  }
}

async function renderTestWidget() {
  const t = await api("/api/tests");
  document.getElementById("test-widget").innerHTML =
    `pytest: <strong>${t.test_files}</strong> files · TestOps <a class="link" href="${t.dashboard_url}" target="_blank">5276</a>`;
}

async function renderHome() {
  const data = await api(`/api/summary?since=${getSince()}`);
  const tiers = data.by_tier || {};
  const totalSaved = data.totals?.saved_vs_cursor || 0;
  const coverage = data.coverage_pct ?? 0;

  const tierColors = {
    tool: "var(--tier-tool)",
    python: "var(--tier-python)",
    ollama: "var(--tier-ollama)",
    rag: "var(--tier-rag)",
    cursor: "var(--tier-cursor)",
  };

  const tierRows = Object.entries(tiers).map(([tier, s]) =>
    `<tr><td>${tier}</td><td>${s.count}</td><td>${fmt(s.saved_vs_cursor)}</td><td>${fmt(s.est_tokens)}</td></tr>`
  ).join("");

  const totalCalls = Object.values(tiers).reduce((a, s) => a + s.count, 0);
  const bar = Object.entries(tiers).map(([tier, s]) => {
    const pct = totalCalls ? (100 * s.count / totalCalls) : 0;
    return `<span style="width:${pct}%;background:${tierColors[tier] || "#666"}" title="${tier}"></span>`;
  }).join("");

  const q = data.quality || {};
  const m = data.metrics || {};
  const pct = (v) => `${Math.round((v ?? 0) * 100)}%`;
  const hasQuality = (q.script_hits || 0) > 0;
  const worst = (q.by_crystal || []).filter((c) => c.override_count > 0).slice(0, 5);
  const worstRows = worst.map((c) =>
    `<tr><td><code>${c.crystal_id}</code></td><td>${pct(c.override_rate)}</td>` +
    `<td>${c.override_count}/${c.script_hits}</td>` +
    `<td>${c.reuse_action ? `<span style="color:var(--warn)">${c.reuse_action}</span>` : "—"}</td></tr>`
  ).join("");
  const overThreshold = hasQuality && (q.override_rate_7d ?? 0) >= (q.disable_threshold ?? 0.3);
  const lat = m.latency || {};
  const latencyValue = lat.p50_ms != null ? `${lat.p50_ms}ms` : "—";
  const costValue = `$${(m.cost_per_task_usd ?? 0).toFixed(3)}`;

  document.getElementById("app").innerHTML = `
    <div class="grid">
      <div class="card" title="estimate vs naive agent-chat baseline (source: ${data.baseline?.source || "default-estimate"})"><h3>Saved vs agent chat (${data.baseline?.source || "default-estimate"})</h3><div class="value">${fmt(totalSaved)}</div></div>
      <div class="card" title="share of events routed to cheap tiers"><h3>Coverage</h3><div class="value">${coverage}%</div></div>
      <div class="card" title="cheap hits kept across all cheap tiers (not re-asked in Cursor)">
        <h3>Cheap hold rate</h3>
        <div class="value">${hasQuality ? pct(q.cheap_hold_rate) : "—"}</div></div>
      <div class="card" title="script_override events / cheap-tier hits (>= threshold: disable/re-shadow)">
        <h3>Override rate</h3>
        <div class="value" style="${overThreshold ? "color:var(--tier-cursor)" : ""}">${hasQuality ? pct(q.override_rate_7d) : "—"}</div></div>
      <div class="card" title="median route execution latency (${lat.samples || 0} samples with duration)">
        <h3>Latency p50</h3>
        <div class="value">${latencyValue}</div></div>
      <div class="card" title="Cursor-estimate spend spread over ${totalCalls} calls in window">
        <h3>Cost / task</h3>
        <div class="value">${costValue}</div></div>
    </div>
    <div class="card">
      <h3>Route quality <span style="font-weight:400;opacity:.6">— not ML accuracy</span></h3>
      <table><thead><tr><th>Worst crystal</th><th>Override rate</th><th>Overrides/hits</th><th>Action</th></tr></thead>
      <tbody>${worstRows || `<tr><td colspan=4 class=empty>${hasQuality ? "No overrides in window — routing holding" : "No cheap-tier hits yet"}</td></tr>`}</tbody></table>
    </div>
    <div class="card">
      <h3>Tier breakdown</h3>
      <div class="tier-bar">${bar}</div>
      <table><thead><tr><th>Tier</th><th>Calls</th><th>Saved</th><th>Spent</th></tr></thead>
      <tbody>${tierRows || "<tr><td colspan=4 class=empty>No events yet</td></tr>"}</tbody></table>
    </div>`;
}

async function renderSessions() {
  const data = await api(`/api/sessions?since=${getSince()}`);
  const rows = (data.sessions || []).map((s) =>
    `<tr><td><code>${s.session_id.slice(0, 12)}…</code></td><td>${s.since}</td><td>${s.calls}</td><td>${fmt(s.saved_vs_cursor)}</td><td>${fmt(s.est_tokens)}</td></tr>`
  ).join("");
  document.getElementById("app").innerHTML = `
    <div class="card">
      <h3>Cursor sessions</h3>
      <table><thead><tr><th>Session</th><th>Since</th><th>Calls</th><th>Saved</th><th>Spent</th></tr></thead>
      <tbody>${rows || "<tr><td colspan=5 class=empty>No sessions — use greedy MCP in Cursor first</td></tr>"}</tbody></table>
    </div>`;
}

async function renderCrystals() {
  const data = await api(`/api/crystals?since=${getSince()}`);
  const crystals = data.crystals || [];
  const rows = crystals.map((c) =>
    `<tr><td><a class="link" href="#/crystals/${encodeURIComponent(c.crystal_id)}">${c.crystal_id}</a></td>
    <td>${c.pattern.slice(0, 60)}</td><td>${c.hits}</td><td>${c.latest_stage || "—"}</td><td>${c.status || "—"}</td></tr>`
  ).join("");
  document.getElementById("app").innerHTML = `
    <div class="grid">
      <div class="card"><h3>Coverage</h3><div class="value">${data.coverage_pct ?? 0}%</div></div>
      <div class="card"><h3>Crystals</h3><div class="value">${crystals.length}</div></div>
    </div>
    <div class="card">
      <h3>Crystallize candidates</h3>
      <table><thead><tr><th>ID</th><th>Pattern</th><th>Hits</th><th>Stage</th><th>Status</th></tr></thead>
      <tbody>${rows || "<tr><td colspan=5 class=empty>No candidates — run greedy-token route with repeated LLM tasks</td></tr>"}</tbody></table>
    </div>`;
}

async function renderCrystalDetail(id) {
  const data = await api(`/api/crystals/${encodeURIComponent(id)}?since=${getSince()}`);
  const latest = data.latest_stage;
  const latestIdx = STAGES.indexOf(latest);
  const pipeline = STAGES.map((s, i) => {
    const cls = data.stages?.[s] ? "done" : (s === latest ? "current" : "");
    return `<span class="stage ${cls}">${s}</span>`;
  }).join("");

  const events = (data.events || []).map((e) =>
    `<tr><td>${e.stage}</td><td>${e.ts}</td><td>${e.status || "—"}</td><td>${e.pr_url || e.jira_key || "—"}</td></tr>`
  ).join("");

  document.getElementById("app").innerHTML = `
    <p><a class="link" href="#/crystals">← Crystals</a></p>
    <div class="card">
      <h3>${id}</h3>
      <p>Saved (route match): <strong>${fmt(data.saved_vs_cursor || 0)}</strong></p>
      <div class="stage-pipeline">${pipeline}</div>
      <table><thead><tr><th>Stage</th><th>Time</th><th>Status</th><th>Link</th></tr></thead>
      <tbody>${events || "<tr><td colspan=4 class=empty>No lifecycle events yet</td></tr>"}</tbody></table>
    </div>`;
}

async function renderRoutes() {
  const data = await api(`/api/routes?since=${getSince()}`);
  const rows = (data.routes || []).slice(0, 30).map((r) =>
    `<tr><td><code>${r.route_id}</code></td><td>${r.count}</td><td>${fmt(r.saved_vs_cursor)}</td><td>${fmt(r.est_tokens)}</td></tr>`
  ).join("");
  document.getElementById("app").innerHTML = `
    <div class="card">
      <h3>Top routes by savings</h3>
      <table><thead><tr><th>Route</th><th>Calls</th><th>Saved</th><th>Spent</th></tr></thead>
      <tbody>${rows || "<tr><td colspan=4 class=empty>No routes yet</td></tr>"}</tbody></table>
    </div>`;
}

async function renderTests() {
  const t = await api("/api/tests");
  document.getElementById("app").innerHTML = `
    <div class="grid">
      <div class="card"><h3>Test files</h3><div class="value">${t.test_files}</div></div>
      <div class="card"><h3>TestOps project</h3><div class="value" style="font-size:1rem">${t.testops_project_id}</div></div>
    </div>
    <div class="card">
      <h3>pytest + Allure dashboard</h3>
      <p><a class="link" href="${t.dashboard_url}" target="_blank">${t.dashboard_url}</a></p>
      <p class="muted">${t.source}</p>
    </div>`;
}

async function renderProviders() {
  const app = document.getElementById("app");
  app.classList.add("app--wide");
  app.innerHTML = `<div id="provider-catalog-root"></div>`;
  if (window.ProviderCatalog) {
    await window.ProviderCatalog.mount(document.getElementById("provider-catalog-root"));
  }
}

function resetAppLayout() {
  document.getElementById("app").classList.remove("app--wide");
}

async function route() {
  renderNav();
  const hash = location.hash || "#/";
  resetAppLayout();
  const crystalMatch = hash.match(/^#\/crystals\/(.+)$/);
  if (crystalMatch) {
    await renderCrystalDetail(decodeURIComponent(crystalMatch[1]));
  } else if (hash.startsWith("#/sessions")) {
    await renderSessions();
  } else if (hash.startsWith("#/crystals")) {
    await renderCrystals();
  } else if (hash.startsWith("#/routes")) {
    await renderRoutes();
  } else if (hash.startsWith("#/providers")) {
    await renderProviders();
  } else if (hash.startsWith("#/tests")) {
    await renderTests();
  } else {
    await renderHome();
  }
  const health = await api("/api/health");
  document.getElementById("foot-meta").textContent =
    `since ${getSince()} · log ${health.log_path} · ${new Date().toLocaleString()}`;
}

window.addEventListener("hashchange", () => route().catch(console.error));
route().catch(console.error);
renderTestWidget().catch(console.error);
