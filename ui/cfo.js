/* CFO Operating System - yeni gorunumler (mevcut app.js'e dokunmadan eklenir). */
(function () {
  const $ = (id) => document.getElementById(id);
  const t = (k, f) => (window.t ? window.t(k, f) : f || k);

  function fmtKurus(kurus) {
    const n = Number(kurus || 0) / 100;
    return "\u20ba" + Math.round(n).toLocaleString("tr-TR");
  }
  function fmtTL(tutar) {
    return "\u20ba" + Number(tutar || 0).toLocaleString("tr-TR", { maximumFractionDigits: 0 });
  }
  function fmtPct(v) {
    if (v === null || v === undefined || v === "") return "\u2014";
    const n = Number(v);
    if (!isFinite(n)) return "\u2014";
    return "%" + n.toFixed(1).replace(".", ",");
  }
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) node.setAttribute(k, v);
    }
    (Array.isArray(children) ? children : children ? [children] : []).forEach((c) => {
      if (c == null) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }
  async function api(url, options) {
    const res = await fetch(url, options);
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) throw new Error(data.hata || `İstek başarısız (${res.status})`);
    return data;
  }
  const faaliyet = () => ($("faaliyet") && $("faaliyet").value) || "tum";
  const qs = (extra) => new URLSearchParams(Object.assign({ faaliyet: faaliyet() }, extra || {})).toString();

  function badge(durum, metin) {
    return el("span", { class: `cfo-badge ${durum || "info"}`, text: metin || durum });
  }
  function skeleton() {
    const wrap = el("div", { class: "cfo-kpis" });
    for (let i = 0; i < 6; i++) wrap.appendChild(el("div", { class: "cfo-skeleton" }));
    return wrap;
  }
  /* Icerik ayri bir kutuya cizilir; yalniz EN SON gezinme hala gecerliyse yerine konur. Boylece
     iskelet (skeleton) kalmaz ve yavas bir onceki yukleme yeni sayfanin ustune yazamaz. */
  let yuklemeSirasi = 0;
  async function withLoad(container, loader) {
    const sira = ++yuklemeSirasi;
    container.replaceChildren(skeleton());
    const kutu = el("div", { class: "cfo-icerik" });
    try {
      await loader(kutu);
      if (sira === yuklemeSirasi) container.replaceChildren(...kutu.childNodes);
    } catch (err) {
      if (sira === yuklemeSirasi) container.replaceChildren(el("div", { class: "cfo-error", text: `${t("error")}: ${err.message}` }));
    }
  }

  /* --- Yeniden kullanilabilir tablolar icin ortak yardimcilar --- */
  function exportCsv(columns, rows) {
    const basliklar = columns.map((c) => c.label).join(";");
    const govde = rows.map((r) => columns.map((c) => {
      const v = c.format ? c.format(r) : (r[c.key] ?? "");
      return String(v).replace(/;/g, ",").replace(/\n/g, " ");
    }).join(";"));
    const blob = new Blob(["\ufeff" + [basliklar, ...govde].join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "cfo-rapor.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }
  function kpiCard(label, valueText, ctx, onclick, trend) {
    const card = el("div", { class: "cfo-kpi" });
    card.appendChild(el("div", { class: "label", text: label }));
    card.appendChild(el("div", { class: "value", text: valueText }));
    const ctxEl = el("div", { class: "ctx" });
    if (trend !== undefined && trend !== null && trend !== 0) {
      ctxEl.appendChild(el("span", { class: trend > 0 ? "cfo-trend-up" : "cfo-trend-down", text: (trend > 0 ? "▲ " : "▼ ") + fmtPct(trend) + " / 30 gün" }));
      ctxEl.appendChild(document.createTextNode(" · "));
    }
    ctxEl.appendChild(document.createTextNode(ctx || ""));
    card.appendChild(ctxEl);
    if (onclick) card.addEventListener("click", onclick);
    return card;
  }

  /* --- Veri tablosu: sorting + filter + search + column visibility + pagination + export --- */
  function dataTable(columns, rows, opts) {
    opts = opts || {};
    const state = { sortKey: opts.sortKey || null, asc: true, q: "", page: 1, per: opts.per || 15 };
    const wrap = el("div");
    const toolbar = el("div", { class: "cfo-toolbar" });
    const search = el("input", { type: "search", placeholder: "Ara…", "aria-label": "Tabloda ara" });
    search.addEventListener("input", () => { state.q = search.value.toLowerCase(); state.page = 1; draw(); });
    toolbar.appendChild(search);
    columns.forEach((c) => {
      const lbl = el("label", { class: "cfo-col-label" });
      const box = el("input", { type: "checkbox" });
      box.checked = !c.hidden;
      lbl.appendChild(box); lbl.appendChild(document.createTextNode(" " + c.label));
      box.addEventListener("change", () => { c.hidden = !box.checked; draw(); });
      toolbar.appendChild(lbl);
    });
    const exportBtn = el("button", { class: "ghost", type: "button", text: t("btn_export") });
    exportBtn.addEventListener("click", () => exportCsv(columns.filter((c) => !c.hidden), filterRows()));
    toolbar.appendChild(exportBtn);
    wrap.appendChild(toolbar);
    const tableWrap = el("div", { class: "cfo-table-wrap" });
    wrap.appendChild(tableWrap);
    const footer = el("div", { class: "cfo-toolbar" });
    wrap.appendChild(footer);

    function filterRows() {
      let out = rows.slice();
      if (state.q) out = out.filter((r) => columns.some((c) => String(r[c.key] ?? "").toLowerCase().includes(state.q)));
      if (state.sortKey) {
        const col = columns.find((c) => c.key === state.sortKey);
        out.sort((a, b) => {
          let x = a[state.sortKey], y = b[state.sortKey];
          if (col && col.num) { x = Number(x || 0); y = Number(y || 0); }
          else { x = String(x ?? ""); y = String(y ?? ""); }
          return (x > y ? 1 : x < y ? -1 : 0) * (state.asc ? 1 : -1);
        });
      }
      return out;
    }
    function draw() {
      const gorunur = columns.filter((c) => !c.hidden);
      const dizi = filterRows();
      const sayfaSayisi = Math.max(1, Math.ceil(dizi.length / state.per));
      state.page = Math.min(state.page, sayfaSayisi);
      const sayfa = dizi.slice((state.page - 1) * state.per, state.page * state.per);
      const thead = el("tr");
      gorunur.forEach((c) => {
        const th = el("th", { class: c.num ? "num" : "", text: c.label + (state.sortKey === c.key ? (state.asc ? " ▲" : " ▼") : "") });
        th.addEventListener("click", () => {
          if (state.sortKey === c.key) state.asc = !state.asc; else { state.sortKey = c.key; state.asc = true; }
          draw();
        });
        thead.appendChild(th);
      });
      const tbody = el("tbody");
      if (!sayfa.length) tbody.appendChild(el("tr", {}, el("td", { colspan: String(gorunur.length), text: opts.empty || t("empty") })));
      sayfa.forEach((r) => {
        const tr = el("tr");
        gorunur.forEach((c) => {
          const td = el("td", { class: c.num ? "num" : "" });
          if (c.badge) td.appendChild(badge(c.badge(r), c.badgeText ? c.badgeText(r) : null));
          else td.textContent = c.format ? c.format(r) : (r[c.key] ?? "");
          tr.appendChild(td);
        });
        if (opts.onRow) { tr.classList.add("cfo-row-link"); tr.addEventListener("click", () => opts.onRow(r)); }
        tbody.appendChild(tr);
      });
      tableWrap.replaceChildren(el("table", { class: "cfo-table" }, [el("thead", {}, thead), tbody]));
      footer.replaceChildren(
        el("span", { class: "cfo-sub", text: `${dizi.length} kayıt · sayfa ${state.page}/${sayfaSayisi}` }),
        el("button", { class: "ghost", type: "button", text: "‹", onclick: () => { state.page = Math.max(1, state.page - 1); draw(); } }),
        el("button", { class: "ghost", type: "button", text: "›", onclick: () => { state.page = Math.min(sayfaSayisi, state.page + 1); draw(); } }),
      );
    }
    draw();
    return wrap;
  }

  window.CFO = { $, t, fmtKurus, fmtTL, fmtPct, el, api, qs, faaliyet, badge, withLoad, exportCsv, kpiCard, dataTable };
})();

/* === Gorunumler, navigasyon ve baslatma === */
(function () {
  const C = window.CFO;
  const { $, t, el, api, qs, faaliyet, withLoad, dataTable, kpiCard, badge, fmtKurus, fmtTL, fmtPct } = C;
  const app = () => document.getElementById("cfoApp");
  const mainEl = () => document.querySelector("main");

  /* Ana alanlar: CFO Asistani -> Holding -> Faaliyetler -> Projeler -> Kayit / Islemler; digerleri mevcut gorunumler. */
  const VIEWS = [
    ["panel", "nav_asistan"], ["holding", "nav_holding"], ["aktivite", "nav_aktivite"],
    ["proje", "nav_proje"], ["islemler", "nav_islemler"], ["nakit", "nav_nakit"], ["alacak", "nav_alacak"],
    ["borc", "nav_borc"], ["butce", "nav_butce"], ["senaryo", "nav_senaryo"],
    ["belge", "nav_belge"], ["ai", "nav_ai"], ["rapor", "nav_rapor"], ["yonetim", "nav_yonetim"],
  ];
  /* Detay sayfalari menude yok; ust menu ogesini aktif gosterir. */
  const UST_MENU = { faaliyetdetay: "aktivite", projedetay: "proje" };
  let aktif = "panel";

  /* --- URL hash yonlendirme: #/holding  #/faaliyet/<key>  #/proje/<faaliyet>/<proje> --- */
  function hashOlustur(view, params) {
    params = params || [];
    const yol = { faaliyetdetay: "faaliyet", projedetay: "proje" }[view] || view;
    return "#/" + [yol, ...params.map((x) => encodeURIComponent(x))].join("/");
  }
  function hashCoz(h) {
    const parcalar = (h || "").replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
    if (!parcalar.length) return { view: "panel", params: [] };
    const [ad, ...params] = parcalar;
    if (ad === "faaliyet" && params.length) return { view: "faaliyetdetay", params };
    if (ad === "proje" && params.length >= 2) return { view: "projedetay", params };
    const bilinen = VIEWS.map((v) => v[0]);
    return { view: bilinen.includes(ad) ? ad : "panel", params: [] };
  }

  function renderNav() {
    const nav = $("cfoNav");
    if (!nav) return;
    const vurgu = UST_MENU[aktif] || aktif;
    nav.replaceChildren(...VIEWS.map(([id, key]) => {
      const btn = el("button", { type: "button", text: t(key), "data-view": id });
      btn.setAttribute("aria-current", id === vurgu ? "true" : "false");
      btn.addEventListener("click", () => git(id));
      return btn;
    }));
  }

  function header(baslik, alt) {
    return el("div", {}, [el("h2", { text: baslik }), el("p", { class: "cfo-sub", text: alt || t("money_note") })]);
  }

  let sonHash = null;
  function git(view, params) {
    const yeni = hashOlustur(view, params);
    if (location.hash === yeni) { render(view, params || []); return; }
    location.hash = yeni;                       // hashchange -> render
  }

  async function render(view, params) {
    aktif = view;
    sonHash = location.hash;
    renderNav();
    const a = app(), m = mainEl();
    if (view === "panel") {
      if (m) m.hidden = false;
      if (a) { a.hidden = true; a.replaceChildren(); }
      if (typeof window.loadDashboard === "function") window.loadDashboard();
      return;
    }
    if (m) m.hidden = true;
    if (!a) return;
    a.hidden = false;
    a.setAttribute("data-view", view);
    const cizici = RENDER[view];
    await withLoad(a, (root) => (cizici ? cizici(root, params || []) : root.appendChild(header(t("empty"), ""))));
    window.scrollTo(0, 0);
  }

  function hashtenCiz() {
    const { view, params } = hashCoz(location.hash);
    return render(view, params);
  }
  window.addEventListener("hashchange", () => { if (location.hash !== sonHash) hashtenCiz(); });

  /* Asistani acar ve (istege bagli) bir mesaj gonderir: "Bu projeye odeme ekle" gibi. */
  function asistanaSor(metin) {
    git("panel");
    if (metin && typeof window.ask === "function") setTimeout(() => window.ask(metin), 50);
  }

  function lineChart(points, esik) {
    const w = 900, h = 220, pad = 34;
    const canvas = el("canvas", { width: String(w), height: String(h), "aria-label": "Nakit projeksiyonu" });
    const ctx = canvas.getContext("2d");
    const degerler = points.map((p) => p.v);
    const min = Math.min(...degerler, esik), max = Math.max(...degerler, esik);
    const span = Math.max(1, max - min);
    const x = (i) => pad + (i * (w - 2 * pad)) / Math.max(1, points.length - 1);
    const y = (v) => h - pad - ((v - min) / span) * (h - 2 * pad);
    ctx.strokeStyle = "#d9e4dc";
    ctx.beginPath(); ctx.moveTo(pad, h - pad); ctx.lineTo(w - pad, h - pad); ctx.stroke();
    ctx.strokeStyle = "#b23a3a"; ctx.setLineDash([5, 4]); ctx.beginPath();
    ctx.moveTo(pad, y(esik)); ctx.lineTo(w - pad, y(esik)); ctx.stroke(); ctx.setLineDash([]);
    ctx.strokeStyle = "#0f5132"; ctx.lineWidth = 2; ctx.beginPath();
    points.forEach((p, i) => { const px = x(i), py = y(p.v); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
    ctx.stroke();
    ctx.fillStyle = "#5c7268"; ctx.font = "11px sans-serif";
    points.forEach((p, i) => { if (i % 2 === 0) ctx.fillText("H" + p.ad, x(i) - 6, h - pad + 14); });
    return canvas;
  }
  C.lineChart = lineChart;
  C.git = git;
  C.asistanaSor = asistanaSor;
  C.header = header;

  const RENDER = {};
  window.CFOshow = (view, params) => git(view, params);
  window.CFOregister = (id, fn) => { RENDER[id] = fn; };
  window.CFOinit = () => hashtenCiz();
  /* Gorunumler (asagidaki bloklar) ayni betikte sonradan kaydedilir; ilk cizim betik tamamen
     degerlendirildikten sonra yapilmalidir, yoksa derin baglanti (#/proje/...) bos sayfa cizer. */
  const baslat = () => setTimeout(hashtenCiz, 0);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", baslat);
  else baslat();
})();

/* === Gorunum tanimlari: Holding -> Faaliyet -> Proje (tiklanabilir, deterministik metrikler) === */
(function () {
  const C = window.CFO;
  const { t, el, api, qs, dataTable, kpiCard, fmtKurus, fmtPct } = C;
  const kayit = window.CFOregister;
  const DNA = "DATA_NOT_AVAILABLE";

  /* Metrik sozlugu {durum, metin, aciklama} -> KPI karti. Hesaplanamayan metrik uydurulmaz. */
  function metrikKarti(baslik, m, ek) {
    const yok = !m || m.durum === DNA;
    const kart = kpiCard(baslik, yok ? DNA : m.metin, (m && m.aciklama) || ek || "");
    kart.setAttribute("data-metrik", baslik);
    if (yok) kart.classList.add("cfo-kpi-yok");
    kart.classList.add("cfo-card-static");
    return kart;
  }
  function metrikIzgarasi(mt, sozlesme) {
    const g = el("div", { class: "cfo-kpis", "data-bolum": "metrikler" });
    if (sozlesme) g.appendChild(metrikKarti("Sözleşme / gelir değeri", mt.sozlesme_gelir_degeri, mt.sozlesme_gelir_degeri && mt.sozlesme_gelir_degeri.kaynak));
    g.append(
      metrikKarti("Kazanılan gelir", mt.kazanilan_gelir, "Hakediş/gelir tahakkuku · tahsilat değildir"),
      metrikKarti("Tahsil edilen", mt.tahsil_edilen, "Alacağı kapatan nakit girişi"),
      metrikKarti("Kalan alacak", mt.kalan_alacak, "Kazanılan − tahsil edilen"),
      metrikKarti("Gider (maliyet)", mt.gider, "Gider tahakkuku · ödeme değildir"),
      metrikKarti("Ödenen", mt.odenen, "Kasa/banka çıkışı · gider değildir"),
      metrikKarti("Açık borç", mt.acik_borc, "Gider − ödenen"),
      metrikKarti("Kâr / zarar", mt.kar_zarar, "Gelir − gider · nakit değildir"),
      metrikKarti("Kâr marjı", mt.kar_marji, mt.kar_marji && mt.kar_marji.durum === DNA ? "" : "Kâr / gelir"),
      metrikKarti("Net nakit", mt.net_nakit, "Tahsilat − ödeme · kâr değildir"),
    );
    return g;
  }
  function uyariKutusu(uyarilar) {
    const kutu = el("div", { "data-bolum": "uyarilar" });
    (uyarilar || []).forEach((u) => kutu.appendChild(el("div", { class: "cfo-insight WARNING" }, [el("div", { class: "d", text: u })])));
    return kutu;
  }
  function kirinti(...ogeler) {
    const nav = el("nav", { class: "cfo-breadcrumb", "aria-label": "Konum" });
    ogeler.forEach((o, i) => {
      if (i) nav.appendChild(document.createTextNode(" › "));
      nav.appendChild(o.git
        ? el("a", { href: o.git, text: o.metin, onclick: (e) => { e.preventDefault(); location.hash = o.git; } })
        : el("strong", { text: o.metin }));
    });
    return nav;
  }
  const hashFaaliyet = (key) => "#/faaliyet/" + encodeURIComponent(key);
  const hashProje = (key, proje) => "#/proje/" + encodeURIComponent(key) + "/" + encodeURIComponent(proje);
  const para = (k) => (k === null || k === undefined ? DNA : fmtKurus(k));

  function eylemSatiri(oneki, tur) {
    const bar = el("div", { class: "cfo-toolbar", "data-bolum": "eylemler" });
    tur.forEach(([etiket, cumle]) => bar.appendChild(el("button", { type: "button", class: "ghost", text: etiket,
      onclick: () => C.asistanaSor(`${oneki} ${cumle}`.trim()) })));
    return bar;
  }
  const cariTablosu = (rows) => dataTable([
    { key: "cari", label: "Cari" },
    { key: "odenen_kurus", label: "Ödenen", num: true, format: (r) => fmtKurus(r.odenen_kurus) },
    { key: "acik_borc_kurus", label: "Açık borç", num: true, format: (r) => fmtKurus(r.acik_borc_kurus) },
    { key: "tahsil_edilen_kurus", label: "Tahsil edilen", num: true, format: (r) => fmtKurus(r.tahsil_edilen_kurus) },
    { key: "kalan_alacak_kurus", label: "Kalan alacak", num: true, format: (r) => fmtKurus(r.kalan_alacak_kurus) },
  ], rows, { empty: "Bu kapsamda cari hareketi yok." });
  const islemTablosu = (rows) => dataTable([
    { key: "tarih", label: "Tarih" }, { key: "tur", label: "Tür" }, { key: "cari", label: "Cari" },
    { key: "proje", label: "Proje" }, { key: "tutar_kurus", label: t("th_tutar"), num: true, format: (r) => r.tutar_metin },
    { key: "aciklama", label: "Açıklama" },
  ], rows, { empty: "Henüz işlem yok.", per: 10 });

  /* ---------- HOLDING ---------- */
  kayit("holding", async (root) => {
    const [kpi, ozet] = await Promise.all([api("/api/kpi?" + qs()), api("/api/holding-ozeti")]);
    root.appendChild(el("h2", { text: "Holding Konsolidasyonu" }));
    root.appendChild(el("p", { class: "cfo-sub", text: t("money_note") + " · grup içi bakiyeler elimine edilmiştir" }));
    root.appendChild(uyariKutusu(ozet.uyarilar));
    const kpis = el("div", { class: "cfo-kpis", "data-bolum": "likidite" });
    kpis.append(
      kpiCard(t("kpi_nakit"), fmtKurus(kpi.kullanilabilir_nakit_kurus), "Toplam " + fmtKurus(kpi.toplam_nakit_kurus), null, kpi.nakit_trend_yuzde),
      kpiCard(t("kpi_korunacak"), fmtKurus(kpi.korunacak_nakit_kurus), kpi.rezerv_gun + " gün rezerv"),
      kpiCard(t("kpi_alacak"), fmtKurus(kpi.toplam_alacak_kurus), "Açık alacak"),
      kpiCard(t("kpi_borc"), fmtKurus(kpi.toplam_borc_kurus), "Toplam borç"),
      kpiCard(t("kpi_net_sermaye"), fmtKurus(kpi.net_isletme_sermayesi_kurus), "Nakit + alacak − borç"),
      kpiCard(t("kpi_brut_marj"), fmtPct(kpi.brut_marj_yuzde), fmtKurus(kpi.brut_kar_kurus) + " brüt kâr · " + kpi.marj_durum),
      kpiCard(t("kpi_yatirim"), fmtKurus(kpi.yatirim_kapasitesi_kurus), "Rezerv sonrası güvenli alan"),
      kpiCard(t("kpi_min13"), fmtKurus(kpi.min_13_hafta_nakit_kurus), "13 hafta en düşük kapanış"),
    );
    root.appendChild(kpis);
    root.appendChild(el("h3", { text: "Konsolide sonuç (defter kayıtlarından)" }));
    root.appendChild(metrikIzgarasi(ozet.konsolide, false));
    root.appendChild(el("h3", { text: "Faaliyetler · " + t("drilldown") }));
    root.appendChild(el("div", { "data-bolum": "faaliyetler" }, dataTable([
      { key: "faaliyet", label: "Faaliyet", format: (r) => r.faaliyet },
      { key: "gelir", label: "Kazanılan gelir", num: true, format: (r) => r.metrikler.kazanilan_gelir.metin },
      { key: "gider", label: "Gider", num: true, format: (r) => r.metrikler.gider.metin },
      { key: "kar", label: "Kâr / zarar", num: true, format: (r) => r.metrikler.kar_zarar.metin },
      { key: "tahsil", label: "Tahsil edilen", num: true, format: (r) => r.metrikler.tahsil_edilen.metin },
      { key: "odenen", label: "Ödenen", num: true, format: (r) => r.metrikler.odenen.metin },
      { key: "alacak", label: "Kalan alacak", num: true, format: (r) => r.metrikler.kalan_alacak.metin },
    ], ozet.faaliyetler.map((f) => ({ ...f, faaliyet: f.faaliyet })), { onRow: (r) => { location.hash = hashFaaliyet(r.faaliyet_key); }, sortKey: null })));
    root.appendChild(el("h3", { text: "Son işlemler" }));
    root.appendChild(islemTablosu(ozet.son_islemler));
  });

  /* ---------- FAALIYETLER (liste) ---------- */
  kayit("aktivite", async (root) => {
    const ozet = await api("/api/holding-ozeti");
    root.appendChild(el("h2", { text: "Faaliyetler" }));
    root.appendChild(el("p", { class: "cfo-sub", text: "Bir faaliyete tıklayın: gelir, gider, ödeme, tahsilat, alacak, borç ve projeleri açılır." }));
    root.appendChild(el("div", { "data-bolum": "faaliyetler" }, dataTable([
      { key: "faaliyet", label: "Faaliyet" },
      { key: "gelir", label: "Kazanılan gelir", num: true, format: (r) => r.metrikler.kazanilan_gelir.metin },
      { key: "gider", label: "Gider", num: true, format: (r) => r.metrikler.gider.metin },
      { key: "kar", label: "Kâr / zarar", num: true, format: (r) => r.metrikler.kar_zarar.metin },
      { key: "kayit_sayisi", label: "Kayıt", num: true },
    ], ozet.faaliyetler, { onRow: (r) => { location.hash = hashFaaliyet(r.faaliyet_key); } })));
  });

  /* ---------- FAALIYET DETAY ---------- */
  kayit("faaliyetdetay", async (root, params) => {
    const [key] = params;
    const d = await api("/api/faaliyet-detay?faaliyet=" + encodeURIComponent(key));
    root.appendChild(kirinti({ metin: "Holding", git: "#/holding" }, { metin: d.faaliyet }));
    root.appendChild(el("h2", { text: d.faaliyet, "data-baslik": "faaliyet" }));
    root.appendChild(el("p", { class: "cfo-sub", text: "Faaliyet özeti · " + t("money_note") }));
    root.appendChild(uyariKutusu(d.uyarilar));
    root.appendChild(metrikIzgarasi(d.metrikler, false));
    root.appendChild(eylemSatiri(d.faaliyet, [["Ödeme ekle", "için ödeme ekle"], ["Gider ekle", "için gider ekle"], ["Tahsilat ekle", "için tahsilat ekle"]]));
    root.appendChild(el("h3", { text: "Projeler · " + t("drilldown") }));
    root.appendChild(el("div", { "data-bolum": "projeler" }, dataTable([
      { key: "proje", label: "Proje" },
      { key: "gelir", label: "Kazanılan gelir", num: true, format: (r) => r.kazanilan_gelir.metin },
      { key: "gider", label: "Gider", num: true, format: (r) => r.gider.metin },
      { key: "kar", label: "Kâr / zarar", num: true, format: (r) => r.kar_zarar.metin },
      { key: "tahsil", label: "Tahsil edilen", num: true, format: (r) => r.tahsil_edilen.metin },
      { key: "odenen", label: "Ödenen", num: true, format: (r) => r.odenen.metin },
      { key: "alacak", label: "Kalan alacak", num: true, format: (r) => r.kalan_alacak.metin },
    ], d.projeler, { onRow: (r) => { location.hash = hashProje(d.faaliyet_key, r.proje); }, empty: "Bu faaliyette tanımlı proje yok." })));
    root.appendChild(el("h3", { text: "Cari bakiyeleri" }));
    root.appendChild(cariTablosu(d.cari_bakiyeleri));
    root.appendChild(el("h3", { text: "Son işlemler" }));
    root.appendChild(islemTablosu(d.son_islemler));
  });

  /* ---------- PROJELER (liste) ---------- */
  kayit("proje", async (root) => {
    const l = await api("/api/proje-listesi");
    root.appendChild(el("h2", { text: "Projeler" }));
    root.appendChild(el("p", { class: "cfo-sub", text: "Bir projeye tıklayın: finansal durumu, iş kalemleri ve hareketleri açılır." }));
    root.appendChild(el("div", { "data-bolum": "projeler" }, dataTable([
      { key: "proje", label: "Proje" }, { key: "faaliyet", label: "Faaliyet" },
      { key: "gelir", label: "Kazanılan gelir", num: true, format: (r) => r.kazanilan_gelir.metin },
      { key: "gider", label: "Gider", num: true, format: (r) => r.gider.metin },
      { key: "kar", label: "Kâr / zarar", num: true, format: (r) => r.kar_zarar.metin },
      { key: "tahsil", label: "Tahsil edilen", num: true, format: (r) => r.tahsil_edilen.metin },
      { key: "odenen", label: "Ödenen", num: true, format: (r) => r.odenen.metin },
      { key: "alacak", label: "Kalan alacak", num: true, format: (r) => r.kalan_alacak.metin },
    ], l.projeler, { onRow: (r) => { location.hash = hashProje(r.faaliyet_key, r.proje); }, empty: "Tanımlı proje yok." })));
  });

  /* ---------- PROJE DETAY ---------- */
  kayit("projedetay", async (root, params) => {
    const [key, proje] = params;
    const d = await api("/api/proje-detay?faaliyet=" + encodeURIComponent(key) + "&proje=" + encodeURIComponent(proje));
    root.appendChild(kirinti({ metin: "Holding", git: "#/holding" }, { metin: d.faaliyet, git: hashFaaliyet(d.faaliyet_key) }, { metin: d.proje }));
    root.appendChild(el("h2", { text: d.proje, "data-baslik": "proje" }));
    root.appendChild(el("p", { class: "cfo-sub", "data-faaliyet": d.faaliyet_key, text: "Faaliyet: " + d.faaliyet + " · " + t("money_note") }));
    root.appendChild(uyariKutusu(d.uyarilar));
    root.appendChild(metrikIzgarasi(d.metrikler, true));
    const tanim = el("details", { class: "cfo-tanim" }, [el("summary", { text: "Kavramlar" }),
      el("p", { class: "cfo-sub", text: Object.values(d.tanimlar).join(" ") })]);
    root.appendChild(tanim);
    root.appendChild(eylemSatiri(`${d.faaliyet} ${d.proje}`, [["Ödeme ekle", "için ödeme ekle"], ["Gider ekle", "için gider ekle"], ["Tahsilat ekle", "için tahsilat ekle"]]));
    const o = d.is_kalemi_ozeti;
    root.appendChild(el("h3", { text: `İş kalemleri · ${o.toplam} kalem · ${o.acik} açık · ${o.odemesi_tamamlanan} ödemesi tamamlanan` }));
    root.appendChild(el("p", { class: "cfo-sub", text: o.tanim }));
    root.appendChild(el("div", { "data-bolum": "is-kalemleri" }, dataTable([
      { key: "ad", label: t("th_kalem") }, { key: "grup", label: "Grup" },
      { key: "onay_durumu", label: "Onay" }, { key: "ilerleme", label: "Durum" },
      { key: "gelir_degeri_kurus", label: "Gelir değeri", num: true, format: (r) => fmtKurus(r.gelir_degeri_kurus) },
      { key: "maliyet_plani_kurus", label: "Planlanan maliyet", num: true, format: (r) => fmtKurus(r.maliyet_plani_kurus) },
      { key: "gerceklesen_gider_kurus", label: "Gerçekleşen gider", num: true, format: (r) => fmtKurus(r.gerceklesen_gider_kurus) },
      { key: "odenen_kurus", label: "Ödenen", num: true, format: (r) => fmtKurus(r.odenen_kurus) },
      { key: "kalan_odeme_kurus", label: "Kalan ödeme", num: true, format: (r) => fmtKurus(r.kalan_odeme_kurus) },
      { key: "butce_kalan_kurus", label: "Plan kalan", num: true, format: (r) => fmtKurus(r.butce_kalan_kurus) },
    ], d.is_kalemleri, { empty: "Bu projede tanımlı iş kalemi yok." })));
    root.appendChild(el("h3", { text: "Cari bakiyeleri" }));
    root.appendChild(cariTablosu(d.cari_bakiyeleri));
    root.appendChild(el("h3", { text: "Son işlemler" }));
    root.appendChild(el("div", { "data-bolum": "son-islemler" }, islemTablosu(d.son_islemler)));
  });

  /* ---------- KAYIT / ISLEMLER ---------- */
  kayit("islemler", async (root) => {
    const ozet = await api("/api/holding-ozeti");
    root.appendChild(el("h2", { text: "Kayıt / İşlemler" }));
    root.appendChild(el("p", { class: "cfo-sub", text: "Doğal dille yazın ya da adım adım ilerleyin. Excel'e yalnız şifreli onaydan sonra yazılır." }));
    const bar = el("div", { class: "cfo-toolbar", "data-bolum": "yeni-kayit" });
    [["Yeni ödeme", "yeni ödeme"], ["Yeni gider", "yeni gider"], ["Yeni tahsilat", "yeni tahsilat"],
     ["Yeni hakediş", "yeni hakediş"]].forEach(([etiket, mesaj]) =>
      bar.appendChild(el("button", { type: "button", text: etiket, onclick: () => C.asistanaSor(mesaj) })));
    bar.appendChild(el("button", { type: "button", class: "ghost", text: "CFO Asistanı'nı aç", onclick: () => C.asistanaSor("") }));
    root.appendChild(bar);
    root.appendChild(el("h3", { text: "Son işlemler (holding)" }));
    root.appendChild(islemTablosu(ozet.son_islemler));
  });
})();

/* === Proje / Nakit === */
(function () {
  const C = window.CFO;
  const { t, el, api, qs, dataTable, kpiCard, fmtKurus, lineChart } = C;
  const kayit = window.CFOregister;
  const marjBadge = (r) => r.marj_durum;
  const marjText = (r) => r.brut_marj_metin;

  kayit("nakit", async (root) => {
    const n = await api("/api/nakit/13hafta?" + qs());
    root.appendChild(el("h2", { text: "13 Haftalık Nakit Akışı" }));
    root.appendChild(el("p", { class: "cfo-sub", text: `Eşik ${n.esik_metin} · kapanış eşik altına düşerse risk` }));
    const kutu = el("div", { class: "cfo-kpis" });
    kutu.append(
      kpiCard("Başlangıç Nakit", fmtKurus(n.baslangic_nakit_kurus), "Bugün"),
      kpiCard("Minimum Kapanış", fmtKurus(n.min_kapanis_kurus), "Hafta " + n.min_hafta),
      kpiCard("Eşik", n.esik_metin, "Korunacak minimum"),
      kpiCard("Risk", n.risk ? "VAR" : "YOK", n.risk ? t("risk_risky") : t("risk_safe")),
    );
    root.appendChild(kutu);
    root.appendChild(el("div", { class: "cfo-scroll-x" }, lineChart(n.haftalar.map((h) => ({ ad: h.hafta_no, v: h.kapanis_kurus })), n.esik_kurus)));
    root.appendChild(el("h3", { text: "Haftalık detay" }));
    root.appendChild(dataTable([
      { key: "hafta_no", label: t("th_hafta"), num: true },
      { key: "acilis_kurus", label: "Açılış", num: true, format: (r) => fmtKurus(r.acilis_kurus) },
      { key: "tahsilat_kurus", label: "Tahsilat", num: true, format: (r) => fmtKurus(r.tahsilat_kurus) },
      { key: "tedarikci_kurus", label: "Tedarikçi", num: true, format: (r) => fmtKurus(r.tedarikci_kurus) },
      { key: "personel_kurus", label: "Personel", num: true, format: (r) => fmtKurus(r.personel_kurus) },
      { key: "vergi_kurus", label: "Vergi", num: true, format: (r) => fmtKurus(r.vergi_kurus) },
      { key: "kapanis_kurus", label: t("th_kapanis"), num: true, badge: (r) => r.esik_alti ? "danger" : "healthy", badgeText: (r) => r.kapanis_metin },
    ], n.haftalar));
  });
})();

/* === Alacak / Borç / Bütçe === */
(function () {
  const C = window.CFO;
  const { t, el, api, qs, faaliyet, dataTable, kpiCard, badge, fmtKurus, fmtPct } = C;
  const kayit = window.CFOregister;
  const kova = (r) => `0-30: ${fmtTL0(r["1_30"])} · 31-60: ${fmtTL0(r["31_60"])} · 61-90: ${fmtTL0(r["61_90"])} · 90+: ${fmtTL0(r["90_ustu"])}`;
  function fmtTL0(v) { return "\u20ba" + Number(v || 0).toLocaleString("tr-TR", { maximumFractionDigits: 0 }); }
  const agingCols = [
    { key: "cari", label: "Cari" },
    { key: "faaliyet", label: "Şirket" },
    { key: "vadesi_gelmemis", label: "Vadesi gelmemiş", num: true, format: (r) => fmtTL0(r.vadesi_gelmemis) },
    { key: "1_30", label: "0-30", num: true, format: (r) => fmtTL0(r["1_30"]) },
    { key: "31_60", label: "31-60", num: true, format: (r) => fmtTL0(r["31_60"]) },
    { key: "61_90", label: "61-90", num: true, format: (r) => fmtTL0(r["61_90"]) },
    { key: "90_ustu", label: "90+", num: true, format: (r) => fmtTL0(r["90_ustu"]) },
    { key: "toplam", label: t("th_tutar"), num: true, format: (r) => r.toplam_metin },
    { key: "gecikmis", label: t("th_durum"), badge: (r) => (r.gecikmis ? "danger" : "healthy"), badgeText: (r) => (r.gecikmis ? "Gecikmiş" : "Normal") },
  ];

  kayit("alacak", async (root) => {
    const a = await api("/api/ticari/alacak?" + qs());
    root.appendChild(el("h2", { text: "Alacaklar (AR) Yaşlandırma" }));
    const k = el("div", { class: "cfo-kpis" });
    k.append(
      kpiCard("Toplam Alacak", "\u20ba" + a.toplam.toLocaleString("tr-TR", { maximumFractionDigits: 0 }), "Açık kalem"),
      kpiCard("Gecikmiş", "\u20ba" + a.gecikmis.toLocaleString("tr-TR", { maximumFractionDigits: 0 }), fmtPct(a.gecikmis_oran) + " gecikme oranı"),
    );
    root.appendChild(k);
    root.appendChild(el("div", { class: "cfo-toolbar" }, Object.entries(a.kovalar).map(([k2, v]) => badge("info", `${k2}: \u20ba${Number(v).toLocaleString("tr-TR", { maximumFractionDigits: 0 })}`))));
    root.appendChild(dataTable(agingCols, a.kalemler, { sortKey: "toplam" }));
  });

  kayit("borc", async (root) => {
    const [b, yak, kpi] = await Promise.all([
      api("/api/ticari/borc?" + qs()),
      api("/api/ticari/yaklasan?" + qs({ gun: 30 })),
      api("/api/kpi?" + qs()),
    ]);
    const yakToplamKurus = yak.odemeler.reduce((toplam, x) => toplam + Math.round(Number(x.tutar || 0) * 100), 0);
    root.appendChild(el("h2", { text: "Borçlar (AP) ve Yaklaşan Ödemeler" }));
    const k = el("div", { class: "cfo-kpis" });
    k.append(
      kpiCard("Toplam Borç", fmtKurus(kpi.toplam_borc_kurus), "Finansal + ticari"),
      kpiCard("Açık Ticari Borç", "\u20ba" + b.toplam.toLocaleString("tr-TR", { maximumFractionDigits: 0 }), "Ödemeler düşülmüş bakiye"),
      kpiCard("Gecikmiş", "\u20ba" + b.gecikmis.toLocaleString("tr-TR", { maximumFractionDigits: 0 }), fmtPct(b.gecikmis_oran) + " gecikme"),
      kpiCard("30 Gün İçinde", fmtKurus(yakToplamKurus), String(yak.odemeler.length) + " ödeme"),
    );
    root.appendChild(k);
    root.appendChild(el("h3", { text: "Yaklaşan ödemeler" }));
    root.appendChild(dataTable([
      { key: "cari", label: "Cari" }, { key: "proje", label: "Proje" }, { key: "vade", label: t("th_vade") },
      { key: "kalan_gun", label: t("th_kalan"), num: true },
      { key: "tutar", label: t("th_tutar"), num: true, format: (r) => r.tutar_metin },
      { key: "gecikmis", label: t("th_durum"), badge: (r) => (r.gecikmis ? "danger" : "warning"), badgeText: (r) => (r.gecikmis ? "Gecikmiş" : "Planlı") },
    ], yak.odemeler));
    root.appendChild(el("h3", { text: "Ticari borç yaşlandırması · gerçekleşen ödemeler düşülmüştür" }));
    root.appendChild(dataTable(agingCols, b.kalemler, { sortKey: "toplam" }));
  });

  kayit("butce", async (root) => {
    const b = await api("/api/butce/gerceklesen?" + qs());
    const ta = await api("/api/taahhut?" + qs());
    root.appendChild(el("h2", { text: "Bütçe & Forecast (Budget vs Actual)" }));
    const k = el("div", { class: "cfo-kpis" });
    k.append(
      kpiCard("Bütçe", "\u20ba" + b.toplam_butce.toLocaleString("tr-TR", { maximumFractionDigits: 0 }), "Dönem"),
      kpiCard("Gerçekleşen", "\u20ba" + b.toplam_gerceklesen.toLocaleString("tr-TR", { maximumFractionDigits: 0 }), "Dönem"),
      kpiCard("Olumsuz Kalem", String(b.olumsuz_kalem), "Sapma uyarısı"),
      kpiCard("Taahhüt", ta.metin.taahhut, "İş kalemleri"),
    );
    root.appendChild(k);
    root.appendChild(dataTable([
      { key: "faaliyet", label: "Şirket" }, { key: "proje", label: "Proje" }, { key: "ad", label: "Hesap" },
      { key: "butce", label: t("th_butce"), num: true, format: (r) => r.butce_metin },
      { key: "gerceklesen", label: t("th_gerceklesen"), num: true, format: (r) => r.gerceklesen_metin },
      { key: "eac", label: t("th_tahmin"), num: true, format: (r) => r.eac_metin },
      { key: "sapma", label: "Sapma", num: true, format: (r) => r.sapma_metin },
      { key: "sapma_orani", label: "Sapma %", num: true, format: (r) => r.sapma_orani_metin },
      { key: "risk", label: t("th_durum"), badge: (r) => r.risk, badgeText: (r) => r.durum },
    ], b.kalemler));
  });
})();

/* === Senaryo Builder === */
(function () {
  const C = window.CFO;
  const { t, el, api, faaliyet, dataTable, badge, fmtKurus } = C;
  const kayit = window.CFOregister;
  const DEGISKENLER = [
    ["ek_tahsilat", "Ek tahsilat (TL)"], ["tahsilat_gecikmesi", "Tahsilat gecikmesi (gün)"],
    ["ek_odeme", "Ek ödeme (TL)"], ["odeme_gecikmesi", "Ödeme gecikmesi (gün)"],
    ["yeni_yatirim", "Yeni yatırım (TL)"], ["yeni_kredi", "Yeni kredi (TL)"],
    ["faiz_maliyeti", "Faiz maliyeti (TL/ay)"], ["kur_degismesi", "Kur değişimi (%)"],
    ["gelir_degismesi", "Gelir değişimi (%)"], ["maliyet_artisi", "Maliyet artışı (%)"],
    ["proje_gecikmesi", "Proje gecikmesi (hafta)"],
  ];
  const seviyeBadge = { "Guvenli": "healthy", "Sinirda": "warning", "Riskli": "danger", "Uygulanamaz": "danger" };
  const seviyeText = { "Guvenli": t("risk_safe"), "Sinirda": t("risk_border"), "Riskli": t("risk_risky"), "Uygulanamaz": t("risk_impossible") };

  kayit("senaryo", async (root) => {
    root.appendChild(el("h2", { text: "Senaryo Builder" }));
    root.appendChild(el("p", { class: "cfo-sub", text: "Karardan önce etkisini gör · sonuç 4 seviyeli ve gerekçeli" }));
    const inputs = {};
    const grid = el("div", { class: "cfo-scenario-grid" });
    DEGISKENLER.forEach(([key, label]) => {
      const lab = el("label", { text: label });
      const inp = el("input", { type: "number", step: "0.01", value: "0" });
      inputs[key] = inp; lab.appendChild(inp); grid.appendChild(lab);
    });
    const tipSel = el("select");
    [["BASE", "Base Case"], ["BEST", "Best Case"], ["WORST", "Worst Case"], ["MGMT", "Management Plan"]]
      .forEach(([v, l]) => tipSel.appendChild(el("option", { value: v, text: l })));
    const adInput = el("input", { type: "text", placeholder: "Senaryo adı", maxlength: "120" });
    const card = el("div", { class: "cfo-card" });
    card.appendChild(grid);
    const actions = el("div", { class: "cfo-toolbar" });
    const btnHesapla = el("button", { type: "button", text: t("btn_hesapla") });
    const btnKaydet = el("button", { class: "ghost", type: "button", text: t("btn_kaydet") });
    actions.append(btnHesapla, tipSel, adInput, btnKaydet);
    card.appendChild(actions);
    const sonuc = el("div");
    card.appendChild(sonuc);
    root.appendChild(card);

    const topla = () => Object.fromEntries(Object.entries(inputs).map(([k, e2]) => [k, Number(e2.value || 0)]));
    function goster(s) {
      const box = el("div", { class: "cfo-card" });
      box.appendChild(el("div", { class: "cfo-toolbar" }, [badge(seviyeBadge[s.seviye] || "info", seviyeText[s.seviye] || s.seviye)]));
      const rows = el("div");
      s.adimlar.forEach((a) => rows.appendChild(el("div", { class: "row" }, [el("span", { class: "ops", text: a.isaret + " " + a.etiket }), el("span", { text: a.metin })])));
      rows.appendChild(el("div", { class: "row" }, [el("span", { text: "= Senaryo nakdi" }), el("strong", { text: s.metin.senaryo_nakit })]));
      rows.appendChild(el("div", { class: "row" }, [el("span", { text: "Kullanılabilir nakit" }), el("strong", { text: s.metin.kullanilabilir })]));
      box.appendChild(rows);
      sonuc.replaceChildren(box);
    }
    btnHesapla.addEventListener("click", async () => {
      try { goster(await api("/api/senaryo/motor", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ faaliyet: faaliyet(), degisiklikler: topla() }) })); }
      catch (e) { sonuc.replaceChildren(el("div", { class: "cfo-error", text: e.message })); }
    });
    const kayitlar = el("div");
    async function yukleKayitlar() {
      const d = await api("/api/senaryolar");
      kayitlar.replaceChildren(el("h3", { text: "Kayıtlı senaryolar" }), dataTable([
        { key: "ad", label: "Ad" }, { key: "tip", label: "Tip" },
        { key: "seviye", label: "Sonuç", badge: (r) => seviyeBadge[r.seviye] || "info", badgeText: (r) => seviyeText[r.seviye] || r.seviye },
        { key: "senaryo_nakit_kurus", label: "Senaryo Nakit", num: true, format: (r) => fmtKurus(r.senaryo_nakit_kurus) },
        { key: "kullanilabilir_nakit_kurus", label: "Kullanılabilir", num: true, format: (r) => fmtKurus(r.kullanilabilir_nakit_kurus) },
        { key: "olusturma", label: "Tarih" },
      ], d.senaryolar));
    }
    btnKaydet.addEventListener("click", async () => {
      try {
        await api("/api/senaryo/kaydet", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ad: adInput.value || "Senaryo", tip: tipSel.value, faaliyet: faaliyet(), degisiklikler: topla() }) });
        await yukleKayitlar();
      } catch (e) { sonuc.replaceChildren(el("div", { class: "cfo-error", text: e.message })); }
    });
    await yukleKayitlar();
    root.appendChild(kayitlar);
  });
})();

