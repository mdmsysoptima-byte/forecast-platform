# Forecast Platformu

System Optima tahmin platformu. Hazır modellerden skor üretir, saha ve ekip girdileriyle
konsensüs tahmin oluşturur, senaryo çalıştırır, istenen kırılımda analiz eder ve sonuçları
raporlar.

Arayüz Türkçe, kod ve veritabanı nesneleri İngilizce.

---

## 1. Kurulum ve çalıştırma

Gereken tek şey Python 3.11 veya üzeri. Node, Docker veya ayrı bir veritabanı sunucusu
gerekmez.

```powershell
cd C:\Users\CanAkyildiz\Desktop\forecast-platform

.\run.ps1 -Install -Demo      # ilk kurulum: bağımlılıklar + demo verisi + sunucu
.\run.ps1                     # sonraki açılışlar
.\run.ps1 -Test               # test paketi
.\run.ps1 -Demo               # demo verisini sıfırdan yeniden yükle
.\run.ps1 -Sector pharma -Demo  # başka sektör konfigürasyonu ile kur
```

Açılınca:

| Adres | İçerik |
| --- | --- |
| http://127.0.0.1:8000 | Uygulama |
| http://127.0.0.1:8000/api/docs | OpenAPI dokümanı (Swagger) |
| http://127.0.0.1:8000/api/openapi.json | Makine okunur şema |

Demo kullanıcıları, şifre hepsinde `demo`:

| E-posta | Rol | Kapsam |
| --- | --- | --- |
| admin@demo.local | admin | tam |
| planner@demo.local | planner | tam |
| sales@demo.local | sales_contributor | yalnızca Marmara bölgesi |
| marketing@demo.local | marketing_contributor | tam |
| finance@demo.local | finance | tam |
| viewer@demo.local | viewer | tam, salt okunur |

Satır bazlı yetkiyi görmek için `sales@demo.local` ile girin: grid yalnızca Marmara
satırlarını gösterir ve yalnızca satış katmanı düzenlenebilir.

---

## 2. Beş dakikada tur

1. **Kokpit**: tek filtre şeridi — pencere (dönem sayısı ya da tarih aralığı),
   başlangıç, bitiş, model, kapsam, alt kapsam, ölçü, kova. Ortadaki büyük grafik
   **agrege konsensüs tahminidir**: solda gerçekleşen, sağda tahmin, aralarında
   "tahmin başlangıcı" çizgisi, modelin belirsizlik bandı ve referans olarak bütçe ile
   geçen yıl. Altında **model karşılaştırma tablosu**: her satır bir tahmin üreticisi
   (gerçekleşen, istatistiksel, satış, pazarlama, planlamacı, konsensüs), sütunlar
   adet ve ciro için geçmiş/gelecek ile MAPE. En altta bütçe farkı. Grafiğin üzerine
   gelince o dönemde değeri olan her seri tek ipucunda listelenir.
2. **Planlama > Tahmin grid'i**: soldan ağaçtan kapsam seçin, üst şeritten seviye ve kovayı
   değiştirin. Bir satırı açınca katman satırları gelir. Düzenlenebilir bir hücreye çift
   tıklayın, değeri yazın, gerekçe penceresi açılır. Üst seviyeden girilen değer alt
   serilere dağıtılır, toplam korunur. `Ctrl+Z` son değişikliği geri alır.
3. **Senaryo**: sürücü sekmesinde geçmiş gri ve salt okunur, gelecek düzenlenebilir.
   Kural sekmesinde sıralı kural listesi. "Çalıştır" taslak versiyon üretir.
4. **Versiyon**: iki versiyonu seçip karşılaştırın. Fark tablosu kural bazında ayrışır.
   Onay akışı: İncelemeye al, Onayla, Kilitle. Kilitte snapshot yazılır.
5. **Analiz**: lag bazlı doğruluk eğrisi, FVA merdiveni, bias ısı haritası, varyans ve
   şelale, bütçe gap.
6. **Rapor**: 12 standart rapor, Excel, CSV, Parquet, REST API durumu.
7. **Yönetim**: bağlantılar, model kayıtları, takvim, boyut doğrulaması, sürücü kataloğu,
   konsensüs kuralı, kullanıcı ve satır yetkisi, audit log.

---

## 3. Mimari özeti

