// Shell: login, sidebar, topbar, theme, shared state, assistant panel.

import { api, ApiError, session } from "./api.js";
import * as charts from "./charts.js";
import { clear, el, icon, loading, pageHeader, toast } from "./ui.js";

import { render as renderCockpit } from "./views/cockpit.js";
import { render as renderPlanning } from "./views/planning.js";
import { render as renderScenarios } from "./views/scenarios.js";
import { render as renderVersions } from "./views/versions.js";
import { render as renderAnalysis } from "./views/analysis.js";
import { render as renderReports } from "./views/reports.js";
import { render as renderAdmin } from "./views/admin.js";
import { render as renderV2 } from "./views/v2.js";

export const state = {
  user: null,
  cycle: null,
  layers: [],
  dimensions: [],
  measures: [],
  reasonCodes: [],
  versions: [],
  ctx: {
    versionId: null,
    scope: {},
    level: { product: "category", geo: "region" },
    grain: "month",
    measure: "units",
    currency: "TRY",
    view: "nominal",
    layerCodes: ["actual", "last_year", "statistical", "sales", "planner", "consensus", "budget"],
    historyPeriods: 12,
  },
  // The cockpit keeps its own selection so leaving and returning is stable.
  cockpit: {
    measure: "units",
    grain: "month",
    // Pencere iki donem etiketi. Bos birakilinca sunucu mevcut donemin
    // etrafinda varsayilan pencereyi kurar ve uclari geri yazar.
    startLabel: null,
    endLabel: null,
    // Karsilastirilan modeller; tablo satirlari ve grafik cizgileri bu secim.
    models: ["statistical", "consensus"],
    gapModel: "consensus",
    // Toplam mi donem ortalamasi mi; tum sayfa buna uyar.
    aggregate: "total",
    scope: {},
    view: "nominal",
    currency: "USD",
  },
};

const AREAS = [
  {
    id: "cockpit", label: "Kokpit", icon: "speedometer2", render: renderCockpit,
    sub: "Agrege konsensüs tahmini ve KPI zaman serileri",
  },
  {
    id: "planning", label: "Planlama", icon: "table", render: renderPlanning,
    sub: "Tahmin grid'i, istisnalar, kayıtlı görünümler, saha bilgileri",
    children: [
      { id: "grid", hash: "#/planning/grid", label: "Tahmin grid'i", icon: "grid-3x3" },
      { id: "exceptions", hash: "#/planning/exceptions", label: "İstisnalar", icon: "exclamation-triangle" },
      { id: "views", hash: "#/planning/views", label: "Kayıtlı görünümler", icon: "bookmark" },
      { id: "insights", hash: "#/planning/insights", label: "Saha bilgileri", icon: "chat-left-text" },
    ],
  },
  {
    id: "scenarios", label: "Senaryo", icon: "diagram-3", render: renderScenarios,
    sub: "Liste, editör, çalıştırma geçmişi, karşılaştırma",
  },
  {
    id: "versions", label: "Versiyon", icon: "layers", render: renderVersions,
    sub: "Taslak, saklanan, konsensüs, snapshot",
  },
  {
    id: "analysis", label: "Analiz", icon: "bar-chart-line", render: renderAnalysis,
    sub: "Doğruluk ve FVA, varyans ve şelale, bütçe gap",
    children: [
      { id: "accuracy", hash: "#/analysis/accuracy", label: "Doğruluk ve FVA", icon: "bullseye" },
      { id: "variance", hash: "#/analysis/variance", label: "Varyans ve şelale", icon: "arrow-left-right" },
      { id: "budget", hash: "#/analysis/budget", label: "Bütçe gap", icon: "wallet2" },
    ],
  },
  {
    id: "reports", label: "Rapor", icon: "file-earmark-text", render: renderReports,
    sub: "Standart raporlar, dışa aktarım, API durumu",
  },
  {
    id: "admin", label: "Yönetim", icon: "gear", render: renderAdmin,
    sub: "Bağlantılar, modeller, takvim, kullanıcı ve roller, audit",
    roles: ["admin", "finance"],
    children: [
      { id: "connections", hash: "#/admin/connections", label: "Bağlantılar", icon: "hdd-network" },
      { id: "models", hash: "#/admin/models", label: "Modeller", icon: "cpu" },
      { id: "calendar", hash: "#/admin/calendar", label: "Takvim", icon: "calendar3" },
      { id: "dimensions", hash: "#/admin/dimensions", label: "Boyutlar", icon: "diagram-2" },
      { id: "drivers", hash: "#/admin/drivers", label: "Sürücüler", icon: "sliders" },
      { id: "rules", hash: "#/admin/rules", label: "Katman kuralları", icon: "stack" },
      { id: "users", hash: "#/admin/users", label: "Kullanıcı ve roller", icon: "people" },
      { id: "audit", hash: "#/admin/audit", label: "Audit log", icon: "journal-text" },
    ],
  },
  // Alternatif arayuz yonu, bagimsiz bir prototip olarak iframe icinde.
  // Kendi verisiyle calisir, v1 ile hicbir sey paylasmaz.
  {
    id: "v2", label: "v2", icon: "window", render: renderV2,
    sub: "Alternatif arayüz prototipi",
    children: [
      { id: "kokpit", hash: "#/v2/kokpit", label: "Kokpit", icon: "speedometer2" },
      { id: "tahminler", hash: "#/v2/tahminler", label: "Tahminler", icon: "table" },
      { id: "karsilastir", hash: "#/v2/karsilastir", label: "Karşılaştır", icon: "arrow-left-right" },
      { id: "dogruluk", hash: "#/v2/dogruluk", label: "Doğruluk", icon: "bullseye" },
      { id: "raporlar", hash: "#/v2/raporlar", label: "Raporlar", icon: "file-earmark-text" },
      { id: "yonetim", hash: "#/v2/yonetim", label: "Yönetim", icon: "gear" },
    ],
  },
];

