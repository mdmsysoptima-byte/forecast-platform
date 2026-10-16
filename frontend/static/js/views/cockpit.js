// Kokpit: agrege konsensüs tahmini ve katmanların yan yana karşılaştırması.
// Operasyonel listeler kendi ekranlarında yaşar: uyarılar Planlama > İstisnalar,
// çalıştırmalar Senaryo, onaylar Versiyon, doğruluk ayrıntısı Analiz.

import { api } from "../api.js";
import * as charts from "../charts.js";
import {
  card, clear, el, empty, fmt, loading, pageHeader, segmented, select, table,
} from "../ui.js";

const UNIT_LABEL = { TRY: "TL", USD: "USD", EUR: "EUR" };

export async function render({ state }) {
  const cfg = state.cockpit;
  const wrap = el("div", {});
  const headerHost = el("div", {});
  const filterHost = el("div", { class: "mb-3" });
  const body = el("div", {});
  wrap.append(headerHost, filterHost, body);

  async function load() {
    clear(body).append(loading("Agrege tahmin hesaplanıyor"));
    let data;
    try {
      data = await api.cockpitOverview({
        version_id: cfg.versionId, measure: cfg.measure, grain: cfg.grain,
        window_mode: cfg.windowMode,
        horizon_periods: cfg.horizon, history_periods: cfg.history,
        start_label: cfg.startLabel, end_label: cfg.endLabel,
        scope: cfg.scope, view: cfg.view, currency: cfg.currency,
      });
    } catch (err) {
      clear(body).append(empty(err.message, "exclamation-triangle"));
      return;
    }
    if (!cfg.versionId) cfg.versionId = data.version.id;
    // Tarih moduna ilk geçişte uçlar mevcut pencereden devralınır, boş açılmaz.
    if (!cfg.startLabel) cfg.startLabel = data.meta.start_label;
    if (!cfg.endLabel) cfg.endLabel = data.meta.end_label;

    clear(headerHost).append(pageHeader("Kokpit"));
    paintFilters(data);
    clear(body);
    // Tablo önce: ekranın cevabı sayılarda, grafik o sayıların seyri.
    body.append(modelTable(data), heroCard(data), bottomRow(data));
  }

  function paintFilters(data) {
    const o = data.options;
    const measure = o.measures.find((m) => m.code === cfg.measure);
    const isValue = measure?.unit_type === "value";
    const isRange = cfg.windowMode === "range";

    // Dönem modunda uçlar "kaç dönem geriye/ileriye", tarih modunda doğrudan
    // tarihtir; etiketler de buna göre değişmeli.
    const windowFields = isRange
      ? [
        ["Başlangıç", periodSelect(o.periods, cfg.startLabel, (v) => { cfg.startLabel = v; load(); })],
        ["Bitiş", periodSelect(o.periods, cfg.endLabel, (v) => { cfg.endLabel = v; load(); })],
      ]
      : [
        ["Geçmiş", segmented("ck-start",
          o.histories.map((h) => ({ value: h, label: `${h} dönem` })),
          cfg.history, (v) => { cfg.history = Number(v); load(); })],
        ["Gelecek", segmented("ck-end",
          o.horizons.map((h) => ({ value: h, label: `${h} dönem` })),
          cfg.horizon, (v) => { cfg.horizon = Number(v); load(); })],
      ];

    const measureFields = [
      ["Ölçü", segmented("ck-measure",
        o.measures.map((m) => ({ value: m.code, label: m.name })),
        cfg.measure, (v) => { cfg.measure = v; load(); })],
      ["Kova", segmented("ck-grain",
        o.grains.map((g) => ({ value: g.code, label: g.label })),
        cfg.grain, (v) => { cfg.grain = v; load(); })],
    ];
    if (isValue) {
      measureFields.push(["Görünüm", select(
        o.views.map((v) => ({ value: v.code, label: v.label })),
        cfg.view, (v) => { cfg.view = v; load(); },
      )]);
      if (cfg.view === "fx") {
        measureFields.push(["Para birimi", select(
          o.currencies.map((c) => ({ value: c, label: c })),
          cfg.currency, (v) => { cfg.currency = v; load(); },
        )]);
      }
    }

    // Yan yana sekiz seçici karışık okunuyordu; ilgili olanlar kendi çerçevesinde.
    const groups = [
      ["Dönem", [
        ["Pencere", segmented("ck-wmode",
          o.window_modes.map((m) => ({ value: m.code, label: m.label })),
          cfg.windowMode, (v) => { cfg.windowMode = v; load(); })],
        ...windowFields,
      ]],
      ["Kapsam", [
        ["Boyut", dimensionSelect(o, cfg, load)],
        ["Alt kapsam", nodeSelect(o, cfg, load)],
      ]],
      ["Model", [
        ["Versiyon", select(
          o.versions.map((v) => ({ value: v.id, label: v.name })),
          data.version.id, (v) => { cfg.versionId = Number(v); load(); }, { style: "min-width:190px" },
        )],
      ]],
      ["Gösterim", measureFields],
    ];

    clear(filterHost).append(el("div", { class: "d-flex flex-wrap gap-3 align-items-start" },
      groups.map(([title, fields]) => el("fieldset", { class: "filter-group" }, [
        el("legend", { class: "filter-group-title", text: title }),
        el("div", { class: "d-flex flex-wrap align-items-end gap-3" },
          fields.map(([label, control]) => el("div", { class: "d-flex flex-column" }, [
            el("label", { class: "form-label mb-1 small text-muted", text: label }),
            control,
          ]))),
      ]))));
  }

  await load();
  return wrap;
}

