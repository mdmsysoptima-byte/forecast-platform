// Analiz: dogruluk ve FVA, varyans ve selale, butce gap.

import { api } from "../api.js";
import {
  barChart, card, clear, el, empty, field, fmt, heatColor, lineChart, loading, pageHeader, select,
  table, tabs, tile,
} from "../ui.js";

const TABS = [
  { id: "accuracy", label: "Doğruluk ve FVA", icon: "bullseye" },
  { id: "variance", label: "Varyans ve şelale", icon: "bar-chart-steps" },
  { id: "budget", label: "Bütçe gap", icon: "wallet2" },
];

/** Filtre çubuğu: ekranların üstünde duran tek satırlık seçici kuşağı. */
function filterBar(fields) {
  return el("div", {
    class: "d-flex flex-wrap align-items-end gap-3 p-3 bg-body-tertiary border rounded",
  }, fields);
}

export async function render({ state }) {
  const sub = location.hash.split("/")[2] || "accuracy";
  const active = TABS.find((t) => t.id === sub) || TABS[0];
  const root = el("div", { class: "d-flex flex-column gap-3" });
  root.append(pageHeader("Analiz", active.label, tabs(
    TABS, active.id, (id) => { location.hash = `#/analysis/${id}`; },
  )));

  const box = el("div", { class: "d-flex flex-column gap-3" });
  root.append(box);
  if (sub === "variance") box.append(await varianceView(state));
  else if (sub === "budget") box.append(await budgetView(state));
  else box.append(await accuracyView(state));
  return root;
}

