// Planlama: tahmin grid'i, istisnalar, kayıtlı görünümler, saha bilgileri.

import { api, session } from "../api.js";
import {
  SERIES_COLOR, SEVERITY, card, clear, confirmDialog, defList, download, el, empty, field, fmt, icon,
  lineChart, loading, modal, pageHeader, sectionLabel, select, table, tabs, toast,
} from "../ui.js";

const VERSION_STATUS = { draft: "taslak", review: "inceleme", approved: "onaylı", locked: "kilitli" };

const TAB_LIST = [
  { id: "grid", label: "Tahmin grid'i", icon: "grid-3x3" },
  { id: "exceptions", label: "İstisnalar", icon: "exclamation-triangle" },
  { id: "views", label: "Kayıtlı görünümler", icon: "bookmark" },
  { id: "insights", label: "Saha bilgileri", icon: "chat-left-text" },
];

export async function render(ctxArgs) {
  const sub = (location.hash.split("/")[2]) || "grid";
  const active = TAB_LIST.find((t) => t.id === sub) || TAB_LIST[0];
  const root = el("div", { class: "d-flex flex-column", style: "min-height:0" });

  root.append(pageHeader("Planlama", active.label, tabs(
    TAB_LIST, active.id, (id) => { location.hash = `#/planning/${id}`; },
  )));

  const area = el("div", { style: "flex:1;min-height:0;display:flex;flex-direction:column" });
  root.append(area);

  if (sub === "exceptions") area.append(await exceptionsView(ctxArgs));
  else if (sub === "views") area.append(await savedViewsView(ctxArgs));
  else if (sub === "insights") area.append(await insightsView(ctxArgs));
  else area.append(await gridView(ctxArgs));
  return root;
}

