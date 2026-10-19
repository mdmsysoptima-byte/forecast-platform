# Mock sürüm

**Yayında:** <https://mdmsysoptima-byte.github.io/forecast-platform/>

Ekranların sunucusuz hali. GitHub Pages yalnızca dosya sunar, Python
çalıştırmaz; burası o kısıt için var. Giriş ekranı yoktur, veriler sabittir,
yazma işlemleri kaydedilmez.

## Nasıl çalışıyor

`mock-api.js` tarayıcıda `fetch`'i yakalar ve `data/` altındaki JSON
dosyalarından cevap verir. Uygulamanın kendi `api.js`'i hiç değiştirilmedi —
mock ile gerçek sürüm **aynı ön yüz kodunu** çalıştırır.

```
mock/
  index.html     uretilir  — frontend/index.html'den, yollar goreli
  mock-api.js    elle      — fetch katmani
  data/          uretilir  — yakalanan API yanitlari, adlari icerik ozeti
```

`index.html` ön yüzü kopyalamaz, `../frontend/static/...` yolunu kullanır.

## İki tür değişiklik

**Ön yüz değişikliği** (`frontend/static/js/**`, `app.css`): hiçbir şey
yapmanıza gerek yok. Mock aynı dosyaları okuduğu için değişiklik anında
yansır. Önyüz üzerinde çalışırken akış budur.

**Backend değişikliği** (yeni uç nokta, değişen yanıt şekli, yeni demo verisi):
veriyi yeniden yakalamak gerekir.

```powershell
.\run.ps1                       # uygulama ayakta olmali
python tools/build_mock.py
```

`frontend/index.html` değişirse de bu betiği koşun: `mock/index.html` ondan
üretiliyor.

## Yerelde deneme

Pages gibi, yalnızca dosya sunan bir sunucuyla:

```powershell
python -m http.server 8090
# http://127.0.0.1:8090/mock/
```

## Bilinen sınırlar

- **Yazma yok.** Hücre düzenleme, senaryo çalıştırma, onay, Excel içe/dışa
  aktarma çalışmaz; arayüz "değişiklik kaydedilmedi" der.
- **Pencerede tek uç değiştirmek tam çalışır.** Her başlangıç x varsayılan bitiş ve
  varsayılan başlangıç x her bitiş yakalandı. İki ucu birden değiştirirseniz en
  yakın yakalanmış aralığa düşer.
- **Filtreler yakalanan kombinasyonlarla sınırlı.** Ölçü, zaman birimi,
  toplam/ortalama, model seçimi, bütçe modeli, dönem pencereleri, kapsam ve
  yaygın tarih aralıkları yakalandı. Dışına çıkan bir kombinasyon en yakın
  yanıta düşer ve konsola `[mock] tam eslesme yok` notu yazar.
- **Veri donmuş.** `data/` ne zaman yakalandıysa o. Tarihler ilerlemez.
- **v2 grubu burada da çalışır.** Alternatif arayüz prototipi bir iframe içinde
  duruyor, hiç API çağırmıyor ve kendi gömülü verisiyle geliyor; bu yüzden mock
  ile gerçek sürümde birebir aynı davranır. Yakalanacak bir şeyi yok.
  Ayrıntı: [`../frontend/static/v2/README.md`](../frontend/static/v2/README.md).

## Neden dosya adları özet

Dosyalar sırayla adlandırılırsa (`f0000.json`) her yeniden üretimde aynı ad
farklı içeriğe denk gelir ve tarayıcıdaki eski kopya yanlış ekrana servis
edilir. İçerik özeti kullanınca değişen yanıt yeni bir ad alır, değişmeyen
dosya önbellekte kalır. `index.json` her açılışta doğrulanır.