// ---------------------------------------------------------------- accuracy
async function accuracyView(state) {
  const wrap = el("div", { class: "d-flex flex-column gap-3" });
  const controls = filterBar([]);
  const content = el("div", { class: "d-flex flex-column gap-3" });
  wrap.append(controls, content);

  const cfg = { layer: "consensus", lag: 1, level: "product=category" };
  const layerSel = select(
    state.layers.filter((l) => !["actual", "prior_version"].includes(l.code))
      .map((l) => ({ value: l.code, label: l.name })),
    cfg.layer, (v) => { cfg.layer = v; paint(); },
  );
  const lagSel = select(
    Array.from({ length: 6 }, (_, i) => ({ value: i + 1, label: `lag ${i + 1}` })),
    cfg.lag, (v) => { cfg.lag = Number(v); paint(); },
  );
  const levelOptions = [];
  for (const d of state.dimensions) {
    const hier = d.hierarchies.find((h) => h.is_default) || d.hierarchies[0];
    for (const l of hier.levels) levelOptions.push({ value: `${d.code}=${l}`, label: `${d.name} / ${l}` });
  }
  const levelSel = select(levelOptions, cfg.level, (v) => { cfg.level = v; paint(); });
  controls.append(field("Katman", layerSel), field("Lag", lagSel), field("Seviye", levelSel));

  async function paint() {
    clear(content).append(loading("Doğruluk hesaplanıyor"));
    const [acc, fva] = await Promise.all([
      api.accuracy({ layer: cfg.layer, lag: cfg.lag, level: cfg.level }),
      api.fva(cfg.lag),
    ]);
    clear(content);

    const m = acc.summary_metrics;
    if (!m.n) { content.append(empty(acc.summary)); return; }
    const metrics = [
      tile({ label: "WMAPE", value: fmt.n(m.wmape, 1), unit: "%", kind: "model",
        note: `${fmt.n(m.n)} gözlem · hacim ${fmt.n(m.volume)}` }),
      tile({ label: "MAPE", value: fmt.n(m.mape, 1), unit: "%", kind: "model", note: "basit ortalama" }),
      tile({ label: "Bias", value: fmt.pct(m.bias), kind: m.bias > 0 ? "alert" : "actual",
        trend: m.bias > 0 ? "neg" : "pos", note: m.bias > 0 ? "yukarı sapma" : "aşağı sapma" }),
      tile({ label: "MAE", value: fmt.n(m.mae, 1), kind: "model" }),
      tile({ label: "RMSE", value: fmt.n(m.rmse, 1), kind: "model" }),
      tile({ label: "FVA toplam", value: fva.total_value_added === null ? "-" : fmt.n(fva.total_value_added, 1),
        unit: "puan", kind: "human", note: "naive'den konsensüse" }),
    ];
    content.append(el("div", { class: "row g-3" }, metrics.map(
      (t) => el("div", { class: "col-6 col-md-4 col-xxl-2" }, [t]),
    )));

    content.append(card("Lag'e göre doğruluk eğrisi", "Lag arttıkça hata büyür", [
      lineChart({
        labels: acc.by_lag.map((r) => `lag ${r.lag}`),
        series: [
          { label: "WMAPE", color: "var(--prov-model)", points: acc.by_lag.map((r) => r.wmape) },
          { label: "Bias", color: "var(--prov-human)", points: acc.by_lag.map((r) => r.bias) },
        ],
        height: 170,
      }),
      el("div", { class: "mt-3" }, [table(
        [
          { key: "lag", label: "Lag", numeric: true, dec: 0 },
          { key: "wmape", label: "WMAPE", numeric: true },
          { key: "mape", label: "MAPE", numeric: true },
          { key: "mae", label: "MAE", numeric: true },
          { key: "rmse", label: "RMSE", numeric: true },
          { key: "bias", label: "Bias", numeric: true },
          { key: "n", label: "Gözlem", numeric: true, dec: 0 },
        ],
        acc.by_lag,
      )]),
    ]));

    content.append(card(
      "FVA merdiveni", `Lag ${fva.lag} · her adımın katkısı WMAPE puanı`, [ladder(fva.steps)], { flush: true },
    ));

    if (fva.by_user?.length || fva.by_reason?.length) {
      content.append(card("FVA ayrışması", "Kullanıcı ve gerekçe kodu bazında ortalama kazanç", [
        el("div", { class: "row g-3" }, [
          el("div", { class: "col-12 col-xl-6" }, [
            fva.by_user?.length
              ? table([{ key: "user", label: "Kullanıcı" }, { key: "n", label: "Kayıt", numeric: true, dec: 0 },
                { key: "avg_gain_points", label: "Kazanç puanı", numeric: true }], fva.by_user)
              : empty("Değerlendirilebilir kullanıcı override'i yok"),
          ]),
          el("div", { class: "col-12 col-xl-6" }, [
            fva.by_reason?.length
              ? table([{ key: "reason", label: "Gerekçe" }, { key: "n", label: "Kayıt", numeric: true, dec: 0 },
                { key: "avg_gain_points", label: "Kazanç puanı", numeric: true }], fva.by_reason)
              : empty("Gerekçe bazında kayıt yok. Override'in sonucu ancak gerçekleşen geldiğinde ölçülür."),
          ]),
        ]),
      ]));
    }

    if (acc.by_level?.length) {
      content.append(card("Seviye bazında doğruluk", cfg.level, [
        table(
          [
            { key: "label", label: "Kırılım" },
            { key: "wmape", label: "WMAPE", numeric: true },
            { key: "bias", label: "Bias", numeric: true },
            { key: "volume", label: "Hacim", numeric: true, dec: 0 },
            { key: "n", label: "Gözlem", numeric: true, dec: 0 },
          ],
          acc.by_level,
        ),
      ]));
    }

    if (acc.bias_heatmap?.cells?.length) {
      content.append(card("Bias ısı haritası", "Seviye x dönem · kırmızı yukarı sapma, mavi aşağı sapma", [
        heatmap(acc.bias_heatmap),
      ]));
    }

    content.append(card("En kötü 20 seri", `Lag ${cfg.lag}, hacim ağırlıklı sıralama`, [
      acc.worst_series.length ? table(
        [
          { key: "label", label: "Seri" },
          { key: "wmape", label: "WMAPE", numeric: true },
          { key: "bias", label: "Bias", numeric: true },
          { key: "volume", label: "Hacim", numeric: true, dec: 0 },
        ],
        acc.worst_series,
      ) : empty("Veri yok"),
    ]));
  }

  await paint();
  return wrap;
}

