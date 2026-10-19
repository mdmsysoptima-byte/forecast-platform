"""Mock üreteci: çalışan uygulamadan statik bir kopya çıkarır.

GitHub Pages yalnızca dosya sunar, Python çalıştırmaz. Bu betik çalışan
uygulamaya bağlanıp her ekranın ihtiyaç duyduğu API yanıtlarını JSON olarak
yakalar; `mock/mock-api.js` tarayıcıda `fetch`'i yakalayıp bu dosyalardan
cevap verir. Böylece ekranlar sunucu olmadan, gerçek veriyle çalışır.

Mock ön yüzün kopyası DEĞİL: `mock/index.html` `../frontend/static/...` yolunu
kullanır. Ön yüzde yapılan her değişiklik mock'a da anında yansır, yalnızca
veri değişince bu betiği yeniden koşmak gerekir.

Kullanım (uygulama ayaktayken):
    python tools/build_mock.py
    python tools/build_mock.py --base http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "mock"
DATA = MOCK / "data"

MODELS = ["statistical", "sales", "planner", "consensus"]
MEASURES = ["units", "revenue"]
GRAINS = ["month", "quarter", "year"]
AGGREGATES = ["total", "average"]

# Yazma uclari: mock'ta kalici bir sey olmaz, ama arayuz hata gostermesin diye
# makul bir yanit doner. Kullanici degisikligin kaydedilmedigini gorur.
WRITE_PATTERNS = [
    r"^/api/v1/override", r"^/api/v1/runs$", r"^/api/v1/scenarios", r"^/api/v1/approvals$",
    r"^/api/v1/time/cycle/advance$", r"^/api/v1/admin/", r"^/api/v1/saved-views$",
    r"^/api/v1/insights$", r"^/api/v1/driver-sets", r"^/api/v1/groups$",
    r"^/api/v1/versions/", r"^/api/v1/import/", r"^/api/v1/export/", r"^/api/v1/reports/export",
    r"^/api/v1/layers/copy$", r"^/api/v1/store/", r"^/api/v1/analysis/alerts/",
]


class Capture:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token: str | None = None
        self.fixtures: dict[str, object] = {}
        self.misses: list[str] = []

    # ------------------------------------------------------------- transport
    def _call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, method=method, data=data)
        req.add_header("content-type", "application/json")
        if self.token:
            req.add_header("authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=120) as res:
            return json.loads(res.read().decode("utf-8"))

    def login(self, email: str, password: str) -> dict:
        out = self._call("POST", "/api/v1/auth/login", {"email": email, "password": password})
        self.token = out["token"]
        return out

    # --------------------------------------------------------------- capture
    def grab(self, method: str, path: str, body=None) -> None:
        key = request_key(method, path, body)
        if key in self.fixtures:
            return
        try:
            self.fixtures[key] = self._call(method, path, body)
        except urllib.error.HTTPError as exc:
            self.misses.append(f"{key} -> {exc.code}")
        except Exception as exc:  # noqa: BLE001
            self.misses.append(f"{key} -> {exc}")

    def get(self, path: str) -> None:
        self.grab("GET", path)

    def post(self, path: str, body) -> None:
        self.grab("POST", path, body)


def stable(value) -> str:
    """Gövde anahtarı sıraya duyarsız olmalı; tarayıcı alan sırasını garanti etmez."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_key(method: str, path: str, body=None) -> str:
    return f"{method} {path}" if body is None else f"{method} {path} {stable(body)}"


