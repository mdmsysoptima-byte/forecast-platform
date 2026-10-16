// Raporlar: standart raporlar, disa aktarim, zamanlanmis raporlar, API durumu.

import { api } from "../api.js";
import {
  card, clear, defList, download, el, empty, field, fmt, icon, jsonBlock, lineChart, loading, modal,
  pageHeader, sectionLabel, select, table, toast,
} from "../ui.js";

/* Demand review paketi bütçe ve doğruluk bloklarını ham anahtarlarla döner;
   ekranda İngilizce alan adı durmaması için burada çeviriyoruz. */
const METRIC_LABEL = {
  total_consensus: "Konsensüs", total_budget: "Bütçe", total_gap: "Fark", total_gap_pct: "Fark yüzdesi",
  ytd_consensus: "YTD konsensüs", ytd_budget: "YTD bütçe", ytd_gap: "YTD fark",
  ytg_consensus: "YTG konsensüs", ytg_budget: "YTG bütçe", ytg_gap: "YTG fark",
  wmape: "WMAPE", mape: "MAPE", mae: "MAE", rmse: "RMSE", bias: "Bias",
  n: "Gözlem", volume: "Hacim", lag: "Lag",
};
/* WMAPE ve MAPE hep pozitif; isaretli gosterim ("%+3,3") yanlis okunur.
   Isaret yalnizca yonu olan olculerde anlamli. */
const PCT_KEYS = new Set(["wmape", "mape"]);
const SIGNED_PCT_KEYS = new Set(["total_gap_pct", "bias"]);
const CYCLE_STATUS = { open: "açık", review: "incelemede", approved: "onaylı", locked: "kilitli" };

function metricList(obj) {
  return defList(Object.entries(obj).map(([k, v]) => {
    if (typeof v !== "number") return [METRIC_LABEL[k] || k, v];
    if (SIGNED_PCT_KEYS.has(k)) return [METRIC_LABEL[k] || k, fmt.pct(v)];
    if (PCT_KEYS.has(k)) return [METRIC_LABEL[k] || k, `%${fmt.n(v, 1)}`];
    return [METRIC_LABEL[k] || k, fmt.n(v, Number.isInteger(v) ? 0 : 1)];
  }));
}

const API_ROUTES = [
  ["Tahmin", "/api/v1/forecast?version=&layer=&grain=&scope="],
  ["Versiyonlar", "/api/v1/versions"],
  ["Çalıştırmalar", "/api/v1/runs"],
  ["Doğruluk", "/api/v1/analysis/accuracy"],
  ["OpenAPI", "/api/openapi.json"],
];

