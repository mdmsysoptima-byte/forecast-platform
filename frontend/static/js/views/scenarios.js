// Senaryolar: liste, editor (surucu ve kural sekmeleri), calistirma gecmisi.

import { api } from "../api.js";
import {
  SERIES_COLOR, card, clear, el, empty, field, fmt, icon, jsonBlock, lineChart, loading, modal,
  pageHeader, select, table, tabs, toast,
} from "../ui.js";

const RULE_FORMS = {
  multiplier: [["pct", "Yüzde", "number"]],
  additive: [["amount", "Dönem başına mutlak tutar", "number"]],
  shift: [["source_bucket", "Kaynak dönem", "bucket"], ["target_bucket", "Hedef dönem", "bucket"],
    ["ratio", "Oran yüzde", "number"]],
  event: [["effect", "Etki tipi", "select:point=nokta,level=seviye,temporary=geçici,ramp=rampa"],
    ["pct", "Büyüklük yüzde", "number"], ["set_value", "Sabit değere ayarla (opsiyonel)", "number"]],
  cap: [["max", "Üst sınır", "number"], ["min", "Alt sınır", "number"],
    ["scope_level", "Sınır seviyesi", "select:series=seri,total=kapsam toplamı"]],
  launch_curve: [["peak", "Zirve değer", "number"],
    ["template", "Şablon", "select:s_curve=S eğrisi,linear=doğrusal"]],
  phase_out: [["profile", "Düşüş profili", "select:linear=doğrusal,geometric=geometrik"]],
  elasticity: [["price_pct", "Fiyat değişimi yüzde", "number"], ["elasticity", "Esneklik katsayısı", "number"]],
};

/* Sürücü değerinin nereden geldiğini anlatan kodlar; tabloda Türkçe durur. */
const ENTRY_LABEL = {
  pct_growth: "dönemsel yüzde artış", constant: "sabit değer", trend: "geçmiş trendi",
  table: "elle tablo", source: "kaynaktan", copy: "kopya",
};

/* Kural tipi kodları kullanıcıya gösterilmez. Etiketler backend'deki
   scenario_rules.RULE_LABELS ile birebir aynı: seçicide yazan ile kural
   listesinde görünen aynı kelime olmalı. */
const RULE_LABEL = {
  multiplier: "Çarpan", additive: "Toplama", shift: "Dönem kaydırma", event: "Olay",
  cap: "Sınır", launch_curve: "Lansman eğrisi", phase_out: "Terk eğrisi", elasticity: "Esneklik",
};

