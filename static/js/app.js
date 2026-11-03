/* Choumran front-end */
(() => {
  "use strict";

  const COLORS = { ordinary: "#3fb68b", fraud: "#ff6b5b", scam: "#f5b041" };
  const LABELS = ["ordinary", "fraud", "scam"];
  const LANGS = ["en", "ar", "fr"];

  const state = {
    lang: LANGS.includes(localStorage.getItem("lang")) ? localStorage.getItem("lang") : "en",
    dict: {}, id: null, summary: null, upload: null, charts: {},
    tx: { page: 1, entity: "", entityName: "" }, network: null, schema: null, entityId: null,
  };
  const dicts = {};

  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => Number(n || 0).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
  const money = (n) => "$" + fmt(n, 0);
  const pct = (n) => fmt(n * 100, 1) + "%";

  // ----------------------------------------------------------------- i18n --
  function t(key, params = {}) {
    let s = state.dict[key] ?? dicts.en?.[key] ?? key;
    for (const [k, v] of Object.entries(params)) s = s.split(`{${k}}`).join(v);
    return s;
  }
  const L = (l) => t("label." + l);
  const isRtl = () => state.dict._dir === "rtl";

  async function fetchDict(lang) {
    if (!dicts[lang]) dicts[lang] = await (await fetch(`/static/i18n/${lang}.json`)).json();
    return dicts[lang];
  }

  async function setLang(lang) {
    if (!dicts.en) await fetchDict("en");
    state.lang = lang;
    state.dict = await fetchDict(lang);
    localStorage.setItem("lang", lang);
    document.documentElement.lang = lang;
    document.documentElement.dir = state.dict._dir || "ltr";
    $("#lang").value = lang;
    $("#sample-link").href = `/api/sample.csv?lang=${lang}`;
    document.querySelectorAll("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
    document.querySelectorAll("[data-i18n-html]").forEach((el) => (el.innerHTML = t(el.dataset.i18nHtml)));
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => (el.placeholder = t(el.dataset.i18nPlaceholder)));
    document.querySelectorAll("[data-i18n-aria]").forEach((el) => el.setAttribute("aria-label", t(el.dataset.i18nAria)));
    Chart.defaults.plugins.legend.rtl = isRtl();
    Chart.defaults.plugins.tooltip.rtl = isRtl();
    rerender();
  }

  /** Re-draw everything that contains translated text. */
  function rerender() {
    if (state.schema) renderSchema();
    if (state.upload) { renderColumnMap(state.upload, true); $("#drop-text").textContent = t("upload.rows", { name: state.upload.filename, n: fmt(state.upload.rows) }); }
    if (!state.entityId) $("#entity-panel").innerHTML = `<p class="muted">${esc(t("entity.placeholder"))}</p>`;
    if (state.tx.entity) renderEntityChip();
    if (!state.id) return;
    renderBadge();
    updateExportLinks();
    renderDashboard();
    renderModel();
    const active = document.querySelector(".tab.active")?.id;
    if (active === "tab-transactions") loadTx();
    if (active === "tab-mapping") {
      state.network?.destroy(); state.network = null;
      loadGraph(state.graphFocus || ""); loadEntities();
      if (state.entityId) showEntity(state.entityId, false);
    } else if (state.network) { state.network.destroy(); state.network = null; }
  }
  $("#lang").addEventListener("change", (e) => setLang(e.target.value));

  Chart.defaults.color = "#8d99b3";
  Chart.defaults.borderColor = "#2a3550";
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

  // ---------------------------------------------------------------- utils --
  function loading(on, key) {
    $("#loading").classList.toggle("hidden", !on);
    $("#loading-text").textContent = t(key || "loading.default");
  }
  let toastTimer;
  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), 6000);
  }
  async function api(url, opts = {}) {
    const res = await fetch(url, { ...opts, headers: { ...(opts.headers || {}), "X-Lang": state.lang } });
    let data;
    try { data = await res.json(); } catch { data = {}; }
    if (!res.ok) throw new Error(data.error || t("err.generic", { status: res.status }));
    return data;
  }
  const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const debounce = (fn, ms = 300) => { let tm; return (...a) => { clearTimeout(tm); tm = setTimeout(() => fn(...a), ms); }; };

  // ----------------------------------------------------------------- tabs --
  function showTab(name) {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.id === "tab-" + name));
    if (name === "transactions") loadTx();
    if (name === "mapping") { loadGraph(); loadEntities(); }
    window.scrollTo({ top: 0 });
  }
  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b && !b.disabled) showTab(b.dataset.tab);
  });

  // --------------------------------------------------------------- schema --
  async function loadSchema() {
    state.schema = await api("/api/schema");
    renderSchema();
  }
  function renderSchema() {
    const rows = state.schema.columns.map((c) =>
      `<tr><td>${esc(c.name)}</td><td>${esc(t("col." + c.name))}</td><td>${c.required ? `<span class="tag fraud">${esc(t("schema.required"))}</span>` : `<span class="muted">${esc(t("schema.optional"))}</span>`}</td></tr>`).join("");
    $("#schema-help").innerHTML = `<div class="table-wrap"><table class="data"><tr><th>${esc(t("schema.col"))}</th><th>${esc(t("schema.meaning"))}</th><th></th></tr>${rows}</table></div>
      <p class="muted">${t("schema.note")}</p>`;
    $("#opt-custom-wrap").classList.toggle("hidden", !state.schema.has_custom_model);
  }

  // ----------------------------------------------------------------- demo --
  $("#btn-demo").addEventListener("click", async () => {
    loading(true, "loading.demo");
    try {
      const r = await post("/api/demo", { persons: +$("#demo-persons").value, days: +$("#demo-days").value, threshold: +$("#thr").value });
      setAnalysis(r);
    } catch (e) { toast(e.message); } finally { loading(false); }
  });

  // --------------------------------------------------------------- upload --
  const drop = $("#drop");
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]); });
  $("#file").addEventListener("change", (e) => { if (e.target.files[0]) uploadFile(e.target.files[0]); });

  async function uploadFile(file) {
    loading(true, "loading.read");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api("/api/upload", { method: "POST", body: fd });
      state.upload = r;
      $("#drop-text").textContent = t("upload.rows", { name: file.name, n: fmt(r.rows) });
      renderColumnMap(r);
    } catch (e) { toast(e.message); } finally { loading(false); $("#file").value = ""; }
  }

  function renderColumnMap(r, keepSelection = false) {
    const current = {};
    if (keepSelection) document.querySelectorAll("#col-map select").forEach((s) => { current[s.dataset.col] = s.value; });
    const opts = (sel) => `<option value="">${esc(t("map.none"))}</option>` +
      r.columns.map((c) => `<option ${c === sel ? "selected" : ""} value="${esc(c)}">${esc(c)}</option>`).join("");
    $("#col-map").innerHTML = state.schema.columns.map((c) => {
      const sel = keepSelection && c.name in current ? current[c.name] : r.mapping[c.name];
      return `<label class="${c.required ? "req" : ""}"><span>${esc(t("col." + c.name))} <small class="muted">(${esc(c.name)})</small></span>
        <select data-col="${esc(c.name)}">${opts(sel)}</select></label>`;
    }).join("");
    if (keepSelection) return;
    const cols = r.columns;
    $("#preview").innerHTML = `<table class="data"><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>` +
      r.preview.map((row) => `<tr>${cols.map((c) => `<td>${esc(row[c])}</td>`).join("")}</tr>`).join("") + "</table>";
    $("#opt-train").checked = false;
    $("#mapping-card").classList.remove("hidden");
    $("#mapping-card").scrollIntoView({ behavior: "smooth" });
  }

  $("#btn-analyze").addEventListener("click", async () => {
    if (!state.upload) return;
    const mapping = {};
    document.querySelectorAll("#col-map select").forEach((s) => { mapping[s.dataset.col] = s.value || null; });
    const doTrain = $("#opt-train").checked;
    loading(true, doTrain ? "loading.train" : "loading.default");
    try {
      const r = await post("/api/analyze", {
        token: state.upload.token, filename: state.upload.filename, mapping,
        train: doTrain, use_custom: $("#opt-custom").checked, threshold: +$("#thr").value,
      });
      setAnalysis(r);
      if (doTrain) loadSchema();
    } catch (e) { toast(e.message); } finally { loading(false); }
  });

  // ------------------------------------------------------------- analysis --
  function renderBadge() {
    const m = state.summary.meta;
    const name = m.source === "demo" ? t("meta.demo", { p: fmt(m.persons), d: fmt(m.days) }) : (m.name || t("meta.upload"));
    $("#current-dataset").textContent = t("dataset.badge", { name, n: fmt(m.rows) });
    $("#current-dataset").classList.remove("hidden");
  }
  function updateExportLinks() {
    $("#tx-export").href = `/api/a/${state.id}/export.csv?what=transactions&lang=${state.lang}`;
    $("#e-export").href = `/api/a/${state.id}/export.csv?what=entities&lang=${state.lang}`;
  }

  function setAnalysis(r) {
    state.id = r.id;
    state.summary = r.summary;
    state.tx = { page: 1, entity: "", entityName: "" };
    state.entityId = null;
    state.graphFocus = "";
    $("#tx-entity-filter").classList.add("hidden");
    if (state.network) { state.network.destroy(); state.network = null; }
    $("#entity-panel").innerHTML = `<p class="muted">${esc(t("entity.placeholder"))}</p>`;
    document.querySelectorAll(".tabs button").forEach((b) => (b.disabled = false));
    $("#thr").value = r.summary.threshold;
    $("#thr-val").textContent = Number(r.summary.threshold).toFixed(2);
    renderBadge();
    updateExportLinks();
    renderDashboard();
    renderModel();
    showTab("dashboard");
  }

  // ------------------------------------------------------------ dashboard --
  function chart(id, config) {
    if (state.charts[id]) state.charts[id].destroy();
    state.charts[id] = new Chart(document.getElementById(id), config);
  }
  const stackedBar = (labels, data, extra = {}) => ({
    type: "bar",
    data: { labels, datasets: LABELS.map((l) => ({ label: L(l), data: data[l], backgroundColor: COLORS[l], stack: "s" })) },
    options: { responsive: true, interaction: { mode: "index", intersect: false },
      scales: { x: { stacked: true, reverse: isRtl(), ...extra.x }, y: { stacked: true, position: isRtl() ? "right" : "left", ...extra.y } },
      plugins: { legend: { position: "bottom" } } },
  });

  function renderDashboard() {
    const s = state.summary, tot = s.totals;
    const kpis = [
      ["kpi.transactions", fmt(tot.transactions), ""],
      ["kpi.amount", money(tot.amount), ""],
      ["kpi.suspicious", `${fmt(tot.suspicious)} <small>(${pct(tot.suspicious / Math.max(1, tot.transactions))})</small>`, "bad"],
      ["kpi.suspicious_amount", money(tot.suspicious_amount), "bad"],
      ["kpi.fraud", fmt(s.by_class.fraud.count), "bad"],
      ["kpi.scam", fmt(s.by_class.scam.count), "warn"],
      ["kpi.entities", `${fmt(tot.persons)} / ${fmt(tot.shops)}`, ""],
      ["kpi.high_risk", fmt(tot.high_risk_entities), "bad"],
    ];
    $("#kpis").innerHTML = kpis.map(([k, v, c]) => `<div class="kpi ${c}"><div class="l">${esc(t(k))}</div><div class="v"><bdi>${v}</bdi></div></div>`).join("") +
      `<div class="kpi"><div class="l">${esc(t("kpi.period"))}</div><div class="v small"><bdi>${esc(tot.date_from)} → ${esc(tot.date_to)}</bdi></div></div>`;

    chart("ch-class", {
      type: "doughnut",
      data: { labels: LABELS.map(L), datasets: [{ data: LABELS.map((l) => s.by_class[l].count), backgroundColor: LABELS.map((l) => COLORS[l]), borderWidth: 0 }] },
      options: { plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => {
        const l = LABELS[c.dataIndex]; return " " + t("chart.tx_amount", { n: fmt(s.by_class[l].count), amount: money(s.by_class[l].amount) }); } } } } },
    });
    const top = s.top_receivers;
    chart("ch-top", {
      type: "bar",
      data: { labels: top.map((r) => r.name), datasets: [
        { label: L("scam"), data: top.map((r) => r.scam), backgroundColor: COLORS.scam, stack: "s" },
        { label: L("fraud"), data: top.map((r) => r.fraud), backgroundColor: COLORS.fraud, stack: "s" }] },
      options: { indexAxis: "y", scales: { x: { stacked: true, reverse: isRtl() }, y: { stacked: true, position: isRtl() ? "right" : "left" } },
        plugins: { legend: { position: "bottom" } },
        onClick: (_e, els) => { if (els[0]) { showTab("mapping"); focusEntity(top[els[0].index].id); } } },
    });
    chart("ch-day", stackedBar(s.per_day.labels.map((d) => d.slice(5)), s.per_day));
    chart("ch-hour", stackedBar([...Array(24).keys()].map((h) => h + ":00"), s.per_hour));
    chart("ch-amount", stackedBar(s.amount_buckets.labels, s.amount_buckets, { y: { type: "logarithmic" } }));
  }

  $("#thr").addEventListener("input", (e) => ($("#thr-val").textContent = Number(e.target.value).toFixed(2)));
  $("#thr").addEventListener("change", async (e) => {
    if (!state.id) return;
    loading(true, "loading.rethreshold");
    try {
      const r = await post(`/api/a/${state.id}/threshold`, { threshold: +e.target.value });
      state.summary = r.summary;
      if (state.network) { state.network.destroy(); state.network = null; }
      renderDashboard(); renderModel();
    } catch (err) { toast(err.message); } finally { loading(false); }
  });

  // --------------------------------------------------------- transactions --
  const riskBar = (r) => {
    const c = r >= 0.6 ? "var(--high)" : r >= 0.25 ? "var(--medium)" : "var(--low)";
    return `<bdi>${fmt(r * 100)}%</bdi><span class="bar"><i style="width:${Math.round(r * 100)}%;background:${c}"></i></span>`;
  };

  async function loadTx() {
    if (!state.id) return;
    const p = new URLSearchParams({ page: state.tx.page, cls: $("#tx-cls").value, sort: $("#tx-sort").value, q: $("#tx-q").value, entity: state.tx.entity });
    try {
      const r = await api(`/api/a/${state.id}/transactions?${p}`);
      const hasLabel = r.rows.length && "label" in r.rows[0];
      const cols = ["class", "risk", "amount", "sender", "receiver", ...(hasLabel ? ["actual"] : []), "why", "date", "id"];
      const head = `<tr>${cols.map((c) => `<th>${esc(t("tx.h." + c))}</th>`).join("")}</tr>`;
      const body = r.rows.map((x) => `<tr>
        <td><span class="tag ${x.predicted}">${esc(L(x.predicted))}</span></td>
        <td class="num">${riskBar(x.risk)}</td>
        <td class="num">${money(x.amount)}</td>
        <td><a href="#" data-ent="${esc(x.sender_id)}">${esc(x.sender_name)}</a></td>
        <td><a href="#" data-ent="${esc(x.receiver_id)}">${esc(x.receiver_name)}</a> <small class="muted">${esc(t("type." + x.receiver_type))}</small></td>
        ${hasLabel ? `<td><span class="tag ${x.label === x.predicted ? "ok" : "miss"}">${x.label ? esc(L(x.label)) : ""}</span></td>` : ""}
        <td class="reasons">${esc((x.reasons || []).map((c) => t("reason." + c)).join(t("_sep")))}</td>
        <td class="num">${esc(x.timestamp)}</td><td class="num">${esc(x.transaction_id)}</td></tr>`).join("");
      $("#tx-table").innerHTML = head + (body || `<tr><td colspan="${cols.length}" class="muted">${esc(t("tx.none"))}</td></tr>`);
      const pages = Math.max(1, Math.ceil(r.total / r.per_page));
      $("#tx-pager").innerHTML = `<button class="btn small" ${r.page <= 1 ? "disabled" : ""} data-p="${r.page - 1}">${esc(t("pager.prev"))}</button>
        <span class="muted">${esc(t("pager.info", { p: r.page, n: pages, t: fmt(r.total) }))}</span>
        <button class="btn small" ${r.page >= pages ? "disabled" : ""} data-p="${r.page + 1}">${esc(t("pager.next"))}</button>`;
    } catch (e) { toast(e.message); }
  }
  $("#tx-pager").addEventListener("click", (e) => { const b = e.target.closest("button[data-p]"); if (b) { state.tx.page = +b.dataset.p; loadTx(); } });
  ["#tx-cls", "#tx-sort"].forEach((s) => $(s).addEventListener("change", () => { state.tx.page = 1; loadTx(); }));
  $("#tx-q").addEventListener("input", debounce(() => { state.tx.page = 1; loadTx(); }));
  $("#tx-table").addEventListener("click", (e) => {
    const a = e.target.closest("a[data-ent]");
    if (a) { e.preventDefault(); showTab("mapping"); focusEntity(a.dataset.ent); }
  });
  function renderEntityChip() {
    $("#tx-entity-filter").innerHTML = `${esc(t("tx.entity_filter"))} <b>${esc(state.tx.entityName)}</b> <button title="${esc(t("tx.clear"))}">✕</button>`;
    $("#tx-entity-filter").classList.remove("hidden");
  }
  function filterTxByEntity(id, name) {
    state.tx = { page: 1, entity: id, entityName: name };
    $("#tx-cls").value = "";
    renderEntityChip();
    showTab("transactions");
  }
  $("#tx-entity-filter").addEventListener("click", (e) => {
    if (e.target.closest("button")) { state.tx.entity = ""; $("#tx-entity-filter").classList.add("hidden"); loadTx(); }
  });

  // -------------------------------------------------------------- mapping --
  const nodeColor = (n) => (n.risk_level === "high" ? "#ff4d4f" : n.risk_level === "medium" ? "#f5b041" : n.type === "shop" ? "#4f8cff" : "#6b7a99");

  async function loadGraph(focus = "") {
    if (!state.id) return;
    if (state.network && !focus && state.graphMode === $("#g-mode").value && !state.graphFocus) return;
    state.graphMode = $("#g-mode").value;
    state.graphFocus = focus;
    try {
      const p = new URLSearchParams({ mode: state.graphMode, focus });
      const g = await api(`/api/a/${state.id}/graph?${p}`);
      const maxVol = Math.max(1, ...g.nodes.map((n) => n.volume));
      const nodes = g.nodes.map((n) => ({
        id: n.id, label: n.name.length > 22 ? n.name.slice(0, 21) + "…" : n.name,
        title: t("g.node", { name: n.name, type: t("type." + n.type), level: t("level." + n.risk_level), tx: n.tx, flagged: n.flagged }) +
          (n.role ? "\n" + t("role." + n.role) : ""),
        shape: n.type === "shop" ? "square" : "dot",
        size: 8 + 22 * Math.sqrt(n.volume / maxVol),
        color: { background: nodeColor(n), border: n.id === focus ? "#fff" : nodeColor(n) },
        borderWidth: n.id === focus ? 3 : 1,
        font: { color: "#e8ecf4", size: 12 },
      }));
      const edges = g.edges.map((e, i) => ({
        id: i, from: e.from, to: e.to, arrows: { to: { enabled: true, scaleFactor: 0.4 } },
        width: Math.min(6, 1 + Math.log2(e.count)),
        color: { color: e.scam ? "#f5b041" : e.fraud ? "#ff6b5b" : "#3a4660", opacity: e.scam || e.fraud ? 0.95 : 0.5 },
        title: t("g.edge", { n: e.count, amount: money(e.total) }) + (e.fraud ? t("g.edge_fraud", { n: e.fraud }) : "") + (e.scam ? t("g.edge_scam", { n: e.scam }) : ""),
      }));
      if (state.network) state.network.destroy();
      state.network = new vis.Network($("#graph"), { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) }, {
        physics: { solver: "forceAtlas2Based", stabilization: { iterations: 200 }, forceAtlas2Based: { gravitationalConstant: -40, springLength: 90 } },
        interaction: { hover: true, tooltipDelay: 120 },
        nodes: { borderWidth: 1 },
      });
      state.network.once("stabilizationIterationsDone", () => state.network.setOptions({ physics: false }));
      state.network.on("click", (prm) => { if (prm.nodes[0]) showEntity(prm.nodes[0]); });
      state.network.on("doubleClick", (prm) => { if (prm.nodes[0]) focusEntity(prm.nodes[0]); });
      $("#g-note").textContent = t("g.note", { n: fmt(nodes.length), e: fmt(edges.length) }) + (g.truncated ? t("g.truncated") : "") + t("g.hint");
    } catch (e) { toast(e.message); }
  }
  function focusEntity(id) { loadGraph(id); showEntity(id); }
  $("#g-mode").addEventListener("change", () => loadGraph());
  $("#g-reset").addEventListener("click", () => { state.graphFocus = ""; state.network?.destroy(); state.network = null; loadGraph(); });
  $("#g-q").addEventListener("input", debounce(async (e) => {
    const q = e.target.value.trim();
    if (!q) return;
    const r = await api(`/api/a/${state.id}/entities?${new URLSearchParams({ q, limit: 1 })}`);
    if (r.rows[0]) focusEntity(r.rows[0].id);
  }, 500));

  async function showEntity(id, scroll = true) {
    try {
      const r = await api(`/api/a/${state.id}/entity/${encodeURIComponent(id)}`);
      state.entityId = id;
      const e = r.entity;
      const list = (items, title) => items.length ? `<h3>${esc(title)}</h3><div class="table-wrap"><table class="data">
        ${items.map((x) => `<tr class="click" data-ent="${esc(x.id)}"><td>${esc(x.name)} <small class="muted">${esc(t("type." + x.type))}</small></td>
          <td class="num">${x.count}×</td><td class="num">${money(x.total)}</td>
          <td>${x.scam ? `<span class="tag scam">${esc(t("n.scam", { n: x.scam }))}</span>` : ""} ${x.fraud ? `<span class="tag fraud">${esc(t("n.fraud", { n: x.fraud }))}</span>` : ""}</td></tr>`).join("")}
        </table></div>` : "";
      const stat = (k, v) => `<div><small>${esc(t(k))}</small><b><bdi>${v}</bdi></b></div>`;
      $("#entity-panel").innerHTML = `
        <div class="entity-head"><div><h2>${esc(e.name)}</h2><small class="muted">${esc(e.id)} · ${esc(t("type." + e.type))}</small></div>
          <span class="tag ${e.risk_level}">${esc(t("entity.risk", { level: t("level." + e.risk_level), pct: fmt(e.risk_score * 100) + "%" }))}</span></div>
        ${e.role ? `<div class="role">${esc(t("role." + e.role))}</div>` : ""}
        <div class="stats">
          ${stat("entity.paid", `${fmt(e.sent_count)} · ${money(e.sent_total)}`)}
          ${stat("entity.received", `${fmt(e.recv_count)} · ${money(e.recv_total)}`)}
          ${stat("entity.flagged", fmt(e.flagged))}
          ${stat("entity.scam_recv", fmt(e.scam_recv))}
          ${stat("entity.fraud_sent", fmt(e.fraud_sent))}
          ${stat("entity.partners", fmt(e.partners_out + e.partners_in))}
        </div>
        <button class="btn small" id="ent-tx">${esc(t("entity.all_tx"))}</button>
        ${list(r.received_from, t("entity.received_from"))}${list(r.paid_to, t("entity.paid_to"))}`;
      $("#ent-tx").addEventListener("click", () => filterTxByEntity(e.id, e.name));
      if (scroll && window.innerWidth < 800) $("#entity-panel").scrollIntoView({ behavior: "smooth" });
    } catch (err) { toast(err.message); }
  }
  $("#entity-panel").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-ent]"); if (tr) focusEntity(tr.dataset.ent); });

  async function loadEntities() {
    if (!state.id) return;
    const p = new URLSearchParams({ type: $("#e-type").value, level: $("#e-level").value, limit: 300 });
    try {
      const r = await api(`/api/a/${state.id}/entities?${p}`);
      $("#e-table").innerHTML = `<tr>${["name", "type", "risk", "flagged", "role"].map((c) => `<th>${esc(t("e.h." + c))}</th>`).join("")}</tr>` +
        r.rows.map((x) => `<tr class="click" data-ent="${esc(x.id)}"><td>${esc(x.name)}</td><td>${esc(t("type." + x.type))}</td>
          <td><span class="tag ${x.risk_level}">${esc(t("level." + x.risk_level))} <bdi>${fmt(x.risk_score * 100)}%</bdi></span></td>
          <td class="num">${fmt(x.flagged)}</td><td class="reasons">${x.role ? esc(t("role." + x.role)) : ""}</td></tr>`).join("");
    } catch (e) { toast(e.message); }
  }
  ["#e-type", "#e-level"].forEach((s) => $(s).addEventListener("change", loadEntities));
  $("#e-table").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-ent]"); if (tr) focusEntity(tr.dataset.ent); });

  // ---------------------------------------------------------------- model --
  function metricsBlock(m) {
    const cells = [
      ["m.accuracy", pct(m.accuracy)],
      ["m.recall", pct(m.suspicious_recall)],
      ["m.precision", pct(m.suspicious_precision)],
      ["m.f1", pct(m.macro_f1)],
    ].map(([k, v]) => `<div class="kpi"><div class="l">${esc(t(k))}</div><div class="v"><bdi>${v}</bdi></div></div>`).join("");
    const labs = m.labels;
    const cm = `<table class="cm"><tr><th>${esc(t("m.cm_corner"))}</th>${labs.map((l) => `<th>${esc(L(l))}</th>`).join("")}</tr>` +
      m.confusion.map((row, i) => `<tr><th>${esc(L(labs[i]))}</th>${row.map((v, j) => `<td class="${i === j ? "diag" : ""}">${fmt(v)}</td>`).join("")}</tr>`).join("") + "</table>";
    return `<div class="metrics">${cells}</div>${cm}<small class="muted">${esc(t("m.on_n", { n: fmt(m.n) }))}</small>`;
  }

  function renderModel() {
    const s = state.summary;
    const imp = s.importances.slice(0, 12);
    chart("ch-imp", {
      type: "bar",
      data: { labels: imp.map((x) => t("feat." + x.feature)), datasets: [{ data: imp.map((x) => x.importance), backgroundColor: "#4f8cff" }] },
      options: { indexAxis: "y", plugins: { legend: { display: false } },
        scales: { x: { reverse: isRtl() }, y: { position: isRtl() ? "right" : "left" } } },
    });
    const src = s.model_source === "uploaded" ? t("model.src.uploaded") : t("model.src.synthetic");
    $("#model-info").innerHTML = `<h3>${esc(src)}</h3>
      <p class="muted">${t("model.desc")}</p>
      <h3>${esc(t("model.perf"))}</h3>${metricsBlock(s.model_metrics)}`;
    $("#eval-card").innerHTML = s.evaluation
      ? `<h2>${esc(t("eval.title"))}</h2>${metricsBlock(s.evaluation)}`
      : `<h2>${esc(t("eval.none_title"))}</h2><p class="muted">${esc(t("eval.none"))}</p>`;
  }

  // ----------------------------------------------------------------- init --
  setLang(state.lang)
    .then(loadSchema)
    .catch((e) => toast(e.message));
})();
