// Kokpit: modellerin yan yana karşılaştırması.
//
// Bir ekran, bir soru: hangi model ne diyor, geçmişte ne kadar tuttu, bütçeye
// göre nerede duruyor. Operasyonel listeler kendi ekranlarında yaşar.

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
    clear(body).append(loading("Modeller hesaplanıyor"));
    let data;
    try {
      data = await api.cockpitOverview({
        measure: cfg.measure, grain: cfg.grain, aggregate: cfg.aggregate,
        window_mode: cfg.windowMode,
        horizon_periods: cfg.horizon, history_periods: cfg.history,
        start_label: cfg.startLabel, end_label: cfg.endLabel,
        models: cfg.models, gap_model: cfg.gapModel,
        scope: cfg.scope, view: cfg.view, currency: cfg.currency,
      });
    } catch (err) {
      clear(body).append(empty(err.message, "exclamation-triangle"));
      return;
    }
    // Tarih moduna ilk geçişte uçlar mevcut pencereden devralınır, boş açılmaz.
    if (!cfg.startLabel) cfg.startLabel = data.meta.start_label;
    if (!cfg.endLabel) cfg.endLabel = data.meta.end_label;
    // Sunucu seçimi düzeltmiş olabilir (boş liste, kapsam dışı model).
    cfg.models = data.meta.models;
    cfg.gapModel = data.meta.gap_model;

    clear(headerHost).append(pageHeader("Kokpit"));
    paintFilters(data);
    clear(body);
    body.append(comparisonCard(data), accuracyCard(data), gapCard(data, cfg, load));
  }

  function paintFilters(data) {
    const o = data.options;
    const measure = o.measures.find((m) => m.code === cfg.measure);
    const isValue = measure?.unit_type === "value";
    const isRange = cfg.windowMode === "range";

    // Dönem modunda uçlar "kaç dönem geriye/ileriye", tarih modunda doğrudan
    // tarihtir; etiketler de buna göre değişir.
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

    const showFields = [
      ["Ölçü", segmented("ck-measure",
        o.measures.map((m) => ({ value: m.code, label: m.name })),
        cfg.measure, (v) => { cfg.measure = v; load(); })],
      ["Kova", segmented("ck-grain",
        o.grains.map((g) => ({ value: g.code, label: g.label })),
        cfg.grain, (v) => { cfg.grain = v; load(); })],
      ["Değer", segmented("ck-agg",
        o.aggregates.map((a) => ({ value: a.code, label: a.label })),
        cfg.aggregate, (v) => { cfg.aggregate = v; load(); })],
    ];
    if (isValue) {
      showFields.push(["Görünüm", select(
        o.views.map((v) => ({ value: v.code, label: v.label })),
        cfg.view, (v) => { cfg.view = v; load(); },
      )]);
      if (cfg.view === "fx") {
        showFields.push(["Para birimi", select(
          o.currencies.map((c) => ({ value: c, label: c })),
          cfg.currency, (v) => { cfg.currency = v; load(); },
        )]);
      }
    }

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
      ["Model", [[null, modelPicker(o, cfg, load)]]],
      ["Gösterim", showFields],
    ];

    clear(filterHost).append(el("div", { class: "d-flex flex-wrap gap-3 align-items-start" },
      groups.map(([title, fields]) => el("fieldset", { class: "filter-group" }, [
        el("legend", { class: "filter-group-title", text: title }),
        el("div", { class: "d-flex flex-wrap align-items-end gap-3" },
          fields.map(([label, control]) => el("div", { class: "d-flex flex-column" }, [
            label ? el("label", { class: "form-label mb-1 small text-muted", text: label }) : null,
            control,
          ]))),
      ]))));
  }

  await load();
  return wrap;
}

/* Modeller çoklu seçim: işaretlenenler hem tabloya hem grafiğe gelir.
   En az biri kalmalı, yoksa ekranda karşılaştıracak bir şey olmaz. */
function modelPicker(options, cfg, reload) {
  const box = el("div", { class: "d-flex flex-column gap-1" });
  for (const m of options.models) {
    const id = `ck-model-${m.code}`;
    const input = el("input", {
      type: "checkbox", class: "form-check-input mt-0", id,
      onchange: () => {
        const next = input.checked
          ? [...cfg.models, m.code]
          : cfg.models.filter((c) => c !== m.code);
        if (!next.length) { input.checked = true; return; }
        cfg.models = next;
        reload();
      },
    });
    input.checked = cfg.models.includes(m.code);
    box.append(el("div", { class: "form-check d-flex align-items-center gap-2 m-0" }, [
      input,
      el("label", {
        class: "form-check-label small", for: id, title: `${m.label}: ${m.source} katmanı`, text: m.label,
      }),
    ]));
  }
  return box;
}

function periodSelect(periods, value, onChange) {
  return select((periods || []).map((p) => ({ value: p.label, label: p.label })),
    value, onChange, { style: "min-width:120px" });
}