```
forecast-platform/
  backend/app/
    core/              config, auth (JWT), db, yapısal JSON log
    domain/            SQLAlchemy veri modeli, series_key üretimi
    adapters/          tek depo arayüzü; local (SQLite), bigquery ve mssql arayüzleri
    forecast_models/   model sözleşmesi, registry, çalıştırma motoru
    services/          takvim, hiyerarşi, uzlaştırma, katman, override, çevrim,
                       senaryo kuralları, doğruluk, uyarı, grid, kokpit, türetilmiş
                       ölçü, rapor, dışa aktarım
    api/routers/       auth, meta, planning, scenarios, analysis, reports, admin, assistant
    assistant/         araç kataloğu ve sistem promptu (bölüm 16, adım 8)
    demo/              sentetik veri üretici ve yükleyici
  backend/tests/       77 test
  frontend/            derleme adımı olmayan tek sayfa uygulaması
    static/vendor/     Metis teması (CSS, fontlar), Bootstrap ve ApexCharts
    static/js/         api, ui bileşenleri, charts (ApexCharts sarmalayıcıları), views/
  models/              model paketleri (seasonal_naive, driver_regression)
  configs/sectors/     demo.yaml, pharma.yaml
  data/                app.db ve store.db (çalışma zamanında oluşur)
```

Kurallar:

- Domain mantığı API ve arayüzden bağımsız, saf Python servisleri olarak yazıldı ve
  arayüz olmadan test edilebilir.
- Sektör içeriği yalnızca YAML'da. Kodda hiçbir sektör adı geçmez.
- Büyük tablolar (gerçekleşen, ham skor, snapshot, kural etkisi) analitik depoda durur,
  uygulama veritabanına kopyalanmaz.
- API her isteği hem rol hem satır kapsamına göre süzer. Ön yüz yetki kontrolü yalnızca
  görsel kolaylıktır.
- Her yazma işlemi audit log'a düşer ve korelasyon kimliği taşır.
- Tüm zamanlar UTC saklanır, Europe/Istanbul gösterilir. Sayı formatı tr-TR.

### Veri modeli çekirdeği

Her sayı bir seri anahtarı, bir zaman kovası, bir katman ve bir versiyon ile adreslenir.

- `series_key = sha1(sıralı "boyut:üye" çiftleri + ölçü kodu)`
- `layer_value` yalnızca temel seviyeyi (yaprak x yaprak) tutar. Üst seviyeler okuma
  anında toplanır, üst seviyeden girilen değer alt serilere dağıtılır. Bu sayede tüm
  seviyeler her zaman tutarlıdır.
- `layer_value` satırı silinmez; yeni değer yeni versiyonda yazılır ya da override kaydı
  ile güncellenir.
- `snapshot_value` yalnızca kilit anında yazılır ve asla güncellenmez.

### Türetilmiş ölçüler

Spesifikasyon bölüm 10'daki "ölçü türetme" okuma anında çalışır. `demo.yaml` içinde
`revenue: formula: units * price` yazar; ciro için ayrı seri üretilmez ve ayrı model
koşulmaz. Her katman ve gerçekleşen, serinin ürün üyesindeki `base_price` ile çarpılır
ve dönem fiyatı TÜFE endeksiyle taşınır (`base_price * tufe[dönem] / tufe[cari dönem]`).
Böylece hacim ile değer hiçbir zaman birbirinden ayrışmaz. Çarpan, kova yuvarlamasından
önce aylık seviyede uygulanır, yani bir çeyreğin cirosu aylarının toplamıdır.
`price_index` ve `fx_rate` tabloları tahmin ufkunu da kapsar, bu yüzden "reel TL" ve
"döviz" görünümleri gelecek dönemlerde de çalışır.

### Katmanlar ve konsensüs

`actual, last_year, statistical, sales, marketing, planner, consensus, budget,
prior_version`. Hiçbir katman diğerinin üzerine yazmaz. Konsensüs kuralı konfigüre
edilebilir (varsayılan: planlamacı, yoksa satış, yoksa istatistiksel) ve her hücrede
hangi katmanın seçildiği kaydedilir. Yönetim ekranından değiştirilebilir.

### Zaman omurgası

Kova, takvim, ufuk, current period, fence, freeze, çevrim, snapshot, lag ve kayan
tahmin birinci sınıf nesnelerdir. Türkiye takvimi `holidays` kütüphanesinden yüklenir;
kayan bayramlar `is_shifting` ile işaretlenir ve "bayram hizalı geçen yıl" karşılaştırması
bu bilgiyi kullanır. İş günü sayısı her kovada saklanır ve zaman dağıtımında kullanılır.