const viewNode = () => document.getElementById("view");

function currentArea() {
  const id = (location.hash.replace("#/", "").split("/")[0]) || "cockpit";
  return AREAS.find((a) => a.id === id) || AREAS[0];
}

export function navigate(path) {
  location.hash = `#/${path}`;
}

export async function refreshShared() {
  const [cycle, layers, dims, measures, reasons, versions] = await Promise.all([
    api.cycle().catch(() => null),
    api.layers(),
    api.dimensions(),
    api.measures(),
    api.reasonCodes(),
    api.versions(),
  ]);
  state.cycle = cycle;
  state.layers = layers.layers;
  state.consensusRule = layers.consensus_rule;
  state.dimensions = dims.dimensions;
  state.measures = measures.measures;
  state.reasonCodes = reasons.reason_codes;
  state.versions = versions.versions;

  if (!state.ctx.versionId || !state.versions.some((v) => v.id === state.ctx.versionId)) {
    const preferred = state.versions.find((v) => v.kind === "baseline")
      || state.versions.find((v) => ["consensus", "saved", "snapshot"].includes(v.kind))
      || state.versions[0];
    state.ctx.versionId = preferred ? preferred.id : null;
  }
  if (!state.ctx.levelInitialised && state.dimensions.length) {
    const level = {};
    for (const d of state.dimensions) {
      const hier = d.hierarchies.find((h) => h.is_default) || d.hierarchies[0];
      if (hier?.levels?.length) level[d.code] = hier.levels[Math.min(1, hier.levels.length - 1)];
    }
    state.ctx.level = level;
    state.ctx.levelInitialised = true;
  }
  paintCycleChip();
}

function paintCycleChip() {
  const chip = document.getElementById("cycle-chip");
  clear(chip);
  const c = state.cycle?.cycle;
  if (!c) { chip.hidden = true; return; }
  chip.hidden = false;
  chip.title = `${c.name}, ufuk ${c.horizon_periods} dönem, fence ${c.fence_periods}, freeze ${c.freeze_periods}`;
  chip.append(
    el("span", { class: `dot ${c.status}` }),
    el("span", { text: `${c.current_period} · ${c.status_label}` }),
  );
}

