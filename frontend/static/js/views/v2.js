// v2: alternatif arayüz prototipi, iframe içinde.
//
// Prototip bağımsız bir HTML dosyası (frontend/static/v2/prototip.html) ve
// kendi tasarım sistemiyle geliyor. Aynı dokümana gömülemez: o da hash ile
// yönleniyor, `*`/`html`/`body`/`a`/`button` gibi global seçiciler tanımlıyor
// ve temasını `data-theme` ile sürüyor (bizimki `data-bs-theme`). iframe üçünü
// birden çözüyor ve dosyaya tek karakter dokunmamayı mümkün kılıyor.

import { el } from "../ui.js";

/* Yol modülün kendi konumundan çözülüyor. Kök-mutlak "/static/v2/..." yazmak
   gerçek uygulamada çalışır ama GitHub Pages'teki mock'ta
   https://<kullanici>.github.io/static/... olur ve 404 verir. import.meta.url
   iki ortamda da doğru adrese çıkar. */
const SRC = new URL("../../v2/prototip.html", import.meta.url).href;
const DEFAULT_PAGE = "kokpit";

let frame = null;
// Iki router birbirini tetiklemesin: biri hash yazarken diğeri susar.
let syncing = false;

/** Adres çubuğundaki v2 alt sayfası; v2'de değilsek null. */
function appPage() {
  const parts = location.hash.replace(/^#\//, "").split("/");
  return parts[0] === "v2" ? (parts[1] || DEFAULT_PAGE) : null;
}

/** iframe'in kendi gösterdiği sayfa. Prototip `#/giris/sales` gibi alt
    segmentler de kullanıyor, tamamı korunur. */
function framePage() {
  try {
    return frame.contentWindow.location.hash.replace(/^#\/?/, "") || DEFAULT_PAGE;
  } catch {
    return null;
  }
}

/* Prototipin kendi 220px menüsü gizlenir: gezinme bizim sol menümüzden
   yürüyor, iki menü yan yana durmasın. Dosya değişmiyor, kural iframe'e
   enjekte ediliyor — prototip her ekran geçişinde #view'ini baştan yazsa bile
   stylesheet kaldığı için kural çalışmaya devam eder.
   `main{flex:999 1 560px}` olduğu için içerik kendiliğinden tam genişliğe
   yayılır, ayrıca bir şey ayarlamak gerekmiyor. */
function hideOwnNav() {
  const doc = frame.contentDocument;
  if (!doc || doc.getElementById("v2-host-style")) return;
  const style = doc.createElement("style");
  style.id = "v2-host-style";
  style.textContent = ".nav{display:none}";
  doc.head.append(style);
}

/* Prototipin içinden gezinildiğinde (Tahminler > bir tahminin içerik girişi,
   senaryo sekmeleri) sol menü ve adres çubuğu geride kalmasın.
   replaceState: prototipin her hash değişimi zaten tarayıcı geçmişine giriyor,
   bir de bizimkini eklersek geri tuşu her adımda iki kez basılmak zorunda
   kalırdı. */
function syncFromFrame() {
  if (syncing || appPage() === null) return;
  const page = framePage();
  if (!page || page === appPage()) return;
  syncing = true;
  history.replaceState(null, "", `#/v2/${page}`);
  syncing = false;
  paintSidebar();
}

/** Sol menüdeki aktif alt maddeyi tazeler; kabuk bunu yalnızca kendi
    render döngüsünde yapıyor, iframe içi gezinmede haberi olmuyor. */
function paintSidebar() {
  const hash = location.hash;
  document.querySelectorAll("#sidebar-items .nav-submenu .nav-link").forEach((a) => {
    a.classList.toggle("active", a.getAttribute("href") === hash);
  });
}

function ensureFrame() {
  if (frame) return frame;
  frame = el("iframe", {
    id: "v2-frame",
    class: "v2-frame",
    src: `${SRC}#/${appPage() || DEFAULT_PAGE}`,
    title: "Alternatif arayüz prototipi",
  });
  frame.addEventListener("load", () => {
    hideOwnNav();
    // Prototipin hashchange'i bizim dinleyicimizden önce çalışıp DOM'u
    // kurduğu icin burada yalnızca adresi takip ediyoruz.
    frame.contentWindow.addEventListener("hashchange", syncFromFrame);
  });
  document.getElementById("main-content").append(frame);
  window.addEventListener("hashchange", syncFromApp);
  return frame;
}

/** Bizim adresimiz değişti: iframe'i göster/gizle ve doğru sayfaya sür. */
function syncFromApp() {
  if (!frame) return;
  const page = appPage();
  const view = document.getElementById("view");
  frame.hidden = page === null;
  if (view) view.hidden = page !== null;
  if (page === null || syncing) return;
  // İlk yükleme bitmeden go() yok; src zaten doğru sayfayı taşıyor.
  const win = frame.contentWindow;
  if (!win || typeof win.go !== "function") return;
  if (framePage() !== page) {
    syncing = true;
    win.go(`#/${page}`);
    syncing = false;
  }
}

export async function render() {
  ensureFrame();
  syncFromApp();
  /* Kabuk, dönen içerikte <h1> yoksa kendi başlığını ekliyor (app.js:281).
     Prototipin her ekranının kendi başlığı var, ikincisi istenmiyor; bu yüzden
     görünmez bir h1 ile o davranış bastırılıyor. Düğümün kendisi #view içinde
     kalıyor ve v2'deyken #view gizli. */
  return el("div", {}, [el("h1", { class: "visually-hidden", text: "v2" })]);
}