export async function render({ state }) {
  const [reports, scheduled] = await Promise.all([api.reports(), api.scheduled().catch(() => ({ scheduled: [] }))]);
  const root = el("div", { class: "d-flex flex-column gap-3" });
  const output = el("div", { class: "d-flex flex-column gap-3" });

  const cfg = { code: reports.reports[0].code };
  const codeSel = select(
    reports.reports.map((r) => ({ value: r.code, label: r.title })), cfg.code, (v) => {
      cfg.code = v;
      syncParams();
    },
  );
  const versionSel = select(
    state.versions.map((v) => ({ value: v.id, label: v.name })), state.ctx.versionId, () => {},
  );
  const levelOptions = [];
  for (const d of state.dimensions) {
    const hier = d.hierarchies.find((h) => h.is_default) || d.hierarchies[0];
    for (const l of hier.levels) levelOptions.push({ value: `${d.code}=${l}`, label: `${d.name} / ${l}` });
  }
  const levelSel = select(levelOptions, levelOptions[0]?.value, () => {});
  const lagSel = select(
    Array.from({ length: 6 }, (_, i) => ({ value: i + 1, label: `lag ${i + 1}` })), 1, () => {},
  );

  /* Her rapor her parametreyi kullanmıyor; ilgisiz seçiciyi açık tutmak yanlış
     bir kontrol hissi veriyor, o yüzden rapor tanımına göre kapatıyoruz. */
  const paramFields = [
    ["version", field("Versiyon", versionSel), versionSel],
    ["level", field("Seviye", levelSel), levelSel],
    ["lag", field("Lag", lagSel), lagSel],
  ];
  function syncParams() {
    const needs = reports.reports.find((r) => r.code === cfg.code)?.params || [];
    for (const [name, wrapper, control] of paramFields) {
      const used = needs.some((p) => p.includes(name));
      wrapper.classList.toggle("opacity-50", !used);
      control.disabled = !used;
    }
  }

  function params() {
    const level = {};
    if (levelSel.value) {
      const [dim, lvl] = levelSel.value.split("=");
      level[dim] = lvl;
    }
    return {
      version_id: Number(versionSel.value),
      version_ids: state.versions.slice(0, 2).map((v) => v.id),
      level, lag: Number(lagSel.value), status: "open",
    };
  }

  const exportBtn = (label, iconName, kind) => el("button", {
    class: "btn btn-sm btn-outline-secondary", type: "button", onclick: () => exportAs(kind),
  }, [icon(iconName), el("span", { class: "ms-2", text: label })]);

  root.append(pageHeader("Raporlar", reports.summary, [
    el("button", {
      class: "btn btn-sm btn-primary", type: "button", onclick: () => run(),
    }, [icon("play-fill"), el("span", { class: "ms-2", text: "Üret" })]),
    el("div", { class: "btn-group btn-group-sm" }, [
      exportBtn("Excel", "file-earmark-spreadsheet", "xlsx"),
      exportBtn("CSV", "filetype-csv", "csv"),
      exportBtn("Parquet", "file-earmark-binary", "parquet"),
    ]),
  ]));

  root.append(el("div", {
    class: "d-flex flex-wrap align-items-end gap-3 p-3 bg-body-tertiary border rounded",
  }, [field("Rapor", codeSel), ...paramFields.map(([, wrapper]) => wrapper)]));
  syncParams();

  root.append(output);

  root.append(card("Standart rapor kataloğu", "Her rapor kendi parametre setiyle çalışır", [
    table(
      [
        { key: "title", label: "Rapor" },
        { key: "code", label: "Kod", render: (v) => el("code", { class: "small", text: v }) },
        { key: "params", label: "Parametreler", render: (p) => el(
          "div", { class: "d-flex flex-wrap gap-1" },
          p.map((x) => el("span", { class: "badge bg-secondary-subtle text-secondary-emphasis", text: x })),
        ) },
      ],
      reports.reports,
    ),
  ], { flush: true }));

  root.append(card("REST API durumu", "ERP ve üretim sistemleri için onaylı tahmin sorgusu", [
    el("ul", { class: "list-unstyled mb-0 d-flex flex-column gap-2" }, API_ROUTES.map(([label, path]) => el(
      "li", { class: "d-flex flex-wrap align-items-baseline gap-2" }, [
        el("span", { class: "text-muted small", style: "min-width:7rem", text: label }),
        el("code", { class: "small text-break", text: path }),
      ],
    ))),
  ], {
    actions: el("div", { class: "d-flex flex-wrap gap-2" }, [
      el("a", {
        class: "btn btn-sm btn-outline-secondary", href: "/api/docs", target: "_blank", rel: "noreferrer",
      }, [icon("book"), el("span", { class: "ms-2", text: "API dokümanı" })]),
      el("button", {
        class: "btn btn-sm btn-outline-secondary", type: "button", onclick: () => apiProbe(),
      }, [icon("send"), el("span", { class: "ms-2", text: "Örnek çağrı" })]),
      el("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onclick: async () => {
        try {
          const res = await api.biViews();
          toast(res.summary, "ok");
        } catch (err) { toast(err.message, "err"); }
      } }, [icon("diagram-3"), el("span", { class: "ms-2", text: "BI görünümleri" })]),
    ]),
  }));

  root.append(card("Zamanlanmış raporlar", scheduled.summary || "", [
    scheduled.note ? el("p", { class: "text-muted small", text: scheduled.note }) : null,
    scheduled.scheduled?.length ? table(
      [
        { key: "report_code", label: "Rapor" },
        { key: "cron", label: "Cron", render: (v) => el("code", { class: "small", text: v }) },
        { key: "recipients", label: "Alıcı" },
        { key: "last_run_at", label: "Son çalışma", render: (v) => fmt.date(v) || "henüz çalışmadı" },
      ],
      scheduled.scheduled,
    ) : empty("Zamanlanmış rapor yok", "calendar-x"),
  ], {
    actions: el("button", {
      class: "btn btn-sm btn-outline-secondary", type: "button", onclick: () => scheduleDialog(),
    }, [icon("clock"), el("span", { class: "ms-2", text: "Rapor zamanla" })]),
  }));

  return root;

  async function run() {
    clear(output).append(loading("Rapor üretiliyor"));
    try {
      const res = await api.runReport(cfg.code, params());
      clear(output);
      output.append(renderReport(res));
      output.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      clear(output).append(card(null, null, [empty(err.message, "exclamation-triangle")]));
    }
  }

  async function exportAs(fmtName) {
    try {
      const blob = await api.exportReport(cfg.code, params(), fmtName);
      download(blob, `${cfg.code}.${fmtName}`);
      toast(`${cfg.code} ${fmtName} olarak indirildi`, "ok");
    } catch (err) { toast(err.message, "err"); }
  }

  async function apiProbe() {
    try {
      const res = await api.forecastApi({ version: Number(versionSel.value), layer: "consensus" });
      await modal({
        title: "Örnek API yanıtı",
        subtitle: res.summary,
        wide: true,
        body: [jsonBlock({ version: res.version, rows: res.rows.slice(0, 12) })],
      });
    } catch (err) { toast(err.message, "err"); }
  }

  async function scheduleDialog() {
    const code = select(reports.reports.map((r) => ({ value: r.code, label: r.title })), cfg.code, () => {});
    const cron = el("input", { type: "text", value: "0 7 1 * *" });
    const to = el("input", { type: "text", placeholder: "planlama@firma.com" });
    const ok = await modal({
      title: "Rapor zamanla",
      subtitle: "Cron ifadesi ile periyodik üretim",
      body: [
        field("Rapor", code),
        field("Cron", cron, "Dakika saat gün ay haftanın günü. Örnek: her ayın 1'i 07:00 için 0 7 1 * *"),
        field("Alıcılar", to, "Virgülle ayrılmış e-posta adresleri"),
      ],
      actions: [{ label: "Vazgeç" }, { label: "Kaydet", primary: true, value: true }],
    });
    if (!ok) return;
    try {
      const res = await api.schedule({
        report_code: code.value, cron: cron.value, recipients: to.value, params: params(),
      });
      toast(res.summary, "ok");
      location.reload();
    } catch (err) { toast(err.message, "err"); }
  }
}