/* === Belge / AI CFO === */
(function () {
  const C = window.CFO;
  const { t, el, api, faaliyet, badge } = C;
  const kayit = window.CFOregister;

  kayit("belge", async (root) => {
    root.appendChild(el("h2", { text: "Belge Otomasyonu" }));
    root.appendChild(el("p", { class: "cfo-sub", text: "UPLOAD → PARSE → OCR → EXTRACTION → SINIFLANDIRMA → DUPLICATE → MATCHING → REVIEW → APPROVAL → POSTING" }));
    const card = el("div", { class: "cfo-card" });
    const taslak = el("textarea", { rows: "4", placeholder: "Belge metni (fatura/dekont metni)…", class: "cfo-input" });
    const dosyaAdi = el("input", { type: "text", placeholder: "Dosya adı (örn: fatura.pdf)", value: "fatura.pdf" });
    const proje = el("input", { type: "text", placeholder: "Proje (opsiyonel)" });
    const btn = el("button", { type: "button", text: "Hattı çalıştır" });
    const out = el("div");
    card.append(taslak, el("div", { class: "cfo-toolbar" }, [dosyaAdi, proje, btn]), out);
    root.appendChild(card);
    btn.addEventListener("click", async () => {
      try {
        const s = await api("/api/belge/pipeline", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ metin: taslak.value, dosya_adi: dosyaAdi.value, firma: faaliyet() === "tum" ? null : faaliyet(), proje: proje.value }) });
        const steps = el("div", { class: "cfo-toolbar" }, s.adimlar.map((a) => badge(a.durum === "PASS" ? "healthy" : a.durum === "FAIL" ? "danger" : a.durum === "ATLANDI" ? "info" : "warning", a.adim)));
        const info = el("div", { class: "cfo-card" });
        info.append(
          el("div", { class: "row" }, [el("span", { text: "Tür" }), el("strong", { text: s.tur_etiket })]),
          el("div", { class: "row" }, [el("span", { text: "Güven" }), el("strong", { text: s.guven + "%" })]),
          el("div", { class: "row" }, [el("span", { text: "Fatura No" }), el("span", { text: s.alanlar.fatura_no || "—" })]),
          el("div", { class: "row" }, [el("span", { text: "Tarih" }), el("span", { text: s.alanlar.tarih || "—" })]),
          el("div", { class: "row" }, [el("span", { text: "Toplam" }), el("span", { text: s.alanlar.toplam ?? "—" })]),
          el("div", { class: "row" }, [el("span", { text: "Mükerrer" }), badge(s.mukerrer.mukerrer ? "danger" : "healthy", s.mukerrer.mukerrer ? "Aynı belge mevcut" : "Yeni belge")]),
        );
        if (s.oneri) info.appendChild(el("div", { class: "cfo-insight INFO" }, [el("div", { class: "t", text: "AI önerisi (" + s.oneri.guven + "% confidence)" }), el("div", { class: "d", text: s.oneri.mesaj })]));
        out.replaceChildren(steps, info);
      } catch (e) { out.replaceChildren(el("div", { class: "cfo-error", text: e.message })); }
    });
  });

  kayit("ai", async (root) => {
    const ins = await api("/api/ai-cfo/insights?" + new URLSearchParams({ faaliyet: faaliyet() }));
    const bag = await api("/api/ai-cfo/baglam?" + new URLSearchParams({ faaliyet: faaliyet() }));
    root.appendChild(el("h2", { text: "AI CFO" }));
    root.appendChild(el("p", { class: "cfo-sub", text: t("ai_data_through") + ": " + bag.veri_tarihi + " · sayılar merkezi finans motorundan" }));
    root.appendChild(el("h3", { text: t("insight_title") }));
    const liste = el("div");
    ins.insights.forEach((i) => liste.appendChild(el("div", { class: `cfo-insight ${i.severity}` }, [
      el("div", { class: "t", text: i.title }), el("div", { class: "d", text: i.description }),
      i.oneri ? el("div", { class: "a", text: "Öneri: " + i.oneri }) : null,
    ])));
    root.appendChild(liste);
    const card = el("div", { class: "cfo-card" });
    const soru = el("input", { type: "text", placeholder: t("ai_placeholder"), class: "cfo-input" });
    const btn = el("button", { type: "button", text: t("btn_sor") });
    const cevap = el("div", { class: "cfo-mt" });
    card.append(soru, el("div", { class: "cfo-toolbar" }, [btn]), cevap);
    root.appendChild(card);
    const sor = async () => {
      if (!soru.value.trim()) return;
      try {
        const c = await api("/api/ai-cfo/sor", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ soru: soru.value, faaliyet: faaliyet() }) });
        const blok = el("div", { class: "cfo-insight INFO" });
        blok.append(el("div", { class: "t", text: c.cevap }), el("div", { class: "d", text: t("ai_data_through") + ": " + c.veri_tarihi }));
        if (c.kanitlar && c.kanitlar.length) blok.appendChild(el("div", { class: "d", text: "Kanıt: " + c.kanitlar.join(" · ") }));
        if (c.oneri) blok.appendChild(el("div", { class: "a", text: "Öneri: " + c.oneri }));
        cevap.replaceChildren(blok);
      } catch (e) { cevap.replaceChildren(el("div", { class: "cfo-error", text: e.message })); }
    };
    btn.addEventListener("click", sor);
    soru.addEventListener("keydown", (e) => { if (e.key === "Enter") sor(); });
  });
})();