export async function render({ state, navigate }) {
  const [scen, models, driverSets, buckets] = await Promise.all([
    api.scenarios(), api.models(), api.driverSets(), api.buckets("month"),
  ]);
  const root = el("div", { class: "d-flex flex-column gap-3" });
  const forecastBuckets = buckets.buckets.filter((b) => b.flag !== "actual");

  const selectedId = Number(location.hash.split("/")[2]) || scen.scenarios[0]?.id;
  const active = scen.scenarios.find((s) => s.id === selectedId);

  const btn = (label, iconName, handler, variant = "btn-outline-secondary") => el("button", {
    class: `btn btn-sm ${variant}`, type: "button", onclick: handler,
  }, [icon(iconName), el("span", { class: "ms-2", text: label })]);

  root.append(pageHeader("Senaryolar", scen.summary, [
    btn("Yeni senaryo", "plus-lg", () => createDialog(), "btn-primary"),
    active ? btn("Kopyala", "copy", () => copyDialog(active)) : null,
    active ? btn("Çalıştır", "play-fill", async () => {
      const res = await api.run([active.id]);
      toast(res.summary, "ok");
      navigate("scenarios");
    }) : null,
    btn("Toplu çalıştır", "collection-play", () => runManyDialog()),
  ]));

  root.append(card(null, null, [
    scen.scenarios.length ? table(
      [
        { key: "name", label: "Ad" },
        { key: "model", label: "Model", render: (m) => (m ? `${m.name} ${m.version}` : "") },
        { key: "driver_set", label: "Sürücü seti", render: (d) => d?.name || "" },
        { key: "rules", label: "Kural", numeric: true, dec: 0, render: (r) => String(r.length) },
        { key: "description", label: "Açıklama" },
        { key: "id", label: "", render: (id) => el("button", {
          class: `btn btn-sm ${id === selectedId ? "btn-primary" : "btn-outline-secondary"}`, type: "button",
          onclick: () => { location.hash = `#/scenarios/${id}`; },
        }, "Editör") },
      ],
      scen.scenarios,
    ) : empty("Senaryo yok. Yeni senaryo ile başlayın."),
  ], { flush: true }));

  if (active) {
    root.append(await editor(active, { state, models: models.models, driverSets: driverSets.driver_sets, forecastBuckets }));
  }
  root.append(await runHistory());
  return root;

  // --------------------------------------------------------------- dialogs
  async function createDialog() {
    const name = el("input", { type: "text", placeholder: "Senaryo adı" });
    const desc = el("textarea", { rows: 2 });
    const modelSel = select(
      models.models.map((m) => ({ value: m.code, label: `${m.name} ${m.version}` })),
      models.models[0]?.code, () => {},
    );
    const dsSel = select(
      [{ value: "", label: "yok" }, ...driverSets.driver_sets.map((d) => ({ value: d.id, label: d.name }))],
      driverSets.driver_sets[0]?.id, () => {},
    );
    const baseSel = select(
      [{ value: "", label: "yok" }, ...state.versions.map((v) => ({ value: v.id, label: v.name }))],
      state.versions.find((v) => v.kind === "baseline")?.id, () => {},
    );
    const modelNote = el("p", { class: "text-muted small mb-0" });
    const syncNote = () => {
      const m = models.models.find((x) => x.code === modelSel.value);
      modelNote.textContent = m
        ? `Zorunlu sürücü: ${m.required_drivers.join(", ") || "yok"} · desteklediği kova: ${m.supported_grains.join(", ")}`
        : "";
    };
    modelSel.addEventListener("change", syncNote);
    syncNote();

    const ok = await modal({
      title: "Yeni senaryo",
      subtitle: "Senaryo bir baz versiyondan kopya olarak doğar",
      body: [field("Ad", name), field("Açıklama", desc), field("Model", modelSel), modelNote,
        field("Sürücü seti", dsSel), field("Baz versiyon", baseSel)],
      actions: [{ label: "Vazgeç" }, { label: "Oluştur", primary: true, value: true }],
    });
    if (!ok || !name.value.trim()) return;
    try {
      const res = await api.createScenario({
        name: name.value.trim(), description: desc.value, model_code: modelSel.value,
        driver_set_id: dsSel.value ? Number(dsSel.value) : null,
        base_version_id: baseSel.value ? Number(baseSel.value) : null,
        level: state.ctx.level, grain: "month", scope: {},
      });
      toast(res.summary, "ok");
      location.hash = `#/scenarios/${res.id}`;
    } catch (err) { toast(err.message, "err"); }
  }

  async function copyDialog(scenario) {
    const name = el("input", { type: "text", value: `${scenario.name} kopya` });
    const ok = await modal({
      title: "Senaryoyu kopyala", body: [field("Yeni ad", name)],
      actions: [{ label: "Vazgeç" }, { label: "Kopyala", primary: true, value: true }],
    });
    if (!ok) return;
    const res = await api.copyScenario(scenario.id, name.value.trim());
    toast(res.summary, "ok");
    location.hash = `#/scenarios/${res.id}`;
  }

  async function runManyDialog() {
    const boxes = scen.scenarios.map((s) => {
      const input = el("input", { type: "checkbox", class: "form-check-input mt-0", value: s.id, id: `run-${s.id}` });
      return el("div", { class: "form-check d-flex align-items-center gap-2 m-0" }, [
        input, el("label", { class: "form-check-label", for: `run-${s.id}`, text: s.name }),
      ]);
    });
    const ok = await modal({
      title: "Birden fazla senaryoyu çalıştır",
      subtitle: "Her senaryo kendi taslak versiyonunu üretir, sonra karşılaştırılır",
      body: boxes,
      actions: [{ label: "Vazgeç" }, { label: "Çalıştır", primary: true, value: true }],
    });
    if (!ok) return;
    const ids = boxes.map((b) => b.querySelector("input")).filter((i) => i.checked).map((i) => Number(i.value));
    if (!ids.length) return;
    const res = await api.run(ids);
    toast(res.summary, "ok");
    location.hash = "#/scenarios";
    setTimeout(() => location.reload(), 1500);
  }
}