function dimensionSelect(options, cfg, reload) {
  const current = Object.keys(cfg.scope || {})[0] || "";
  return select(
    [{ value: "", label: "Tüm kapsam" },
      ...options.scopes.map((s) => ({ value: s.dimension, label: s.name }))],
    current,
    (v) => { cfg.scope = v ? { [v]: { nodes: [] } } : {}; reload(); },
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
  const agg = m.aggregate === "average" ? "dönem ortalaması" : "toplam";
  return [
    `${m.measure_name}${viewLabel ? ` (${viewLabel})` : ""}`,
    scopeName(data),
    `${m.start_label} – ${m.end_label}`,
    `${grain} kovası`,
    agg,
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

function modelColor(code, provenance) {
  return charts.MODEL_COLOR(code) || ({
    actual: charts.PROVENANCE.actual(),
    model: charts.PROVENANCE.model(),
    human: charts.PROVENANCE.human(),
    budget: charts.PROVENANCE.budget(),
  })[provenance] || charts.PROVENANCE.muted();
}

/** Satır etiketi: grafikle aynı renk noktası, böylece ikisi aynı dili konuşur. */
function rowLabel(r) {
  return el("span", { class: "d-inline-flex align-items-center gap-2" }, [
    el("span", { class: "legend-dot", style: `background:${modelColor(r.code, r.provenance)}` }),
    el("span", { class: r.code === "actual" ? "fw-semibold" : "", text: r.label }),
  ]);
}

/* Grafik ve tablo tek kartta: aynı pencereye bakıyorlar, ayrı başlık ikisini
   iki ayrı şey gibi gösteriyordu. */
function comparisonCard(data) {
  const t = data.table;
  const tl = data.timeline;
  const labels = tl.buckets.map((b) => b.label);
  const currentLabel = tl.current_index >= 0 ? labels[tl.current_index] : null;
  const host = el("div", { class: "chart-host" });
  const legend = el("div", {});

  requestAnimationFrame(() => charts.forecastChart(host, {
    labels,
    models: tl.models,
    actual: tl.series.actual,
    budget: tl.series.budget,
    lastYear: tl.series.last_year,
    lower: tl.series.lower,
    upper: tl.series.upper,
    currentLabel,
    unit: unitLabel(data.meta),
    height: 320,
    legendHost: legend,
  }));

  const columns = [
    { key: "label", label: "", group: "label" },
    ...t.columns.map((c) => ({
      key: c.key, label: c.label, group: c.group, numeric: true, render: (v) => v,
    })),
  ];
  // Üst satır ölçüyü, alt satır geçmiş/gelecek ayrımını taşır.
  const groups = [{ label: "", span: 1 }, ...t.column_groups.map((g) => ({ label: g.label, span: g.span }))];
  const rows = t.rows.map((r) => {
    const flat = { label: rowLabel(r) };
    for (const c of t.columns) {
      const v = r.cells[c.key];
      flat[c.key] = v === null || v === undefined ? "—" : fmt.compact(v);
    }
    return flat;
  });

  const note = `${filterNote(data)} · geçmiş ${t.past_periods}, gelecek ${t.future_periods} dönem`;
  return card("Model karşılaştırma", note, [
    legend, host,
    el("div", { class: "mt-3" }, [table(columns, rows, { groups })]),
  ], { class: "mb-3" });
}

function accuracyCard(data) {
  const a = data.accuracy;
  const columns = [
    { key: "label", label: "Model" },
    { key: "mape", label: "MAPE", numeric: true, render: (v) => v },
    { key: "gap", label: "Bütçe farkı", numeric: true, render: (v) => v },
  ];
  const rows = a.rows.map((r) => ({
    label: rowLabel(r),
    mape: r.mape === null || r.mape === undefined ? "—" : `%${fmt.n(r.mape, 1)}`,
    gap: r.budget_gap === null || r.budget_gap === undefined
      ? "—"
      : el("span", {
        class: r.budget_gap >= 0 ? "text-success-emphasis" : "text-danger-emphasis",
        text: fmt.pct(r.budget_gap),
      }),
  }));
  const note = `Lag ${a.lag} · bütçe farkı ${a.budget_periods} dönem üzerinden`;
  return card("Doğruluk ve bütçe farkı", note, [table(columns, rows)], { class: "mb-3" });
}

/* Bütçe farkı tek bir modele bakar; seçenekleri üst filtrede işaretlenenler. */
function gapCard(data, cfg, reload) {
  const chart = data.charts[0];
  if (!chart) return el("div", {});
  const host = el("div", { class: "chart-host" });
  const pts = chart.points;
  const content = pts.some((p) => p.gap !== null)
    ? [host]
    : [empty("Bu pencerede bütçe yok", "wallet2")];

  if (pts.some((p) => p.gap !== null)) {
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

  const picker = select(
    data.meta.models.map((code) => ({
      value: code,
      label: (data.options.models.find((m) => m.code === code) || {}).label || code,
    })),
    data.meta.gap_model,
    (v) => { cfg.gapModel = v; reload(); },
    { style: "min-width:130px" },
  );

  return card(chart.label, filterNote(data), content, {
    actions: el("div", { class: "d-flex align-items-end gap-2" }, [
      el("label", { class: "form-label mb-1 small text-muted align-self-center", text: "Model" }),
      picker,
    ]),
  });
}