---

## 4. Model ekleme

Platform modelin içini bilmez, yalnızca sözleşmeyi çağırır.

1. `models/<kod>/` klasörü açın.
2. `manifest.yaml` yazın:

```yaml
code: benim_modelim
name: Benim modelim
version: "1.0.0"
entry: model.py
class: BenimModelim
supported_grains: [month]
supported_levels:
  product: [sku]
  geo: [city]
required_drivers: [cpi]
optional_drivers: []
output_has_interval: true
```

3. `model.py` içinde sözleşmeye uyan bir sınıf yazın:

```python
from app.forecast_models.contract import PredictRequest, PredictResponse

class BenimModelim:
    code = "benim_modelim"
    version = "1.0.0"
    supported_grains = ["month"]
    supported_levels = {"product": ["sku"], "geo": ["city"]}
    required_drivers = ["cpi"]
    optional_drivers = []
    output_has_interval = True

    def predict(self, req: PredictRequest) -> PredictResponse:
        # req.history: series_key, bucket_id, value
        # req.drivers: driver_code, bucket_id, member_id, value (geçmiş ve gelecek)
        # req.future_buckets: hedef kova listesi
        ...
```

4. Yönetim > Modeller ekranında "Manifest tara ve yenile" deyin. Kod değişikliği gerekmez.

Platform, `required_drivers` için senaryodaki sürücü setinde değer yoksa çalıştırmayı
reddeder ve eksik sürücüyü dönem listesiyle birlikte söyler. Model çıktısı seçilen
seviyede değilse uzlaştırma servisi devreye girer ve bu durum çalıştırma notlarında
görünür.

---

## 5. Sektör ekleme

`configs/sectors/<sektor>.yaml` yazmak ve model paketini kaydetmek yeterlidir. Şema
`demo.yaml` ve `pharma.yaml` dosyalarında görülebilir: takvim, boyutlar ve hiyerarşiler,
ölçüler, birim dönüşümleri, sürücüler, gerekçe kodları, olay tipleri, konsensüs kuralı,
çevrim parametreleri, uyarı eşikleri, ek raporlar ve modeller.

```powershell
.\run.ps1 -Sector pharma -Demo
```

İlçe, bölge, mağaza, SKU, terapötik grup gibi ifadeler veri modelinde kolon veya tablo
adı değildir; hepsi `dimension`, `member` ve `hierarchy_node` tablolarındaki satırlardır.
Yeni bir sektör veya değişken eklemek şema değişikliği gerektirmez.

---

## 6. Test ve kabul kriterleri

```powershell
.\run.ps1 -Test
```

77 test; domain ve servis katmanı test altında. Spesifikasyondaki kabul kriterlerinin
karşılığı:

| Adım | Kriter | Test |
| --- | --- | --- |
| 1 | Tek komut boş ortamdan demo verisini yükler, hiyerarşi doğrulama raporu üretir | `conftest.py`, `test_api_and_export.py` |
| 2 | Üstten girilen değer alta dağıtılıp toplandığında toplam korunur; aylık seri haftaya dağıtılıp aya toplandığında eşittir; 53 haftalık yıl doğru üretilir | `test_reconciliation.py`, `test_time.py` |
| 3 | 200 seri 60 saniyenin altında çalışır; eksik sürücüde anlamlı hata; taslak süresi dolunca silinir | `test_accuracy_and_runs.py` |
| 4 | İki senaryo karşılaştırıldığında fark kural bazında ayrışır; saklanan versiyon senaryo tanımını değişmez taşır | `test_api_and_export.py`, `test_scenario_rules.py` |
| 5 | Fence içinde override reddedilir; freeze içinde yeniden çalıştırma override'ı korur; onay adımları atlanamaz; kilitte snapshot ve lag doğru yazılır; satış katkıcısı yalnızca kendi kapsamını görür | `test_governance.py` |
| 6 | 1.000 seri x 24 dönem grid 2 saniyenin altında yüklenir | `test_api_and_export.py::test_grid_performance_target` |
| 7 | Excel'den geri yükleme fark listesi üretir ve onayla uygulanır; pharma.yaml ile yeni kurulum kod değişikliği olmadan ayağa kalkar | `test_api_and_export.py` |
| Genel | Tüm uç noktalar OpenAPI'de belgeli ve yetki kontrollü; audit log her yazma işlemini kaydeder | `test_api_and_export.py`, `test_governance.py` |
| Kokpit | Geçmişe tahmin, geleceğe gerçekleşen sızmaz; iki pencere modu aynı aralığı aynı sayıyla anlatır; çeyrek ve yıl toplamı aylık toplama eşittir; model bandı istatistiksel çizgiyi sarar; ciro = adet x fiyat; satış katkıcısı yalnızca kendi kapsamını görür | `test_cockpit.py` |
| Kokpit tablosu | Gerçekleşen satırının geleceği ve MAPE'si boştur; tablodaki MAPE ile Analiz ekranındaki aynı sayıdır; kapsam daralınca MAPE de daralır; kısmi katmanlar işaretlenir | `test_cockpit.py` |