// ------------------------------------------------------------------ sidebar
function paintSidebar(activeId, alertCount) {
  const list = document.getElementById("sidebar-items");
  clear(list);
  const hash = location.hash || "#/cockpit";

  for (const area of AREAS) {
    if (area.roles && !session.can(...area.roles)) continue;
    const isActive = area.id === activeId;
    const item = el("li", { class: "nav-item" });

    if (area.children) {
      const panelId = `sub-${area.id}`;
      const link = el("a", {
        class: `nav-link ${isActive ? "active" : ""}`,
        href: "#", role: "button",
        "data-bs-toggle": "collapse", "data-bs-target": `#${panelId}`,
        "aria-expanded": isActive ? "true" : "false", "aria-controls": panelId,
        onclick: (e) => e.preventDefault(),
      }, [
        icon(area.icon), el("span", { text: area.label }),
        alertCount && area.id === "planning"
          ? el("span", {
            class: "badge bg-danger rounded-pill ms-auto me-2",
            text: alertCount > 99 ? "99+" : String(alertCount),
          })
          : null,
        el("i", { class: "bi bi-chevron-down ms-auto" }),
      ]);
      const panel = el("div", { class: `collapse ${isActive ? "show" : ""}`, id: panelId }, [
        el("ul", { class: "nav nav-submenu" }, area.children.map((c) => el("li", { class: "nav-item" }, [
          el("a", { class: `nav-link ${hash === c.hash ? "active" : ""}`, href: c.hash },
            [icon(c.icon), el("span", { text: c.label })]),
        ]))),
      ]);
      item.append(link, panel);
    } else {
      item.append(el("a", {
        class: `nav-link ${isActive ? "active" : ""}`,
        href: `#/${area.id}`,
        "aria-current": isActive ? "page" : null,
      }, [icon(area.icon), el("span", { text: area.label })]));
    }
    list.append(item);
  }
}

function setupSidebarToggle() {
  const wrapper = document.getElementById("admin-wrapper");
  const sidebar = document.getElementById("admin-sidebar");
  const backdrop = document.querySelector(".sidebar-backdrop");
  const toggle = document.querySelector("[data-sidebar-toggle]");
  const DESKTOP = 992;
  const isDesktop = () => window.innerWidth >= DESKTOP;

  const closeMobile = () => {
    sidebar.classList.remove("show");
    backdrop?.classList.remove("show");
    document.body.style.overflow = "";
    toggle?.setAttribute("aria-expanded", "false");
  };

  if (localStorage.getItem("sidebar-collapsed") === "1") wrapper.classList.add("sidebar-collapsed");

  toggle?.addEventListener("click", () => {
    if (isDesktop()) {
      const collapsed = wrapper.classList.toggle("sidebar-collapsed");
      localStorage.setItem("sidebar-collapsed", collapsed ? "1" : "0");
      toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    } else {
      const open = sidebar.classList.toggle("show");
      backdrop?.classList.toggle("show", open);
      document.body.style.overflow = open ? "hidden" : "";
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }
  });
  backdrop?.addEventListener("click", closeMobile);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !isDesktop()) closeMobile(); });
  sidebar.addEventListener("click", (e) => {
    if (e.target.closest("a.nav-link[href^='#/']") && !isDesktop()) closeMobile();
  });
}

// ------------------------------------------------------------------ theme
function applyTheme(mode) {
  document.documentElement.setAttribute("data-bs-theme", mode);
  localStorage.setItem("theme", mode);
  const light = document.querySelector("[data-theme-icon='light']");
  const dark = document.querySelector("[data-theme-icon='dark']");
  if (light) light.hidden = mode === "dark";
  if (dark) dark.hidden = mode !== "dark";
}