function periodSelect(periods, value, onChange) {
  return select((periods || []).map((p) => ({ value: p.label, label: p.label })),
    value, onChange, { style: "min-width:120px" });
}

/** Kapsam: hangi boyut. Alt kapsam onun düğümlerinden seçilir. */
function dimensionSelect(options, cfg, reload) {
  const current = Object.keys(cfg.scope || {})[0] || "";
  return select(
    [{ value: "", label: "Tüm kapsam" },
      ...options.scopes.map((s) => ({ value: s.dimension, label: s.name }))],
    current,
    (v) => {
      cfg.scope = v ? { [v]: { nodes: [] } } : {};
      reload();
    },
    { style: "min-width:140px" },
  );
}

function nodeSelect(options, cfg, reload) {
  const dim = Object.keys(cfg.scope || {})[0] || "";
  const scope = options.scopes.find((s) => s.dimension === dim);
  const nodes = scope?.nodes || [];
  const current = cfg.scope?.[dim]?.nodes?.[0] ?? "";
  const sel = select(
    [{ value: "", label: nodes.length ? "Tümü" : "—" },
      ...nodes.map((n) => ({ value: n.node_id, label: n.label }))],
    current,
    (v) => {
      cfg.scope = v ? { [dim]: { nodes: [Number(v)] } } : (dim ? { [dim]: { nodes: [] } } : {});
      reload();
    },
    { style: "min-width:160px" },
  );
  sel.disabled = !nodes.length;
  return sel;
}

function unitLabel(meta) {
  if (meta.unit_type !== "value") return meta.unit;
  return meta.view === "fx" ? meta.currency : "TL";
}

/* Her blok hangi filtrelerle çizildiğini kendi başlığında söyler. Filtre
   değişince sayılar yeniden ölçekleniyor ama şekil benzer kaldığı için
   ekranın tepki verdiği başka türlü anlaşılmıyor. */
function filterNote(data) {
  const m = data.meta;
  const viewLabel = m.unit_type === "value"
    ? ({ nominal: "nominal", real: "reel", fx: m.currency }[m.view] || m.view)
    : null;
  const grain = { month: "ay", quarter: "çeyrek", year: "yıl" }[m.grain] || m.grain;
  const scopeLabel = scopeName(data);
  return [
    `${m.measure_name}${viewLabel ? ` (${viewLabel})` : ""}`,
    scopeLabel,
    `${m.start_label} – ${m.end_label}`,
    `${grain} kovası`,
  ].filter(Boolean).join(" · ");
}

function scopeName(data) {
  const dim = Object.keys(data.meta.scope || {})[0];
  if (!dim) return null;
  const scope = (data.options.scopes || []).find((s) => s.dimension === dim);
  const nodeId = data.meta.scope[dim]?.nodes?.[0];
  const node = scope?.nodes.find((n) => String(n.node_id) === String(nodeId));
  if (!scope) return null;
  return node ? `${scope.name} / ${node.label}` : scope.name;
}

function heroCard(data) {
  const t = data.timeline;
  const labels = t.buckets.map((b) => b.label);
  const currentLabel = t.current_index >= 0 ? labels[t.current_index] : null;
  const host = el("div", { class: "chart-host" });
  const legend = el("div", {});

  requestAnimationFrame(() => charts.forecastChart(host, {
    labels,
    actual: t.series.actual,
    consensus: t.series.consensus,
    statistical: t.series.statistical,
    budget: t.series.budget,
    lastYear: t.series.last_year,
    lower: t.series.lower,
    upper: t.series.upper,
    currentLabel,
    unit: unitLabel(data.meta),
    height: 340,
    legendHost: legend,
  }));

  return card("Agrege konsensüs tahmini", filterNote(data), [legend, host], { class: "mb-3" });
}

