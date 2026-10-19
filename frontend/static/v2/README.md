# v2 prototipi

`prototip.html` dışarıdan geldi, **elle düzenlenmedi**. Uygulamaya sol menüdeki
**v2** grubundan, bir iframe içinde bağlanıyor
(`frontend/static/js/views/v2.js`).

Yeni sürüm gelirse bu dosyanın üzerine yazın; başka hiçbir yere dokunmak
gerekmez. Tek koşul rotaların adlarının korunması — sol menü maddeleri
`#/kokpit`, `#/tahminler`, `#/karsilastir`, `#/dogruluk`, `#/raporlar`,
`#/yonetim` adreslerine gidiyor.

## Neden iframe

Aynı dokümana gömülemez, üç bağımsız sebeple:

1. Prototip de `location.hash` ile yönleniyor; kabuk da öyle. İki router aynı
   adres üzerinde çakışır.
2. `*`, `html`, `body`, `a`, `button`, `h1` gibi global seçiciler ve `.btn`,
   `.card`, `.row`, `.panel` gibi genel sınıf adları tanımlıyor. Aynı DOM'da v1
   ekranları bozulur.
3. Temasını `<html data-theme>` ile sürüyor, bizimki `data-bs-theme` kullanıyor.

iframe üçünü de çözüyor ve dosyaya dokunmamayı mümkün kılıyor.

## Kabuğun yaptığı tek müdahale

Prototipin kendi 220px sol menüsü, iframe'e enjekte edilen tek satırlık bir
kuralla gizleniyor: `.nav{display:none}`. Gezinme bizim sol menümüzden yürüyor,
iki menü yan yana durmasın diye. Dosya değişmiyor; prototip her ekran geçişinde
kendi DOM'unu yeniden yazsa bile kural stylesheet'te kaldığı için çalışmaya
devam ediyor.

## Prototip hakkında

- **Tamamen bağımsız.** Sıfır `fetch`/XHR/WebSocket, API çağrısı yok,
  `postMessage` yok. Tek dış kaynak Google Fonts (IBM Plex Sans); erişilemezse
  `system-ui`'ye düşer, düzen bozulmaz.
- **`localStorage` kullanmıyor.** Tema seçimi ve tüm durum yalnızca bellekte;
  iframe yeniden yüklenirse sıfırlanır. View bu yüzden iframe'i kalıcı tutuyor.
- **Verisi gömülü ve sabit.** `Math.random` yok, her açılışta aynı sayılar.
  Demo veritabanımızla ilgisi yok; kullanıcı "M. Kaya, Planlamacı" sabit.
- **`state` dışa kapalı** (bir `const`), dışarıdan beslenemez. `go()`,
  `render()`, `route()` ise `window` üzerinde erişilebilir — view gezinme için
  `go()` kullanıyor.

## Bilinen kusurlar

Bunların hiçbiri düzeltilmedi; prototip olduğu gibi duruyor. Liste, ileride
düzeltme kararı verilirse sıfırdan aranmasın diye burada.

### Gerçek hatalar

| Ne | Nerede |
| --- | --- |
| Dolu bir hücrede "Vazgeç, değeri geri al" hücreyi **siliyor** — `pending` yalnızca satır içi düzenlemede set ediliyor, gerekçe modalı başka yoldan açılınca `null`'a düşüyor | satır 668 |
| `pending` hücre değil **satır** bazlı: aynı satırda önce 3. sütunu düzenleyip sonra 7. sütunun modalında "Vazgeç" denince 3. sütunun eski değeri 7. sütuna yazılıyor | satır 679 + 668 |
| Kapsam modalında hiçbir düğüm seçmeden "Uygula" denebiliyor; `factor` 0 oluyor ve **tüm ekran sıfıra düşüyor** | satır 608 + 227 |
| "2 düşük doğruluk, 1 eksik veri…" bağlantısı (`href="#uyarilar"`) kaydırmıyor; hash router devreye girip Kokpit'i yeniden çiziyor ve sol menüde hiçbir madde seçili kalmıyor | satır 331 |
| Yinelenen `id`: `dv` ve `wo` ikişer kez geçiyor, ikinci kontroller okunamıyor | satır 464, 545 |
| "Toplam gir ve dağıt" tek bölge seçiliyken girilen toplamın yalnızca o bölgenin payını yazıyor | satır 664 |

### Ölü kontroller (~30)

Handler'ı hiç bağlanmamış, tıklanınca hiçbir şey olmayan kontroller:
Kokpit'te Adet/Ciro, Ay/Çeyrek/Yıl, Toplam/Ortalama segmentleri ve tarih
aralığı select'leri (metni değiştiriyor, grafiği değiştirmiyor); "Dönem sayısı /
Tarih aralığı" segmenti; Tahminler'deki arama kutusu; Doğruluk'ta
MAPE/WMAPE/Bias ve "Son 6 ay / Son 12 ay"; Konsensüs'te hesap seviyesi,
geçerlilik, formül girişi ve üç alt seçenek; Senaryo'da ufuk, sürücü seti ve
kural formundaki seviye; sihirbazdaki ölçü, boş hücre, referans, kaynak, model
paketi, zamanlama select'leri.

### Kasti sahteler

Prototip bunları kendi metninde zaten söylüyor, "düzeltmek" yeni ekran yazmak
olurdu: 21 rapor butonu (Ekranda / Excel / PDF), Yönetim ekranının 8 "Aç"
düğmesi ("bu prototipte yok" diyor), 4 senaryo kural tipi (Kaydırma, Olay,
Lansman eğrisi, Sonlandırma — ekleniyor ama etkisi 0), 3 giriş yöntemi
("bu prototipte tabloyu değiştirmez" diyor), Excel yükleme (her zaman aynı 3
hücreyi yazıyor), "Şablonu indir" ve "Excel indir" (yalnızca toast).
