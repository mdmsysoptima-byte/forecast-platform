// Yonetim: baglantilar, modeller, takvim, boyutlar, surucüler, cevrim, katman kurallari, kullanicilar, audit.

import { api, session } from "../api.js";
import {
  card, clear, defList, el, empty, field, fmt, icon, jsonBlock, loading, modal, pageHeader, select,
  table, tabs, toast,
} from "../ui.js";

const TABS = [
  { id: "connections", label: "Bağlantılar", icon: "hdd-network" },
  { id: "models", label: "Modeller", icon: "cpu" },
  { id: "calendar", label: "Takvim", icon: "calendar3" },
  { id: "dimensions", label: "Boyutlar", icon: "diagram-3" },
  { id: "drivers", label: "Sürücüler", icon: "sliders" },
  { id: "rules", label: "Katman kuralları", icon: "layers" },
  { id: "users", label: "Kullanıcı ve roller", icon: "people" },
  { id: "audit", label: "Audit log", icon: "journal-text" },
];

/** Kart başlığına oturan eylem düğmesi. */
function action(label, iconName, handler, variant = "btn-outline-secondary") {
  return el("button", {
    class: `btn btn-sm ${variant}`, type: "button", onclick: handler,
  }, [icon(iconName), el("span", { class: "ms-2", text: label })]);
}

/** Yazma işlemini çağırıp sonucunu toast'a düşürür; isteğe göre sayfayı yeniler. */
function callAndReport(fn, { reload = false } = {}) {
  return async () => {
    try {
      const res = await fn();
      toast(res.summary, res.ok === false ? "err" : "ok");
      if (reload && res.ok !== false) location.reload();
    } catch (err) { toast(err.message, "err"); }
  };
}

export async function render({ state }) {
  const sub = location.hash.split("/")[2] || "connections";
  const active = TABS.find((t) => t.id === sub) || TABS[0];
  const root = el("div", { class: "d-flex flex-column gap-3" });
  root.append(pageHeader("Yönetim", active.label));
  root.append(tabs(TABS, active.id, (id) => { location.hash = `#/admin/${id}`; }));

  const box = el("div", { class: "d-flex flex-column gap-3" });
  root.append(box);

  const views = {
    connections: connectionsView,
    models: modelsView,
    calendar: calendarView,
    dimensions: dimensionsView,
    drivers: driversView,
    rules: rulesView,
    users: usersView,
    audit: auditView,
  };
  box.append(await views[active.id](state));
  return root;
}

async function connectionsView() {
  const data = await api.connections();
  return card("Veri bağlantıları", "Büyük tablolar uygulama veritabanına kopyalanmaz, sorgu depoda çalışır", [
    defList([
      ["Uygulama DB", `${data.app_db.engine} · ${data.app_db.url}`],
      ["Analitik depo", `${data.store.backend} · ${data.store.path}`],
      ["Veri tazeliği", fmt.date(data.store.data_as_of)],
    ]),
    el("div", { class: "mt-3" }, [table(
      [{ key: "name", label: "Adaptör" }, { key: "code", label: "Kod" },
        { key: "status", label: "Durum", render: (v, r) => el("span", {
          class: `badge ${r.code === data.store.backend ? "bg-success-subtle text-success-emphasis"
            : "bg-secondary-subtle text-secondary-emphasis"}`,
          text: v,
        }) }],
      data.available_backends,
    )]),
  ], {
    actions: el("div", { class: "d-flex flex-wrap gap-2" }, [
      action("Bağlantıyı test et", "plug", callAndReport(() => api.testConnection())),
      action("Süresi geçen taslakları sil", "trash", callAndReport(() => api.purgeDrafts())),
    ]),
  });
}