// ------------------------------------------------------------------ editor
async function editor(scenario, { state, models, driverSets, forecastBuckets }) {
  const tab = location.hash.split("/")[3] || "drivers";
  const head = tabs(
    [{ id: "drivers", label: "Sürücüler", icon: "sliders" },
      { id: "rules", label: "Kurallar", icon: "diagram-3" }],
    tab,
    (id) => { location.hash = `#/scenarios/${scenario.id}/${id}`; },
  );

  const model = models.find((m) => m.code === scenario.model?.code);
  const facts = [
    ["Model", scenario.model ? `${scenario.model.name} ${scenario.model.version}` : "-"],
    ["Sürücü seti", scenario.driver_set?.name || "-"],
    ["Baz versiyon", scenario.base_version_id ? `#${scenario.base_version_id}` : "-"],
    ["Seviye", Object.entries(scenario.level || {}).map(([k, v]) => `${k}=${v}`).join(" ") || "-"],
    ["Kova", scenario.grain],
    ["Ufuk", `${scenario.horizon_periods} dönem`],
    ["Zorunlu sürücü", model?.required_drivers.join(", ") || "yok"],
  ];
  const header = el("div", { class: "row g-3 border-bottom pb-3 mb-3" }, facts.map(([k, v]) => el(
    "div", { class: "col-6 col-md-4 col-xl-3" }, [
      el("div", { class: "text-muted text-uppercase fw-semibold small", text: k }),
      el("div", { class: "text-truncate", title: v, text: v ?? "-" }),
    ],
  )));

  const body = el("div", { class: "d-flex flex-column gap-3" });
  if (tab === "rules") body.append(await rulesTab(scenario, { state, forecastBuckets }));
  else body.append(await driversTab(scenario, { driverSets }));

  return card(`Senaryo editörü: ${scenario.name}`, scenario.description, [header, body], { actions: head });
}

