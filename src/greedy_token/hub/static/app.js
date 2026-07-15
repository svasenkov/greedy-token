const PAGES = [
  { id: "home", label: "Overview", path: "#/" },
  { id: "sessions", label: "Sessions", path: "#/sessions" },
  { id: "crystals", label: "Crystals", path: "#/crystals" },
  { id: "routes", label: "Routes", path: "#/routes" },
  { id: "providers", label: "Providers", path: "#/providers" },
  { id: "tests", label: "Tests", path: "#/tests" },
];

const STAGES = ["report", "watch", "extract", "register", "route", "smoke", "promote"];
const SINCE = "7d";

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
  document.getElementById("nav").innerHTML = PAGES.map(
    (p) => `<a href="${p.path}" class="${hash === p.path ? "active" : ""}">${p.label}</a>`
  ).join("");
}

async function renderTestWidget() {
  const t = await api("/api/tests");
  document.getElementById("test-widget").innerHTML =
    `pytest: <strong>${t.test_files}</strong> files · TestOps <a class="link" href="${t.dashboard_url}" target="_blank">5276</a>`;
}

async function renderHome() {
  const data = await api(`/api/summary?since=${SINCE}`);
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

  document.getElementById("app").innerHTML = `
    <div class="grid">
      <div class="card"><h3>Saved vs Cursor</h3><div class="value">${fmt(totalSaved)}</div></div>
      <div class="card"><h3>Script coverage</h3><div class="value">${coverage}%</div></div>
      <div class="card"><h3>Events</h3><div class="value">${data.events || 0}</div></div>
      <div class="card"><h3>Budget mode</h3><div class="value" style="font-size:1rem">${data.budget?.mode || "—"}</div></div>
    </div>
    <div class="card">
      <h3>Tier breakdown</h3>
      <div class="tier-bar">${bar}</div>
      <table><thead><tr><th>Tier</th><th>Calls</th><th>Saved</th><th>Spent</th></tr></thead>
      <tbody>${tierRows || "<tr><td colspan=4 class=empty>No events yet</td></tr>"}</tbody></table>
    </div>`;
}

async function renderSessions() {
  const data = await api(`/api/sessions?since=${SINCE}`);
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
  const data = await api(`/api/crystals?since=${SINCE}`);
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
  const data = await api(`/api/crystals/${encodeURIComponent(id)}?since=${SINCE}`);
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
  const data = await api(`/api/routes?since=${SINCE}`);
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
    `since ${SINCE} · log ${health.log_path} · ${new Date().toLocaleString()}`;
}

window.addEventListener("hashchange", () => route().catch(console.error));
route().catch(console.error);
renderTestWidget().catch(console.error);