def cockpit_body(measure="units", grain="month", aggregate="total", gap="consensus",
                 scope=None, start=None, end=None) -> dict:
    """Kokpit gövdesinin kanonik hali.

    Tarayıcı gövdeyi daha fazla alanla gönderiyor (görünüm, para birimi), bu
    yüzden tam eşleşme hiç tutmaz. mock-api.js aynı sadeleştirmeyi yapıp bu
    anahtarı üretir; iki taraf da burada tanımlı şekle uymak zorunda.

    Pencere iki etiketten ibaret. İlk açılışta ön yüz ikisini de boş gönderir
    ve sunucunun kurduğu varsayılanı devralır; o yüzden hem boş hal hem de
    varsayılan uçlar yakalanır. models sıralı, çünkü dizi sırası anahtarın
    parçası.
    """
    body = {"aggregate": aggregate, "end_label": end, "gap_model": gap, "grain": grain,
            "measure": measure, "models": sorted(MODELS), "start_label": start}
    if scope is not None:
        body["scope"] = scope
    return body


def capture_all(cap: Capture) -> None:
    # --- kabuk: her ekran acilirken cagrilanlar
    for path in [
        "/api/v1/auth/demo-users", "/api/v1/sector", "/api/v1/dimensions", "/api/v1/layers",
        "/api/v1/measures", "/api/v1/reason-codes", "/api/v1/versions", "/api/v1/time/cycle",
        "/api/v1/models", "/api/v1/drivers", "/api/v1/driver-sets", "/api/v1/groups",
        "/api/v1/saved-views", "/api/v1/insights", "/api/v1/scenarios", "/api/v1/runs",
        "/api/v1/reports", "/api/v1/reports/scheduled", "/api/v1/bi/views",
        "/api/v1/assistant/tools", "/api/v1/assistant/system-prompt",
    ]:
        cap.get(path)

    for status in ("open", "ack", "closed"):
        cap.get(f"/api/v1/analysis/alerts?status={status}")

    dims = cap.fixtures.get("GET /api/v1/dimensions", {}).get("dimensions", [])
    dim_codes = [d["code"] for d in dims]
    for code in dim_codes:
        cap.get(f"/api/v1/dimensions/{code}/tree")
        cap.get(f"/api/v1/dimensions/{code}/validate")

    for grain in GRAINS:
        cap.get(f"/api/v1/buckets?grain={grain}")

    # --- kokpit: olcu x kova x toplam/ortalama x butce modeli
    # Tum modeller yakalanir; alt kume secimi tarayicida suzulur, cunku yanit
    # zaten model basina satir ve seri tasiyor. Butce farki grafigi tek bir
    # modele bagli oldugu icin o boyut yakalanmak zorunda.
    scopes = []
    cockpit_probe = cap._call("POST", "/api/v1/cockpit/overview", {"models": MODELS})
    for s in cockpit_probe.get("options", {}).get("scopes", []):
        scopes.append((s["dimension"], None))
        for node in s["nodes"][:3]:
            scopes.append((s["dimension"], node["node_id"]))

    # Varsayilan pencerenin uclari: on yuz ilk istekte bos gonderip bunlari
    # devraliyor, sonraki her istek bu etiketleri tasiyor.
    meta = cockpit_probe.get("meta", {})
    d_start, d_end = meta.get("start_label"), meta.get("end_label")

    for measure in MEASURES:
        for grain in GRAINS:
            for aggregate in AGGREGATES:
                for gap in MODELS:
                    for start, end in ((None, None), (d_start, d_end)):
                        cap.post("/api/v1/cockpit/overview",
                                 cockpit_body(measure=measure, grain=grain, aggregate=aggregate,
                                              gap=gap, start=start, end=end))
    # Kapsam varyantlari, iki olcude birden
    for dim, node in scopes:
        scope = {dim: {"nodes": [node] if node else []}}
        for measure in MEASURES:
            for start, end in ((None, None), (d_start, d_end)):
                cap.post("/api/v1/cockpit/overview",
                         cockpit_body(measure=measure, scope=scope, start=start, end=end))

    # Pencere varyantlari.
    #
    # Kullanici uclari tek tek degistiriyor: once baslangic, sonra bitis. Bu
    # yuzden "her baslangic x varsayilan bitis" ve "varsayilan baslangic x her
    # bitis" taranir — tek bir degisiklik her zaman tam eslesir. Capa ciftleri
    # ise ikisini birden degistiren durumu kabaca karsilar; tum ciftler
    # (60x60) yakalanamazdi.
    periods = [p["label"] for p in cockpit_probe.get("options", {}).get("periods", [])]
    for label in periods:
        cap.post("/api/v1/cockpit/overview", cockpit_body(start=label, end=d_end))
        cap.post("/api/v1/cockpit/overview", cockpit_body(start=d_start, end=label))
    anchors = periods[::max(1, len(periods) // 8)][:9] if periods else []
    for i, start in enumerate(anchors):
        for end in anchors[i + 1:]:
            for grain in GRAINS:
                cap.post("/api/v1/cockpit/overview",
                         cockpit_body(start=start, end=end, grain=grain))

    # --- planlama grid'i
    layer_codes = ["actual", "last_year", "statistical", "sales", "planner", "consensus", "budget"]
    for level in ({"product": "subcategory", "geo": "region"},
                  {"product": "category", "geo": "country"},
                  {"product": "category", "geo": "region"}):
        for grain in ("month", "quarter"):
            cap.post("/api/v1/grid/query", {
                "version_id": 1, "scope": {}, "level": level, "grain": grain,
                "layers": layer_codes, "measure": "units", "currency": "TRY",
                "view": "nominal", "history_periods": 12,
            })

    # --- analiz
    level_opts = []
    for d in dims:
        hier = next((h for h in d["hierarchies"] if h.get("is_default")), d["hierarchies"][0])
        level_opts += [f"{d['code']}={lvl}" for lvl in hier["levels"]]
    # Sorgu dizesi tarayicidakiyle birebir ayni kodlanmali: on yuz
    # URLSearchParams kullaniyor ve "product=category" degerini
    # "product%3Dcategory" yapiyor. Elle birlestirince anahtar tutmuyor.
    for layer in ("statistical", "sales", "marketing", "planner", "consensus"):
        for lag in (1, 2, 3, 4, 5, 6):
            cap.get("/api/v1/analysis/accuracy?" + urlencode(
                {"layer": layer, "lag": lag, "level": level_opts[0]}))
    for level in level_opts:
        for lag in (1, 2, 3):
            cap.get("/api/v1/analysis/accuracy?" + urlencode(
                {"layer": "consensus", "lag": lag, "level": level}))
    for lag in (1, 2, 3, 4, 5, 6):
        cap.get(f"/api/v1/analysis/fva?lag={lag}")

    versions = cap.fixtures.get("GET /api/v1/versions", {}).get("versions", [])
    vids = [v["id"] for v in versions]
    for vid in vids:
        cap.get(f"/api/v1/analysis/budget-gap?version_id={vid}")
        cap.get(f"/api/v1/approvals/{vid}")
    layer_opts = [l["code"] for l in cap.fixtures.get("GET /api/v1/layers", {}).get("layers", [])]
    for a in vids:
        for b in vids:
            if a == b:
                continue
            for level in level_opts[:4]:
                for layer in (layer_opts or ["consensus"]):
                    cap.get("/api/v1/analysis/variance?" + urlencode(
                        {"version_a": a, "version_b": b, "level": level, "layer": layer}))
    if len(vids) >= 2:
        for layer in ("consensus", "statistical"):
            cap.get(f"/api/v1/versions/compare?ids={vids[0]},{vids[1]}&layer={layer}")

    # --- raporlar
    reports = cap.fixtures.get("GET /api/v1/reports", {}).get("reports", [])
    base_params = {
        "version_id": vids[0] if vids else 1,
        "version_ids": vids[:2],
        "level": {"product": "category"}, "lag": 1, "status": "open",
    }
    for r in reports:
        cap.post("/api/v1/reports/run", {"code": r["code"], "params": base_params})
    cap.get("/api/v1/forecast?version=1&layer=consensus")

    # --- yonetim
    for path in ["/api/v1/admin/connections", "/api/v1/admin/users", "/api/v1/admin/consensus-rule",
                 "/api/v1/admin/model-packages"]:
        cap.get(path)
    for limit in (100, 200):
        cap.get(f"/api/v1/admin/audit?limit={limit}")
        for entity in ("override", "version", "run", "cycle", "scenario", "insight"):
            cap.get(f"/api/v1/admin/audit?limit={limit}&entity={entity}")
    cycle = cap.fixtures.get("GET /api/v1/time/cycle", {}).get("cycle", {})
    year = int(str(cycle.get("current_period", "2026-10"))[:4])
    for y in (year - 1, year, year + 1):
        cap.get(f"/api/v1/time/calendar-events?year={y}")

    # --- senaryo surucu degerleri
    sets = cap.fixtures.get("GET /api/v1/driver-sets", {}).get("driver_sets", [])
    for s in sets:
        cap.get(f"/api/v1/driver-sets/{s['id']}/values")


def write_fixtures(cap: Capture) -> dict:
    """Dosya adları içerik özeti.

    Sıra numarası verilirse (f0000.json) her yeniden üretimde aynı ad farklı
    içeriğe denk gelir ve tarayıcıdaki eski kopya yanlış ekrana servis edilir —
    varyans ekranına bütçe yanıtı gitmesi bu yüzdendi. Özet kullanınca değişen
    yanıt yeni bir ad alır, değişmeyen dosya önbellekte kalmaya devam eder.
    """
    DATA.mkdir(parents=True, exist_ok=True)
    for old in DATA.glob("*.json"):
        old.unlink()

    index: dict[str, str] = {}
    for key, value in sorted(cap.fixtures.items()):
        blob = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        name = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16] + ".json"
        path = DATA / name
        if not path.exists():
            path.write_text(blob, encoding="utf-8")
        index[key] = name
    (DATA / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    return index


def write_index_html(session: dict) -> None:
    """mock/index.html'i frontend/index.html'den üretir.

    Elle kopyalanmaz: kabukta bir değişiklik olursa bu betiği yeniden koşmak
    yeterli. Yollar göreli hale getirilir ki GitHub Pages alt dizinde de çalışsın.
    """
    src = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    html = src.replace('"/static/', '"../frontend/static/')
    boot = (
        '  <script>window.__MOCK_SESSION__ = '
        + json.dumps({"token": "mock", "user": session["user"]}, ensure_ascii=False)
        + ";</script>\n  <script src=\"mock-api.js\"></script>\n"
    )
    html = html.replace('  <script type="module" src="../frontend/static/js/app.js">', boot
                        + '  <script type="module" src="../frontend/static/js/app.js">')
    html = html.replace("<title>Forecast Platformu</title>",
                        "<title>Forecast Platformu — mock</title>")
    MOCK.mkdir(parents=True, exist_ok=True)
    (MOCK / "index.html").write_text(html, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--email", default="admin@demo.local")
    ap.add_argument("--password", default="demo")
    args = ap.parse_args()

    cap = Capture(args.base)
    try:
        session = cap.login(args.email, args.password)
    except Exception as exc:  # noqa: BLE001
        print(f"Uygulamaya baglanilamadi ({args.base}): {exc}")
        print("Once uygulamayi calistirin: .\\run.ps1")
        return 1

    print(f"baglandi: {session['user']['name']}")
    capture_all(cap)
    index = write_fixtures(cap)
    write_index_html(session)

    total = sum(f.stat().st_size for f in DATA.glob("*.json"))
    print(f"yakalanan yanit : {len(index)}")
    print(f"toplam boyut    : {total / 1024 / 1024:.1f} MB")
    if cap.misses:
        print(f"atlanan ({len(cap.misses)}):")
        for m in cap.misses[:10]:
            print("   ", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