async function driversTab(scenario, { driverSets }) {
  const wrap = el("div", { class: "d-flex flex-column gap-3" });
  const setSel = select(
    driverSets.map((d) => ({ value: d.id, label: d.name })),
    scenario.driver_set?.id || driverSets[0]?.id,
    () => paint(),
  );
  const compareSel = select(
    [{ value: "", label: "yok" }, ...driverSets.map((d) => ({ value: d.id, label: d.name }))],
    "", () => paint(),
  );
  const content = el("div", { class: "d-flex flex-column gap-3" });
  wrap.append(
    el("div", { class: "d-flex flex-wrap align-items-end gap-3" }, [
      field("Sürücü seti", setSel),
      field("Üst üste çiz", compareSel),
      el("div", { class: "d-flex gap-2 ms-auto" }, [
        el("button", {
          class: "btn btn-sm btn-primary", type: "button", onclick: () => assumptionDialog(),
        }, [icon("pencil-square"), el("span", { class: "ms-2", text: "Varsayım gir" })]),
        el("button", {
          class: "btn btn-sm btn-outline-secondary", type: "button", onclick: () => newSetDialog(),
        }, [icon("plus-lg"), el("span", { class: "ms-2", text: "Yeni set" })]),
      ]),
    ]),
    content,
  );

  async function paint() {
    clear(content).append(loading("Sürücü değerleri okunuyor"));
    const main = await api.driverSetValues(Number(setSel.value));
    const other = compareSel.value ? await api.driverSetValues(Number(compareSel.value)) : null;
    clear(content);
    if (!main.drivers.length) { content.append(empty("Bu sette sürücü değeri yok")); return; }
    for (const d of main.drivers) {
      const labels = [...d.history.map((h) => h.label), ...d.future.map((f) => f.label)];
      const histPoints = [...d.history.map((h) => h.value), ...d.future.map(() => null)];
      const futPoints = [
        ...d.history.map(() => null).slice(0, Math.max(0, d.history.length - 1)),
        ...(d.history.length ? [d.history.at(-1).value] : []),
        ...d.future.map((f) => f.value),
      ];
      const series = [
        { label: "Geçmiş (salt okunur)", color: "var(--ink-muted)", points: histPoints },
        { label: "Gelecek (düzenlenebilir)", color: SERIES_COLOR.statistical, points: futPoints },
      ];
      if (other) {
        const od = other.drivers.find((x) => x.driver.code === d.driver.code);
        if (od) {
          series.push({
            label: `Karşılaştırma: ${od.driver.name}`, color: SERIES_COLOR.sales, dashed: true,
            points: [...od.history.map(() => null), ...od.future.map((f) => f.value)],
          });
        }
      }
      content.append(card(
        `${d.driver.name} (${d.driver.code})`,
        `${d.driver.unit || "birimsiz"} · ${d.history.length} geçmiş, ${d.future.length} gelecek dönem`,
        [
          lineChart({ labels, series, dividerIndex: d.history.length - 1, height: 160 }),
          el("div", { class: "mt-3" }, [table(
            [{ key: "label", label: "Dönem" }, { key: "value", label: "Değer", numeric: true, dec: 1 },
              { key: "entry_method", label: "Giriş yöntemi", render: (v) => ENTRY_LABEL[v] || v }],
            d.future,
          )]),
        ],
      ));
    }
  }

  async function assumptionDialog() {
    const drivers = await api.drivers();
    const driverSel = select(
      drivers.drivers.map((d) => ({ value: d.code, label: `${d.name} (${d.code})` })),
      drivers.drivers[0]?.code, () => {},
    );
    const methodSel = select(
      [["pct_growth", "Dönemsel yüzde artis"], ["constant", "Sabit değer"],
        ["trend", "Geçmiş trendi uzat"], ["table", "Elle tablo"]].map(([value, label]) => ({ value, label })),
      "pct_growth", () => {},
    );
    const valueInput = el("input", { type: "number", step: "any", value: "2.5" });
    const tableInput = el("textarea", { rows: 3, placeholder: "2026-11=42.5\n2026-12=43.1" });
    const ok = await modal({
      title: "Sürücü varsayımı",
      subtitle: "Geçmiş degerler kaynaktan gelir ve salt okunurdur, gelecek değerleri siz girersiniz",
      body: [field("Sürücü", driverSel), field("Yöntem", methodSel),
        field("Değer veya aylik yüzde", valueInput), field("Tablo (yöntem elle tablo ise)", tableInput)],
      actions: [{ label: "Vazgeç" }, { label: "Uygula", primary: true, value: true }],
    });
    if (!ok) return;
    const payload = {
      driver_code: driverSel.value, method: methodSel.value, value: Number(valueInput.value),
    };
    if (methodSel.value === "table") {
      payload.values = Object.fromEntries(
        tableInput.value.split(/\n/).map((l) => l.split("=")).filter((p) => p.length === 2)
          .map(([k, v]) => [k.trim(), Number(v)]),
      );
    }
    try {
      const res = await api.setAssumption(Number(setSel.value), payload);
      toast(res.summary, "ok");
      await paint();
    } catch (err) { toast(err.message, "err"); }
  }

  async function newSetDialog() {
    const name = el("input", { type: "text", placeholder: "Örnek: yüksek enflasyon" });
    const desc = el("input", { type: "text" });
    const copy = select(
      [{ value: "", label: "boş" }, ...driverSets.map((d) => ({ value: d.id, label: d.name }))],
      String(setSel.value), () => {},
    );
    const ok = await modal({
      title: "Yeni sürücü seti", body: [field("Ad", name), field("Açıklama", desc), field("Kopyalanacak set", copy)],
      actions: [{ label: "Vazgeç" }, { label: "Oluştur", primary: true, value: true }],
    });
    if (!ok || !name.value.trim()) return;
    const res = await api.createDriverSet({
      name: name.value.trim(), description: desc.value,
      copy_from_set_id: copy.value ? Number(copy.value) : null,
    });
    toast(res.summary, "ok");
    location.reload();
  }

  await paint();
  return wrap;
}