/* Merdiven: her katman bir satır, WMAPE bir ilerleme çubuğu, katkı ise bir
   önceki katmana göre kazanılan puan. Çubuk sutunu en uzun WMAPE'ye göre
   ölçeklenir, böylece adımlar arasındaki mesafe göz ile okunur. */
function ladder(steps) {
  const max = Math.max(1, ...steps.map((s) => s.wmape || 0));
  return table(
    [
      { key: "label", label: "Katman" },
      { key: "wmape", label: "", render: (v) => el("div", {
        class: "progress", style: "height:8px;min-width:120px", role: "progressbar",
        "aria-valuenow": String(v ?? 0), "aria-valuemin": "0", "aria-valuemax": String(max),
      }, [el("div", {
        class: "progress-bar", style: `width:${((v || 0) / max) * 100}%;background:var(--prov-model)`,
      })]) },
      { key: "wmape", label: "WMAPE", numeric: true },
      { key: "contribution", label: "Katkı", numeric: true, render: (v) => (
        v === null || v === undefined
          ? el("span", { class: "text-muted", text: "-" })
          : el("span", {
            class: v > 0 ? "text-success-emphasis fw-semibold" : v < 0 ? "text-danger-emphasis" : "text-muted",
            text: fmt.pct(v),
          })
      ) },
    ],
    steps,
  );
}

function heatmap(data) {
  const byKey = new Map(data.cells.map((c) => [`${c.group}|${c.bucket_id}`, c.bias]));
  const thead = el("thead", { class: "table-light" }, [el("tr", {}, [
    el("th", { scope: "col", text: "Kırılım" }),
    ...data.columns.map((c) => el("th", { scope: "col", class: "text-center", text: c.label })),
  ])]);
  const tbody = el("tbody", {}, data.rows.map((r) => el("tr", {}, [
    el("th", { scope: "row", class: "fw-normal text-nowrap", text: r.label }),
    ...data.columns.map((c) => {
      const v = byKey.get(`${r.group}|${c.bucket_id}`);
      return el("td", {
        style: `background:${heatColor(v)}`,
        title: `${r.label} · ${c.label} · bias ${fmt.pct(v)}`,
        text: v === null || v === undefined ? "" : fmt.n(v, 0),
      });
    }),
  ])));
  return el("div", { class: "table-responsive" }, [
    el("table", { class: "table table-sm table-bordered align-middle mb-0 heat" }, [thead, tbody]),
  ]);
}

// ---------------------------------------------------------------- variance
async function varianceView(state) {
  const wrap = el("div", { class: "d-flex flex-column gap-3" });
  const controls = filterBar([]);
  const content = el("div", { class: "d-flex flex-column gap-3" });
  wrap.append(controls, content);

  const versions = state.versions;
  if (versions.length < 2) {
    controls.remove();
    content.append(empty("Karşılaştırmak için en az iki versiyon gerekli"));
    return wrap;
  }
  const cfg = { a: versions.at(-1).id, b: versions[0].id, level: "product=category", layer: "consensus" };

  const aSel = select(versions.map((v) => ({ value: v.id, label: v.name })), cfg.a, (v) => { cfg.a = Number(v); paint(); });
  const bSel = select(versions.map((v) => ({ value: v.id, label: v.name })), cfg.b, (v) => { cfg.b = Number(v); paint(); });
  const levelOptions = [];
  for (const d of state.dimensions) {
    const hier = d.hierarchies.find((h) => h.is_default) || d.hierarchies[0];
    for (const l of hier.levels) levelOptions.push({ value: `${d.code}=${l}`, label: `${d.name} / ${l}` });
  }
  const levelSel = select(levelOptions, cfg.level, (v) => { cfg.level = v; paint(); });
  const layerSel = select(
    state.layers.map((l) => ({ value: l.code, label: l.name })), cfg.layer,
    (v) => { cfg.layer = v; paint(); },
  );
  controls.append(field("Versiyon A", aSel), field("Versiyon B", bSel),
    field("Seviye", levelSel), field("Katman", layerSel));

  async function paint() {
    clear(content).append(loading("Varyans hesaplanıyor"));
    try {
      const res = await api.variance({ version_a: cfg.a, version_b: cfg.b, level: cfg.level, layer: cfg.layer });
      clear(content);
      const totals = [
        tile({ label: "Versiyon A", value: fmt.n(res.total_from), kind: "model" }),
        tile({ label: "Versiyon B", value: fmt.n(res.total_to), kind: "model" }),
        tile({
          label: "Fark", value: fmt.n(res.total_delta), kind: res.total_delta >= 0 ? "actual" : "alert",
          trend: res.total_delta >= 0 ? "pos" : "neg", note: res.summary,
        }),
      ];
      content.append(el("div", { class: "row g-3" }, totals.map(
        (t) => el("div", { class: "col-12 col-md-4" }, [t]),
      )));
      content.append(card("Şelale", "Kırılım bazında katkı, ilk 12 kalem", [
        barChart({
          labels: res.rows.slice(0, 12).map((r) => r.label.split(" / ")[0]),
          values: res.rows.slice(0, 12).map((r) => r.delta),
          colors: res.rows.slice(0, 12).map((r) => (r.delta >= 0 ? "var(--prov-model)" : "var(--prov-human)")),
          height: 190,
        }),
        el("div", { class: "mt-3" }, [table(
          [
            { key: "label", label: "Kırılım" },
            { key: "from", label: "A", numeric: true },
            { key: "to", label: "B", numeric: true },
            { key: "delta", label: "Fark", numeric: true },
            { key: "delta_pct", label: "Yüzde", numeric: true, render: (v) => fmt.pct(v) },
          ],
          res.rows,
        )]),
      ]));
    } catch (err) {
      clear(content).append(empty(err.message));
    }
  }

  await paint();
  return wrap;
}

