// Shared UI helpers. Every primitive emits Metis (Bootstrap 5) markup, so a
// screen built from these helpers inherits the theme without touching its code.

import * as charts from "./charts.js";

const NUM = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });
const NUM1 = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 1 });
const PCT = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 1, signDisplay: "exceptZero" });

export const fmt = {
  n(v, dec = 0) {
    if (v === null || v === undefined || v === "") return "";
    const num = Number(v);
    if (!Number.isFinite(num)) return "";
    // -0,04 bir ondaliga yuvarlaninca "-0" cikiyor; isaret anlamsiz, atilir.
    const scale = dec ? 10 : 1;
    const rounded = Math.round(num * scale) / scale || 0;
    return dec ? NUM1.format(rounded) : NUM.format(rounded);
  },
  compact: charts.compact,
  pct(v) {
    if (v === null || v === undefined) return "";
    return `%${PCT.format(Number(v))}`;
  },
  money(v, ccy = "TRY") {
    if (v === null || v === undefined) return "";
    const sym = { TRY: "TL", USD: "USD", EUR: "EUR" }[ccy] || ccy;
    return `${NUM.format(Number(v))} ${sym}`;
  },
  date(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString("tr-TR", {
      day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
      timeZone: "Europe/Istanbul",
    });
  },
  day(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("tr-TR", { timeZone: "Europe/Istanbul" });
  },
  ms(v) {
    if (v === null || v === undefined) return "";
    return v < 1000 ? `${v} ms` : `${NUM1.format(v / 1000)} sn`;
  },
};

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else node.setAttribute(k, String(v));
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(typeof c === "string" || typeof c === "number" ? String(c) : c);
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/* Metis, Bootstrap Icons'un tamamini degil kendi sayfalarinda gecen ~158 glifi
   derliyor. Setde olmayan bir ad bos kare olarak cikar ve bu sessizce olur, bu
   yuzden istedigimiz adlari burada mevcut en yakin glife bagliyoruz: cagri
   yerleri anlamli adi kullanmaya devam eder, ekranda bosluk kalmaz. */
const ICON_ALIAS = {
  "arrow-left-right": "arrow-repeat",
  "arrow-right-circle": "arrow-right",
  "bar-chart-line": "graph-up",
  "bar-chart-steps": "graph-up-arrow",
  "bookmark-plus": "bookmark",
  braces: "code-slash",
  bullseye: "check-all",
  "calendar-x": "calendar",
  "chat-left-text": "chat-dots",
  check2: "check",
  "clipboard-check": "clipboard",
  "collection-play": "play-circle",
  cpu: "puzzle",
  "database-up": "cloud-upload",
  "diagram-2": "share",
  "diagram-3": "share",
  "file-earmark-binary": "file-earmark",
  "file-earmark-excel": "file-earmark-spreadsheet",
  "filetype-csv": "file-text",
  "grid-3x3": "grid",
  "hand-thumbs-down": "x-circle",
  "hand-thumbs-up": "check-circle",
  "hdd-network": "hdd",
  inbox: "archive",
  "journal-text": "clock-history",
  layers: "archive",
  "pencil-square": "pencil",
  plug: "power",
  sliders: "tools",
  stack: "list-check",
  wallet2: "currency-dollar",
};

export function icon(name) {
  return el("i", { class: `bi bi-${ICON_ALIAS[name] || name}`, "aria-hidden": "true" });
}

/* Metis yalnızca .form-control / .form-select / .form-check-input tasiyan
   denetimleri biçimlendirir. Ekranlar ham <input> da uretebildigi icin dogru
   sınıfı burada, tek yerde takiyoruz. */
const CONTROL_CLASS = {
  checkbox: "form-check-input", radio: "form-check-input",
  range: "form-range", color: "form-control form-control-color",
};
const DRESSED = /\b(form-control|form-select|form-check-input|form-range|btn-check)\b/;