Demo ortamında ölçülen değerler: 200 seri çalıştırma 1,6 ile 2,5 saniye; temel seviye
grid sorgusu (200 satır x 30 dönem) 170 ms civarı; demo yüklemesi uçtan uca 8 saniye.

---

## 7. Varsayımlar

Spesifikasyonun "belirsizlikte en basit ve geri alınabilir seçeneği uygula" kuralı
uyarınca alınan kararlar ve bu sürümde bilinçli olarak farklı yapılan noktalar:

**Altyapı**

1. **PostgreSQL yerine SQLite.** Uygulama veritabanı SQLAlchemy 2 üzerinden konuşur;
   `APP_DB_URL` ortam değişkeni PostgreSQL'e çevrildiğinde model katmanı değişmez.
   `ltree` yerine `hierarchy_node.path` noktalı metin olarak tutulur ve alt ağaç sorgusu
   önek eşlemesiyle yapılır.
2. **Celery ve Redis yerine FastAPI arka plan görevi.** Çalıştırma motoru aynı arayüzü
   kullanır, durum `run` tablosunda izlenir, aynı senaryo ve kapsam için ikinci çalıştırma
   kilit anahtarıyla (scenario_id + scope hash) kuyruğa alınır. Celery'ye geçiş
   `runner.execute_run` çağrısını bir göreve sarmaktan ibarettir.
3. **Docker yerine tek PowerShell betiği.** Hedef tek makinede hızlı kurulumdu.
4. **Analitik depo yerel SQLite dosyası.** `app/adapters/base.py` tek arayüzü tanımlar;
   `local_store.py` onu uygular. BigQuery ve MS SQL adaptörleri aynı arayüzü bildirir ama
   bu sürümde bağlanmamıştır (`warehouse_stubs.py`, TODO). Aynı test setini geçmeden
   açılmamalıdır.
5. **React ve AG Grid yerine derleme adımı olmayan tek sayfa uygulaması.** Grid soyutlaması
   tek dosyada (`frontend/static/js/views/planning.js`) toplandı; hücre durumu, dağıtım,
   gerekçe akışı ve bilgi kartı aynı sözleşmeyi kullanır, bu yüzden AG Grid'e geçiş tek
   bileşen değişimidir.
6. **Metis teması derlenmiş olarak vendor'landı.** Tema paketi yalnızca SCSS içeriyor ve
   Vite ile derleniyor. Derleme bir kez geçici bir klasörde yapıldı, çıktı
   `frontend/static/vendor/` altına kopyalandı, font yolları göreli hale getirildi.
   Uygulamanın kendisinde hâlâ derleme adımı yok. Temanın Alpine.js bağımlılığı alınmadı;
   yalnızca sınıf yapısı kullanılıyor, etkileşim bizim JS'imizde. Tema sürümü 3.6.0,
   lisans MIT (Colorlib, ThemeWagon); kopya `vendor/METIS-LICENSE.md` dosyasında.
7. **Elle yazılmış SVG grafikler yerine ApexCharts.** Temanın kendi grafik kütüphanesi.
   Current period çizgisi `annotations.xaxis`, bütçe ve geçen yıl `stroke.dashArray`,
   model belirsizlik bandı `rangeArea` ile çizilir. Tema canlı tema değişiminde
   grafikleri yeniden renklendirmediği için tema düğmesi ekranı yeniden çizer.
   Bileşik grafikte `rangeArea` varken ApexCharts iki değerli biçimlendiricisini
   bütün serilere uyguluyor ve çizgi değerleri `undefined` dönüyor; bu yüzden tahmin
   grafiğinin ipucu `tooltip.custom` ile elle üretilir ve yalnızca o dönemde değeri
   olan seriler listelenir.