async function modelsView() {
  const [registry, packages] = await Promise.all([api.models(), api.modelPackages().catch(() => null)]);
  return el("div", { class: "d-flex flex-column gap-3" }, [
    card("Kayıtlı modeller", registry.summary, [
      table(
        [
          { key: "code", label: "Kod" }, { key: "name", label: "Ad" }, { key: "version", label: "Versiyon" },
          { key: "supported_grains", label: "Kova", render: (v) => v.join(", ") },
          { key: "supported_levels", label: "Seviye", render: (v) => Object.entries(v)
            .map(([k, l]) => `${k}: ${l.join("/")}`).join(" · ") },
          { key: "required_drivers", label: "Zorunlu sürücü", render: (v) => v.join(", ") || "yok" },
          { key: "output_has_interval", label: "Aralık", render: (v) => (v ? "var" : "yok") },
        ],
        registry.models,
      ),
      el("p", { class: "text-muted small mt-3 mb-0", text:
        "Yeni model eklemek: models/<kod>/ altına model.py, manifest.yaml ve requirements.txt koyun, "
        + "sonra manifest tarayın. Kod değişikliği gerekmez." }),
    ], {
      actions: action(
        "Manifest tara ve yenile", "arrow-clockwise",
        callAndReport(() => api.syncModels(), { reload: true }),
      ),
    }),
    packages ? card("Disk üzerindeki paketler", packages.directory, [
      table(
        [{ key: "code", label: "Kod" }, { key: "version", label: "Versiyon" }, { key: "path", label: "Yol" }],
        packages.packages,
      ),
    ], { flush: true }) : null,
  ]);
}

async function calendarView() {
  const year = new Date().getFullYear();
  const [cycle, events] = await Promise.all([api.cycle(), api.calendarEvents(year)]);
  const c = cycle.cycle;
  return el("div", { class: "d-flex flex-column gap-3" }, [
    card("Çevrim tanımı", cycle.summary, [
      defList([
        ["Ad", c.name],
        ["Durum", c.status_label],
        ["Kova", c.grain],
        ["Mevcut dönem", c.current_period],
        ["Ufuk", `${c.horizon_periods} dönem`],
        ["Fence", `${c.fence_periods} dönem düzenlenemez`],
        ["Freeze", `${c.freeze_periods} dönem kullanıcı katmanı korunur`],
      ]),
    ], {
      actions: session.can("admin")
        ? action("Çevrimi ilerlet", "arrow-right-circle",
          callAndReport(() => api.advanceCycle(), { reload: true }), "btn-outline-primary")
        : null,
    }),
    card("Dönem bayrakları", "Gri gerçekleşen, fence kilitli, freeze korumalı, açık düzenlenebilir", [
      table(
        [
          { key: "label", label: "Dönem" },
          { key: "flag", label: "Durum", render: (v) => el("span", {
            class: `badge ${v === "actual" ? "bg-secondary-subtle text-secondary-emphasis"
              : v === "fence" ? "bg-danger-subtle text-danger-emphasis"
                : v === "freeze" ? "bg-primary-subtle text-primary-emphasis"
                  : "bg-success-subtle text-success-emphasis"}`,
            text: { actual: "gerçekleşen", fence: "fence", freeze: "freeze", open: "açık" }[v] || v,
          }) },
          { key: "working_days", label: "İş günü", numeric: true, dec: 0 },
          { key: "is_current", label: "Mevcut", render: (v) => (v
            ? el("span", { class: "badge bg-primary", text: "bu dönem" }) : "") },
        ],
        cycle.buckets,
      ),
    ], { flush: true }),
    card(`${year} takvim olayları`, `${events.summary} · kayan bayramlar yıllık olarak kayar`, [
      table(
        [
          { key: "name", label: "Olay" }, { key: "type", label: "Tip" },
          { key: "start", label: "Başlangıç", render: (v) => fmt.day(v) },
          { key: "is_shifting", label: "Kayan", render: (v) => (v
            ? el("span", { class: "badge bg-warning-subtle text-warning-emphasis", text: "kayan" }) : "") },
        ],
        events.events,
      ),
    ], { flush: true }),
  ]);
}