function setupTheme() {
  const saved = localStorage.getItem("theme");
  applyTheme(saved || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  document.getElementById("theme-toggle").onclick = () => {
    applyTheme(document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark");
    // Chart colours are baked in at render time, so repaint the screen.
    renderCurrent();
  };
}

// ------------------------------------------------------------------ router
async function renderCurrent() {
  const area = currentArea();
  document.getElementById("view-title").textContent = area.label;
  document.title = `${area.label} · Forecast Platformu`;
  paintSidebar(area.id, state.alertCount);
  charts.destroyAll();
  const node = clear(viewNode());
  node.append(loading());
  try {
    const content = await area.render({ state, navigate, refreshShared });
    clear(node);
    // Screens that build their own page header keep it; the rest get one here so
    // every page starts with the same title block.
    if (!content.querySelector("h1")) node.append(pageHeader(area.label, area.sub));
    node.append(content);
    document.getElementById("main-content").scrollTop = 0;
  } catch (err) {
    clear(node).append(el("div", { class: "card" }, [
      el("div", { class: "card-body" }, [
        el("h2", { class: "h5", text: "Ekran yüklenemedi" }),
        el("p", { class: "text-muted", text: err instanceof ApiError ? err.message : String(err) }),
        el("button", { class: "btn btn-outline-secondary", type: "button", onclick: () => renderCurrent() },
          "Yeniden dene"),
      ]),
    ]));
  }
}

// ------------------------------------------------------------------ assistant
function assistantMessage(text, mine = false, extra) {
  const log = document.getElementById("assistant-log");
  const node = el("div", { class: `assistant-msg ${mine ? "me" : ""}` }, [el("div", { text })]);
  if (extra) node.append(el("div", { class: "text-muted mt-2", style: "font-size:.6875rem" }, extra));
  log.append(node);
  log.scrollTop = log.scrollHeight;
}

async function setupAssistant() {
  const panel = document.getElementById("assistant");
  const close = () => { panel.hidden = true; document.body.classList.remove("assistant-open"); };
  document.getElementById("assistant-toggle").onclick = async () => {
    const opening = panel.hidden;
    panel.hidden = !opening;
    document.body.classList.toggle("assistant-open", opening);
    if (opening && !panel.dataset.ready) {
      panel.dataset.ready = "1";
      const tools = await api.assistantTools();
      assistantMessage(
        `Merhaba ${state.user.name}. ${tools.summary}. Yazma işleyen her istek önce özetlenir, `
        + "onayınızdan sonra çalışır.",
        false,
        tools.groups.map((g) => el("div", { text: `${g.group}: ${g.tools.map((t) => t.name).join(", ")}` })),
      );
    }
  };
  document.getElementById("assistant-close").onclick = close;
  document.getElementById("assistant-form").onsubmit = async (e) => {
    e.preventDefault();
    const input = document.getElementById("assistant-input");
    const text = input.value.trim();
    if (!text) return;
    assistantMessage(text, true);
    input.value = "";
    try {
      const res = await api.assistantChat(text, { ...state.ctx, cockpit: state.cockpit });
      assistantMessage(
        res.message, false,
        res.would_use_tools?.length
          ? [el("div", { text: `Kullanılabilir araçlar: ${res.would_use_tools.join(", ")}` })]
          : null,
      );
    } catch (err) {
      assistantMessage(err.message, false);
    }
  };
}

// ------------------------------------------------------------------ boot
async function startApp() {
  document.getElementById("login").hidden = true;
  document.getElementById("shell").hidden = false;
  state.user = session.user;

  const initials = (state.user.name || "?")
    .split(/\s+/).map((p) => p[0]).slice(0, 2).join("")
    .toLocaleUpperCase("tr-TR");
  document.getElementById("who-initials").textContent = initials;
  document.getElementById("who-name").textContent = state.user.name;
  document.getElementById("who-roles").textContent = state.user.roles.join(", ");

  setupTheme();
  setupSidebarToggle();
  await refreshShared();
  try {
    const alerts = await api.alerts("open");
    state.alertCount = alerts.alerts.length;
  } catch { state.alertCount = 0; }
  await setupAssistant();
  if (!location.hash) location.hash = "#/cockpit";
  await renderCurrent();
}

async function paintDemoUsers() {
  try {
    const res = await api.demoUsers();
    const box = document.getElementById("login-user-list");
    clear(box);
    for (const u of res.users) {
      box.append(el("button", {
        class: "btn btn-sm btn-outline-secondary", type: "button", title: u.roles.join(", "),
        onclick: () => {
          document.getElementById("email").value = u.email;
          document.getElementById("password").value = res.password;
        },
      }, u.name));
    }
  } catch { /* demo data may not be loaded yet */ }
}

document.getElementById("login-form").onsubmit = async (e) => {
  e.preventDefault();
  const errBox = document.getElementById("login-error");
  errBox.hidden = true;
  try {
    const res = await api.login(
      document.getElementById("email").value.trim().toLowerCase(),
      document.getElementById("password").value,
    );
    session.save(res.token, res.user);
    await startApp();
  } catch (err) {
    errBox.textContent = err.message;
    errBox.hidden = false;
  }
};

document.getElementById("logout").onclick = () => {
  session.clear();
  location.reload();
};

window.addEventListener("hashchange", renderCurrent);
window.addEventListener("session-expired", () => {
  toast("Oturum süresi doldu", "err");
  setTimeout(() => location.reload(), 1200);
});

if (session.token && session.user) {
  startApp().catch((err) => {
    toast(err.message, "err");
    session.clear();
    document.getElementById("login").hidden = false;
    document.getElementById("shell").hidden = true;
    paintDemoUsers();
  });
} else {
  const saved = localStorage.getItem("theme");
  if (saved) document.documentElement.setAttribute("data-bs-theme", saved);
  paintDemoUsers();
}