8. **Kokpitte operasyonel liste yok.** Spesifikasyonun bölüm 11 ve 12'de kokpite
   koyduğu maddeler kendi ekranlarında yaşıyor: uyarılar Planlama > İstisnalar,
   çalıştırmalar Senaryo > Çalıştırma geçmişi, onay izi Versiyon, lag ayrıntısı ve
   FVA merdiveni Analiz. Kokpit yalnızca tahmin ekranıdır.
9. **Kokpit tablosunun satırı katmandır, model değil.** `snapshot_value` anahtarı
   `(çevrim, katman, seri, dönem)`; içinde model ya da versiyon yok. Aynı çevrimde
   iki model çalışırsa ikisi de `statistical` katmanına yazar ve birbirinin üzerine
   biner. Model bazında MAPE istenirse bu tabloya model sütunu eklemek ve birincil
   anahtarı değiştirmek gerekir; geçmiş veri de ancak o günden sonra birikmeye başlar.
   Katman bazında ölçüm bugün çalışıyor ve 12 çevrimlik geçmişi var.
10. **Geçmiş sütunu snapshot'tan gelir.** `layer_value` tahmin katmanları için geçmiş
   dönem tutmuyor; zaten doğrusu da snapshot'tır: gerçekleşen gelmeden önce donmuş
   tahmin. Snapshot penceresi 12 dönem olduğundan daha geriye gidildiğinde model
   satırlarının geçmişi boşalır, gerçekleşen satırı dolu kalır; boş hücre sıfır değil
   tire gösterir.
11. **polars yerine pandas.** Model sözleşmesindeki kolon adları spesifikasyondakiyle
   birebir aynıdır.
12. **Alembic migrasyonu yerine `create_all`.** Tek kurulumluk demo için yeterli; üretim
   öncesi ilk migrasyon bu şemadan üretilmelidir.

**Ürün**

13. **Doldurma tutamacı yerine aralık seçimi.** Hücreye tıklayıp `Shift` ile aralık seçilir,
   "Seçili aralığı düzenle" ile yüzde veya mutlak değer uygulanır, "Kopyala" seçimi panoya
   sekme ayraçlı yazar. Sürükleyerek doldurma uygulanmadı.
14. **Zamanlanmış raporlar ve zamanlanmış çalıştırma** kayıt olarak tutulur; cron
   tetikleyicisi ve e-posta gönderimi bağlanmadı (TODO).
15. **Parquet çıktısı** `pyarrow` kuruluysa çalışır; kurulu değilse anlamlı hata döner.
16. **Asistan katmanı (bölüm 16, adım 8)** arayüz olarak hazır: araç kataloğu rol ve
    kapsama göre süzülür, sistem promptu sektör konfigürasyonundan üretilir, onay kuralları
    tanımlıdır. Claude API tool use döngüsü `ANTHROPIC_API_KEY` gerektirdiği için
    bağlanmadı; `/api/v1/assistant/chat` ne yapacağını söyler, sayı üretmez.
17. **Entra ID SSO** kapsam dışı; kimlik JWT ile çalışır.
18. **`user` tablosu `app_user` olarak adlandırıldı**, çünkü `user` PostgreSQL'de ayrılmış
    sözcüktür.
19. **Demo doğruluk ve FVA verisi** 12 kapanmış çevrim ve bunların snapshot'ları üretilerek
    oluşturulur. Böylece lag bazlı doğruluk eğrisi ve FVA merdiveni gerçek satırlardan
    hesaplanır, sahte sayı gösterilmez.
20. **FVA'nın kullanıcı ve gerekçe kırılımı** yalnızca gerçekleşenin geldiği dönemler için
    doludur. Demo verisinde override'lar gelecek dönemlerde olduğu için bu tablo boştur;
    bu doğru davranıştır, geleceğin override'ı henüz ölçülemez.

**Ortam**

21. Bu makinede `PYTHONPATH` kullanıcı seviyesinde Python 3.9 `site-packages` dizinine
    işaret ediyor ve sanal ortama sızıyor. `run.ps1` bu değişkeni temizler ve
    `PYTHONNOUSERSITE=1` ayarlar. Betik dışında elle çalıştırırken aynısını yapın.