async function rulesTab(scenario, { state, forecastBuckets }) {
  const wrap = el("div", { class: "d-flex flex-column gap-3" });
  const rows = scenario.rules.map((r, i) => ({
    ...r,
    order_label: String(i + 1),
    params_text: Object.entries(r.params || {}).map(([k, v]) => `${k}=${v}`).join(", "),
    range: [r.bucket_from, r.bucket_to].filter(Boolean).join(" - ") || "tüm ufuk",
  }));

  wrap.append(
    el("div", { class: "d-flex flex-wrap align-items-center gap-2" }, [
      el("button", {
        class: "btn btn-sm btn-primary", type: "button", onclick: () => addRuleDialog(),
      }, [icon("plus-lg"), el("span", { class: "ms-2", text: "Kural ekle" })]),
      el("span", {
        class: "text-muted small", text: "Kurallar sıralı uygulanır, her kural etkisini ayrı saklar",
      }),
    ]),
    rows.length ? table(
      [
        { key: "order_label", label: "Sıra" },
        { key: "type_label", label: "Tip" },
        { key: "name", label: "Ad" },
        { key: "range", label: "Dönem aralığı" },
        { key: "params_text", label: "Parametreler" },
        { key: "id", label: "", render: (id, r) => el("div", { class: "btn-group btn-group-sm" }, [
          el("button", {
            class: "btn btn-outline-secondary", type: "button", title: "Yukarı taşı",
            onclick: () => move(r, -1),
          }, [icon("arrow-up")]),
          el("button", {
            class: "btn btn-outline-secondary", type: "button", title: "Aşağı taşı",
            onclick: () => move(r, 1),
          }, [icon("arrow-down")]),
          el("button", { class: "btn btn-outline-danger", type: "button", title: "Sil", onclick: async () => {
            await api.deleteRule(id);
            toast("Kural silindi", "ok");
            location.reload();
          } }, [icon("trash")]),
        ]) },
      ],
      rows,
    ) : empty("Kural yok. Senaryo yalnızca sürücü varsayımlarıyla çalışır."),
  );

  async function move(rule, delta) {
    const ids = scenario.rules.map((r) => r.id);
    const i = ids.indexOf(rule.id);
    const j = i + delta;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    await api.reorderRules(scenario.id, ids);
    toast("Kural sırası güncellendi", "ok");
    location.reload();
  }

  async function addRuleDialog() {
    const typeSel = select(
      Object.keys(RULE_FORMS).map((k) => ({ value: k, label: RULE_LABEL[k] || k })),
      "multiplier", () => paintParams(),
    );
    const nameInput = el("input", { type: "text", placeholder: "Kural adı" });
    const fromSel = select(
      [{ value: "", label: "ufuk başı" }, ...forecastBuckets.map((b) => ({ value: b.label, label: b.label }))],
      "", () => {},
    );
    const toSel = select(
      [{ value: "", label: "ufuk sonu" }, ...forecastBuckets.map((b) => ({ value: b.label, label: b.label }))],
      "", () => {},
    );
    const dimSel = select(
      [{ value: "", label: "tüm kapsam" },
        ...state.dimensions.map((d) => ({ value: d.code, label: d.name }))],
      "", () => paintScope(),
    );
    const nodeBox = el("div", {});
    const paramBox = el("div", { class: "d-flex flex-column gap-3" });
    const inputs = {};

    function paintParams() {
      clear(paramBox);
      inputs.values = {};
      for (const [key, label, kind] of RULE_FORMS[typeSel.value]) {
        let control;
        if (kind === "number") control = el("input", { type: "number", step: "any" });
        else if (kind === "bucket") {
          control = select(forecastBuckets.map((b) => ({ value: b.label, label: b.label })),
            forecastBuckets[0]?.label, () => {});
        } else if (kind.startsWith("select:")) {
          const opts = kind.slice(7).split(",").map((pair) => {
            const [value, lbl] = pair.split("=");
            return { value, label: lbl };
          });
          control = select(opts, opts[0].value, () => {});
        } else control = el("input", { type: "text" });
        inputs.values[key] = control;
        paramBox.append(field(label, control));
      }
    }

    async function paintScope() {
      clear(nodeBox);
      if (!dimSel.value) return;
      const tree = await api.tree(dimSel.value);
      const flat = [];
      const walk = (nodes, depth) => {
        for (const n of nodes) {
          flat.push({ value: n.node_id, label: `${"  ".repeat(depth)}${n.label} (${n.level})` });
          walk(n.children, depth + 1);
        }
      };
      walk(tree.nodes, 0);
      inputs.node = select([{ value: "", label: "tüm düğümler" }, ...flat], "", () => {});
      nodeBox.append(field("Düğüm", inputs.node));
    }

    paintParams();
    const ok = await modal({
      title: "Kural ekle",
      subtitle: "Kurallar skor üzerine uygulanır, istenen seviyede yazılır ve uzlaştırma alt seviyeleri günceller",
      body: [
        field("Tip", typeSel), field("Ad", nameInput),
        el("div", { class: "row g-3" }, [
          el("div", { class: "col-6" }, [field("Başlangıç dönemi", fromSel)]),
          el("div", { class: "col-6" }, [field("Bitiş dönemi", toSel)]),
        ]),
        field("Kapsam boyutu", dimSel), nodeBox, paramBox,
      ],
      actions: [{ label: "Vazgeç" }, { label: "Ekle", primary: true, value: true }],
    });
    if (!ok) return;

    const params = {};
    for (const [key, control] of Object.entries(inputs.values)) {
      const raw = control.value;
      if (raw === "" || raw === null) continue;
      params[key] = Number.isNaN(Number(raw)) ? raw : Number(raw);
    }
    const target_scope = {};
    if (dimSel.value && inputs.node?.value) {
      target_scope[dimSel.value] = { nodes: [Number(inputs.node.value)] };
    }
    try {
      const res = await api.addRule(scenario.id, {
        type: typeSel.value, name: nameInput.value.trim(), params, target_scope,
        bucket_from: fromSel.value || null, bucket_to: toSel.value || null,
      });
      toast(res.summary, "ok");
      location.reload();
    } catch (err) { toast(err.message, "err"); }
  }

  return wrap;
}

