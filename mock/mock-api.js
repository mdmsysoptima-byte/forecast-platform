/* Mock katmanı: sunucu yok, veriler data/ altındaki JSON dosyalarından gelir.
 *
 * `fetch` burada yakalanır, böylece uygulamanın kendi api.js'i hiç değişmez —
 * mock ile gerçek sürüm aynı ön yüz kodunu çalıştırır. Yeni bir ekran ya da
 * yeni bir çağrı eklediğinizde burada bir şey yapmanız gerekmez; yalnızca
 * `python tools/build_mock.py` ile veriyi yeniden yakalarsınız.
 *
 * Bu dosya elle yazılır, üretilmez.
 */
(() => {
  const DATA = "data/";
  const SESSION = window.__MOCK_SESSION__ || { token: "mock", user: null };

  // Giris ekrani atlanir: oturum onceden kurulmus sayilir.
  if (SESSION.user) {
    localStorage.setItem("forecast.token", SESSION.token);
    localStorage.setItem("forecast.user", JSON.stringify(SESSION.user));
  }

  const realFetch = window.fetch.bind(window);
  let index = null;
  const cache = new Map();

  /* Anahtar sıraya duyarsız olmalı: gövde alanlarının sırası tarayıcıda
     garanti değil, yakalama tarafında da sıralanmış yazılıyor. */
  const stable = (value) => {
    if (value === null || typeof value !== "object") return JSON.stringify(value);
    if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
    const keys = Object.keys(value).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${stable(value[k])}`).join(",")}}`;
  };

  const WRITE = [
    /^\/api\/v1\/override/, /^\/api\/v1\/runs$/, /^\/api\/v1\/scenarios/,
    /^\/api\/v1\/approvals$/, /^\/api\/v1\/time\/cycle\/advance$/, /^\/api\/v1\/admin\//,
    /^\/api\/v1\/saved-views$/, /^\/api\/v1\/insights$/, /^\/api\/v1\/driver-sets\//,
    /^\/api\/v1\/groups$/, /^\/api\/v1\/versions\//, /^\/api\/v1\/import\//,
    /^\/api\/v1\/export\//, /^\/api\/v1\/reports\/export/, /^\/api\/v1\/layers\/copy$/,
    /^\/api\/v1\/store\//, /^\/api\/v1\/analysis\/alerts\/[^/]+\/status/,
  ];

  const json = (body, status = 200) => new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json" },
  });

  /* index.json her seferinde doğrulanır: içerik özetli dosya adlarına o
     işaret ediyor, bayat bir kopya tüm ekranları yanlış veriye bağlar.
     Fixture'lar ise adlarını içeriğinden aldığı için sonsuza dek önbelleklenir. */
  async function loadIndex() {
    if (index) return index;
    const res = await realFetch(`${DATA}index.json`, { cache: "no-cache" });
    index = await res.json();
    return index;
  }

  async function fixture(file) {
    if (cache.has(file)) return cache.get(file);
    const res = await realFetch(DATA + file, { cache: "force-cache" });
    const value = await res.json();
    cache.set(file, value);
    return value;
  }

  /* Kokpit yanıtı her model için satır, seri ve bütçe farkı taşıyor; model
     seçimini burada süzmek yeterli, her alt küme için ayrı dosya tutmaya gerek
     yok. Yakalama dört modelin hepsiyle yapılır, süzme hep daraltır. */
  function filterModels(body, wanted) {
    if (!body || !Array.isArray(wanted) || !wanted.length) return body;
    const keep = new Set(wanted);
    const out = structuredClone(body);
    if (out.timeline?.models) out.timeline.models = out.timeline.models.filter((m) => keep.has(m.code));
    if (out.table?.rows) {
      out.table.rows = out.table.rows.filter((r) => r.code === "actual" || keep.has(r.code));
    }
    if (out.accuracy?.rows) out.accuracy.rows = out.accuracy.rows.filter((r) => keep.has(r.code));
    // Butce farki seciciye yalnizca secili modelleri sunmali; gosterilen model
    // listeden dustuyse ilkine kayar.
    for (const chart of out.charts || []) {
      if (!chart.models) continue;
      chart.models = chart.models.filter((m) => keep.has(m.code));
      if (!chart.models.some((m) => m.code === chart.model)) {
        chart.model = chart.models[0] ? chart.models[0].code : chart.model;
      }
    }
    if (out.meta) {
      out.meta.models = wanted.filter((c) => keep.has(c));
      if (!keep.has(out.meta.gap_model)) {
        out.meta.gap_model = out.meta.models[out.meta.models.length - 1];
      }
    }
    return out;
  }

  /* Tam eşleşme yoksa aynı uca ait en yakın yanıt. Mock sabit bir veri
     kümesi; her filtre kombinasyonu yakalanmış olamaz. Hata vermek yerine
     en yakınını gösterip konsola not düşüyoruz.

     GET'lerde sorgu dizesi anahtarın parçası: tutmazsa sorguyu atıp aynı yola
     ait ilk yanıta düşüyoruz, yoksa ekran bomboş kalırdı. */
  function nearest(idx, method, path) {
    if (idx[`${method} ${path}`]) return `${method} ${path}`;
    const withBody = Object.keys(idx).find((k) => k.startsWith(`${method} ${path} `));
    if (withBody) return withBody;
    const bare = path.split("?")[0];
    return Object.keys(idx).find((k) => k === `${method} ${bare}`
      || k.startsWith(`${method} ${bare}?`)) || null;
  }

  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const path = url.startsWith("http") ? new URL(url).pathname + new URL(url).search : url;

    // API disi istekler (statik dosyalar, fontlar) dogrudan gecer.
    if (!path.startsWith("/api/")) return realFetch(input, init);

    const method = (init.method || (typeof input === "object" && input.method) || "GET").toUpperCase();
    const bodyText = typeof init.body === "string" ? init.body : null;
    let body = null;
    try { body = bodyText ? JSON.parse(bodyText) : null; } catch { body = null; }

    if (path.startsWith("/api/v1/auth/login")) {
      return json({ token: SESSION.token, user: SESSION.user, summary: "Mock oturum" });
    }
    if (path.startsWith("/api/v1/health")) {
      return json({ status: "ok", app: "Forecast Platformu (mock)", summary: "Mock sürüm" });
    }

    const idx = await loadIndex();
    const exact = body === null ? `${method} ${path}` : `${method} ${path} ${stable(body)}`;

    let key = idx[exact] ? exact : null;
    if (!key) {
      // Kokpitte model secimi ve bos alanlar disindaki farkliliklar icin
      // gövdeyi sadelestirip tekrar dene.
      /* Tarayıcı gövdeyi pencere modu, etiketler, görünüm ve para birimiyle
         gönderiyor; yakalama ise sadeleştirilmiş kanonik şekli kullanıyor
         (bkz. tools/build_mock.py: cockpit_body). Aynı sadeleştirmeyi burada
         yapıp en özelden en genele doğru deniyoruz. */
      if (path === "/api/v1/cockpit/overview" && body) {
        const ALL_MODELS = ["consensus", "planner", "sales", "statistical"];
        const base = {
          aggregate: body.aggregate ?? "total",
          gap_model: body.gap_model ?? "consensus",
          grain: body.grain ?? "month",
          measure: body.measure ?? "units",
          models: ALL_MODELS,
        };
        const win = { start_label: body.start_label ?? null, end_label: body.end_label ?? null };
        const EMPTY_WIN = { start_label: null, end_label: null };
        const hasScope = body.scope && Object.keys(body.scope).length;

        /* En ozelden en genele. Pencere en sonda gevsetilir: on yuz ilk
           yanittan sonra uclari her istekte tasiyor, once gelseydi olcu ve
           zaman birimi secimleri hic etki etmezdi. */
        const tries = [];
        if (hasScope) tries.push({ ...base, ...win, scope: body.scope });
        if (hasScope) tries.push({ ...base, ...EMPTY_WIN, scope: body.scope });
        tries.push({ ...base, ...win });
        tries.push({ ...base, ...EMPTY_WIN });
        // Pencere varyantlari yalnizca varsayilan olculerle yakalandi.
        tries.push({ ...base, ...win, measure: "units", grain: body.grain ?? "month",
          aggregate: "total", gap_model: "consensus" });
        tries.push({ ...base, ...EMPTY_WIN, measure: "units", grain: "month",
          aggregate: "total", gap_model: "consensus" });
        for (const candidate of tries) {
          const k = `${method} ${path} ${stable(candidate)}`;
          if (idx[k]) { key = k; break; }
        }
      }
    }
    if (!key) key = nearest(idx, method, path);

    if (key) {
      let value = await fixture(idx[key]);
      if (path === "/api/v1/cockpit/overview" && body?.models) {
        value = filterModels(value, body.models);
      }
      if (key !== exact) {
        console.info("[mock] tam eslesme yok, en yakini kullanildi:", exact);
      }
      return json(value);
    }

    if (WRITE.some((re) => re.test(path))) {
      return json({
        ok: true, applied: 0, created: 0,
        summary: "Mock sürüm: değişiklik kaydedilmedi",
      });
    }

    console.warn("[mock] yakalanmamis uc:", exact);
    return json({ detail: "Bu çağrı mock sürümde yok" }, 404);
  };

  // Mock oldugu ekranda da gorunsun.
  document.addEventListener("DOMContentLoaded", () => {
    const badge = document.createElement("span");
    badge.className = "badge bg-warning-subtle text-warning-emphasis";
    badge.textContent = "mock";
    badge.title = "Statik sürüm: veriler sabit, değişiklikler kaydedilmez";
    document.getElementById("cycle-chip")?.after(badge);
  });
})();
