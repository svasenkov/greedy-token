window.ProviderCatalog = (function () {
  "use strict";

  const HOME_REGION = "RU";
  const PROFILE_KEYS = [
    "classify",
    "generate",
    "audit",
    "architecture",
    "prod_default",
    "local_default",
  ];

  const state = {
    providers: [],
    models: [],
    activeTab: "providers",
    charts: {},
    sourcePath: "",
    asOf: "",
    syncCommand: "",
  };

  let rootEl = null;

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function api(path) {
    const res = await fetch(path);
    const payload = await res.json();
    if (!res.ok) {
      throw new Error(payload.error || `HTTP ${res.status}`);
    }
    return payload;
  }

  function template() {
    return `
      <div class="provider-catalog" data-testid="provider-catalog">
        <p class="provider-catalog__intro">
          SSOT: <code>projects/infra-home/raw/providers/</code> · schema
          <code>scripts/infra/catalog-schema.md</code>
        </p>
        <div class="provider-catalog__meta">
          <span class="provider-catalog__pill">Providers <strong id="pc-provider-total">—</strong></span>
          <span class="provider-catalog__pill">Local models <strong id="pc-model-total">—</strong></span>
          <span class="provider-catalog__pill">Home <strong>${HOME_REGION}</strong></span>
          <span class="provider-catalog__pill" id="pc-as-of">as of —</span>
          <span class="provider-catalog__pill" id="pc-source-path"></span>
          <button type="button" class="provider-catalog__reload" id="pc-reload" title="Reload from JSONL">Reload</button>
        </div>
        <div class="provider-catalog__tabs" role="tablist" aria-label="Catalog views">
          <button type="button" class="provider-catalog__tab" id="pc-tab-providers" aria-selected="true">Providers</button>
          <button type="button" class="provider-catalog__tab" id="pc-tab-models" aria-selected="false">Local models</button>
        </div>
        <section id="pc-panel-providers" class="provider-catalog__panel">
          <form class="provider-catalog__toolbar" id="pc-provider-filters" autocomplete="off">
            <label class="provider-catalog__field">
              <span class="provider-catalog__label">Search</span>
              <input type="search" id="pc-filter-search" placeholder="provider, product, id, tags…">
            </label>
            <label class="provider-catalog__field">
              <span class="provider-catalog__label">Category</span>
              <select id="pc-filter-category"><option value="">All categories</option></select>
            </label>
            <label class="provider-catalog__field provider-catalog__field--check">
              <input type="checkbox" id="pc-filter-free-tier">
              <span class="provider-catalog__label">Free tier</span>
            </label>
            <label class="provider-catalog__field provider-catalog__field--check">
              <input type="checkbox" id="pc-filter-trial">
              <span class="provider-catalog__label">Trial</span>
            </label>
            <label class="provider-catalog__field provider-catalog__field--check">
              <input type="checkbox" id="pc-filter-no-vpn">
              <span class="provider-catalog__label">No VPN (RU)</span>
            </label>
            <label class="provider-catalog__field provider-catalog__field--check">
              <input type="checkbox" id="pc-filter-fz152">
              <span class="provider-catalog__label">152-FZ</span>
            </label>
            <div class="provider-catalog__field">
              <button type="button" class="provider-catalog__tab" id="pc-filter-reset">Reset</button>
            </div>
          </form>
          <div class="provider-catalog__summary">
            <span id="pc-provider-summary">Loading…</span>
            <span>VPN: RU ∉ <code>requires_vpn_in</code></span>
          </div>
          <div class="provider-catalog__charts">
            <article class="chart-tile"><h3 class="chart-tile__title">By category</h3><div class="chart-tile__body" id="pc-chart-category"></div></article>
            <article class="chart-tile"><h3 class="chart-tile__title">RU access</h3><div class="chart-tile__body" id="pc-chart-ru-access"></div></article>
            <article class="chart-tile"><h3 class="chart-tile__title">Pricing flags</h3><div class="chart-tile__body" id="pc-chart-pricing"></div></article>
            <article class="chart-tile"><h3 class="chart-tile__title">152-FZ</h3><div class="chart-tile__body" id="pc-chart-fz"></div></article>
          </div>
          <div class="provider-catalog__table-wrap">
            <table id="pc-provider-table">
              <thead>
                <tr>
                  <th>Provider / product</th><th>Category</th><th>Pricing</th><th>Geo</th><th>Compliance</th><th>Flags</th><th>Verified</th>
                </tr>
              </thead>
              <tbody id="pc-provider-table-body"></tbody>
            </table>
            <p class="provider-catalog__empty" id="pc-provider-empty" hidden>No providers match filters.</p>
          </div>
        </section>
        <section id="pc-panel-models" class="provider-catalog__panel" hidden>
          <form class="provider-catalog__toolbar" id="pc-model-filters" autocomplete="off">
            <label class="provider-catalog__field">
              <span class="provider-catalog__label">Search</span>
              <input type="search" id="pc-model-search" placeholder="id, family, quant…">
            </label>
            <label class="provider-catalog__field">
              <span class="provider-catalog__label">Family</span>
              <select id="pc-model-family"><option value="">All families</option></select>
            </label>
            <label class="provider-catalog__field provider-catalog__field--check">
              <input type="checkbox" id="pc-model-prod-default">
              <span class="provider-catalog__label">prod_default</span>
            </label>
            <label class="provider-catalog__field provider-catalog__field--check">
              <input type="checkbox" id="pc-model-local-default">
              <span class="provider-catalog__label">local_default</span>
            </label>
            <label class="provider-catalog__field provider-catalog__field--check">
              <input type="checkbox" id="pc-model-hide-deprecated">
              <span class="provider-catalog__label">Hide deprecated</span>
            </label>
            <div class="provider-catalog__field">
              <button type="button" class="provider-catalog__tab" id="pc-model-reset">Reset</button>
            </div>
          </form>
          <div class="provider-catalog__summary"><span id="pc-model-summary">Loading…</span></div>
          <div class="provider-catalog__charts">
            <article class="chart-tile"><h3 class="chart-tile__title">By family</h3><div class="chart-tile__body" id="pc-chart-model-family"></div></article>
            <article class="chart-tile"><h3 class="chart-tile__title">Recommended profiles</h3><div class="chart-tile__body" id="pc-chart-model-profiles"></div></article>
            <article class="chart-tile"><h3 class="chart-tile__title">VRAM buckets</h3><div class="chart-tile__body" id="pc-chart-model-vram"></div></article>
            <article class="chart-tile"><h3 class="chart-tile__title">Deprecated</h3><div class="chart-tile__body" id="pc-chart-model-deprecated"></div></article>
          </div>
          <div class="provider-catalog__table-wrap">
            <table id="pc-model-table">
              <thead>
                <tr>
                  <th>Model</th><th>Family</th><th>Params</th><th>VRAM / RAM</th><th>Recommended</th><th>Status</th><th>Verified</th>
                </tr>
              </thead>
              <tbody id="pc-model-table-body"></tbody>
            </table>
            <p class="provider-catalog__empty" id="pc-model-empty" hidden>No models match filters.</p>
          </div>
        </section>
      </div>`;
  }

  function $(id) {
    return rootEl.querySelector(id);
  }

  function chartTheme() {
    const styles = getComputedStyle(document.documentElement);
    return {
      text: styles.getPropertyValue("--text").trim() || "#e7ecf3",
      muted: styles.getPropertyValue("--muted").trim() || "#8b9cb3",
      border: styles.getPropertyValue("--border").trim() || "#2a3548",
      palette: ["#5b9bd5", "#3dd68c", "#c77dff", "#f5a623", "#ff6b6b", "#67e8f9", "#94a3b8"],
    };
  }

  function baseChartOptions() {
    const theme = chartTheme();
    return {
      chart: { backgroundColor: "transparent", style: { fontFamily: "inherit" }, spacing: [8, 8, 8, 8] },
      title: { text: null },
      credits: { enabled: false },
      legend: { itemStyle: { color: theme.text } },
      colors: theme.palette,
      xAxis: {
        labels: { style: { color: theme.muted, fontSize: "11px" } },
        lineColor: theme.border,
        tickColor: theme.border,
      },
      yAxis: {
        title: { text: null },
        labels: { style: { color: theme.muted, fontSize: "11px" } },
        gridLineColor: theme.border,
      },
      tooltip: {
        backgroundColor: "#1a2332",
        borderColor: theme.border,
        style: { color: theme.text },
      },
    };
  }

  function destroyCharts(prefix) {
    Object.keys(state.charts).forEach((id) => {
      if (!id.startsWith(prefix)) return;
      state.charts[id].destroy();
      delete state.charts[id];
    });
  }

  function renderChart(id, options) {
    if (typeof Highcharts === "undefined") return;
    if (state.charts[id]) state.charts[id].destroy();
    state.charts[id] = Highcharts.chart(id, { ...baseChartOptions(), ...options });
  }

  function formatPricing(pricing) {
    if (!pricing) return "—";
    const parts = [];
    if (pricing.free_tier) parts.push("free tier");
    if (pricing.trial) parts.push(`trial: ${pricing.trial}`);
    if (pricing.amount != null && pricing.currency) {
      parts.push(`${pricing.amount} ${pricing.currency}/${pricing.unit || "unit"}`);
    } else if (pricing.input != null && pricing.output != null && pricing.currency) {
      parts.push(`in ${pricing.input} / out ${pricing.output} ${pricing.currency}`);
      if (pricing.unit) parts.push(pricing.unit);
    } else if (pricing.notes) {
      parts.push(pricing.notes);
    }
    return parts.length ? parts.join(" · ") : "—";
  }

  function countBy(items, selector) {
    const counts = new Map();
    items.forEach((item) => {
      const key = selector(item);
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([name, y]) => ({ name, y }));
  }

  function providerMatches(row) {
    const query = $("#pc-filter-search").value.trim().toLowerCase();
    if (query) {
      const haystack = [row.id, row.provider, row.product, row.category, (row.tags || []).join(" "), (row.compliance || []).join(" ")]
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    const category = $("#pc-filter-category").value;
    if (category && row.category !== category) return false;
    if ($("#pc-filter-free-tier").checked && !row.pricing?.free_tier) return false;
    if ($("#pc-filter-trial").checked && !row.pricing?.trial) return false;
    if ($("#pc-filter-no-vpn").checked && (row.requires_vpn_in || []).includes(HOME_REGION)) return false;
    if ($("#pc-filter-fz152").checked && !(row.compliance || []).includes("152-FZ")) return false;
    return true;
  }

  function ruAccessBucket(row) {
    if ((row.blocked_in || []).includes(HOME_REGION)) return "Blocked in RU";
    if ((row.requires_vpn_in || []).includes(HOME_REGION)) return "VPN required";
    return "No VPN";
  }

  function renderProviderCharts(rows) {
    destroyCharts("pc-chart-");
    const categorySeries = countBy(rows, (row) => row.category);
    renderChart("pc-chart-category", {
      chart: { type: categorySeries.length > 8 ? "bar" : "pie" },
      plotOptions: { pie: { innerSize: "55%" } },
      series: [{ name: "Providers", data: categorySeries }],
    });
    renderChart("pc-chart-ru-access", {
      chart: { type: "pie" },
      plotOptions: { pie: { innerSize: "55%" } },
      series: [{ name: "RU access", data: countBy(rows, ruAccessBucket) }],
    });
    const pricingSeries = [
      { name: "Free tier", y: rows.filter((row) => row.pricing?.free_tier).length },
      { name: "Trial", y: rows.filter((row) => row.pricing?.trial).length },
      { name: "Paid only", y: rows.filter((row) => !row.pricing?.free_tier && !row.pricing?.trial).length },
    ].filter((point) => point.y > 0);
    renderChart("pc-chart-pricing", {
      chart: { type: "column" },
      xAxis: { categories: pricingSeries.map((point) => point.name) },
      series: [{ name: "Count", data: pricingSeries.map((point) => point.y), colorByPoint: true }],
      legend: { enabled: false },
    });
    const fzYes = rows.filter((row) => (row.compliance || []).includes("152-FZ")).length;
    renderChart("pc-chart-fz", {
      chart: { type: "pie" },
      plotOptions: { pie: { innerSize: "55%" } },
      series: [{ name: "Compliance", data: [{ name: "152-FZ", y: fzYes }, { name: "Other", y: Math.max(rows.length - fzYes, 0) }].filter((p) => p.y > 0) }],
    });
  }

  function renderProviderTable(rows) {
    $("#pc-provider-summary").textContent = `Showing ${rows.length} / ${state.providers.length} providers`;
    const hasRows = rows.length > 0;
    $("#pc-provider-table").hidden = !hasRows;
    $("#pc-provider-empty").hidden = hasRows;
    $("#pc-provider-table-body").innerHTML = rows.map((row) => {
      const flags = [];
      if (row.pricing?.free_tier) flags.push('<span class="provider-catalog__badge provider-catalog__badge--ok">free</span>');
      if (row.pricing?.trial) flags.push('<span class="provider-catalog__badge provider-catalog__badge--warn">trial</span>');
      if ((row.requires_vpn_in || []).includes(HOME_REGION)) flags.push('<span class="provider-catalog__badge provider-catalog__badge--warn">vpn</span>');
      if ((row.blocked_in || []).includes(HOME_REGION)) flags.push('<span class="provider-catalog__badge provider-catalog__badge--danger">blocked</span>');
      if ((row.compliance || []).includes("152-FZ")) flags.push('<span class="provider-catalog__badge provider-catalog__badge--ok">152-FZ</span>');
      const compliance = (row.compliance || []).length
        ? (row.compliance || []).map((tag) => `<span class="provider-catalog__badge provider-catalog__badge--muted">${escapeHtml(tag)}</span>`).join(" ")
        : '<span class="provider-catalog__badge provider-catalog__badge--muted">—</span>';
      const geoBadge = (row.blocked_in || []).includes(HOME_REGION)
        ? "danger"
        : (row.requires_vpn_in || []).includes(HOME_REGION) ? "warn" : "ok";
      const geoLabel = (row.blocked_in || []).includes(HOME_REGION) ? "blocked in RU" : ((row.requires_vpn_in || []).includes(HOME_REGION) ? "VPN" : "native");
      return `<tr>
        <td><strong>${escapeHtml(row.provider)}</strong><br>${escapeHtml(row.product)}<br><code>${escapeHtml(row.id)}</code><br><a class="link" href="${escapeHtml(row.source_url)}" target="_blank" rel="noopener noreferrer">source</a></td>
        <td><code>${escapeHtml(row.category)}</code></td>
        <td>${escapeHtml(formatPricing(row.pricing))}</td>
        <td>${escapeHtml((row.availability || []).join(", ") || "—")}<br><span class="provider-catalog__badge provider-catalog__badge--${geoBadge}">${geoLabel}</span></td>
        <td><div class="provider-catalog__flags">${compliance}</div></td>
        <td><div class="provider-catalog__flags">${flags.join(" ") || '<span class="provider-catalog__badge provider-catalog__badge--muted">—</span>'}</div></td>
        <td>${escapeHtml(row.verified_at || "—")}</td>
      </tr>`;
    }).join("");
  }

  function renderProviders() {
    const rows = state.providers.filter(providerMatches);
    renderProviderCharts(rows);
    renderProviderTable(rows);
  }

  function modelMatches(row) {
    const query = $("#pc-model-search").value.trim().toLowerCase();
    if (query) {
      const haystack = [row.id, row.family, row.quant, row.replacement || ""].join(" ").toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    if ($("#pc-model-family").value && row.family !== $("#pc-model-family").value) return false;
    if ($("#pc-model-prod-default").checked && !row.recommended?.prod_default) return false;
    if ($("#pc-model-local-default").checked && !row.recommended?.local_default) return false;
    if ($("#pc-model-hide-deprecated").checked && row.deprecated) return false;
    return true;
  }

  function vramBucket(gb) {
    if (gb <= 8) return "≤8 GB";
    if (gb <= 16) return "9–16 GB";
    if (gb <= 24) return "17–24 GB";
    return "25+ GB";
  }

  function renderModelCharts(rows) {
    destroyCharts("pc-chart-model");
    const familySeries = countBy(rows, (row) => row.family);
    renderChart("pc-chart-model-family", {
      chart: { type: familySeries.length > 8 ? "bar" : "pie" },
      plotOptions: { pie: { innerSize: "55%" } },
      series: [{ name: "Models", data: familySeries }],
    });
    const profileSeries = PROFILE_KEYS.map((key) => ({
      name: key,
      y: rows.filter((row) => row.recommended?.[key]).length,
    })).filter((point) => point.y > 0);
    renderChart("pc-chart-model-profiles", {
      chart: { type: "bar" },
      xAxis: { categories: profileSeries.map((point) => point.name) },
      series: [{ name: "Models", data: profileSeries.map((point) => point.y) }],
      legend: { enabled: false },
    });
    const vramSeries = countBy(rows, (row) => vramBucket(row.min_vram_gb || 0));
    renderChart("pc-chart-model-vram", {
      chart: { type: "column" },
      xAxis: { categories: vramSeries.map((point) => point.name) },
      series: [{ name: "Models", data: vramSeries.map((point) => point.y), colorByPoint: true }],
      legend: { enabled: false },
    });
    const deprecatedYes = rows.filter((row) => row.deprecated).length;
    renderChart("pc-chart-model-deprecated", {
      chart: { type: "pie" },
      plotOptions: { pie: { innerSize: "55%" } },
      series: [{ name: "Status", data: [{ name: "Active", y: Math.max(rows.length - deprecatedYes, 0) }, { name: "Deprecated", y: deprecatedYes }].filter((p) => p.y > 0) }],
    });
  }

  function renderModelTable(rows) {
    $("#pc-model-summary").textContent = `Showing ${rows.length} / ${state.models.length} local models`;
    const hasRows = rows.length > 0;
    $("#pc-model-table").hidden = !hasRows;
    $("#pc-model-empty").hidden = hasRows;
    $("#pc-model-table-body").innerHTML = rows.map((row) => {
      const recommended = PROFILE_KEYS.filter((key) => row.recommended?.[key])
        .map((key) => `<span class="provider-catalog__badge provider-catalog__badge--ok">${escapeHtml(key)}</span>`)
        .join(" ");
      const status = row.deprecated
        ? `<span class="provider-catalog__badge provider-catalog__badge--warn">deprecated</span>${row.replacement ? `<br><code>${escapeHtml(row.replacement)}</code>` : ""}`
        : '<span class="provider-catalog__badge provider-catalog__badge--ok">active</span>';
      return `<tr>
        <td><code>${escapeHtml(row.id)}</code><br><a class="link" href="${escapeHtml(row.source_url)}" target="_blank" rel="noopener noreferrer">ollama</a></td>
        <td>${escapeHtml(row.family)}</td>
        <td>${escapeHtml(String(row.params_b))}B · ${escapeHtml(row.quant)}</td>
        <td>${escapeHtml(String(row.min_vram_gb))} GB VRAM<br>${escapeHtml(String(row.min_ram_gb))} GB RAM</td>
        <td><div class="provider-catalog__flags">${recommended || '<span class="provider-catalog__badge provider-catalog__badge--muted">—</span>'}</div></td>
        <td>${status}</td>
        <td>${escapeHtml(row.verified_at || "—")}</td>
      </tr>`;
    }).join("");
  }

  function renderModels() {
    const rows = state.models.filter(modelMatches);
    renderModelCharts(rows);
    renderModelTable(rows);
  }

  function populateCategoryOptions() {
    const categories = [...new Set(state.providers.map((row) => row.category))].sort();
    $("#pc-filter-category").innerHTML = '<option value="">All categories</option>' +
      categories.map((category) => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join("");
  }

  function populateFamilyOptions() {
    const families = [...new Set(state.models.map((row) => row.family))].sort();
    $("#pc-model-family").innerHTML = '<option value="">All families</option>' +
      families.map((family) => `<option value="${escapeHtml(family)}">${escapeHtml(family)}</option>`).join("");
  }

  function setActiveTab(tab) {
    state.activeTab = tab;
    const isProviders = tab === "providers";
    $("#pc-tab-providers").setAttribute("aria-selected", String(isProviders));
    $("#pc-tab-models").setAttribute("aria-selected", String(!isProviders));
    $("#pc-panel-providers").hidden = !isProviders;
    $("#pc-panel-models").hidden = isProviders;
    if (isProviders) renderProviders();
    else renderModels();
  }

  function wireEvents() {
    $("#pc-tab-providers").addEventListener("click", () => setActiveTab("providers"));
    $("#pc-tab-models").addEventListener("click", () => setActiveTab("models"));
    ["#pc-filter-search", "#pc-filter-category", "#pc-filter-free-tier", "#pc-filter-trial", "#pc-filter-no-vpn", "#pc-filter-fz152"]
      .forEach((sel) => $(sel).addEventListener("input", renderProviders));
    $("#pc-filter-reset").addEventListener("click", () => {
      $("#pc-filter-search").value = "";
      $("#pc-filter-category").value = "";
      $("#pc-filter-free-tier").checked = false;
      $("#pc-filter-trial").checked = false;
      $("#pc-filter-no-vpn").checked = false;
      $("#pc-filter-fz152").checked = false;
      renderProviders();
    });
    ["#pc-model-search", "#pc-model-family", "#pc-model-prod-default", "#pc-model-local-default", "#pc-model-hide-deprecated"]
      .forEach((sel) => $(sel).addEventListener("input", renderModels));
    $("#pc-model-reset").addEventListener("click", () => {
      $("#pc-model-search").value = "";
      $("#pc-model-family").value = "";
      $("#pc-model-prod-default").checked = false;
      $("#pc-model-local-default").checked = false;
      $("#pc-model-hide-deprecated").checked = false;
      renderModels();
    });
  }

  async function loadData() {
    const [catalog, models] = await Promise.all([
      api("/api/providers/catalog"),
      api("/api/providers/local-models"),
    ]);
    state.providers = catalog.items || [];
    state.models = models.items || [];
    state.sourcePath = catalog.path || "";
    state.asOf = models.as_of || catalog.as_of || "";
    state.syncCommand = models.sync_command || "python scripts/infra/sync_local_models.py --dry-run";
    $("#pc-provider-total").textContent = String(state.providers.length);
    $("#pc-model-total").textContent = String(state.models.length);
    $("#pc-as-of").textContent = state.asOf ? `as of ${state.asOf}` : "as of —";
    $("#pc-source-path").textContent = state.sourcePath
      ? `source ${state.sourcePath.split("/").slice(-3).join("/")}`
      : "";
    populateCategoryOptions();
    populateFamilyOptions();
    setActiveTab(state.activeTab);
  }

  async function mount(container) {
    rootEl = container;
    container.innerHTML = template();
    wireEvents();
    $("#pc-reload").addEventListener("click", async () => {
      const btn = $("#pc-reload");
      btn.disabled = true;
      btn.textContent = "Loading…";
      try {
        await loadData();
      } catch (error) {
        container.innerHTML = `<div class="provider-catalog__error">${escapeHtml(error.message)}</div>`;
      } finally {
        btn.disabled = false;
        btn.textContent = "Reload";
      }
    });
    try {
      await loadData();
    } catch (error) {
      container.innerHTML = `<div class="provider-catalog__error">${escapeHtml(error.message)}</div>`;
    }
  }

  return { mount, reload: loadData };
})();
