// Versiyonlar: liste, karsilastirma, onay akisi.

import { api, session } from "../api.js";
import {
  SERIES_COLOR, card, clear, confirmDialog, defList, el, empty, field, fmt, icon, lineChart, loading,
  modal, pageHeader, sectionLabel, select, table, toast,
} from "../ui.js";

const KIND_LABEL = {
  draft: "taslak", saved: "saklanan", baseline: "baz", consensus: "konsensüs",
  consensus_candidate: "konsensüs adayı", snapshot: "snapshot",
};
const STATUS_LABEL = { draft: "taslak", review: "inceleme", approved: "onaylı", locked: "kilitli" };
const STEP_NEXT = { draft: "review", review: "approved", approved: "locked" };
const STEP_LABEL = { review: "İncelemeye al", approved: "Onayla", locked: "Kilitle ve snapshot al" };
const STEP_ICON = { review: "eye", approved: "check2", locked: "lock" };

export async function render({ state, refreshShared }) {
  const data = await api.versions();
  state.versions = data.versions;
  const root = el("div", { class: "d-flex flex-column gap-3" });
  const selected = new Set();

  const compareBox = el("div", {});
  const compareBtn = el("button", {
    class: "btn btn-sm btn-primary", type: "button", disabled: true,
    onclick: () => runCompare([...selected]),
  }, [icon("bar-chart-steps"), el("span", { class: "ms-2", text: "Seçilenleri karşılaştır" })]);
  const syncCompareBtn = () => {
    compareBtn.disabled = selected.size < 2;
    compareBtn.title = selected.size < 2 ? "En az iki versiyon seçin" : `${selected.size} versiyon seçili`;
  };
  syncCompareBtn();

  root.append(pageHeader("Versiyonlar", data.summary, compareBtn));

  root.append(card(null, null, [
    table(
      [
        { key: "id", label: "", render: (id) => {
          const cb = el("input", { type: "checkbox", class: "form-check-input mt-0", "aria-label": "Karşılaştırmaya al" });
          cb.onchange = () => {
            if (cb.checked) selected.add(id); else selected.delete(id);
            syncCompareBtn();
          };
          return cb;
        } },
        { key: "name", label: "Ad" },
        { key: "kind", label: "Tür", render: (v) => el("span", {
          class: `badge ${v === "snapshot" ? "bg-success-subtle text-success-emphasis"
            : v === "draft" ? "bg-secondary-subtle text-secondary-emphasis"
              : "bg-primary-subtle text-primary-emphasis"}`,
          text: KIND_LABEL[v] || v,
        }) },
        { key: "status", label: "Durum", render: (v) => el("span", {
          class: `badge ${v === "locked" ? "bg-dark-subtle text-body"
            : v === "approved" ? "bg-success-subtle text-success-emphasis"
              : "bg-secondary-subtle text-secondary-emphasis"}`,
          text: STATUS_LABEL[v] || v,
        }) },
        { key: "scenario", label: "Senaryo" },
        { key: "provenance", label: "Model", render: (p) => p?.model || "" },
        { key: "created_at", label: "Oluşturma", render: (v) => fmt.date(v) },
        { key: "expires_at", label: "Taslak süresi", render: (v) => (v ? fmt.date(v) : "-") },
        { key: "run_id", label: "İşlem", render: (_v, r) => actions(r) },
      ],
      data.versions,
    ),
  ], { flush: true }));
  root.append(compareBox);
  return root;

  /* Satır başına beş eylem sığmadığı için hepsi ikon olarak tek bir düğme
     grubunda durur; adı tooltip taşır. */
  function actions(version) {
    const box = el("div", { class: "btn-group btn-group-sm flex-nowrap" });
    const act = (label, iconName, handler, variant = "btn-outline-secondary") => box.append(el("button", {
      class: `btn ${variant}`, type: "button", title: label, "aria-label": label, onclick: handler,
    }, [icon(iconName)]));

    act("İzlenebilirlik zinciri", "diagram-2", () => provenanceDialog(version));
    if (version.kind === "draft") {
      act("Sakla", "save", () => saveDialog(version));
      act("Taslağı at", "trash", async () => {
        const ok = await confirmDialog("Taslağı at", `${version.name} silinecek. Devam edilsin mi?`, "At");
        if (!ok) return;
        await api.discardVersion(version.id);
        toast("Taslak atıldı", "ok");
        location.reload();
      }, "btn-outline-danger");
    }
    const next = STEP_NEXT[version.status];
    if (next) act(STEP_LABEL[next], STEP_ICON[next], () => approve(version, next), "btn-outline-primary");
    if (version.status === "locked" && session.can("admin", "planner", "finance")) {
      act("Depoya yaz", "database-up", async () => {
        try {
          const res = await api.writeConsensus(version.id);
          toast(res.summary, "ok");
        } catch (err) { toast(err.message, "err"); }
      });
    }
    return box;
  }

  async function approve(version, step) {
    const comment = el("textarea", { rows: 2, placeholder: "Yorum" });
    const ok = await modal({
      title: STEP_LABEL[step],
      subtitle: `${version.name} · adım atlanamaz, her adım audit log'a düşer`,
      body: [field("Yorum", comment)],
      actions: [{ label: "Vazgeç" }, { label: "Devam", primary: true, value: true }],
    });
    if (!ok) return;
    try {
      let res = await api.approve({ version_id: version.id, step, comment: comment.value });
      if (res.needs_acknowledge) {
        const warn = await modal({
          title: "Açık uyarılar var",
          subtitle: "Onaylamadan önce bunların görülmesi gerekiyor",
          body: [table(
            [{ key: "severity", label: "Önem" }, { key: "type", label: "Tip" }, { key: "title", label: "Açıklama" }],
            res.warnings,
          )],
          actions: [{ label: "Vazgeç" }, { label: "Gördüm, onayla", primary: true, value: true }],
        });
        if (!warn) return;
        res = await api.approve({
          version_id: version.id, step, comment: comment.value, acknowledge_warnings: true,
        });
      }
      toast(res.summary, "ok");
      if (res.snapshot_rows) toast(`${fmt.n(res.snapshot_rows)} snapshot satırı yazıldı`, "ok");
      await refreshShared();
      location.reload();
    } catch (err) { toast(err.message, "err"); }
  }

  async function saveDialog(version) {
    const name = el("input", { type: "text", value: version.name });
    const notes = el("textarea", { rows: 2, value: version.notes || "" });
    const kind = select(
      [["saved", "Saklanan"], ["baseline", "Baz tahmin"], ["consensus_candidate", "Konsensüs adayı"]]
        .map(([value, label]) => ({ value, label })), "saved", () => {},
    );
    const ok = await modal({
      title: "Versiyonu sakla", body: [field("Ad", name), field("Açıklama", notes), field("Tür", kind)],
      actions: [{ label: "Vazgeç" }, { label: "Sakla", primary: true, value: true }],
    });
    if (!ok) return;
    try {
      const res = await api.saveVersion(version.id, {
        name: name.value.trim(), notes: notes.value, kind: kind.value,
      });
      toast(res.summary, "ok");
      location.reload();
    } catch (err) { toast(err.message, "err"); }
  }

  async function provenanceDialog(version) {
    const trail = await api.approvalTrail(version.id).catch(() => ({ trail: [] }));
    const p = version.provenance || {};
    await modal({
      title: "İzlenebilirlik zinciri",
      subtitle: "Her sayı hangi versiyon, senaryo ve varsayımla üretildiğini taşır",
      wide: true,
      body: [
        defList([
          ["Versiyon", version.name],
          ["Tür", KIND_LABEL[version.kind] || version.kind],
          ["Durum", STATUS_LABEL[version.status] || version.status],
          ["Senaryo", p.scenario],
          ["Model", p.model],
          ["Sürücü seti", p.driver_set_id ? `#${p.driver_set_id}` : null],
          ["Seviye", Object.entries(p.level || {}).map(([k, v]) => `${k}=${v}`).join(" ")],
          ["Kova", p.grain],
          ["Veri tarihi", p.data_as_of ? fmt.day(p.data_as_of) : null],
          ["Korelasyon", p.correlation_id],
        ]),
        p.rules?.length
          ? el("div", {}, [
            sectionLabel("Kurallar"),
            table([{ key: "order", label: "Sıra" }, { key: "type", label: "Tip" }, { key: "name", label: "Ad" }],
              p.rules),
          ])
          : el("p", { class: "text-muted small mb-0", text: "Kural yok" }),
        p.notes?.length ? el("div", {}, [
          sectionLabel("Notlar"),
          el("ul", { class: "small text-muted mb-0 ps-3" }, p.notes.map((n) => el("li", { text: n }))),
        ]) : null,
        trail.trail?.length ? el("div", {}, [
          sectionLabel("Onay izi"),
          table([{ key: "label", label: "Adım" }, { key: "comment", label: "Yorum" },
            { key: "at", label: "Zaman", render: (v) => fmt.date(v) }], trail.trail),
        ]) : null,
      ],
    });
  }

  async function runCompare(ids) {
    if (ids.length < 2) { toast("En az iki versiyon seçin", "err"); return; }
    clear(compareBox).append(loading("Karşılaştırma hazırlanıyor"));
    try {
      const res = await api.compare(ids);
      clear(compareBox);
      const series = res.columns.map((c, i) => ({
        label: c.name,
        color: [SERIES_COLOR.statistical, SERIES_COLOR.sales, SERIES_COLOR.consensus, SERIES_COLOR.budget][i % 4],
        points: res.buckets.map((b) => c.per_bucket[b] ?? null),
      }));
      const rows = res.columns.map((c) => ({
        name: c.name, kind: KIND_LABEL[c.kind] || c.kind, total: c.total,
        delta: c.delta_vs_base, delta_pct: c.delta_pct,
        rules: c.rule_effects.map((e) => `${e.name}: ${fmt.n(e.delta)}`).join("; ") || "kural yok",
      }));
      compareBox.append(card("Versiyon karşılaştırma", `${res.summary} · katman ${res.layer}`, [
        lineChart({ labels: res.buckets, series, height: 200 }),
        el("div", { class: "mt-3" }, [table(
          [
            { key: "name", label: "Versiyon" }, { key: "kind", label: "Tür" },
            { key: "total", label: "Toplam", numeric: true },
            { key: "delta", label: "Baza fark", numeric: true },
            { key: "delta_pct", label: "Yüzde", numeric: true, render: (v) => fmt.pct(v) },
            { key: "rules", label: "Kural bazında etki" },
          ],
          rows,
        )]),
      ]));
      compareBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      clear(compareBox).append(empty(err.message));
    }
  }
}