function renderReport(res) {
  const children = [];
  if (res.chart) {
    children.push(lineChart({
      labels: res.chart[0]?.points.map((p) => p.label) || [],
      series: res.chart.map((s, i) => ({
        label: s.layer,
        color: ["var(--prov-actual)", "var(--prov-model)", "var(--ink-primary)", "var(--prov-budget)"][i % 4],
        points: s.points.map((p) => p.value ?? null),
      })),
      height: 210,
    }));
  }
  if (res.columns?.length && res.rows?.length) {
    children.push(el("div", { class: children.length ? "mt-3" : "" }, [table(
      res.columns.map((c) => ({ key: c.key, label: c.label, numeric: c.numeric })),
      res.rows,
    )]));
  }
  if (res.extra?.worst?.length) {
    children.push(el("div", { class: "mt-4" }, [
      sectionLabel("En kötü seriler"),
      table(
        [{ key: "label", label: "Seri" }, { key: "wmape", label: "WMAPE", numeric: true },
          { key: "bias", label: "Bias", numeric: true }],
        res.extra.worst,
      ),
    ]));
  }
  if (res.extra?.by_user?.length) {
    children.push(el("div", { class: "mt-4" }, [
      sectionLabel("Kullanıcı bazında"),
      table(
        [{ key: "user", label: "Kullanıcı" }, { key: "n", label: "Kayıt", numeric: true, dec: 0 },
          { key: "avg_gain_points", label: "Kazanç", numeric: true }],
        res.extra.by_user,
      ),
    ]));
  }
  if (res.sections) {
    const s = res.sections;
    const panels = [
      ["Çevrim", `${s.cycle.name} · ${CYCLE_STATUS[s.cycle.status] || s.cycle.status}`, [
        el("p", { class: "text-muted small", text: `Mevcut dönem ${s.cycle.current_period}` }),
        s.cycle.approval_trail?.length
          ? table([{ key: "label", label: "Adım" }, { key: "comment", label: "Yorum" }], s.cycle.approval_trail)
          : empty("Onay adımı yok"),
      ]],
      ["Versiyon", s.version.name, [jsonBlock(s.version.provenance)]],
      ["Bütçe farkı", "", [metricList(s.budget_gap)]],
      ["Doğruluk ve FVA", `Lag ${s.fva.lag}`, [metricList(s.accuracy)]],
      ["En büyük değişimler", "", [
        s.biggest_changes?.length
          ? table([{ key: "label", label: "Kırılım" }, { key: "delta", label: "Fark", numeric: true }],
            s.biggest_changes)
          : empty("Değişim yok"),
      ]],
      ["İstisnalar", "", [
        s.exceptions?.length
          ? table([{ key: "type_label", label: "Tip" }, { key: "title", label: "Açıklama" }], s.exceptions)
          : empty("Açık uyarı yok", "check-circle"),
      ]],
    ];
    children.push(el("div", { class: "row g-3 mt-1" }, panels.map(([title, note, body]) => el(
      "div", { class: "col-12 col-xl-6" }, [card(title, note, body, { class: "h-100" })],
    ))));
  }
  if (!children.length) children.push(empty("Rapor boş döndü"));
  return card(res.title, res.summary, children);
}