async function dimensionsView(state) {
  const box = el("div", { class: "d-flex flex-column gap-3" });
  for (const dim of state.dimensions) {
    const validation = await api.validate(dim.code).catch((err) => ({ ok: false, errors: [err.message] }));
    box.append(card(`${dim.name} (${dim.code})`, validation.summary || "", [
      table(
        [
          { key: "code", label: "Hiyerarşi" }, { key: "name", label: "Ad" },
          { key: "levels", label: "Seviyeler", render: (v) => v.join(" > ") },
          { key: "is_default", label: "Varsayılan", render: (v) => (v
            ? el("span", { class: "badge bg-primary-subtle text-primary-emphasis", text: "varsayılan" }) : "") },
        ],
        dim.hierarchies,
      ),
      validation.errors?.length
        ? el("div", { class: "alert alert-danger mt-3 mb-0 py-2" }, [
          el("ul", { class: "mb-0 ps-3 small" }, validation.errors.map((e) => el("li", { text: e }))),
        ])
        : null,
    ], {
      actions: el("div", { class: "d-flex align-items-center gap-2" }, [
        el("span", {
          class: `badge ${validation.ok ? "bg-success" : "bg-danger"}`,
          text: validation.ok ? "doğrulama başarılı" : "doğrulama hatalı",
        }),
        el("span", {
          class: "text-muted small",
          text: `${validation.nodes || 0} düğüm, ${validation.leaves || 0} yaprak`,
        }),
      ]),
    }));
  }
  box.append(card("Hiyerarşi yükleme", "Sektör YAML'i veya CSV", [
    el("p", { class: "text-muted small mb-0", text:
      "Hiyerarşi tanımı configs/sectors/<sektör>.yaml dosyasından gelir. Ebeveyn-çocuk doğrulaması "
      + "her yüklemede çalışır: her çocuk tek ebeveyne bağlıdır ve döngü kabul edilmez. "
      + "Yeni sektör eklemek için yalnızca YAML yazılır, kod değişmez." }),
  ]));
  return box;
}

async function driversView() {
  const [drivers, sets] = await Promise.all([api.drivers(), api.driverSets()]);
  return el("div", { class: "d-flex flex-column gap-3" }, [
    card("Sürücü kataloğu", drivers.summary, [
      table(
        [
          { key: "code", label: "Kod" }, { key: "name", label: "Ad" }, { key: "unit", label: "Birim" },
          { key: "source_type", label: "Kaynak" }, { key: "source_ref", label: "Referans" },
          { key: "grain", label: "Kova" }, { key: "scope", label: "Kapsam" },
        ],
        drivers.drivers,
      ),
    ], { flush: true }),
    card("Sürücü setleri", sets.summary, [
      table(
        [{ key: "name", label: "Ad" }, { key: "description", label: "Açıklama" },
          { key: "is_shared", label: "Paylaşım", render: (v) => el("span", {
            class: `badge ${v ? "bg-info-subtle text-info-emphasis" : "bg-secondary-subtle text-secondary-emphasis"}`,
            text: v ? "paylaşılan" : "kişisel",
          }) }],
        sets.driver_sets,
      ),
    ], { flush: true }),
  ]);
}

async function rulesView(state) {
  const [rule, reasons] = await Promise.all([api.consensusRule(), api.reasonCodes()]);
  const sel = select(rule.options.map((o) => ({ value: o, label: o })), rule.rule, () => {});
  const yesNo = (v) => el("span", {
    class: `badge ${v ? "bg-success-subtle text-success-emphasis" : "bg-secondary-subtle text-secondary-emphasis"}`,
    text: v ? "evet" : "hayır",
  });
  return el("div", { class: "d-flex flex-column gap-3" }, [
    card("Konsensüs kuralı", rule.summary, [
      el("div", { class: "d-flex flex-wrap align-items-end gap-3" }, [
        field("Kural", sel),
        session.can("admin") ? el("button", {
          class: "btn btn-sm btn-primary",
          type: "button",
          onclick: callAndReport(() => api.setConsensusRule(sel.value)),
        }, [icon("check2"), el("span", { class: "ms-2", text: "Kaydet" })]) : null,
      ]),
      el("p", { class: "text-muted small mt-3 mb-0", text:
        `Öncelik sırası: ${rule.priority.join(" > ")}. Ağırlıklı ortalama seçilirse `
        + `${Object.entries(rule.weights).map(([k, v]) => `${k} ${v}`).join(", ")} ağırlıkları kullanılır. `
        + "Her hesaplamada hangi katmanın seçildiği hücre bazında kaydedilir." }),
    ]),
    card("Katmanlar", `${state.layers.length} katman`, [
      table(
        [
          { key: "name", label: "Katman" }, { key: "code", label: "Kod" },
          { key: "provenance", label: "Kaynak", render: (v) => el("span", { class: `badge prov-${v}`, text: v }) },
          { key: "editable", label: "Düzenlenebilir", render: yesNo },
          { key: "edit_roles", label: "Yetkili roller", render: (v) => el(
            "div", { class: "d-flex flex-wrap gap-1" },
            v.length
              ? v.map((r) => el("span", { class: "badge bg-secondary-subtle text-secondary-emphasis", text: r }))
              : [el("span", { class: "text-muted", text: "-" })],
          ) },
        ],
        state.layers,
      ),
    ], { flush: true }),
    card("Gerekçe kodları", reasons.summary, [
      table(
        [{ key: "code", label: "Kod" }, { key: "name", label: "Ad" },
          { key: "requires_note", label: "Not zorunlu", render: yesNo }],
        reasons.reason_codes,
      ),
      el("p", { class: "text-muted small mt-3 mb-0", text:
        "Gerekçe kodları sektör konfigürasyonundan gelir. Override gerekçesiz kaydedilemez." }),
    ]),
  ]);
}