/* Katman karşılaştırması. Geçmiş sütunu snapshot'tan gelir: o katman gerçekleşen
   gelmeden önce ne demişti. Gelecek sütunu seçili modelin katman değeridir. */
function modelTable(data) {
  const t = data.table;
  if (!t.rows.length) return card("Model karşılaştırma", null, [empty("Gösterilecek satır yok")]);

  // Ay kovasında alt sütun yok; iki satırlık başlık "Adet geçmiş / Adet" diye
  // kendini tekrar eder. O durumda tek satır başlık kullanıyoruz.
  const groupLabel = Object.fromEntries(t.column_groups.map((g) => [g.key, g.label]));
  const columns = [
    { key: "label", label: "", group: "label" },
    ...t.columns.map((c) => ({
      key: `${c.key}__${c.part}`,
      label: t.show_avg ? c.label : (groupLabel[c.key] || c.label),
      group: c.group,
      numeric: true,
      // Değerler burada biçimlendi (kısaltma ve boş için tire); table()
      // bunları yeniden sayıya çevirmeye çalışmasın.
      render: (v) => v,
    })),
  ];
  const groups = t.show_avg
    ? [{ label: "", span: 1 }, ...t.column_groups.map((g) => ({ label: g.label, span: g.span }))]
    : null;

  const rows = t.rows.map((r) => {
    const flat = {
      label: el("span", {
        class: "d-inline-flex align-items-center gap-2",
        // Hangi katman olduğu ekranda yazmıyor; ipucunda duruyor.
        title: r.source ? `${r.label}: ${r.source} katmanı` : r.label,
      }, [
        el("span", { class: "legend-dot", style: `background:${provColor(r.provenance)}` }),
        el("span", { class: r.code === "actual" ? "fw-semibold" : "", text: r.label }),
        r.future_partial
          ? el("span", {
            class: "badge bg-secondary-subtle text-secondary-emphasis",
            title: `Gelecek hücrelerin yalnızca %${fmt.n(r.future_coverage * 100, 1)} kadarı dolu; `
              + "bu katman tam bir tahmin değil, dokunulan hücrelerden oluşan bir örtü.",
            text: "kısmi",
          })
          : null,
      ]),
      mape__total: r.mape === null || r.mape === undefined ? "—" : `%${fmt.n(r.mape, 1)}`,
      budget_gap__total: r.budget_gap === null || r.budget_gap === undefined
        ? "—"
        : el("span", {
          class: r.budget_gap >= 0 ? "text-success-emphasis" : "text-danger-emphasis",
          text: fmt.pct(r.budget_gap),
        }),
    };
    for (const c of t.columns) {
      if (c.key === "mape" || c.key === "budget_gap") continue;
      const v = r.cells[c.key]?.[c.part];
      flat[`${c.key}__${c.part}`] = v === null || v === undefined ? "—" : fmt.compact(v);
    }
    return flat;
  });

  const windowNote = `${filterNote(data)} · geçmiş ${t.past_periods}, gelecek ${t.future_periods} dönem`
    + ` · lag ${t.lag}`
    + (t.budget_periods ? ` · bütçe farkı ${t.budget_periods} dönem üzerinden` : "");
  return card("Model karşılaştırma", windowNote,
    [table(columns, rows, groups ? { groups } : {})], { class: "mb-3" });
}

function provColor(p) {
  return ({
    actual: charts.PROVENANCE.actual(),
    model: charts.PROVENANCE.model(),
    human: charts.PROVENANCE.human(),
    budget: charts.PROVENANCE.budget(),
  })[p] || charts.PROVENANCE.muted();
}

function bottomRow(data) {
  const row = el("div", { class: "row g-3" });
  for (const chart of data.charts) {
    const host = el("div", { class: "chart-host" });
    let content = [host];
    // Boş noktalar elenirse eksen bütçe verisinin kapsamına daralıyor ve
    // grafik pencere seçimini takip etmiyormuş gibi görünüyordu. Pencerenin
    // tamamı çizilir; bütçesi olmayan dönemde çubuk olmaz, o kadar.
    const pts = chart.points;
    if (!pts.some((p) => p.gap !== null)) content = [empty("Bütçe yüklenmedi", "wallet2")];
    else {
      requestAnimationFrame(() => charts.barLineChart(host, {
        labels: pts.map((p) => p.label),
        bars: pts.map((p) => p.gap),
        line: pts.map((p) => p.cumulative),
        barLabel: "Dönem farkı",
        lineLabel: "Kümülatif",
        unit: unitLabel(data.meta),
        height: 240,
        maxTicks: 8,
      }));
    }
    row.append(el("div", { class: "col-12" }, [card(chart.label, filterNote(data), content)]));
  }
  return row;
}
