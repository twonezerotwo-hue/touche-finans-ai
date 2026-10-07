# Touche Finans AI

Touche Holding için yerel çalışan finans karar destek ve belge işleme sistemi.

## Özellikler

- Tek master Excel mimarisi
- Faaliyet, proje, cari, işlem ve belge hiyerarşisi
- Yerel Ollama modelleriyle finans asistanı
- Qwen2.5-VL ile fatura/dekont okuma
- Deterministik Python doğrulama katmanı
- 13 haftalık nakit projeksiyonu
- Risk, veri kalitesi ve faaliyet performans göstergeleri
- Tahsilat, ödeme ve yatırım senaryo analizi
- Holding ortak hesap planı ve faaliyet bazlı hesap eşleme
- Şirketler arası otomatik çift kayıt ve konsolidasyon eliminasyonu
- Cari alacak/borç yaşlandırma ve bütçe-gerçekleşen sapma raporu
- Döviz kuru tablosu ve dönem sonu kur değerleme fişleri
- Gelişmiş banka mutabakatı ve eşleşme puanlaması
- Rol, tutar ve görev ayrılığına bağlı onay matrisi
- SHA-256 belge manifesti ve değiştirilemez hash-zincirli audit log
- Dönem kapanış kontrol listesi ve kapalı döneme kayıt engeli
- Güvenli Excel yedekleme ve atomik kayıt
- Audit log ve çift kayıtlı SQLite finans defteri
- Telegram ve yetkili telefon erişimi

## Yerel kurulum

Gereksinimler:

- Windows 11
- Python 3.12
- Ollama
- `qwen2.5vl:7b` ve `qwen3:8b`

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
ollama pull qwen2.5vl:7b
ollama pull qwen3:8b
py -3.12 scripts\finans_asistani_sunucu.py
```

Arayüz: `http://127.0.0.1:8765/`

## Yapılandırma

Yerel Excel ve çalışma klasörü yolları `scripts/config.py` içinden yönetilir. Telegram bot anahtarı kaynak koda yazılmaz; Windows ortam değişkeni veya kullanıcı kayıt alanından okunur.

## Güvenlik

Excel dosyaları, finans veritabanı, yüklenen belgeler, loglar, yedekler ve kullanıcı yetki dosyaları `.gitignore` ile Git deposunun dışında tutulur. Bu depo yalnızca kaynak kodu, testleri ve güvenli şablon tanımlarını içerir.

## Test

```powershell
py -3.12 -m unittest discover -s tests -v
```

Finans asistanı para transferi yapmaz. Öneriler kayıtlı verilere dayalı karar desteğidir.

## Sohbetten kontrollü kayıt

CFO asistanına doğal dille gelir, gider, ödeme, tahsilat veya hakediş talebi yazılabilir:

- `Gürsar Mimarlık için bugün Sebahattin Çakmak'a 25.000 TL ödeme kaydet.`
- `Gürsar Göktürk Okulu için İşveren'e 150.000 TL hakediş ekle.`

Asistan önce bir taslak gösterir. Master data veya zorunlu alan eksikse kayıt kilitlenir;
`Düzelt: ...` yazarak taslak tamamlanır. Yalnız açık **Onayla** işleminden sonra yedek,
geçici dosya doğrulaması ve atomik değiştirme kullanılarak tek master Excel ile merkez
finans defteri birlikte güncellenir. **İptal** edilen taslak Excel'e yazılmaz.

Eksik bir alan varsa asistan tarih, proje, cari, tutar, para birimi veya hesap/kasa
bilgilerini tek mesajda sorar; takip cevabı aynı taslağa uygulanır. Aynı carinin önceki
onaylı kayıtlarında proje ve hesap tekil ve tutarlıysa bunlar otomatik eşleştirilir ve
taslakta eşleştirme nedeni gösterilir. Tutar, tarih, kur ve işlem türü geçmiş kayıttan
tahmin edilmez; belirsiz banka/kasa seçimi kullanıcıya sorulur.

## Kurumsal finans merkezi

Dashboard içindeki **Konsolidasyon ve denetim merkezi** üzerinden grup içi transfer,
kur değerleme, dönem kapanış kontrolü, cari yaşlandırma, banka mutabakatı,
hesap eşlemeleri ve onay kuralları yönetilir. Grup içi transferler kaynak ve hedef
faaliyete birlikte kaydedilir; holding raporunda 131/331 grup içi bakiyeleri elimine edilir.
