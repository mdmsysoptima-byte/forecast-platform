// ApexCharts sarmalayicilari.
// Palet ve eksen renkleri Metis temasinin chart-palette modulunden alindi;
// renk dekor degil koken kodlar: teal gerceklesen, mavi model, amber insan, notr butce.

const CATEGORICAL_LIGHT = ["#2563eb", "#0d9488", "#d97706", "#7c3aed", "#db2777", "#0891b2"];
const CATEGORICAL_DARK = ["#3b82f6", "#0d9488", "#d97706", "#8b5cf6", "#ec4899", "#0891b2"];

export const STATUS = {
  success: "#16a34a", warning: "#d97706", danger: "#dc2626",
  info: "#0891b2", neutral: "#71717a",
};

const isDark = () => document.documentElement.getAttribute("data-bs-theme") === "dark";

export function categorical(count) {
  const seq = isDark() ? CATEGORICAL_DARK : CATEGORICAL_LIGHT;
  return count ? seq.slice(0, count) : [...seq];
}
export const axisInk = () => (isDark() ? "#a1a1aa" : "#71717a");
export const gridLine = () => (isDark() ? "#27272a" : "#e4e4e7");
export const surfacePanel = () => (isDark() ? "#18181b" : "#ffffff");

/** Provenance colours: what produced the number decides its hue. */
export const PROVENANCE = {
  actual: () => (isDark() ? "#2dd4bf" : "#0d9488"),
  model: () => (isDark() ? "#60a5fa" : "#2563eb"),
  human: () => (isDark() ? "#fbbf24" : "#d97706"),
  budget: () => (isDark() ? "#a1a1aa" : "#71717a"),
  muted: () => (isDark() ? "#52525b" : "#d4d4d8"),
};

const NUM = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });
const NUM1 = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 1 });

