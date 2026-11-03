/* Choumran front-end */
(() => {
  "use strict";

  const COLORS = { ordinary: "#3fb68b", fraud: "#ff6b5b", scam: "#f5b041" };
  const LABEL_AR = { ordinary: "عادية", fraud: "احتيال", scam: "نصب" };
  const LEVEL_AR = { high: "عالي", medium: "متوسط", low: "منخفض" };
  const TYPE_AR = { person: "شخص", shop: "محل" };
  const LABELS = ["ordinary", "fraud", "scam"];

  const state = { id: null, summary: null, upload: null, charts: {}, tx: { page: 1, entity: "" }, network: null, schema: null };

  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => Number(n || 0).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
  const money = (n) => "$" + fmt(n, 0);
  const pct = (n) => fmt(n * 100, 1) + "%";

  Chart.defaults.color = "#8d99b3";
  Chart.defaults.borderColor = "#2a3550";
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

  // ---------------------------------------------------------------- utils --
  function loading(on, text) {
    $("#loading").classList.toggle("hidden", !on);
    if (text) $("#loading-text").textContent = text;
  }
  let toastTimer;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add("hidden"), 6000);
  }
  async function api(url, opts = {}) {
    const res = await fetch(url, opts);
    let data;
    try { data = await res.json(); } catch { data = {}; }
    if (!res.ok) throw new Error(data.error || `خطأ ${res.status}`);
    return data;
  }
  const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  // ----------------------------------------------------------------- tabs --
  function showTab(name) {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
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
    const rows = state.schema.columns.map((c) =>
      `<tr><td>${esc(c.name)}</td><td>${esc(c.label)}</td><td>${c.required ? '<span class="tag fraud">إجباري</span>' : '<span class="muted">اختياري</span>'}</td></tr>`).join("");
    $("#schema-help").innerHTML = `<div class="table-wrap"><table class="data"><tr><th>العمود</th><th>المعنى</th><th></th></tr>${rows}</table></div>
      <p class="muted">أسماء الأعمدة ما لازم تكون نفسها، بتقدر تربطها بإيدك بعد ما ترفع الملف. عمود <b>label</b> (ordinary / fraud / scam) اختياري: إذا موجود، منقارن نتايج النموذج فيه، أو بتقدر تدرّب نموذج جديد عليه.</p>`;
    $("#opt-custom-wrap").classList.toggle("hidden", !state.schema.has_custom_model);
  }

  // ----------------------------------------------------------------- demo --
  $("#btn-demo").addEventListener("click", async () => {
    loading(true, "عم نولّد بيانات تجريبية ونحلّلها... (أول مرة ممكن ياخد دقيقة لأن النموذج عم يتدرّب)");
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
    loading(true, "عم نقرا الملف...");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api("/api/upload", { method: "POST", body: fd });
      state.upload = r;
      $("#drop-text").textContent = `${file.name} (${fmt(r.rows)} سطر)`;
      renderColumnMap(r);
    } catch (e) { toast(e.message); } finally { loading(false); $("#file").value = ""; }
  }

  function renderColumnMap(r) {
    const opts = (sel) => `<option value="">— ما في —</option>` +
      r.columns.map((c) => `<option ${c === sel ? "selected" : ""} value="${esc(c)}">${esc(c)}</option>`).join("");
    $("#col-map").innerHTML = state.schema.columns.map((c) =>
      `<label class="${c.required ? "req" : ""}"><span>${esc(c.label)} <small class="muted">(${esc(c.name)})</small></span>
        <select data-col="${esc(c.name)}">${opts(r.mapping[c.name])}</select></label>`).join("");
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
    loading(true, doTrain ? "عم ندرّب نموذج جديد على بياناتك..." : "عم نحلّل...");
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
  function setAnalysis(r) {
    state.id = r.id;
    state.summary = r.summary;
    state.tx = { page: 1, entity: "" };
    if (state.network) { state.network.destroy(); state.network = null; }
    $("#entity-panel").innerHTML = '<p class="muted">اضغط على أي شخص أو محل بالشبكة أو بالجدول لتشوف تفاصيله.</p>';
    document.querySelectorAll(".tabs button").forEach((b) => (b.disabled = false));
    const m = r.summary.meta;
    $("#current-dataset").textContent = `📄 ${m.name} · ${fmt(m.rows)} عملية`;
    $("#current-dataset").classList.remove("hidden");
    $("#thr").value = r.summary.threshold;
    $("#thr-val").textContent = Number(r.summary.threshold).toFixed(2);
    $("#tx-export").href = `/api/a/${r.id}/export.csv?what=transactions`;
    $("#e-export").href = `/api/a/${r.id}/export.csv?what=entities`;
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
    data: { labels, datasets: LABELS.map((l) => ({ label: LABEL_AR[l], data: data[l], backgroundColor: COLORS[l], stack: "s" })) },
    options: { responsive: true, interaction: { mode: "index", intersect: false },
      scales: { x: { stacked: true, ...extra.x }, y: { stacked: true, ...extra.y } }, plugins: { legend: { position: "bottom" } } },
  });

  function renderDashboard() {
    const s = state.summary, t = s.totals;
    const kpis = [
      ["عدد العمليات", fmt(t.transactions), ""],
      ["مجموع المبالغ", money(t.amount), ""],
      ["عمليات مشبوهة", `${fmt(t.suspicious)} <small>(${pct(t.suspicious / Math.max(1, t.transactions))})</small>`, "bad"],
      ["مبالغ مشبوهة", money(t.suspicious_amount), "bad"],
      ["احتيال", fmt(s.by_class.fraud.count), "bad"],
      ["نصب", fmt(s.by_class.scam.count), "warn"],
      ["أشخاص / محلات", `${fmt(t.persons)} / ${fmt(t.shops)}`, ""],
      ["جهات خطر عالي", fmt(t.high_risk_entities), "bad"],
    ];
    $("#kpis").innerHTML = kpis.map(([l, v, c]) => `<div class="kpi ${c}"><div class="l">${l}</div><div class="v">${v}</div></div>`).join("") +
      `<div class="kpi"><div class="l">الفترة</div><div class="v" style="font-size:1rem">${esc(t.date_from)} → ${esc(t.date_to)}</div></div>`;

    chart("ch-class", {
      type: "doughnut",
      data: { labels: LABELS.map((l) => LABEL_AR[l]), datasets: [{ data: LABELS.map((l) => s.by_class[l].count), backgroundColor: LABELS.map((l) => COLORS[l]), borderWidth: 0 }] },
      options: { plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => {
        const l = LABELS[c.dataIndex]; return ` ${fmt(s.by_class[l].count)} عملية · ${money(s.by_class[l].amount)}`; } } } } },
    });
    const top = s.top_receivers;
    chart("ch-top", {
      type: "bar",
      data: { labels: top.map((r) => r.name), datasets: [
        { label: "نصب", data: top.map((r) => r.scam), backgroundColor: COLORS.scam, stack: "s" },
        { label: "احتيال", data: top.map((r) => r.fraud), backgroundColor: COLORS.fraud, stack: "s" }] },
      options: { indexAxis: "y", scales: { x: { stacked: true }, y: { stacked: true } }, plugins: { legend: { position: "bottom" } },
        onClick: (_e, els) => { if (els[0]) { showTab("mapping"); focusEntity(top[els[0].index].id); } } },
    });
    chart("ch-day", stackedBar(s.per_day.labels.map((d) => d.slice(5)), s.per_day));
    chart("ch-hour", stackedBar([...Array(24).keys()].map((h) => h + ":00"), s.per_hour));
    chart("ch-amount", stackedBar(s.amount_buckets.labels, s.amount_buckets, { y: { type: "logarithmic" } }));
  }

  $("#thr").addEventListener("input", (e) => ($("#thr-val").textContent = Number(e.target.value).toFixed(2)));
  $("#thr").addEventListener("change", async (e) => {
    if (!state.id) return;
    loading(true, "عم نعيد التصنيف...");
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
    return `${fmt(r * 100)}%<span class="bar"><i style="width:${Math.round(r * 100)}%;background:${c}"></i></span>`;
  };

  async function loadTx() {
    if (!state.id) return;
    const p = new URLSearchParams({ page: state.tx.page, cls: $("#tx-cls").value, sort: $("#tx-sort").value, q: $("#tx-q").value, entity: state.tx.entity });
    try {
      const r = await api(`/api/a/${state.id}/transactions?${p}`);
      const hasLabel = r.rows.length && "label" in r.rows[0];
      const head = `<tr><th>التصنيف</th><th>الخطورة</th><th>المبلغ</th><th>المرسل</th><th>المستلم</th>${hasLabel ? "<th>الحقيقي</th>" : ""}<th>ليش؟</th><th>التاريخ</th><th>الرقم</th></tr>`;
      const body = r.rows.map((x) => `<tr>
        <td><span class="tag ${x.predicted}">${LABEL_AR[x.predicted]}</span></td>
        <td class="num">${riskBar(x.risk)}</td>
        <td class="num">${money(x.amount)}</td>
        <td><a href="#" data-ent="${esc(x.sender_id)}">${esc(x.sender_name)}</a></td>
        <td><a href="#" data-ent="${esc(x.receiver_id)}">${esc(x.receiver_name)}</a> <small class="muted">${TYPE_AR[x.receiver_type] || ""}</small></td>
        ${hasLabel ? `<td><span class="tag ${x.label === x.predicted ? "ok" : "miss"}">${LABEL_AR[x.label] || esc(x.label)}</span></td>` : ""}
        <td class="reasons">${esc(x.reasons)}</td>
        <td class="num">${esc(x.timestamp)}</td><td class="num">${esc(x.transaction_id)}</td></tr>`).join("");
      $("#tx-table").innerHTML = head + (body || `<tr><td colspan="9" class="muted">ما في عمليات</td></tr>`);
      const pages = Math.max(1, Math.ceil(r.total / r.per_page));
      $("#tx-pager").innerHTML = `<button class="btn small" ${r.page <= 1 ? "disabled" : ""} data-p="${r.page - 1}">السابق</button>
        <span class="muted">صفحة ${r.page} من ${pages} · ${fmt(r.total)} عملية</span>
        <button class="btn small" ${r.page >= pages ? "disabled" : ""} data-p="${r.page + 1}">التالي</button>`;
    } catch (e) { toast(e.message); }
  }
  $("#tx-pager").addEventListener("click", (e) => { const b = e.target.closest("button[data-p]"); if (b) { state.tx.page = +b.dataset.p; loadTx(); } });
  ["#tx-cls", "#tx-sort"].forEach((s) => $(s).addEventListener("change", () => { state.tx.page = 1; loadTx(); }));
  $("#tx-q").addEventListener("input", debounce(() => { state.tx.page = 1; loadTx(); }));
  $("#tx-table").addEventListener("click", (e) => {
    const a = e.target.closest("a[data-ent]");
    if (a) { e.preventDefault(); showTab("mapping"); focusEntity(a.dataset.ent); }
  });
  function filterTxByEntity(id, name) {
    state.tx = { page: 1, entity: id };
    $("#tx-cls").value = "";
    $("#tx-entity-filter").innerHTML = `عمليات: <b>${esc(name)}</b> <button title="إلغاء">✕</button>`;
    $("#tx-entity-filter").classList.remove("hidden");
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
        title: `${n.name} (${TYPE_AR[n.type]})\nخطورة: ${LEVEL_AR[n.risk_level]} · ${n.tx} عملية · ${n.flagged} مشبوهة${n.role ? "\n" + n.role : ""}`,
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
        title: `${e.count} عملية · ${money(e.total)}${e.fraud ? ` · ${e.fraud} احتيال` : ""}${e.scam ? ` · ${e.scam} نصب` : ""}`,
      }));
      if (state.network) state.network.destroy();
      state.network = new vis.Network($("#graph"), { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) }, {
        physics: { solver: "forceAtlas2Based", stabilization: { iterations: 200 }, forceAtlas2Based: { gravitationalConstant: -40, springLength: 90 } },
        interaction: { hover: true, tooltipDelay: 120 },
        nodes: { borderWidth: 1 },
      });
      state.network.once("stabilizationIterationsDone", () => state.network.setOptions({ physics: false }));
      state.network.on("click", (p) => { if (p.nodes[0]) showEntity(p.nodes[0]); });
      state.network.on("doubleClick", (p) => { if (p.nodes[0]) focusEntity(p.nodes[0]); });
      $("#g-note").textContent = `${fmt(nodes.length)} جهة · ${fmt(edges.length)} رابط` +
        (g.truncated ? " · (الشبكة كبيرة، عم نعرض الأهم بس)" : "") + " · كبسة وحدة = تفاصيل، كبستين = ركّز عليه";
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

  async function showEntity(id) {
    try {
      const r = await api(`/api/a/${state.id}/entity/${encodeURIComponent(id)}`);
      const e = r.entity;
      const list = (items, title) => items.length ? `<h3>${title}</h3><div class="table-wrap"><table class="data">
        ${items.map((x) => `<tr class="click" data-ent="${esc(x.id)}"><td>${esc(x.name)} <small class="muted">${TYPE_AR[x.type]}</small></td>
          <td class="num">${x.count}×</td><td class="num">${money(x.total)}</td>
          <td>${x.scam ? `<span class="tag scam">${x.scam} نصب</span>` : ""} ${x.fraud ? `<span class="tag fraud">${x.fraud} احتيال</span>` : ""}</td></tr>`).join("")}
        </table></div>` : "";
      $("#entity-panel").innerHTML = `
        <div class="entity-head"><div><h2>${esc(e.name)}</h2><small class="muted">${esc(e.id)} · ${TYPE_AR[e.type]}</small></div>
          <span class="tag ${e.risk_level}">خطورة ${LEVEL_AR[e.risk_level]} · ${fmt(e.risk_score * 100)}%</span></div>
        ${e.role ? `<div class="role">${esc(e.role)}</div>` : ""}
        <div class="stats">
          <div><small>دفع</small><b>${fmt(e.sent_count)} · ${money(e.sent_total)}</b></div>
          <div><small>استلم</small><b>${fmt(e.recv_count)} · ${money(e.recv_total)}</b></div>
          <div><small>عمليات مشبوهة</small><b>${fmt(e.flagged)}</b></div>
          <div><small>نصب مستلم</small><b>${fmt(e.scam_recv)}</b></div>
          <div><small>احتيال من حسابه</small><b>${fmt(e.fraud_sent)}</b></div>
          <div><small>عدد الجهات المرتبطة</small><b>${fmt(e.partners_out + e.partners_in)}</b></div>
        </div>
        <button class="btn small" id="ent-tx">شوف كل عملياته</button>
        ${list(r.received_from, "استلم من")}${list(r.paid_to, "دفع لـ")}`;
      $("#ent-tx").addEventListener("click", () => filterTxByEntity(e.id, e.name));
      if (window.innerWidth < 800) $("#entity-panel").scrollIntoView({ behavior: "smooth" });
    } catch (err) { toast(err.message); }
  }
  $("#entity-panel").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-ent]"); if (tr) focusEntity(tr.dataset.ent); });

  async function loadEntities() {
    if (!state.id) return;
    const p = new URLSearchParams({ type: $("#e-type").value, level: $("#e-level").value, limit: 300 });
    try {
      const r = await api(`/api/a/${state.id}/entities?${p}`);
      $("#e-table").innerHTML = `<tr><th>الاسم</th><th>النوع</th><th>الخطورة</th><th>مشبوهة</th><th>الدور</th></tr>` +
        r.rows.map((x) => `<tr class="click" data-ent="${esc(x.id)}"><td>${esc(x.name)}</td><td>${TYPE_AR[x.type]}</td>
          <td><span class="tag ${x.risk_level}">${LEVEL_AR[x.risk_level]} ${fmt(x.risk_score * 100)}%</span></td>
          <td class="num">${fmt(x.flagged)}</td><td class="reasons">${esc(x.role)}</td></tr>`).join("");
    } catch (e) { toast(e.message); }
  }
  ["#e-type", "#e-level"].forEach((s) => $(s).addEventListener("change", loadEntities));
  $("#e-table").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-ent]"); if (tr) focusEntity(tr.dataset.ent); });

  // ---------------------------------------------------------------- model --
  function metricsBlock(m) {
    const cells = [
      ["الدقة الكلية", pct(m.accuracy)],
      ["كشف المشبوه (Recall)", pct(m.suspicious_recall)],
      ["صحة الإنذار (Precision)", pct(m.suspicious_precision)],
      ["Macro F1", pct(m.macro_f1)],
    ].map(([l, v]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join("");
    const L = m.labels;
    const cm = `<table class="cm"><tr><th>حقيقي ↓ / متوقع ←</th>${L.map((l) => `<th>${LABEL_AR[l]}</th>`).join("")}</tr>` +
      m.confusion.map((row, i) => `<tr><th>${LABEL_AR[L[i]]}</th>${row.map((v, j) => `<td class="${i === j ? "diag" : ""}">${fmt(v)}</td>`).join("")}</tr>`).join("") + "</table>";
    return `<div class="metrics">${cells}</div>${cm}<small class="muted">على ${fmt(m.n)} عملية</small>`;
  }

  function renderModel() {
    const s = state.summary;
    const imp = s.importances.slice(0, 12);
    chart("ch-imp", {
      type: "bar",
      data: { labels: imp.map((x) => x.label), datasets: [{ data: imp.map((x) => x.importance), backgroundColor: "#4f8cff" }] },
      options: { indexAxis: "y", plugins: { legend: { display: false } }},
    });
    const src = s.model_source === "uploaded" ? "نموذج مدرّب على بياناتك" : "نموذج مدرّب على بيانات تجريبية";
    $("#model-info").innerHTML = `<h3>${src}</h3>
      <p class="muted">النموذج (Random Forest) بيتعلّم نمط كل نوع من العمليات: <b>عادية</b>، <b>احتيال</b> (حساب أو بطاقة مسروقة: صرف كبير بالليل من جهاز أو مدينة جديدة)، و<b>نصب</b> (مستلم جديد عم يجمع مبالغ مدوّرة من ناس كتير ما بيعرفوه). بعدين بيقارن كل عملية جديدة مع هالأنماط.</p>
      <h3>أداء النموذج على بيانات اختبار ما شافها وقت التدريب</h3>${metricsBlock(s.model_metrics)}`;
    $("#eval-card").innerHTML = s.evaluation
      ? `<h2>مقارنة النتايج مع التصنيف الموجود بملفك</h2>${metricsBlock(s.evaluation)}`
      : `<h2>مقارنة مع التصنيف الحقيقي</h2><p class="muted">ملفك ما فيه عمود تصنيف (label)، فما فينا نقيس دقة النتايج عليه.</p>`;
  }

  loadSchema().catch((e) => toast(e.message));
})();