export function dress(node) {
  if (!(node instanceof Element)) return node;
  for (const c of [node, ...node.querySelectorAll("input, textarea, select")]) {
    const tag = c.tagName;
    if (tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") continue;
    if (DRESSED.test(c.className)) continue;
    const type = tag === "INPUT" ? c.getAttribute("type") || "text" : "";
    const cls = CONTROL_CLASS[type] || (tag === "SELECT" ? "form-select" : "form-control");
    c.classList.add(...cls.split(" "));
    const base = cls.split(" ")[0];
    if (base === "form-control" || base === "form-select") c.classList.add(`${base}-sm`);
  }
  return node;
}

export function field(label, control, hint) {
  return el("div", { class: "d-flex flex-column" }, [
    el("label", { class: "form-label mb-1 small text-muted", text: label }),
    dress(control),
    hint ? el("div", { class: "form-text", text: hint }) : null,
  ]);
}

export function select(options, value, onChange, attrs = {}) {
  const node = el("select", {
    class: "form-select form-select-sm",
    ...attrs,
    onchange: (e) => onChange(e.target.value),
  });
  for (const o of options) {
    const opt = el("option", { value: o.value, text: o.label });
    if (String(o.value) === String(value)) opt.selected = true;
    node.append(opt);
  }
  return node;
}

/**
 * Çoklu seçim: kapalıyken seçilenleri çip olarak gösterir, açılınca işaretlenir.
 *
 * Checkbox listesi her seçeneği aynı anda ekranda tutuyordu; dört model için
 * dört satır, filtre çubuğunun yarısı. Burada kapalı hal seçimin kendisini
 * söyler, liste yalnızca değiştirirken açılır.
 *
 * `minOne` ile son seçenek işareti kaldırılamaz — karşılaştıracak bir şey
 * kalmayan ekran boş bir grafikten ibaret olurdu.
 */
export function multiSelect(options, values, onChange, { minOne = true, placeholder = "Seçin", label = "" } = {}) {
  const wrap = el("div", { class: "dropdown multi-select" });
  const toggle = el("button", {
    type: "button", class: "form-select form-select-sm text-start multi-select-toggle",
    "data-bs-toggle": "dropdown", "data-bs-auto-close": "outside",
    "aria-expanded": "false", "aria-label": label || placeholder,
  });
  const menu = el("div", { class: "dropdown-menu p-2 multi-select-menu" });

  const paint = () => {
    clear(toggle);
    const picked = options.filter((o) => values.includes(o.value));
    if (!picked.length) {
      toggle.append(el("span", { class: "text-muted", text: placeholder }));
      return;
    }
    toggle.append(el("span", { class: "d-inline-flex flex-wrap gap-1 align-middle" },
      picked.map((o) => el("span", {
        class: "chip", title: o.title || o.label,
      }, [
        o.color ? el("span", { class: "legend-dot", style: `background:${o.color}` }) : null,
        el("span", { text: o.label }),
      ]))));
  };

  for (const o of options) {
    const id = `ms-${Math.random().toString(36).slice(2, 8)}`;
    const input = el("input", { type: "checkbox", class: "form-check-input mt-0", id });
    input.checked = values.includes(o.value);
    input.addEventListener("change", () => {
      const next = input.checked
        ? [...values, o.value]
        : values.filter((v) => v !== o.value);
      if (minOne && !next.length) { input.checked = true; return; }
      onChange(next);
    });
    menu.append(el("label", {
      class: "dropdown-item d-flex align-items-center gap-2 rounded", for: id,
      title: o.title || "",
    }, [
      input,
      o.color ? el("span", { class: "legend-dot", style: `background:${o.color}` }) : null,
      el("span", { class: "small", text: o.label }),
    ]));
  }

  paint();
  wrap.append(toggle, menu);
  return wrap;
}

/** Radio backed segmented control, the theme's own pattern for ranges. */
export function segmented(name, options, value, onChange) {
  const group = el("div", { class: "btn-group btn-group-sm segmented", role: "group" });
  for (const o of options) {
    const id = `${name}-${String(o.value).replace(/\W+/g, "")}`;
    const input = el("input", {
      type: "radio", class: "btn-check", name, id, autocomplete: "off",
      onchange: () => onChange(o.value),
    });
    if (String(o.value) === String(value)) input.checked = true;
    group.append(input, el("label", { class: "btn btn-outline-secondary", for: id, text: o.label }));
  }
  return group;
}

export function tile({ label, value, note, unit, kind, trend }) {
  const toneClass = trend === "pos" ? "text-success-emphasis"
    : trend === "neg" ? "text-danger-emphasis" : "text-muted";
  return el("div", { class: "card stats-card h-100" }, [
    el("div", { class: "card-body" }, [
      el("div", { class: "d-flex align-items-start justify-content-between gap-2" }, [
        el("div", { class: "min-w-0" }, [
          el("p", { class: "h6 mb-0 text-muted text-uppercase", text: label }),
          el("div", { class: "kpi-value" }, [
            String(value ?? "-"),
            unit ? el("span", { class: "kpi-unit", text: ` ${unit}` }) : null,
          ]),
          note ? el("small", { class: toneClass, text: note }) : null,
        ]),
        kind ? el("div", { class: "stats-icon" }, [icon(KIND_ICON[kind] || "graph-up")]) : null,
      ]),
    ]),
  ]);
}

const KIND_ICON = {
  model: "activity", human: "person-gear", actual: "clipboard-check",
  budget: "wallet2", alert: "exclamation-triangle",
};

export function card(title, note, children, opts = {}) {
  const head = title || note || opts.actions
    ? el("div", { class: "card-header d-flex flex-wrap justify-content-between align-items-center gap-2" }, [
      el("div", { class: "min-w-0" }, [
        title ? el("h2", { class: "h5 card-title mb-0 text-truncate", text: title }) : null,
        note ? el("div", { class: "text-muted small text-truncate", text: note }) : null,
      ]),
      opts.actions || null,
    ])
    : null;
  return el("section", { class: `card ${opts.class || ""}` }, [
    head,
    el("div", { class: opts.flush ? "card-body p-0" : "card-body" }, [].concat(children)),
  ]);
}

/** Sütun, grubunun ilk sütunu mu? Grup sınırına ince bir çizgi koymak için. */
function isGroupStart(columns, i) {
  return i > 0 && columns[i].group !== columns[i - 1].group;
}

/**
 * groups verildiğinde başlık iki satır olur: üstte gruplanmış etiketler
 * (ör. "Adet geçmiş"), altta alt sütunlar (Toplam, Ortalama). groups dizisi
 * columns ile aynı sırada ilerler ve span'leri toplamı sütun sayısına eşit olmalı.
 */
export function table(columns, rows, { totalRow, small = true, groups } = {}) {
  const headRows = [];
  if (groups?.length) {
    headRows.push(el("tr", {}, groups.map((g) => el("th", {
      class: `text-center border-start ${g.label ? "" : "border-0"}`,
      scope: "colgroup", colspan: String(g.span || 1), text: g.label || "",
    }))));
  }
  headRows.push(el("tr", {}, columns.map((c, i) => el("th", {
    class: [
      c.numeric ? "text-end" : "",
      groups?.length && isGroupStart(columns, i) ? "border-start" : "",
    ].filter(Boolean).join(" "),
    scope: "col", text: c.label,
  }))));
  const thead = el("thead", { class: "table-light" }, headRows);
  const makeRow = (r, extraClass = "") => el("tr", { class: extraClass }, columns.map((c, i) => {
    const raw = r[c.key];
    const value = c.render ? c.render(raw, r) : c.numeric ? fmt.n(raw, c.dec ?? 1) : raw ?? "";
    const cell = el("td", {
      class: [
        c.numeric ? "text-end tabular" : "",
        groups?.length && isGroupStart(columns, i) ? "border-start" : "",
      ].filter(Boolean).join(" "),
    });
    if (value instanceof Node) cell.append(value);
    else cell.textContent = value === null || value === undefined ? "" : String(value);
    if (c.numeric && Number(raw) < 0) cell.classList.add("text-danger-emphasis");
    return cell;
  }));
  const body = el("tbody", {}, rows.map((r) => makeRow(r, r._total ? "fw-semibold" : "")));
  if (totalRow) body.append(makeRow(totalRow, "fw-semibold border-top"));
  return el("div", { class: "table-responsive" }, [
    el("table", { class: `table table-hover align-middle mb-0 ${small ? "table-sm" : ""}` }, [thead, body]),
  ]);
}

export const SEVERITY = { high: "yüksek", medium: "orta", low: "düşük" };

/** Etiket-deger listesi. pairs: [[etiket, deger], ...]; bos degerler tire olur. */
export function defList(pairs) {
  return el("dl", { class: "kv" }, pairs.filter(Boolean).flatMap(([k, v]) => [
    el("dt", { text: k }),
    v instanceof Node ? el("dd", {}, [v]) : el("dd", { text: v === null || v === undefined || v === "" ? "-" : String(v) }),
  ]));
}

/** Modal ve kart içindeki alt bölüm başlığı. */
export function sectionLabel(text) {
  return el("div", { class: "text-muted text-uppercase fw-semibold small mb-1", text });
}

/** Tanı ve yük gövdesi gibi ham JSON'lar icin okunabilir, kaydirilabilir blok. */
export function jsonBlock(data) {
  return el("pre", {
    class: "bg-body-tertiary border rounded p-3 mb-0 small",
    style: "font-family:var(--bs-font-monospace);white-space:pre-wrap;max-height:50vh;overflow:auto",
    text: typeof data === "string" ? data : JSON.stringify(data ?? {}, null, 2),
  });
}

export function empty(message, iconName = "inbox") {
  return el("div", { class: "text-center text-muted py-4" }, [
    el("div", { class: "fs-3 opacity-50 mb-2" }, [icon(iconName)]),
    el("div", { text: message }),
  ]);
}

export function loading(message = "Yükleniyor") {
  return el("div", { class: "d-flex align-items-center gap-2 text-muted py-4 justify-content-center" }, [
    el("div", { class: "spinner-border spinner-border-sm", role: "status" }),
    el("span", { text: message }),
  ]);
}

// ------------------------------------------------------------------ toast
export function toast(message, kind = "") {
  const root = document.getElementById("toast-root");
  const tone = kind === "err" ? "danger" : kind === "ok" ? "success" : "secondary";
  const node = el("div", {
    class: `toast align-items-center border-${tone}`, role: "alert",
    "aria-live": "polite", "aria-atomic": "true",
  }, [
    el("div", { class: "d-flex" }, [
      el("div", { class: "toast-body d-flex gap-2 align-items-start" }, [
        el("i", { class: `bi bi-${kind === "err" ? "exclamation-triangle" : kind === "ok" ? "check-circle" : "info-circle"} text-${tone}` }),
        el("span", { text: message }),
      ]),
      el("button", {
        type: "button", class: "btn-close me-2 m-auto", "data-bs-dismiss": "toast", "aria-label": "Kapat",
      }),
    ]),
  ]);
  root.append(node);
  const t = new bootstrap.Toast(node, { delay: kind === "err" ? 7000 : 4200 });
  node.addEventListener("hidden.bs.toast", () => node.remove());
  t.show();
}

// ------------------------------------------------------------------ modal
export function modal({ title, subtitle, body, actions, wide }) {
  const root = document.getElementById("modal-root");
  let resolveFn;
  const done = new Promise((r) => { resolveFn = r; });
  let settled = null;

  const foot = el("div", { class: "modal-footer" });
  for (const a of actions || [{ label: "Kapat" }]) {
    foot.append(el("button", {
      class: `btn ${a.primary ? "btn-primary" : a.danger ? "btn-outline-danger" : "btn-outline-secondary"}`,
      type: "button",
      onclick: async () => {
        if (!a.onClick) { settled = a.value ?? null; instance.hide(); return; }
        const result = await a.onClick();
        if (result === false) return;
        settled = result ?? a.value ?? true;
        instance.hide();
      },
    }, a.label));
  }

  const node = el("div", { class: "modal fade", tabindex: "-1", "aria-hidden": "true" }, [
    el("div", { class: `modal-dialog modal-dialog-centered modal-dialog-scrollable ${wide ? "modal-xl" : ""}` }, [
      el("div", { class: "modal-content" }, [
        el("div", { class: "modal-header" }, [
          el("div", {}, [
            el("h5", { class: "modal-title", text: title }),
            subtitle ? el("div", { class: "text-muted small", text: subtitle }) : null,
          ]),
          el("button", { type: "button", class: "btn-close", "data-bs-dismiss": "modal", "aria-label": "Kapat" }),
        ]),
        el("div", { class: "modal-body d-flex flex-column gap-3" }, [].concat(body || [])),
        foot,
      ]),
    ]),
  ]);
  root.append(node);
  dress(node.querySelector(".modal-body"));
  const instance = new bootstrap.Modal(node);
  node.addEventListener("hidden.bs.modal", () => {
    node.remove();
    resolveFn(settled);
  });
  node.addEventListener("shown.bs.modal", () => {
    const first = node.querySelector(".modal-body input, .modal-body select, .modal-body textarea");
    if (first) first.focus();
  });
  instance.show();
  return done;
}

export async function confirmDialog(title, message, confirmLabel = "Devam et") {
  return modal({
    title,
    body: [el("p", { class: "mb-0", text: message })],
    actions: [
      { label: "Vazgeç", value: false },
      { label: confirmLabel, primary: true, value: true },
    ],
  });
}

// ------------------------------------------------------------------ charts
// Layer to colour, resolved on access so a theme switch is picked up.
const PROVENANCE_OF = {
  actual: charts.PROVENANCE.actual,
  last_year: charts.PROVENANCE.muted,
  statistical: charts.PROVENANCE.model,
  sales: charts.PROVENANCE.human,
  marketing: () => "#9a5fb0",
  planner: charts.PROVENANCE.human,
  consensus: () => (document.documentElement.getAttribute("data-bs-theme") === "dark" ? "#f4f4f5" : "#18181b"),
  budget: charts.PROVENANCE.budget,
  prior_version: charts.PROVENANCE.muted,
  naive: charts.PROVENANCE.muted,
};

export const SERIES_COLOR = new Proxy({}, {
  get: (_t, code) => (PROVENANCE_OF[code] ? PROVENANCE_OF[code]() : undefined),
  has: (_t, code) => code in PROVENANCE_OF,
  ownKeys: () => Reflect.ownKeys(PROVENANCE_OF),
  getOwnPropertyDescriptor: () => ({ enumerable: true, configurable: true }),
});

const colorOf = (code) => (PROVENANCE_OF[code] ? PROVENANCE_OF[code]() : charts.PROVENANCE.model());

/* ApexCharts SVG'ye rengi oznitelik olarak yazar, bu yuzden var(--x) referansi
   cozulmez. Ekranlar token adiyla yazabilsin diye degeri burada okuyoruz. */
function safeColor(value, fallback) {
  const raw = typeof value === "string" ? value.trim() : "";
  if (/^(#|rgb|hsl)/i.test(raw)) return raw;
  const token = raw.match(/^var\(\s*(--[\w-]+)\s*\)$/);
  if (token) {
    const resolved = getComputedStyle(document.documentElement).getPropertyValue(token[1]).trim();
    if (resolved) return resolved;
  }
  return fallback;
}

/** Render a chart once its host is attached to the document. */
export function mountChart(host, render) {
  let frames = 0;
  const tick = () => {
    if (host.isConnected) { render(host); return; }
    if (frames++ > 40) return;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  return host;
}

/**
 * Backwards compatible line chart. Existing screens pass
 * {labels, series:[{label,color,points,dashed}], dividerIndex, height}.
 */
export function lineChart({ labels, series, dividerIndex, height = 260, unit = "" }) {
  const host = el("div", { class: "chart-host" });
  return mountChart(host, () => {
    charts.draw(host, {
      chart: {
        type: "line", height, width: "100%", fontFamily: "inherit",
        toolbar: { show: false }, zoom: { enabled: false },
        animations: { enabled: false }, redrawOnParentResize: true,
      },
      series: series.map((s) => ({ name: s.label, data: s.points })),
      colors: series.map((s, i) => safeColor(s.color, charts.categorical(6)[i % 6])),
      stroke: {
        curve: "smooth",
        width: series.map((s) => s.width || 2),
        dashArray: series.map((s) => (s.dashed ? 5 : 0)),
      },
      markers: { size: 0, hover: { size: 4 } },
      dataLabels: { enabled: false },
      legend: { position: "top", horizontalAlign: "left", fontSize: "12px", markers: { radius: 3 } },
      grid: { borderColor: charts.gridLine(), strokeDashArray: 3 },
      xaxis: {
        categories: labels,
        tickAmount: charts.tickAmount(labels),
        labels: { style: { colors: charts.axisInk(), fontSize: "11px" }, hideOverlappingLabels: true },
        axisBorder: { show: false },
        tooltip: { enabled: false },
      },
      yaxis: { labels: { formatter: charts.compact, style: { colors: charts.axisInk(), fontSize: "11px" } } },
      annotations: Number.isFinite(dividerIndex) && dividerIndex >= 0 && labels[dividerIndex] ? {
        xaxis: [{
          x: labels[dividerIndex],
          borderColor: charts.axisInk(),
          strokeDashArray: 4,
        }],
      } : {},
      tooltip: {
        shared: true, intersect: false,
        theme: document.documentElement.getAttribute("data-bs-theme") === "dark" ? "dark" : "light",
        y: { formatter: (v) => (v === null || v === undefined ? "-" : `${fmt.n(v, 1)} ${unit}`.trim()) },
      },
    });
  });
}

export function barChart({ labels, values, height = 240, unit = "", horizontal = false }) {
  const host = el("div", { class: "chart-host" });
  return mountChart(host, () => charts.barChart(host, { labels, values, height, unit, horizontal }));
}

export function layerColor(code) {
  return colorOf(code);
}

export function heatColor(value, scale = 25) {
  if (value === null || value === undefined) return "transparent";
  const v = Math.max(-1, Math.min(1, Number(value) / scale));
  if (v >= 0) return `color-mix(in srgb, var(--bs-danger) ${Math.round(Math.abs(v) * 55)}%, var(--surface-panel))`;
  return `color-mix(in srgb, var(--bs-primary) ${Math.round(Math.abs(v) * 55)}%, var(--surface-panel))`;
}

export function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function pageHeader(title, subtitle, actions) {
  return el("div", {
    class: "d-flex flex-column flex-md-row justify-content-between align-items-start align-items-md-center gap-3 mb-3",
  }, [
    el("div", { class: "min-w-0" }, [
      el("h1", { class: "h4 mb-0", text: title }),
      subtitle ? el("p", { class: "text-muted mb-0 small", text: subtitle }) : null,
    ]),
    actions ? el("div", { class: "d-flex flex-wrap gap-2 flex-shrink-0 align-items-end" }, [].concat(actions)) : null,
  ]);
}

export function tabs(items, activeId, onSelect) {
  return el("ul", { class: "nav nav-pills gap-1 mb-0" }, items.map((t) => el("li", { class: "nav-item" }, [
    el("button", {
      class: `nav-link ${t.id === activeId ? "active" : ""}`,
      type: "button",
      onclick: () => onSelect(t.id),
    }, [t.icon ? icon(t.icon) : null, el("span", { class: t.icon ? "ms-2" : "", text: t.label })]),
  ])));
}