export function compact(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "";
  const v = Number(value);
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${NUM1.format(v / 1e9)} mr`;
  if (abs >= 1e6) return `${NUM1.format(v / 1e6)} mn`;
  if (abs >= 1e4) return `${NUM1.format(v / 1e3)} b`;
  return NUM.format(v);
}

/**
 * 36 geçmiş + 18 gelecek dönem kategori ekseninde yan yana sığmaz; ApexCharts'in
 * hideOverlappingLabels'i yalnızca çakışanı gizler, kalan dizilim düzensiz olur.
 * Bunun yerine tik sayısını baştan sınırlıyoruz, böylece etiketler eşit aralıklı
 * ve okunur kalır.
 */
/** Binli degerlerde kisaltma, kucuk degerlerde ondalik. */
function axisFormatter(series) {
  let peak = 0;
  for (const s of series || []) {
    for (const v of s.points || []) {
      if (v !== null && v !== undefined && Number.isFinite(Number(v))) {
        peak = Math.max(peak, Math.abs(Number(v)));
      }
    }
  }
  return peak >= 10000 ? compact : (v) => NUM1.format(v);
}

export function tickAmount(labels, max = 12) {
  const n = Array.isArray(labels) ? labels.length : 0;
  return n > max ? max : undefined;
}

const registry = new Map();

function baseOptions(height) {
  return {
    chart: {
      height,
      width: "100%",
      fontFamily: "inherit",
      toolbar: { show: false },
      zoom: { enabled: false },
      animations: { enabled: !window.matchMedia("(prefers-reduced-motion: reduce)").matches },
      redrawOnParentResize: true,
      redrawOnWindowResize: true,
    },
    dataLabels: { enabled: false },
    grid: { borderColor: gridLine(), strokeDashArray: 3, padding: { left: 4, right: 4 } },
    legend: { position: "top", horizontalAlign: "left", fontSize: "12px", markers: { radius: 3 } },
    tooltip: { shared: true, intersect: false },
  };
}

/** Create or update a chart in place. Returns the ApexCharts instance. */
export function draw(el, options) {
  if (!el) return null;
  const key = el.dataset.chartKey || (el.dataset.chartKey = `c${registry.size}_${Math.random().toString(36).slice(2, 8)}`);
  const existing = registry.get(key);
  if (existing) {
    try {
      existing.destroy();
    } catch { /* already gone */ }
    registry.delete(key);
  }
  const chart = new ApexCharts(el, options);
  chart.render();
  registry.set(key, chart);
  return chart;
}

export function destroyAll() {
  for (const chart of registry.values()) {
    try { chart.destroy(); } catch { /* already gone */ }
  }
  registry.clear();
}

/** Re-render every live chart after a theme switch so axis ink follows. */
export function refreshTheme() {
  for (const chart of registry.values()) {
    try {
      chart.updateOptions(
        {
          grid: { borderColor: gridLine() },
          xaxis: { labels: { style: { colors: axisInk() } } },
          yaxis: { labels: { style: { colors: axisInk() } } },
          tooltip: { theme: isDark() ? "dark" : "light" },
        },
        false, false,
      );
    } catch { /* chart removed from the DOM */ }
  }
}

/**
 * The cockpit hero: realised history and forecast in one timeline, split by a
 * vertical rule at the current period, with the model interval as a band.
 */
export function forecastChart(el, {
  labels, actual, consensus, statistical, budget, lastYear, lower, upper,
  currentLabel, height = 340, unit = "", showBand = true, legendHost = null,
}) {
  const series = [];
  if (showBand && lower && upper && lower.some((v) => v !== null)) {
    series.push({
      name: "Model aralığı",
      type: "rangeArea",
      data: labels.map((x, i) => ({ x, y: [lower[i], upper[i]] })),
    });
  }
  // Gerçekleşen son geçmiş dönemde biter, konsensüs mevcut dönemde başlardı;
  // aradaki bir dönemlik boşluk grafiği kopuk gösteriyordu. Gerçekleşenin son
  // noktasını konsensüse de yazıp iki çizgiyi birleştiriyoruz.
  const bridged = [...consensus];
  const lastActual = actual.reduce((acc, v, i) => (v !== null && v !== undefined ? i : acc), -1);
  if (lastActual >= 0 && (bridged[lastActual] === null || bridged[lastActual] === undefined)) {
    bridged[lastActual] = actual[lastActual];
  }

  // Çizim sırası arkadan öne. Geçmişte konsensüs gerçekleşene çok yakın
  // seyrediyor; gerçekleşen en sona yazılmazsa altta kalıp kayboluyor.
  if (budget && budget.some((v) => v !== null)) {
    series.push({ name: "Bütçe", type: "line", data: labels.map((x, i) => ({ x, y: budget[i] })) });
  }
  if (lastYear && lastYear.some((v) => v !== null)) {
    series.push({ name: "Geçen yıl", type: "line", data: labels.map((x, i) => ({ x, y: lastYear[i] })) });
  }
  if (statistical && statistical.some((v) => v !== null)) {
    series.push({ name: "İstatistiksel", type: "line", data: labels.map((x, i) => ({ x, y: statistical[i] })) });
  }
  series.push({ name: "Konsensüs", type: "line", data: labels.map((x, i) => ({ x, y: bridged[i] })) });
  series.push({ name: "Gerçekleşen", type: "line", data: labels.map((x, i) => ({ x, y: actual[i] })) });

  // Alti cizgi yan yana duruyor; birbirine yakin maviler ayirt edilemiyordu.
  // Her seri ayri bir aileden: teal, siyah, mavi, gri, fusya.
  const colors = series.map((s) => {
    if (s.name === "Model aralığı") return isDark() ? "#60a5fa" : "#3b82f6";
    if (s.name === "İstatistiksel") return isDark() ? "#60a5fa" : "#2563eb";
    if (s.name === "Gerçekleşen") return PROVENANCE.actual();
    if (s.name === "Konsensüs") return isDark() ? "#f4f4f5" : "#18181b";
    if (s.name === "Bütçe") return PROVENANCE.budget();
    return isDark() ? "#f0abfc" : "#c026d3";  // geçen yıl
  });
  const dash = series.map((s) => (s.name === "Bütçe" || s.name === "Geçen yıl" ? 6 : 0));
  const widths = series.map((s) => {
    if (s.name === "Model aralığı") return 0;
    if (s.name === "Gerçekleşen") return 3;
    if (s.name === "Konsensüs") return 2.25;
    return 1.75;
  });

  // ApexCharts göstergesi her seriyi dolu bir nokta olarak çiziyor; kesikli mi
  // düz mü olduğu görünmüyordu. Göstergeyi kendimiz çiziyoruz: çizgi biçimi
  // neyse rozet de o. Tıklayınca seri gizlenir, Apex'in davranışı korunur.
  const chart = draw(el, {
    ...baseOptions(height),
    series,
    colors,
    legend: { show: false },
    stroke: { curve: "smooth", width: widths, dashArray: dash },
    fill: {
      type: series.map((s) => (s.name === "Model aralığı" ? "solid" : "solid")),
      opacity: series.map((s) => (s.name === "Model aralığı" ? 0.14 : 1)),
    },
    markers: { size: 0, hover: { size: 4 } },
    xaxis: {
      type: "category",
      tickAmount: tickAmount(labels),
      labels: {
        style: { colors: axisInk(), fontSize: "11px" },
        rotate: -45, rotateAlways: false, hideOverlappingLabels: true,
      },
      axisBorder: { show: false },
      tooltip: { enabled: false },
    },
    yaxis: {
      labels: { formatter: compact, style: { colors: axisInk(), fontSize: "11px" } },
      forceNiceScale: true,
    },
    annotations: currentLabel ? {
      xaxis: [{
        x: currentLabel,
        borderColor: axisInk(),
        strokeDashArray: 4,
        label: {
          text: "tahmin başlangıcı",
          position: "top",
          orientation: "horizontal",
          style: { fontSize: "10px", color: axisInk(), background: surfacePanel() },
        },
      }],
    } : {},
    tooltip: {
      shared: true,
      intersect: false,
      theme: isDark() ? "dark" : "light",
      // When a rangeArea series is in the combo, ApexCharts applies its two value
      // formatter to every series and the line values come back undefined. The
      // rows are built here instead, straight from the series data.
      custom: ({ dataPointIndex, w }) => sharedTooltip(series, colors, labels, dataPointIndex, unit, w),
    },
    responsive: [
      { breakpoint: 992, options: { chart: { height: Math.max(240, height - 60) } } },
    ],
  });

  if (legendHost) {
    buildLegend(legendHost, chart, series.map((s, i) => ({
      name: s.name,
      color: colors[i],
      dashed: dash[i] > 0,
      band: s.type === "rangeArea",
    })));
  }
  return chart;
}

/* Çizim sırası arkadan öne, okuma sırası ise önemden önemsize. İkisi aynı
   olmak zorunda değil; gösterge kendi sırasını kullanır. */
const LEGEND_ORDER = [
  "Gerçekleşen", "Konsensüs", "İstatistiksel", "Model aralığı", "Bütçe", "Geçen yıl",
];

/** Çizgi biçimini de gösteren gösterge. Tıklamak seriyi gizler. */
function buildLegend(host, chart, entries) {
  host.textContent = "";
  host.className = "chart-legend";
  const ranked = [...entries].sort(
    (a, b) => (LEGEND_ORDER.indexOf(a.name) + 1 || 99) - (LEGEND_ORDER.indexOf(b.name) + 1 || 99),
  );
  for (const e of ranked) {
    const swatch = document.createElement("span");
    swatch.className = `chart-legend-mark${e.dashed ? " dashed" : ""}${e.band ? " band" : ""}`;
    swatch.style.setProperty("--mark", e.color);
    const item = document.createElement("button");
    item.type = "button";
    item.className = "chart-legend-item";
    item.append(swatch, document.createTextNode(e.name));
    item.onclick = () => {
      item.classList.toggle("off");
      try { chart.toggleSeries(e.name); } catch { /* chart gone */ }
    };
    host.append(item);
  }
}

const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));

function sharedTooltip(series, colors, labels, index, unit, w) {
  const suffix = unit ? ` ${escapeHtml(unit)}` : "";
  const hidden = new Set(
    (w?.globals?.collapsedSeries || []).map((s) => s.index),
  );
  const rows = series.map((s, i) => {
    if (hidden.has(i)) return "";
    const point = s.data[index];
    const value = point ? point.y : null;
    let text = "";
    if (Array.isArray(value)) {
      if (value[0] !== null && value[0] !== undefined) {
        text = `${NUM1.format(value[0])} - ${NUM1.format(value[1])}`;
      }
    } else if (value !== null && value !== undefined) {
      text = NUM1.format(value);
    }
    if (!text) return "";
    return `<div class="apexcharts-tooltip-series-group apexcharts-active" style="display:flex">`
      + `<span class="apexcharts-tooltip-marker" style="background-color:${escapeHtml(colors[i])}"></span>`
      + `<div class="apexcharts-tooltip-text"><div class="apexcharts-tooltip-y-group">`
      + `<span class="apexcharts-tooltip-text-y-label">${escapeHtml(s.name)}: </span>`
      + `<span class="apexcharts-tooltip-text-y-value">${text}${suffix}</span>`
      + `</div></div></div>`;
  }).join("");
  const title = labels[index] === undefined ? "" : escapeHtml(labels[index]);
  if (!rows) return `<div class="apexcharts-tooltip-title">${title}</div>`
    + `<div class="apexcharts-tooltip-series-group apexcharts-active" style="display:flex">`
    + `<div class="apexcharts-tooltip-text"><span class="apexcharts-tooltip-text-y-label">Veri yok</span></div></div>`;
  return `<div class="apexcharts-tooltip-title">${title}</div>${rows}`;
}

/** Small sparkline for a KPI card. */
export function sparkline(el, { values, color, height = 38 }) {
  const clean = (values || []).map((v) => (v === null || v === undefined ? null : Number(v)));
  if (!clean.some((v) => v !== null)) return null;
  return draw(el, {
    chart: {
      type: "area", height, sparkline: { enabled: true }, fontFamily: "inherit",
      animations: { enabled: false },
    },
    series: [{ name: "", data: clean }],
    colors: [color || PROVENANCE.model()],
    stroke: { curve: "smooth", width: 1.75 },
    fill: { type: "gradient", gradient: { shadeIntensity: 1, opacityFrom: 0.35, opacityTo: 0.02 } },
    tooltip: {
      theme: isDark() ? "dark" : "light",
      x: { show: false },
      y: { formatter: (v) => NUM1.format(v), title: { formatter: () => "" } },
    },
  });
}

/** Period bars with a cumulative line on a second axis. */
export function barLineChart(el, {
  labels, bars, line, barLabel, lineLabel, height = 260, unit = "", maxTicks = 12,
}) {
  return draw(el, {
    ...baseOptions(height),
    series: [
      { name: barLabel, type: "column", data: bars },
      { name: lineLabel, type: "line", data: line },
    ],
    colors: [PROVENANCE.model(), PROVENANCE.human()],
    stroke: { width: [0, 2], curve: "smooth" },
    plotOptions: { bar: { borderRadius: 3, columnWidth: "55%" } },
    xaxis: {
      categories: labels,
      tickAmount: tickAmount(labels, maxTicks),
      labels: { style: { colors: axisInk(), fontSize: "11px" }, hideOverlappingLabels: true },
      axisBorder: { show: false },
    },
    yaxis: [
      { labels: { formatter: compact, style: { colors: axisInk(), fontSize: "11px" } }, title: { text: barLabel, style: { color: axisInk(), fontSize: "11px", fontWeight: 500 } } },
      { opposite: true, labels: { formatter: compact, style: { colors: axisInk(), fontSize: "11px" } }, title: { text: lineLabel, style: { color: axisInk(), fontSize: "11px", fontWeight: 500 } } },
    ],
    tooltip: {
      shared: true, intersect: false, theme: isDark() ? "dark" : "light",
      y: { formatter: (v) => (v === null || v === undefined ? "-" : `${NUM1.format(v)} ${unit}`.trim()) },
    },
  });
}

/** Simple multi series line, used for accuracy trends and the KPI timelines. */
export function lineChart(el, {
  labels, series, height = 260, unit = "", colors, dividerLabel, maxTicks = 12,
}) {
  return draw(el, {
    ...baseOptions(height),
    series: series.map((s) => ({ name: s.label, data: s.points })),
    colors: colors || categorical(series.length),
    stroke: { curve: "smooth", width: 2 },
    markers: { size: 0, hover: { size: 4 } },
    annotations: dividerLabel ? {
      xaxis: [{ x: dividerLabel, borderColor: axisInk(), strokeDashArray: 4 }],
    } : {},
    xaxis: {
      categories: labels,
      tickAmount: tickAmount(labels, maxTicks),
      labels: {
        style: { colors: axisInk(), fontSize: "11px" },
        rotate: 0, rotateAlways: false, hideOverlappingLabels: true,
      },
      axisBorder: { show: false },
    },
    // Ciro ekseninde 7.000.000 yazmak sutunu sisiriyor, WMAPE ekseninde ise
    // kisaltma 3,3'u 3 yapip ondaligi yutuyor. Buyukluge gore seciyoruz.
    yaxis: {
      labels: {
        formatter: axisFormatter(series),
        style: { colors: axisInk(), fontSize: "11px" },
      },
    },
    tooltip: {
      shared: true, intersect: false, theme: isDark() ? "dark" : "light",
      y: { formatter: (v) => (v === null || v === undefined ? "-" : `${NUM1.format(v)} ${unit}`.trim()) },
    },
  });
}

/** Horizontal bars, used for variance and biggest changes. */
export function barChart(el, { labels, values, height = 260, unit = "", horizontal = false }) {
  const colors = values.map((v) => (Number(v) >= 0 ? PROVENANCE.model() : PROVENANCE.human()));
  return draw(el, {
    ...baseOptions(height),
    series: [{ name: "Değer", data: values }],
    colors,
    legend: { show: false },
    plotOptions: {
      bar: { borderRadius: 3, horizontal, distributed: true, columnWidth: "55%", barHeight: "60%" },
    },
    xaxis: {
      categories: labels,
      labels: {
        style: { colors: axisInk(), fontSize: "11px" },
        formatter: horizontal ? compact : undefined,
        hideOverlappingLabels: true,
      },
      axisBorder: { show: false },
    },
    yaxis: {
      labels: { formatter: horizontal ? undefined : compact, style: { colors: axisInk(), fontSize: "11px" } },
    },
    tooltip: {
      theme: isDark() ? "dark" : "light",
      y: { formatter: (v) => `${NUM1.format(v)} ${unit}`.trim() },
    },
  });
}