// ----------------------------------------------------------- run history
async function runHistory() {
  const data = await api.runs();
  return card("Çalıştırma geçmişi", data.summary, [
    data.runs.length ? table(
      [
        { key: "id", label: "No", numeric: true, dec: 0 },
        { key: "scenario", label: "Senaryo" },
        { key: "model", label: "Model" },
        { key: "status", label: "Durum", render: (v, r) => el("span", {
          class: `badge ${v === "done" ? "bg-success" : v === "failed" ? "bg-danger" : "bg-info"}`,
          text: { done: "bitti", failed: "hata", running: "çalışıyor", queued: "kuyrukta" }[v] || v,
          title: r.error || "",
        }) },
        { key: "series_count", label: "Seri", numeric: true, dec: 0 },
        { key: "duration_ms", label: "Süre", render: (v) => fmt.ms(v) },
        { key: "data_as_of", label: "Veri tarihi", render: (v) => fmt.day(v) },
        { key: "version", label: "Versiyon", render: (v) => v?.name || "" },
        { key: "diagnostics", label: "Tanı", render: (d, r) => el("button", {
          class: "btn btn-sm btn-outline-secondary", type: "button", title: "Tanı bilgisini göster",
          onclick: () => modal({
            title: `Çalıştırma ${r.id} tanı bilgisi`,
            subtitle: r.error || "Hata yok",
            wide: true,
            body: [jsonBlock(d)],
          }),
        }, [icon("braces")]) },
      ],
      data.runs,
    ) : empty("Çalıştırma yok"),
  ]);
}