// ------------------------------------------------------------------ budget
async function budgetView(state) {
  const wrap = el("div", { class: "d-flex flex-column gap-3" });
  const controls = filterBar([]);
  const content = el("div", { class: "d-flex flex-column gap-3" });
  wrap.append(controls, content);

  const cfg = { version: state.ctx.versionId || state.versions[0]?.id };
  const vSel = select(
    state.versions.map((v) => ({ value: v.id, label: v.name })), cfg.version,
    (v) => { cfg.version = Number(v); paint(); },
  );
  controls.append(field("Versiyon", vSel));

  async function paint() {
    clear(content).append(loading("Bütçe farkı hesaplanıyor"));
    try {
      const res = await api.budgetGap(cfg.version);
      clear(content);
      const totals = [
        tile({ label: "Konsensüs", value: fmt.n(res.total_consensus), kind: "human" }),
        tile({ label: "Bütçe", value: fmt.n(res.total_budget), kind: "budget" }),
        tile({
          label: "Fark", value: fmt.n(res.total_gap), kind: res.total_gap >= 0 ? "actual" : "alert",
          trend: res.total_gap >= 0 ? "pos" : "neg", note: fmt.pct(res.total_gap_pct),
        }),
      ];
      content.append(el("div", { class: "row g-3" }, totals.map(
        (t) => el("div", { class: "col-12 col-md-4" }, [t]),
      )));
      content.append(card("Dönem bazında", "Kesikli çizgi bütçe", [
        lineChart({
          labels: res.rows.map((r) => r.label),
          series: [
            { label: "Konsensüs", color: "var(--ink-primary)", points: res.rows.map((r) => r.consensus) },
            { label: "Bütçe", color: "var(--prov-budget)", dashed: true, points: res.rows.map((r) => r.budget) },
            { label: "Kumulatif fark", color: "var(--prov-human)", points: res.rows.map((r) => r.cum_gap) },
          ],
          height: 200,
        }),
        el("div", { class: "mt-3" }, [table(
          [
            { key: "label", label: "Dönem" },
            { key: "consensus", label: "Konsensüs", numeric: true },
            { key: "budget", label: "Bütçe", numeric: true },
            { key: "gap", label: "Fark", numeric: true },
            { key: "gap_pct", label: "Yüzde", numeric: true, render: (v) => fmt.pct(v) },
            { key: "cum_gap", label: "Kümülatif", numeric: true },
          ],
          res.rows,
        )]),
      ]));
    } catch (err) {
      clear(content).append(empty(err.message));
    }
  }

  await paint();
  return wrap;
}