/* === Raporlar / Yönetim === */
(function () {
  const C = window.CFO;
  const { t, el, api, faaliyet, dataTable, badge } = C;
  const kayit = window.CFOregister;

  kayit("rapor", async (root) => {
    const liste = await api("/api/raporlar");
    root.appendChild(el("h2", { text: "Raporlar" }));
    root.appendChild(el("p", { class: "cfo-sub", text: "Muhasebe eşlemesi yeterliyse Mizan/P&L/Bilanço; aksi halde 'requires accounting mapping'." }));
    const bar = el("div", { class: "cfo-toolbar" });
    const cikti = el("div");
    liste.raporlar.forEach((r) => {
      const b = el("button", { class: "ghost", type: "button", text: r.baslik });
      b.addEventListener("click", async () => {
        try {
          const d = await api("/api/rapor?ad=" + encodeURIComponent(r.ad) + "&" + new URLSearchParams({ faaliyet: faaliyet() }));
          cikti.replaceChildren(renderRapor(d));
        } catch (e) { cikti.replaceChildren(el("div", { class: "cfo-error", text: e.message })); }
      });
      bar.appendChild(b);
    });
    root.append(bar, cikti);
  });

  function renderRapor(d) {
    const box = el("div");
    box.appendChild(el("h3", { text: (d.tip || "").replace(/-/g, " ") }));
    if (d.kaynak) box.appendChild(el("p", { class: "cfo-sub", text: "Kaynak: " + d.kaynak + (d.denk === false ? " · ⚠ requires accounting mapping" : "") }));
    if (Array.isArray(d.kalemler)) {
      const keys = Object.keys(d.kalemler[0] || {});
      box.appendChild(dataTable(keys.map((k) => ({ key: k, label: k, num: typeof (d.kalemler[0] || {})[k] === "number" })), d.kalemler));
      return box;
    }
    if (d.kpi) {
      const k = el("div", { class: "cfo-kpis" });
      Object.entries(d.kpi.metin || {}).forEach(([kk, vv]) => k.appendChild(el("div", { class: "cfo-kpi" }, [el("div", { class: "label", text: kk }), el("div", { class: "value", text: vv })])));
      box.appendChild(k);
    }
    const diger = Object.entries(d).filter(([k, v]) => k !== "tip" && k !== "kalemler" && k !== "kpi" && (typeof v !== "object" || v === null));
    const rows = el("div", { class: "cfo-card" });
    diger.forEach(([k, v]) => rows.appendChild(el("div", { class: "row" }, [el("span", { text: k }), el("span", { text: String(v) })])));
    box.appendChild(rows);
    return box;
  }

  kayit("yonetim", async (root) => {
    const roller = await api("/api/yetki/roller");
    const audit = await api("/api/audit?limit=50").catch(() => ({ kayitlar: [] }));
    root.appendChild(el("h2", { text: "Yönetim · Roller & Denetim" }));
    root.appendChild(el("p", { class: "cfo-sub", text: "Aktif rol: " + roller.aktif_rol + " · UI gizleme yetkilendirme yerine geçmez; sunucu tarafında uygulanır." }));
    root.appendChild(el("h3", { text: "Roller ve izinler" }));
    root.appendChild(dataTable([
      { key: "rol", label: "Rol" },
      { key: "izinler", label: "İzinler", format: (r) => r.izinler.join(", ") },
    ], roller.roller));
    root.appendChild(el("h3", { text: "Audit log" }));
    root.appendChild(dataTable([
      { key: "zaman", label: "Zaman" }, { key: "aktor", label: "Aktör" }, { key: "eylem", label: "Eylem" },
      { key: "varlik_tipi", label: "Varlık" }, { key: "varlik_id", label: "Kimlik" },
    ], audit.kayitlar));
    root.appendChild(el("h3", { text: "İleri yönetim araçları (mevcut)" }));
    const bar = el("div", { class: "cfo-toolbar" });
    [["transferButton", "Grup içi transfer"], ["fxButton", "Kur değerleme"], ["periodButton", "Dönem kontrolü"], ["governanceButton", "Yönetim ayarları"]].forEach(([id2, lbl]) => {
      const b = el("button", { class: "ghost", type: "button", text: lbl });
      b.addEventListener("click", () => { const hedef = document.getElementById(id2); if (hedef) hedef.click(); });
      bar.appendChild(b);
    });
    root.appendChild(bar);
  });
})();