// ===================================================================== grid
async function gridView({ state, navigate }) {
  const ctx = state.ctx;
  const wrap = el("div", { class: "plan", style: "flex:1;min-height:0" });
  const ribbon = el("div", { class: "ribbon" });
  const bodyRow = el("div", { class: "plan-body" });
  const treePanel = el("aside", { class: "tree-panel" });
  const gridPanel = el("div", { class: "grid-panel" });
  const gridScroll = el("div", { class: "grid-scroll" });
  const legend = el("div", { class: "grid-legend" });
  const detail = el("div", { class: "detail" });
  const detailChart = el("div", { class: "detail-chart" });
  const detailCard = el("div", { class: "detail-card" });

  detail.append(detailChart, detailCard);
  gridPanel.append(gridScroll, legend, detail);
  bodyRow.append(treePanel, gridPanel);
  wrap.append(ribbon, bodyRow);

  const local = {
    payload: null,
    expanded: new Set(),
    selection: null,     // {group, layer, bucketIds: []}
    lastOverrideId: null,
    showQuarters: false,
    trees: {},
    activeDim: state.dimensions[0]?.code,
    checked: {},         // dimension -> Set(node ids)
  };

  async function loadGrid() {
    clear(gridScroll).append(loading("Grid yükleniyor"));
    const scope = {};
    for (const [dim, set] of Object.entries(local.checked)) {
      if (set && set.size) scope[dim] = { nodes: [...set] };
    }
    ctx.scope = scope;
    local.payload = await api.gridQuery({
      version_id: ctx.versionId, scope, level: ctx.level, grain: ctx.grain,
      layers: ctx.layerCodes, measure: ctx.measure, currency: ctx.currency, view: ctx.view,
      history_periods: ctx.historyPeriods,
    });
    paintGrid();
    revealCurrentPeriod();
    paintLegend();
    paintDetail();
  }

  /* Grid soldan, yani en eski gerçekleşen dönemden açılıyordu; düzenlenebilir
     pencere ekranın dışında kalıyor ve kullanıcı kapalı hücreye tıklayıp
     hiçbir şey olmamasıyla karşılaşıyordu. Yeni veri geldiğinde mevcut dönemi
     görünür yere çekiyoruz. Yalnızca yüklemede: her yeniden çizimde kaydırmak
     kullanıcının baktığı yeri elinden alır.

     İki incelik var. Birincisi, ilk yüklemede grid henüz belgeye eklenmemiş
     oluyor ve bağlı olmayan bir öğede scrollLeft yok sayılıyor, bu yüzden
     bağlanmasını bekliyoruz. İkincisi, offsetLeft en yakın konumlanmış ataya
     göre ölçülüyor ve bu kenar menüyü de içine alıyor; viewport farkı
     kullanmak düzenden bağımsız olarak doğru sonucu veriyor. */
  function revealCurrentPeriod() {
    let frames = 0;
    const tick = () => {
      if (!gridScroll.isConnected || gridScroll.clientWidth === 0) {
        if (frames++ < 40) requestAnimationFrame(tick);
        return;
      }
      const th = gridScroll.querySelector("table.fgrid thead th.is-current");
      if (!th) return;
      const label = gridScroll.querySelector("thead th.h-label");
      const frozen = label ? label.getBoundingClientRect().width : 0;
      const delta = th.getBoundingClientRect().left - gridScroll.getBoundingClientRect().left;
      gridScroll.scrollLeft += delta - frozen - 40;
    };
    requestAnimationFrame(tick);
  }

  // ------------------------------------------------------------- ribbon
  function paintRibbon() {
    clear(ribbon);
    const controls = [
      ["Versiyon", select(
        state.versions.map((v) => ({ value: v.id, label: `${v.name} (${v.kind})` })),
        ctx.versionId,
        async (v) => { ctx.versionId = Number(v); await loadGrid(); },
        { style: "min-width:200px" },
      )],
    ];
    for (const dim of state.dimensions) {
      const hier = dim.hierarchies.find((h) => h.is_default) || dim.hierarchies[0];
      controls.push([`${dim.name} seviyesi`, select(
        [{ value: "", label: "yok" }, ...hier.levels.map((l) => ({ value: l, label: l }))],
        ctx.level[dim.code] || "",
        async (v) => {
          if (v) ctx.level[dim.code] = v; else delete ctx.level[dim.code];
          local.expanded.clear();
          await loadGrid();
        },
      )]);
    }
    controls.push(["Ölçü", select(
      state.measures.map((m) => ({ value: m.code, label: m.name })),
      ctx.measure, async (v) => { ctx.measure = v; await loadGrid(); },
    )]);
    controls.push(["Kova", select(
      [["month", "Ay"], ["quarter", "Çeyrek"], ["year", "Yıl"], ["week", "Hafta"]]
        .map(([value, label]) => ({ value, label })),
      ctx.grain, async (v) => { ctx.grain = v; await loadGrid(); },
    )]);
    const measure = state.measures.find((m) => m.code === ctx.measure);
    if (measure?.unit_type === "value") {
      controls.push(["Görünüm", select(
        [["nominal", "Nominal TL"], ["real", "Reel TL"], ["fx", "Döviz"]]
          .map(([value, label]) => ({ value, label })),
        ctx.view, async (v) => { ctx.view = v; await loadGrid(); },
      )]);
      if (ctx.view === "fx") {
        controls.push(["Para birimi", select(
          ["USD", "EUR"].map((c) => ({ value: c, label: c })), ctx.currency,
          async (v) => { ctx.currency = v; await loadGrid(); },
        )]);
      }
    }
    controls.push(["Katmanlar", el("button", {
      class: "btn btn-sm btn-outline-secondary", type: "button", onclick: () => layerPicker(),
    }, [icon("layers"), el("span", { class: "ms-1", text: `${ctx.layerCodes.length} katman` })])]);

    const action = (label, iconName, handler, variant = "btn-outline-secondary") => el("button", {
      class: `btn btn-sm ${variant}`, type: "button", onclick: handler,
    }, [icon(iconName), el("span", { class: "ms-1 d-none d-xl-inline", text: label })]);

    ribbon.append(
      el("div", { class: "d-flex flex-wrap align-items-end gap-3" },
        controls.map(([label, control]) => el("div", { class: "d-flex flex-column" }, [
          el("label", { class: "form-label mb-1 small text-muted", text: label }),
          control,
        ]))),
      el("div", { class: "d-flex flex-wrap justify-content-end gap-2 mt-2" }, [
        action("Çalıştır", "play-fill", () => runDialog(), "btn-primary"),
        action("Sakla", "save", () => saveVersionDialog()),
        action("Karşılaştır", "arrow-left-right", () => navigate("versions")),
        action("Excel", "file-earmark-excel", () => exportGrid()),
        action("Geri yükle", "upload", () => importGrid()),
        action("Görünümü kaydet", "bookmark-plus", () => saveViewDialog()),
        action("Yenile", "arrow-clockwise", () => loadGrid()),
      ]),
    );
  }

  async function layerPicker() {
    const boxes = state.layers.map((l) => {
      const input = el("input", { type: "checkbox", class: "form-check-input", value: l.code, id: `lp-${l.code}` });
      input.checked = ctx.layerCodes.includes(l.code);
      return el("div", { class: "form-check d-flex align-items-center gap-2" }, [
        input,
        el("label", { class: "form-check-label d-flex align-items-center gap-2 flex-grow-1", for: `lp-${l.code}` }, [
          el("span", { text: l.name }),
          el("span", { class: `badge prov-${l.provenance}`, text: l.provenance }),
          l.can_edit ? el("span", { class: "badge bg-secondary-subtle text-secondary-emphasis", text: "düzenlenebilir" }) : null,
        ]),
      ]);
    });
    const res = await modal({
      title: "Katman seçimi",
      subtitle: "Renk kodu kaynağı gösterir: teal gerçekleşen, mavi model, amber insan müdahalesi, nötr bütçe",
      body: boxes,
      actions: [{ label: "Vazgeç" }, { label: "Uygula", primary: true, value: true }],
    });
    if (!res) return;
    const chosen = boxes
      .map((b) => b.querySelector("input"))
      .filter((i) => i.checked)
      .map((i) => i.value);
    ctx.layerCodes = chosen.length ? chosen : ["consensus"];
    paintRibbon();
    await loadGrid();
  }

  // ------------------------------------------------------------- tree
  async function paintTree() {
    clear(treePanel);
    const tabs = el("div", { class: "tree-tabs" }, state.dimensions.map((d) => el("button", {
      type: "button", "aria-selected": d.code === local.activeDim ? "true" : "false",
      onclick: async () => { local.activeDim = d.code; await paintTree(); },
    }, d.name)));
    const searchInput = el("input", {
      type: "search", class: "form-control form-control-sm", placeholder: "Üye kodu veya adı ara",
    });
    const scroll = el("div", { class: "tree-scroll" });
    const foot = el("div", { class: "tree-foot" }, [
      el("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onclick: async () => {
        local.checked = {}; await paintTree(); await loadGrid();
      } }, "Kapsamı temizle"),
      el("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onclick: () => groupDialog() }, "Grup oluştur"),
    ]);
    treePanel.append(tabs, el("div", { class: "tree-search" }, [searchInput]), scroll, foot);

    const dim = local.activeDim;
    if (!local.trees[dim]) local.trees[dim] = await api.tree(dim);
    const data = local.trees[dim];
    local.checked[dim] = local.checked[dim] || new Set();

    const renderNodes = (nodes, container, filter) => {
      for (const n of nodes) {
        const matches = !filter
          || n.label.toLowerCase().includes(filter)
          || n.member_code.toLowerCase().includes(filter);
        const childBox = el("div", { class: "tree-children" });
        const hasVisibleChild = n.children.length
          ? renderNodes(n.children, childBox, filter)
          : false;
        if (!matches && !hasVisibleChild) continue;

        const open = local.expanded.has(`t:${n.node_id}`) || Boolean(filter);
        const cb = el("input", { type: "checkbox", class: "form-check-input mt-0" });
        cb.checked = local.checked[dim].has(n.node_id);
        cb.onchange = async () => {
          if (cb.checked) local.checked[dim].add(n.node_id);
          else local.checked[dim].delete(n.node_id);
          await loadGrid();
        };
        const row = el("div", { class: "tree-node" }, [
          n.children.length
            ? el("button", {
              class: "twist", type: "button", "aria-label": open ? "Kapat" : "Ac",
              onclick: async () => {
                const key = `t:${n.node_id}`;
                if (local.expanded.has(key)) local.expanded.delete(key);
                else local.expanded.add(key);
                await paintTree();
              },
            }, open ? "▼" : "▶")
            : el("span", { class: "twist" }),
          el("label", { class: "mb-0" }, [cb, el("span", { class: "lbl", text: n.label, title: n.member_code })]),
          el("span", { class: "lvl text-muted", text: n.level }),
        ]);
        container.append(row);
        if (n.children.length) {
          childBox.hidden = !open;
          container.append(childBox);
        }
      }
      return container.childElementCount > 0;
    };

    const paintList = () => {
      clear(scroll);
      const box = el("div", {});
      renderNodes(data.nodes, box, searchInput.value.trim().toLowerCase());
      scroll.append(box.childElementCount ? box : empty("Eşleşme yok"));
    };
    searchInput.oninput = paintList;
    paintList();
  }

  async function groupDialog() {
    const dim = local.activeDim;
    const codes = el("textarea", { rows: 4, placeholder: "SKU001, SKU002, SKU003" });
    const name = el("input", { type: "text", placeholder: "Grup adı" });
    const shared = el("input", { type: "checkbox", class: "form-check-input mt-0", id: "view-shared" });
    const ok = await modal({
      title: "Kullanıcı tanımlı grup",
      subtitle: `${dim} boyutunda ağaçta olmayan üye kümesi tanımla`,
      body: [
        field("Ad", name), field("Üye kodları", codes),
        el("div", { class: "form-check d-flex align-items-center gap-2 m-0" }, [
        shared, el("label", { class: "form-check-label", for: shared.id, text: "Paylaşılan" }),
      ]),
      ],
      actions: [{ label: "Vazgeç" }, { label: "Oluştur", primary: true, value: true }],
    });
    if (!ok) return;
    try {
      const res = await api.createGroup({
        name: name.value.trim(), dimension_code: dim, is_shared: shared.checked,
        member_codes: codes.value.split(/[,\n;]/).map((s) => s.trim()).filter(Boolean),
      });
      toast(res.summary, "ok");
    } catch (err) { toast(err.message, "err"); }
  }

  // ------------------------------------------------------------- grid table
  function quarterKeys() {
    const first = local.payload.rows[0] || local.payload.total;
    const subs = (first.subtotals || {})[ctx.layerCodes[0]] || {};
    return Object.keys(subs).filter((k) => /^\d{4}-Q\d$/.test(k)).sort();
  }

  function paintGrid() {
    const p = local.payload;
    clear(gridScroll);
    if (!p.rows.length) {
      gridScroll.append(empty(p.meta?.message || "Kapsamda gösterilecek satır yok"));
      return;
    }
    const subCols = [
      ...(local.showQuarters ? quarterKeys().map((k) => ({ key: k, label: k })) : []),
      { key: "year", label: "Yıl" }, { key: "ytd", label: "YTD" }, { key: "ytg", label: "YTG" },
    ];

    const head = el("tr", {}, [
      el("th", { class: "h-label" }, [
        el("div", { class: "d-flex flex-wrap align-items-center gap-2", style: "gap:6px" }, [
          el("span", { text: "Kırılım" }),
          el("button", {
            class: "btn btn-sm btn-outline-secondary", type: "button", title: "Çeyrek toplamlarını göster",
            onclick: () => { local.showQuarters = !local.showQuarters; paintGrid(); },
          }, local.showQuarters ? "Çeyrek kapat" : "Çeyrek"),
        ]),
      ]),
      ...p.columns.map((c) => el("th", {
        class: `${c.flag === "actual" ? "is-actual" : ""} ${c.is_current ? "is-current" : ""}`,
        title: `${c.label} · ${flagLabel(c.flag)} · ${c.working_days} iş günü`,
      }, [
        el("div", { text: c.label }),
        el("div", { style: "font-size:9px;opacity:.7", text: flagLabel(c.flag) }),
      ])),
      ...subCols.map((s) => el("th", { class: "sub", text: s.label })),
    ]);

    const tbody = el("tbody", {});
    for (const row of p.rows) {
      const isOpen = local.expanded.has(row.group);
      tbody.append(groupRow(row, p, subCols, isOpen));
      if (isOpen) {
        for (const layer of p.layers) tbody.append(layerRow(row, layer, p, subCols));
      }
    }
    tbody.append(totalRow(p, subCols));

    gridScroll.append(el("table", { class: "fgrid" }, [el("thead", {}, [head]), tbody]));
  }

  function primaryLayer(p) {
    return p.layers.find((l) => l.code === "consensus") || p.layers[0];
  }

  /** The summary row reads actual for the past and the primary layer ahead. */
  function summaryValue(row, p, col) {
    const primary = primaryLayer(p);
    if (col.flag === "actual") {
      const actual = row.layers.actual?.[String(col.bucket_id)];
      if (actual !== undefined) return { value: actual, layer: "actual" };
    }
    const v = row.layers[primary.code]?.[String(col.bucket_id)];
    return { value: v, layer: primary.code };
  }

  function groupRow(row, p, subCols, isOpen) {
    const layer = primaryLayer(p);
    const tr = el("tr", { class: "grow-row" });
    tr.append(el("td", { class: "c-label" }, [
      el("div", { class: "rowlabel" }, [
        el("button", {
          class: "twist", type: "button", "aria-expanded": isOpen ? "true" : "false",
          "aria-label": isOpen ? "Katmanları kapat" : "Katmanları aç",
          onclick: () => {
            if (local.expanded.has(row.group)) local.expanded.delete(row.group);
            else local.expanded.add(row.group);
            paintGrid();
          },
        }, isOpen ? "▼" : "▶"),
        el("span", { class: "name", text: row.label, title: row.label }),
        el("span", { class: "meta", text: `${row.series_count} seri` }),
      ]),
    ]));
    for (const c of p.columns) {
      const { value, layer: sourceCode } = summaryValue(row, p, c);
      const source = p.layers.find((l) => l.code === sourceCode) || layer;
      tr.append(cellNode(row, source, c, p, { readonly: true, overrideValue: value }));
    }
    for (const s of subCols) {
      tr.append(el("td", { class: "cell sub", text: fmt.n((row.subtotals[layer.code] || {})[s.key]) }));
    }
    return tr;
  }

  function layerRow(row, layer, p, subCols) {
    const tr = el("tr", { class: `lrow p-${layer.provenance}` });
    tr.append(el("td", { class: "c-label" }, [
      el("div", { class: "rowlabel layer" }, [
        el("span", { class: "name", text: layer.name }),
        layer.can_edit ? el("span", { class: "meta", text: "düzenlenebilir" }) : null,
      ]),
    ]));
    for (const c of p.columns) tr.append(cellNode(row, layer, c, p));
    for (const s of subCols) {
      tr.append(el("td", { class: "cell sub", text: fmt.n((row.subtotals[layer.code] || {})[s.key]) }));
    }
    return tr;
  }

  function totalRow(p, subCols) {
    const layer = primaryLayer(p);
    const tr = el("tr", { class: "total-row" });
    tr.append(el("td", { class: "c-label" }, [
      el("div", { class: "rowlabel" }, [el("span", { class: "name", text: `Toplam · ${layer.name}` })]),
    ]));
    for (const c of p.columns) {
      const fromActual = c.flag === "actual" ? p.total.layers.actual?.[String(c.bucket_id)] : undefined;
      tr.append(el("td", {
        class: `cell ${c.flag === "actual" ? "f-actual" : ""} ${c.is_current ? "is-current" : ""}`,
        text: fmt.n(fromActual !== undefined ? fromActual : p.total.layers[layer.code]?.[String(c.bucket_id)]),
      }));
    }
    for (const s of subCols) {
      tr.append(el("td", { class: "cell sub", text: fmt.n((p.total.subtotals[layer.code] || {})[s.key]) }));
    }
    return tr;
  }

  function cellNode(row, layer, col, p, opts = {}) {
    const key = String(col.bucket_id);
    const value = opts.overrideValue !== undefined
      ? opts.overrideValue
      : row.layers[layer.code]?.[key];
    const flags = row.flags[key] || {};
    const editable = !opts.readonly && layer.can_edit && ["freeze", "open"].includes(col.flag);
    const td = el("td", {
      class: [
        "cell",
        `f-${col.flag}`,
        col.is_current ? "is-current" : "",
        editable ? "editable" : "",
        flags.override ? "has-override" : "",
      ].filter(Boolean).join(" "),
      title: cellTitle(row, layer, col, flags, value),
      tabindex: 0,
    }, [
      value === undefined || value === null ? "" : fmt.n(value),
      flags.alert ? el("span", { class: "mark-alert" }) : null,
      flags.insight ? el("span", { class: "mark-insight", text: "●" }) : null,
    ]);

    td.onclick = (e) => {
      if (e.shiftKey && local.selection && local.selection.layer === layer.code
        && local.selection.group === row.group) {
        extendSelection(col.bucket_id, p);
      } else {
        local.selection = { group: row.group, layer: layer.code, bucketIds: [col.bucket_id], label: row.label };
        markSelection();
      }
      paintDetail();
    };
    /* Kapalı bir hücreye çift tıklamak sessizce hiçbir şey yapmıyordu; bu
       arayüzün bozuk olduğu izlenimi veriyor. Artık nedenini söylüyoruz. */
    const tryEdit = () => {
      if (editable) { startEdit(td, row, layer, col); return; }
      toast(whyLocked(layer, col, opts), "err");
    };
    td.ondblclick = tryEdit;
    td.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === "F2") { e.preventDefault(); tryEdit(); }
    };
    td.dataset.group = row.group;
    td.dataset.layer = layer.code;
    td.dataset.bucket = key;
    return td;
  }

  /** Hücre neden düzenlenemiyor? Sıra önemli: en dıştaki engeli söyleriz. */
  function whyLocked(layer, col, opts) {
    if (opts.readonly) return "Bu versiyon salt okunur, önce bir taslak seçin";
    if (!layer.can_edit) {
      if (layer.editable) return `${layer.name} katmanını düzenleme yetkiniz yok`;
      return layer.provenance === "actual"
        ? `${layer.name} kaynaktan gelen gerçek veri, elle değiştirilemez`
        : `${layer.name} hesaplanan bir katman, elle değiştirilemez`;
    }
    if (col.flag === "actual") return `${col.label} gerçekleşen dönem, değiştirilemez`;
    if (col.flag === "fence") return `${col.label} fence içinde, çevrim bu dönemi kapattı`;
    return `${col.label} düzenlemeye kapalı`;
  }

  function cellTitle(row, layer, col, flags, value) {
    const parts = [`${row.label} · ${layer.name} · ${col.label}`, `${flagLabel(col.flag)}`];
    if (value !== undefined && value !== null) parts.push(fmt.n(value, 1));
    if (flags.override) parts.push("override var");
    if (flags.insight) parts.push("saha bilgisi var");
    if (flags.alert) parts.push("uyarı var");
    if (flags.consensus_from) parts.push(`konsensüs kaynağı: ${flags.consensus_from.join(", ")}`);
    if (layer.can_edit && ["freeze", "open"].includes(col.flag)) parts.push("düzenlemek için çift tıkla");
    return parts.join(" · ");
  }

  function extendSelection(bucketId, p) {
    const ids = p.columns.map((c) => c.bucket_id);
    const from = ids.indexOf(local.selection.bucketIds[0]);
    const to = ids.indexOf(bucketId);
    const [a, b] = from <= to ? [from, to] : [to, from];
    local.selection.bucketIds = ids.slice(a, b + 1);
    markSelection();
  }

  function markSelection() {
    for (const node of gridScroll.querySelectorAll("td.cell.sel")) node.classList.remove("sel");
    if (!local.selection) return;
    for (const b of local.selection.bucketIds) {
      const node = gridScroll.querySelector(
        `td.cell[data-group="${cssEscape(local.selection.group)}"][data-layer="${local.selection.layer}"][data-bucket="${b}"]`,
      );
      if (node) node.classList.add("sel");
    }
  }

  function startEdit(td, row, layer, col) {
    const old = row.layers[layer.code]?.[String(col.bucket_id)] ?? 0;
    const input = el("input", { type: "text", value: String(old ?? "") });
    clear(td).append(input);
    input.focus();
    input.select();
    const finish = async (commit) => {
      const raw = input.value.trim().replace(/\./g, "").replace(",", ".");
      clear(td).append(String(fmt.n(old)));
      if (!commit) { paintGrid(); markSelection(); return; }
      const next = Number(raw);
      if (!Number.isFinite(next)) { toast("Geçersiz sayı", "err"); return; }
      if (Math.abs(next - Number(old)) < 0.5) { paintGrid(); return; }
      await commitOverride(row, layer, [col.bucket_id], { mode: "absolute", value: next, old });
    };
    input.onblur = () => finish(true);
    input.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.onblur = null; finish(true); }
      if (e.key === "Escape") { e.preventDefault(); input.onblur = null; finish(false); }
    };
  }

  async function commitOverride(row, layer, bucketIds, preset) {
    const p = local.payload;
    const reasonSel = select(
      state.reasonCodes.map((r) => ({ value: r.id, label: `${r.name}${r.requires_note ? " (not zorunlu)" : ""}` })),
      state.reasonCodes[0]?.id, () => {},
    );
    const noteInput = el("textarea", { rows: 2, placeholder: "Gerekçe notu" });
    const modeSel = select(
      [["absolute", "Mutlak değer"], ["pct", "Yüzde"], ["delta", "Fark"]].map(([value, label]) => ({ value, label })),
      preset.mode, () => {},
    );
    const valueInput = el("input", { type: "number", step: "any", value: String(preset.value ?? "") });
    const allocSel = select(
      [["layer_ratio", "Mevcut katman payı"], ["history_share", "Geçmiş pay (12 dönem)"],
        ["last_n_share", "Son N dönem payı"], ["equal", "Eşit"]].map(([value, label]) => ({ value, label })),
      "layer_ratio", () => {},
    );
    const bucketLabels = p.columns.filter((c) => bucketIds.includes(c.bucket_id)).map((c) => c.label);

    const ok = await modal({
      title: "Değişiklik gerekçesi",
      subtitle: `${row.label} · ${layer.name} · ${bucketLabels.join(", ")}`,
      body: [
        el("div", { class: "row g-3" }, [
          el("div", { class: "col-6" }, [field("Mod", modeSel)]),
          el("div", { class: "col-6" }, [field("Değer", valueInput)]),
        ]),
        field("Gerekçe kodu", reasonSel),
        field("Not", noteInput),
        field("Dağıtım anahtarı", allocSel),
        el("p", { class: "text-muted small", text:
          "Üst seviyeden girilen değer alt serilere dağıtılır, toplam korunur. "
          + "Her değişiklik override tablosuna ve audit log'a yazılır." }),
      ],
      actions: [{ label: "Vazgeç" }, { label: "Uygula", primary: true, value: true }],
    });
    if (!ok) { paintGrid(); return; }

    const reason = state.reasonCodes.find((r) => String(r.id) === String(reasonSel.value));
    if (reason?.requires_note && !noteInput.value.trim()) {
      toast(`${reason.name} gerekçesi için not zorunlu`, "err");
      return;
    }
    const scope = scopeFromGroup(row.group);
    let applied = 0;
    for (const bucketId of bucketIds) {
      try {
        const res = await api.override({
          version_id: ctx.versionId, layer: layer.code, bucket_id: bucketId,
          mode: modeSel.value, value: Number(valueInput.value),
          reason_code_id: Number(reasonSel.value), note: noteInput.value,
          scope, level: ctx.level, allocation_method: allocSel.value,
        });
        local.lastOverrideId = res.override_id;
        applied += 1;
        if (res.alerts?.length) toast(res.alerts[0].title, "err");
      } catch (err) {
        toast(err.message, "err");
        break;
      }
    }
    if (applied) toast(`${applied} dönem güncellendi. Geri almak için Ctrl+Z`, "ok");
    await loadGrid();
  }

  // ------------------------------------------------------------- legend
  function paintLegend() {
    const p = local.payload;
    clear(legend);
    legend.append(
      el("span", { class: "sw" }, [el("span", { class: "box actual" }), "gerçekleşen ve fence"]),
      el("span", { class: "sw" }, [el("span", { class: "box freeze" }), "freeze"]),
      el("span", { class: "sw" }, [el("span", { class: "box override" }), "override"]),
      el("span", { class: "sw" }, [el("span", { class: "box current" }), "mevcut dönem"]),
      el("span", { class: "sw" }, [el("span", { style: "width:5px;height:5px;border-radius:50%;background:var(--bs-danger);display:inline-block" }), "uyarı"]),
      el("span", { class: "sw" }, [el("span", { style: "color:var(--prov-human)" }, "●"), "saha bilgisi"]),
      el("span", { class: "flex-grow-1" }),
      el("span", { text: `${p.meta.series_in_scope} seri · ${p.rows.length} satır · konsensüs kuralı ${state.consensusRule}` }),
    );
    if (p.meta.restricted) legend.append(el("span", { class: "badge bg-warning-subtle text-warning-emphasis", text: "satır yetkisi sınırlı" }));
    const selectionActions = el("span", { class: "d-flex flex-wrap align-items-center gap-2", style: "gap:6px" }, [
      el("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onclick: () => bulkApply() }, "Seçili aralığı düzenle"),
      el("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onclick: () => copySelection() }, "Kopyala"),
      el("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onclick: () => undoLast() }, "Geri al (Ctrl+Z)"),
      el("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onclick: () => copyLayerDialog() }, "Katman kopyala"),
      el("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onclick: () => insightDialog() }, "Saha bilgisi"),
    ]);
    legend.append(selectionActions);
  }

  async function bulkApply() {
    if (!local.selection) { toast("Önce bir hücre veya aralık seçin"); return; }
    const p = local.payload;
    const row = p.rows.find((r) => r.group === local.selection.group);
    const layer = p.layers.find((l) => l.code === local.selection.layer);
    if (!row || !layer?.can_edit) { toast("Bu katmanda düzenleme yetkiniz yok", "err"); return; }
    const editable = p.columns
      .filter((c) => local.selection.bucketIds.includes(c.bucket_id) && ["freeze", "open"].includes(c.flag))
      .map((c) => c.bucket_id);
    if (!editable.length) { toast("Seçili aralık düzenlenemez (gerçekleşen veya fence)", "err"); return; }
    await commitOverride(row, layer, editable, { mode: "pct", value: 5 });
  }

  function copySelection() {
    if (!local.selection) { toast("Önce bir hücre veya aralık seçin"); return; }
    const p = local.payload;
    const row = p.rows.find((r) => r.group === local.selection.group);
    const values = local.selection.bucketIds.map((b) => row.layers[local.selection.layer]?.[String(b)] ?? "");
    navigator.clipboard?.writeText(values.join("\t"));
    toast(`${values.length} değer kopyalandı`);
  }

  async function undoLast() {
    if (!local.lastOverrideId) { toast("Geri alınacak değişiklik yok"); return; }
    try {
      const res = await api.revertOverride(local.lastOverrideId);
      local.lastOverrideId = null;
      toast(res.summary, "ok");
      await loadGrid();
    } catch (err) { toast(err.message, "err"); }
  }

  async function copyLayerDialog() {
    const from = select(state.layers.map((l) => ({ value: l.code, label: l.name })), "sales", () => {});
    const to = select(
      state.layers.filter((l) => l.can_edit).map((l) => ({ value: l.code, label: l.name })), "planner", () => {},
    );
    const ok = await modal({
      title: "Katman kopyala",
      subtitle: "Gerçekleşen ve fence dönemleri atlanir",
      body: [field("Kaynak", from), field("Hedef", to)],
      actions: [{ label: "Vazgeç" }, { label: "Kopyala", primary: true, value: true }],
    });
    if (!ok) return;
    try {
      const res = await api.copyLayer({ version_id: ctx.versionId, from_layer: from.value, to_layer: to.value });
      toast(res.summary, "ok");
      await loadGrid();
    } catch (err) { toast(err.message, "err"); }
  }

  async function insightDialog() {
    const text = el("textarea", { rows: 3, placeholder: "Sayıdan bağımsız saha bilgisi" });
    const reasonSel = select(
      state.reasonCodes.map((r) => ({ value: r.id, label: r.name })), state.reasonCodes[0]?.id, () => {},
    );
    const ok = await modal({
      title: "Saha bilgisi ekle",
      subtitle: local.selection ? `${local.selection.label}` : "Kapsam seçilmedi",
      body: [field("Metin", text), field("Gerekçe kodu", reasonSel)],
      actions: [{ label: "Vazgeç" }, { label: "Kaydet", primary: true, value: true }],
    });
    if (!ok || !text.value.trim()) return;
    try {
      const res = await api.addInsight({
        text: text.value.trim(), reason_code_id: Number(reasonSel.value),
        scope: local.selection ? scopeFromGroup(local.selection.group) : {},
        bucket_from_id: local.selection?.bucketIds[0],
        bucket_to_id: local.selection?.bucketIds.at(-1),
      });
      toast(res.summary, "ok");
    } catch (err) { toast(err.message, "err"); }
  }

  // ------------------------------------------------------------- detail
  async function paintDetail() {
    const p = local.payload;
    clear(detailChart);
    clear(detailCard);
    const row = local.selection
      ? p.rows.find((r) => r.group === local.selection.group)
      : p.rows[0];
    if (!row) { detailChart.append(empty("Satır yok")); return; }

    const dividerIndex = p.columns.findIndex((c) => c.is_current);
    const series = p.layers
      .filter((l) => Object.keys(row.layers[l.code] || {}).length)
      .map((l) => ({
        label: l.name,
        color: SERIES_COLOR[l.code] || "var(--prov-model)",
        dashed: ["budget", "last_year", "prior_version"].includes(l.code),
        points: p.columns.map((c) => {
          const v = row.layers[l.code]?.[String(c.bucket_id)];
          return v === undefined ? null : v;
        }),
      }));
    detailChart.append(
      el("div", { class: "d-flex flex-wrap align-items-center gap-2", style: "margin-bottom:4px" }, [
        el("span", { class: "text-muted text-uppercase fw-semibold small", text: row.label }),
        el("span", { class: "text-muted small", text: `${row.series_count} seri · kesikli çizgi referans katman` }),
      ]),
      lineChart({ labels: p.columns.map((c) => c.label), series, dividerIndex, height: 180 }),
    );

    if (!local.selection) {
      detailCard.append(el("p", { class: "text-muted small", text: "Bir hücre seçin: bu sayı nereden geldi bilgisi burada açılır." }));
      return;
    }
    detailCard.append(sectionLabel("Bu sayı nereden geldi"));
    detailCard.append(loading("Hücre zinciri okunuyor"));
    try {
      const info = await api.explain({
        version_id: ctx.versionId, layer: local.selection.layer, group: local.selection.group,
        bucket_id: local.selection.bucketIds[0], level: ctx.level,
      });
      clear(detailCard);
      const prov = info.version?.provenance || {};

      detailCard.append(
        sectionLabel("Bu sayı nereden geldi"),
        defList([
          ["Dönem", info.bucket.label],
          ["Versiyon", info.version?.name],
          ["Durum", VERSION_STATUS[info.version?.status] || info.version?.status],
          ["Senaryo", prov.scenario],
          ["Model", prov.model],
          ["Sürücü seti", prov.driver_set_id ? `#${prov.driver_set_id}` : null],
          ["Seviye", Object.entries(prov.level || {}).map(([k, v]) => `${k}=${v}`).join(" ")],
          ["Veri tarihi", prov.data_as_of ? fmt.day(prov.data_as_of) : null],
          ["Konsensüs", info.consensus_rule],
          ["Seri", `${info.series_in_cell} seri`],
        ]),
        el("div", { class: "mt-3" }, [sectionLabel("Katman değerleri")]),
        table(
          [{ key: "layer", label: "Katman" }, { key: "value", label: "Değer", numeric: true }],
          Object.entries(info.layer_values).map(([layer, value]) => ({
            layer: state.layers.find((l) => l.code === layer)?.name || layer, value,
          })),
        ),
      );
      if (info.rule_effects.length) {
        detailCard.append(
          el("div", { class: "mt-3" }, [sectionLabel("Kural etkileri")]),
          table(
            [{ key: "name", label: "Kural" }, { key: "delta", label: "Etki", numeric: true }],
            info.rule_effects,
          ),
        );
      }
      if (info.override_history.length) {
        detailCard.append(
          el("div", { class: "mt-3" }, [sectionLabel("Override geçmişi")]),
          table(
            [
              { key: "new_value", label: "Yeni", numeric: true },
              { key: "reason", label: "Gerekçe" },
              { key: "at", label: "Zaman", render: (v) => fmt.day(v) },
            ],
            info.override_history,
          ),
        );
      }
      if (info.insights.length) {
        detailCard.append(
          el("div", { style: "margin-top:10px" }, [el("span", { class: "text-muted text-uppercase fw-semibold small", text: "Saha bilgileri" })]),
          ...info.insights.map((i) => el("p", { class: "text-muted small", text: i.text })),
        );
      }
      if (prov.notes?.length) {
        detailCard.append(
          el("div", { style: "margin-top:10px" }, [el("span", { class: "text-muted text-uppercase fw-semibold small", text: "Çalıştırma notları" })]),
          ...prov.notes.map((n) => el("p", { class: "text-muted small", text: n })),
        );
      }
    } catch (err) {
      clear(detailCard).append(el("p", { class: "text-muted small", text: err.message }));
    }
  }

  // ------------------------------------------------------------- actions
  async function runDialog() {
    const scenarios = await api.scenarios();
    const boxes = scenarios.scenarios.map((s) => {
      const input = el("input", { type: "checkbox", value: s.id });
      return el("label", { class: "d-flex flex-wrap align-items-center gap-2", style: "gap:6px;align-items:flex-start" }, [
        input,
        el("div", {}, [
          el("div", { text: s.name, style: "font-weight:500" }),
          el("div", { class: "text-muted small", text: `${s.model?.name || ""} · ${s.rules.length} kural · ${s.driver_set?.name || "sürücü seti yok"}` }),
        ]),
      ]);
    });
    const ok = await modal({
      title: "Senaryo çalıştır",
      subtitle: "Birden fazla senaryo seçilebilir, her biri taslak versiyon üretir",
      body: boxes.length ? boxes : [empty("Senaryo yok")],
      actions: [{ label: "Vazgeç" }, { label: "Çalıştır", primary: true, value: true }],
    });
    if (!ok) return;
    const ids = boxes.map((b) => b.querySelector("input")).filter((i) => i.checked).map((i) => Number(i.value));
    if (!ids.length) { toast("Senaryo seçilmedi"); return; }
    const res = await api.run(ids);
    toast(res.summary, "ok");
    await watchRuns(res.runs.map((r) => r.run_id));
  }

  async function watchRuns(runIds) {
    const done = [];
    for (let attempt = 0; attempt < 240 && done.length < runIds.length; attempt += 1) {
      await new Promise((r) => setTimeout(r, 900));
      for (const id of runIds) {
        if (done.includes(id)) continue;
        const st = await api.runStatus(id);
        if (st.status === "done") {
          done.push(id);
          toast(`Çalıştırma ${id} bitti: ${st.version?.name || "taslak"} (${fmt.n(st.series_count)} seri)`, "ok");
          ctx.versionId = st.version?.id || ctx.versionId;
        } else if (st.status === "failed") {
          done.push(id);
          toast(`Çalıştırma ${id} başarısız: ${st.error}`, "err");
        }
      }
    }
    await api.versions().then((v) => { state.versions = v.versions; });
    paintRibbon();
    await loadGrid();
  }

  async function saveVersionDialog() {
    const current = state.versions.find((v) => v.id === ctx.versionId);
    const name = el("input", { type: "text", value: current ? `${current.name} kopya` : "" });
    const notes = el("textarea", { rows: 2 });
    const kind = select(
      [["saved", "Saklanan"], ["baseline", "Baz tahmin"], ["consensus_candidate", "Konsensüs adayı"]]
        .map(([value, label]) => ({ value, label })),
      "saved", () => {},
    );
    const ok = await modal({
      title: "Versiyonu sakla",
      subtitle: "Taslak ucuzdur, versiyon ad ve açıklama ister",
      body: [field("Ad", name), field("Açıklama", notes), field("Tur", kind)],
      actions: [{ label: "Vazgeç" }, { label: "Sakla", primary: true, value: true }],
    });
    if (!ok) return;
    try {
      const res = await api.saveVersion(ctx.versionId, {
        name: name.value.trim(), notes: notes.value, kind: kind.value,
      });
      toast(res.summary, "ok");
      const v = await api.versions();
      state.versions = v.versions;
      paintRibbon();
    } catch (err) { toast(err.message, "err"); }
  }

  async function exportGrid() {
    try {
      const blob = await api.exportGrid({
        version_id: ctx.versionId, scope: ctx.scope, level: ctx.level, grain: ctx.grain,
        layers: ctx.layerCodes, measure: ctx.measure, history_periods: 6,
      });
      download(blob, `tahmin-${ctx.versionId}.xlsx`);
      toast("Excel indirildi. Kilitli hücreler korumalı.", "ok");
    } catch (err) { toast(err.message, "err"); }
  }

  async function importGrid() {
    const input = el("input", { type: "file", accept: ".xlsx" });
    const ok = await modal({
      title: "Excel geri yükleme",
      subtitle: "Yalnizca platformdan indirilen şablon okunur, fark listesi onayınızı bekler",
      body: [field("Dosya", input)],
      actions: [{ label: "Vazgeç" }, { label: "Oku", primary: true, value: true }],
    });
    if (!ok || !input.files?.[0]) return;
    let preview;
    try {
      preview = await api.importPreview(input.files[0]);
    } catch (err) { toast(err.message, "err"); return; }
    if (!preview.count) { toast(preview.summary); return; }

    const reasonSel = select(
      state.reasonCodes.map((r) => ({ value: r.id, label: r.name })), state.reasonCodes[0]?.id, () => {},
    );
    const apply = await modal({
      title: "Fark listesi",
      subtitle: preview.summary,
      wide: true,
      body: [
        table(
          [
            { key: "group_label", label: "Kırılım" },
            { key: "layer", label: "Katman" },
            { key: "bucket_label", label: "Dönem" },
            { key: "old", label: "Eski", numeric: true },
            { key: "new", label: "Yeni", numeric: true },
            { key: "delta", label: "Fark", numeric: true },
          ],
          preview.diffs.slice(0, 200),
        ),
        field("Gerekçe kodu", reasonSel),
      ],
      actions: [{ label: "Vazgeç" }, { label: "Uygula", primary: true, value: true }],
    });
    if (!apply) return;
    try {
      const res = await api.importApply(preview, Number(reasonSel.value), "Excel geri yükleme");
      toast(res.summary, res.errors?.length ? "err" : "ok");
      await loadGrid();
    } catch (err) { toast(err.message, "err"); }
  }

  async function saveViewDialog() {
    const name = el("input", { type: "text", placeholder: "Görünüm adı" });
    const shared = el("input", { type: "checkbox", class: "form-check-input mt-0", id: "view-shared" });
    const ok = await modal({
      title: "Görünümü kaydet",
      subtitle: "Ağaç durumu, seviye, filtre, katman seçimi ve kova birlikte saklanır",
      body: [field("Ad", name), el("div", { class: "form-check d-flex align-items-center gap-2 m-0" }, [
        shared, el("label", { class: "form-check-label", for: shared.id, text: "Paylaşılan" }),
      ])],
      actions: [{ label: "Vazgeç" }, { label: "Kaydet", primary: true, value: true }],
    });
    if (!ok || !name.value.trim()) return;
    try {
      const res = await api.saveView({
        name: name.value.trim(), is_shared: shared.checked,
        state: { ...ctx, checked: Object.fromEntries(Object.entries(local.checked).map(([k, v]) => [k, [...v]])) },
      });
      toast(res.summary, "ok");
    } catch (err) { toast(err.message, "err"); }
  }

  // keyboard: Ctrl+Z undo, arrow navigation handled by tabindex
  const onKey = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); undoLast(); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c" && local.selection) copySelection();
  };
  document.addEventListener("keydown", onKey);
  wrap.addEventListener("remove", () => document.removeEventListener("keydown", onKey));

  paintRibbon();
  await paintTree();
  const applied = location.hash.split("/")[3];
  if (applied) ctx.versionId = Number(applied) || ctx.versionId;
  await loadGrid();
  return wrap;
}

function flagLabel(flag) {
  return { actual: "gerçekleşen", fence: "fence", freeze: "freeze", open: "açık" }[flag] || flag;
}

function scopeFromGroup(group) {
  const scope = {};
  if (!group || group === "total") return scope;
  for (const part of group.split("|")) {
    const [dim, nodeId] = part.split("=");
    scope[dim] = { nodes: [Number(nodeId)] };
  }
  return scope;
}

function cssEscape(value) {
  return String(value).replace(/["\\]/g, "\\$&");
}

// =============================================================== exceptions
async function exceptionsView({ state, navigate }) {
  const data = await api.alerts("open");
  const root = el("div", { class: "d-flex flex-column gap-3" });
  const rows = data.alerts.map((a) => ({ ...a, _a: a }));
  root.append(card(
    null,
    `${data.summary} · eşikler: önceki versiyona göre yüzde ${data.thresholds.change_vs_prior_pct}, `
    + `lag 1 WMAPE ${data.thresholds.wmape_lag1_threshold}, eksik dönem ${data.thresholds.missing_periods}`,
    [
      rows.length ? table(
        [
          { key: "severity", label: "Önem", render: (v) => el("span", {
            class: `badge ${v === "high" ? "bg-danger" : "bg-warning-subtle text-warning-emphasis"}`,
            text: SEVERITY[v] || v,
          }) },
          { key: "type_label", label: "Tip" },
          { key: "title", label: "Açıklama" },
          { key: "bucket", label: "Dönem" },
          { key: "_a", label: "", render: (a) => el("div", { class: "btn-group btn-group-sm flex-nowrap" }, [
            el("button", {
              class: "btn btn-outline-secondary", type: "button", title: "Grid'de aç", onclick: () => {
                if (a.version_id) state.ctx.versionId = a.version_id;
                navigate("planning/grid");
              },
            }, [icon("box-arrow-in-right")]),
            el("button", {
              class: "btn btn-outline-secondary", type: "button", title: "Okundu olarak işaretle",
              onclick: async () => {
                await api.setAlertStatus(a.id, "ack");
                toast("Uyarı okundu olarak işaretlendi", "ok");
                location.reload();
              },
            }, [icon("eye")]),
            el("button", {
              class: "btn btn-outline-secondary", type: "button", title: "Uyarıyı kapat",
              onclick: async () => {
                await api.setAlertStatus(a.id, "closed");
                toast("Uyarı kapatıldı", "ok");
                location.reload();
              },
            }, [icon("check2")]),
          ]) },
        ],
        rows,
      ) : empty("Açık uyarı yok", "check-circle"),
    ],
  ));
  return root;
}

// ============================================================== saved views
async function savedViewsView({ state, navigate }) {
  const data = await api.savedViews();
  const root = el("div", { class: "d-flex flex-column gap-3" });
  root.append(card(null, data.summary, [
    data.views.length ? table(
      [
        { key: "name", label: "Ad" },
        { key: "is_shared", label: "Paylaşım", render: (v) => el("span", {
          class: `badge ${v ? "bg-info-subtle text-info-emphasis" : "bg-secondary-subtle text-secondary-emphasis"}`,
          text: v ? "paylaşılan" : "kişisel",
        }) },
        { key: "mine", label: "Sahip", render: (v) => (v
          ? el("span", { class: "badge bg-primary-subtle text-primary-emphasis", text: "ben" })
          : el("span", { class: "text-muted", text: "başka kullanıcı" })) },
        { key: "state", label: "", render: (st) => el("button", {
          class: "btn btn-sm btn-outline-secondary", type: "button", onclick: () => {
            Object.assign(state.ctx, st, {
              checked: undefined,
            });
            navigate("planning/grid");
          },
        }, [icon("box-arrow-in-right"), el("span", { class: "ms-2", text: "Uygula" })]) },
      ],
      data.views,
    ) : empty("Kayıtlı görünüm yok. Grid ekranından kaydedin.", "bookmark"),
  ]));
  return root;
}

// ================================================================= insights
async function insightsView() {
  const data = await api.insights();
  const root = el("div", { class: "d-flex flex-column gap-3" });
  const mark = async (id, outcome) => {
    await api.post(`/api/v1/insights/${id}/outcome?outcome=${outcome}`);
    toast(outcome === "correct" ? "Doğru olarak işaretlendi" : "Yanlış olarak işaretlendi", "ok");
    location.reload();
  };
  root.append(card(null, data.summary, [
    data.insights.length ? table(
      [
        { key: "user", label: "Kullanıcı" },
        { key: "reason", label: "Gerekçe" },
        { key: "text", label: "Metin" },
        { key: "from", label: "Başlangıç" },
        { key: "to", label: "Bitiş" },
        { key: "outcome", label: "Sonuç", render: (v) => (v
          ? el("span", {
            class: `badge ${v === "correct" ? "bg-success-subtle text-success-emphasis"
              : "bg-danger-subtle text-danger-emphasis"}`,
            text: v === "correct" ? "doğru" : "yanlış",
          })
          : el("span", { class: "text-muted small", text: "beklemede" })) },
        { key: "id", label: "", render: (id) => el("div", { class: "btn-group btn-group-sm flex-nowrap" }, [
          el("button", {
            class: "btn btn-outline-success", type: "button", title: "Doğru çıktı",
            onclick: () => mark(id, "correct"),
          }, [icon("hand-thumbs-up")]),
          el("button", {
            class: "btn btn-outline-danger", type: "button", title: "Yanlış çıktı",
            onclick: () => mark(id, "wrong"),
          }, [icon("hand-thumbs-down")]),
        ]) },
      ],
      data.insights,
    ) : empty("Saha bilgisi yok", "chat-left-text"),
  ]));
  return root;
}