22. Temayı yeniden derlemek gerekirse Node 20, 22 veya 24 ve ağ erişimi gerekir.
    Uygulamayı çalıştırmak için gerekmez; derlenmiş varlıklar depoda duruyor.

---

## 8. Yapılandırma

Gizli bilgiler ortam değişkeninde tutulur, repoda kimlik bilgisi yoktur.

| Değişken | Varsayılan | Açıklama |
| --- | --- | --- |
| `APP_DB_URL` | `sqlite:///data/app.db` | Uygulama veritabanı |
| `STORE_BACKEND` | `local` | `local`, `bigquery`, `mssql` |
| `STORE_PATH` | `data/store.db` | Yerel depo dosyası |
| `SECTOR` | `demo` | Yüklenecek sektör konfigürasyonu |
| `JWT_SECRET` | geliştirme değeri | Üretimde mutlaka değiştirin |
| `JWT_TTL_MINUTES` | `720` | Oturum süresi |
| `DRAFT_TTL_DAYS` | `7` | Taslak versiyonların ömrü |
| `DEMO_PASSWORD` | `demo` | Demo kullanıcı şifresi |

---

## 9. Tasarım notu

Görsel dil Metis temasından gelir: gölgesiz kartlar, yapıyı taşıyan 1 piksel kenarlıklar,
nötr Zinc rampası ve tek aksan rengi. Aksan bir etkileşim sinyalidir, süs değil; birincil
eylemi, aktif menü öğesini ve odak halkasını işaretler.

Renk dekor değil, köken kodlar: **teal gerçekleşen, mavi model çıktısı, amber insan
müdahalesi, nötr bütçe**. Katman satırlarının sol kenarındaki şerit, grid hücre renkleri
ve grafik serileri bu sözlüğü izler. Tahmin grafiğinde gerçekleşen ile tahmin aynı zaman
çizgisinde yaşar; aralarındaki dikey kesikli çizgi olanla iddia edileni ayırır.

Arayüz açık ve koyu temayı destekler. Tema `<html data-bs-theme>` ile değişir, seçim
`localStorage`'da tutulur ve ilk açılışta işletim sistemi tercihi okunur. Grafik renkleri
çizim anında hesaplandığı için tema düğmesi ekranı yeniden çizer.

Sayı biçimi tr-TR, tarih gg.aa.yyyy, saat dilimi Europe/Istanbul. `<html lang="tr">`
ayarı zorunludur: tema tablo başlıklarını ve KPI etiketlerini büyük harfe çevirir ve
Türkçe kurala göre "i" harfi "İ" olmalıdır.

### Ekranları beslerken bilinmesi gereken üç kısıt

Bunların üçü de sessizce bozulan türden; her biri bir kez yakalandı ve tek noktada
çözüldü, o yüzden yeni ekran yazarken aynı yoldan geçmek gerekmez.

1. **İkon adları sınırlı.** Metis, Bootstrap Icons'un tamamını değil kendi sayfalarında
   geçen 158 glifi derliyor. Setde olmayan bir ad boş kare olarak çıkar ve konsola hiçbir
   şey düşmez. `ui.js` içindeki `ICON_ALIAS` tablosu istediğimiz adları mevcut en yakın
   glife bağlar; yeni bir ad kullanmadan önce çizilip çizilmediği kontrol edilmeli.
2. **ApexCharts CSS değişkeni okumaz.** Renk SVG'ye öznitelik olarak yazıldığı için
   `var(--prov-model)` çözülmez. `ui.js` içindeki `safeColor` bu referansları çizim anında
   `getComputedStyle` ile gerçek değere çevirir; ekranlar token adıyla yazmaya devam eder.
3. **Form denetimleri kendiliğinden biçimlenmez.** Tema yalnızca `.form-control`,
   `.form-select` ve `.form-check-input` taşıyan denetimleri boyar. `field()` ve `modal()`
   gövdesi `dress()` üzerinden geçtiği için ham `<input>` yazmak yeterlidir, doğru sınıf
   tek yerden takılır.

Kategori ekseninde 36 geçmiş artı 18 gelecek dönem yan yana sığmaz. Grafik sarmalayıcıları
`charts.tickAmount()` ile tik sayısını sınırlar; etiketler eşit aralıklı ve okunur kalır.