async function usersView() {
  const data = await api.users();
  return card("Kullanıcı ve satır yetkisi", data.summary, [
    table(
      [
        { key: "name", label: "Ad" }, { key: "email", label: "E-posta" },
        { key: "roles", label: "Roller", render: (v) => el("div", { class: "d-flex flex-wrap gap-1" },
          v.map((r) => el("span", { class: "badge bg-secondary-subtle text-secondary-emphasis", text: r }))) },
        { key: "row_scope", label: "Satır kapsamı", render: (v) => (v.length
          ? v.map((s) => `${s.dimension}:${s.node}${s.can_edit ? " (düzenleyebilir)" : ""}`).join(", ")
          : el("span", { class: "text-muted", text: "tam kapsam" })) },
      ],
      data.users,
    ),
    el("p", { class: "text-muted small mt-3 mb-0", text:
      "API her isteği hem rol hem satır kapsamına göre süzer. Ön yüz yetki kontrolü yalnızca görsel kolaylıktır." }),
  ]);
}

async function auditView() {
  const content = el("div", {});
  const entitySel = select(
    [["", "tümü"], ["override", "override"], ["version", "version"], ["run", "run"],
      ["cycle", "cycle"], ["scenario", "scenario"], ["insight", "insight"]]
      .map(([value, label]) => ({ value, label })),
    "", () => paint(),
  );
  const wrap = card("Audit log", "Her yazma işlemi kaydedilir", [content], {
    actions: field("Varlık", entitySel),
  });

  async function paint() {
    clear(content).append(loading("Kayıtlar okunuyor"));
    const data = await api.get(`/api/v1/admin/audit?limit=200${entitySel.value ? `&entity=${entitySel.value}` : ""}`);
    clear(content).append(
      data.entries.length ? table(
        [
          { key: "at", label: "Zaman", render: (v) => fmt.date(v) },
          { key: "entity", label: "Varlık" }, { key: "entity_id", label: "No" },
          { key: "action", label: "İşlem" }, { key: "actor", label: "Aktör" },
          { key: "actor_kind", label: "Tip", render: (v) => el("span", {
            class: `badge ${v === "user" ? "bg-secondary-subtle text-secondary-emphasis"
              : "bg-info-subtle text-info-emphasis"}`,
            text: v,
          }) },
          { key: "after", label: "", render: (_v, r) => el("button", {
            class: "btn btn-sm btn-outline-secondary", type: "button", title: "Değişimin tamamını göster",
            onclick: () => modal({
              title: `${r.entity} ${r.action}`,
              subtitle: `Korelasyon ${r.correlation_id}`,
              wide: true,
              body: [jsonBlock({ before: r.before, after: r.after })],
            }),
          }, [icon("braces")]) },
        ],
        data.entries,
      ) : empty("Kayıt yok"),
    );
  }

  await paint();
  return wrap;
}
