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
- Tek ANA + tek YEDEK Excel, atomik kayıt, doğrulanmış yedek yenileme (tarihli yedek yok)
- Doğal dille kayıt + adım adım sihirbaz (LLM'siz), deterministik sorgular ve proje/faaliyet sayfaları
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

Yerel Excel ve çalışma klasörü yolları `scripts/config.py` içinden yönetilir (başka hiçbir modül yol tanımlamaz).
Telegram bot anahtarı kaynak koda yazılmaz; Windows ortam değişkeni veya kullanıcı kayıt alanından okunur.

| Ortam değişkeni | Anlamı | Varsayılan |
|---|---|---|
| `TOUCHE_MAIN_WORKBOOK` | ANA Smart Excel | Windows: `%USERPROFILE%\OneDrive\Desktop\Touche\Touche Holding - AI Ready Finans Yönetim Sistemi.xlsx` (mevcut dosya, adı değişmedi); diğer: `veri/…xlsx` |
| `TOUCHE_BACKUP_WORKBOOK` | tek YEDEK Excel | ANA ile aynı klasörde `<ana adı>_backup.xlsx` |
| `TOUCHE_BASE_DIR` | çalışma klasörü (log, veri, geçici) | Windows: `C:\ai-excel`; diğer: depo klasörü |
| `TOUCHE_LEDGER_SON_SATIR` | `03_Kayıt Merkezi` formül şablonunun son satırı | `300` |
| `TOUCHE_PAKET_KAYBI_IZNI=1` | Excel'deki grafik/resim kaybına bilerek izin ver | kapalı |

## Smart Excel çalışma modeli

* Çalışma zamanında yalnız **ANA** ve **YEDEK** Excel vardır. Her onaylı yazmada: doğrula → YEDEK'i mevcut ANA ile yenile ve
  hash'le doğrula → bellekte değiştir → geçici dosyaya kaydet, yeniden aç ve doğrula → atomik değiştir → geçici dosyayı sil → denetim kaydı.
  Sonuç: ANA = V11, YEDEK = V10. Tarihli/işlem başı yedek, yedek klasörü yoktur. Ayrıntı: [ARCHITECTURE.md](ARCHITECTURE.md).
* Geçici Excel dosyaları yalnız yazma sırasında, projenin `temp` klasöründe (`<base>\temp`) oluşur ve hemen silinir; OneDrive klasörüne geçici dosya
  düşmez (temp, ANA ile aynı diskte değilse hedefin klasörü kullanılır). Başlangıçta `SMART_EXCEL_ROOT / _MAIN_WORKBOOK / _BACKUP_WORKBOOK` satırları yazılır.
* Geri dönüş yalnız insan/operasyon eylemidir (ANA ile YEDEK'i **takas** eder); yapay zeka, web arayüzü veya Telegram geri dönemez:
  `py -3.12 scripts\excel_operasyon.py geri-don --neden "…" --onay GERI-DON`
* Operasyon aracı: `durum`, `dogrula`, `mutabakat` (Excel ↔ merkez defter farkı, salt-okunur), `defteri-aktar`, `envanter`,
  `temizle`, `geri-don`, `entegre-et` — bkz. [EXCEL_FILE_INVENTORY.md](EXCEL_FILE_INVENTORY.md).
* Geçmişte üretilmiş eski Excel kopyaları otomatik silinmez; `envanter` kanıtlayıp listeler, `temizle --sil --onay SIL` yalnız kanıtlı
  artık kopyaları siler. Başka Excel dosyalarına dokunulmaz.

## CFO Asistanı

Tam cümle → tek taslak; eksik bilgi → her seferinde tek soru (Faaliyet → Proje → İşlem → İş kalemi/Cari → Kime → Tutar → PB → Tarih →
Hesap), bilinen değerler korunur. Sorular salt-okunurdur. Holding → Faaliyet → Proje sayfaları tıklanabilir ve tüm sayılar defterden
hesaplanır (hesaplanamayan: `DATA_NOT_AVAILABLE`). Yetenekler: [CFO_CAPABILITY_MATRIX.md](CFO_CAPABILITY_MATRIX.md).

## Güvenlik

Excel dosyaları, finans veritabanı, yüklenen belgeler, loglar, yedekler ve kullanıcı yetki dosyaları `.gitignore` ile Git deposunun dışında tutulur. Bu depo yalnızca kaynak kodu, testleri ve güvenli şablon tanımlarını içerir.

## Test

```powershell
py -3.12 -m pytest -q          # testler üretim Excel'ine asla dokunmaz (geçici klasör; bkz. TESTING.md)
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

---

# CFO Operating System katmanı (yeni)

Bu katman mevcut mimariyi bozmadan eklenmiştir: aynı Python + SQLite + Vanilla JS
stack'i, aynı merkezi defter (`finans_cekirdegi`), aynı Excel master. Yeni modüller
`scripts/` altındadır ve yalnızca **ek tablo/sütun** (additive migration) ekler;
var olan sütunlar yeniden adlandırılmaz veya silinmez.

## Domain hiyerarşisi

```
Holding → Faaliyet Alanı → Şirket → Proje → Sözleşme → Hakediş → İş Kalemi → Finansal İşlem → Belge
```

`veri_katmani.py` şu tabloları ve `fisler` tablosuna şu dimension sütunlarını (nullable)
ekler: `aktivite_id, sozlesme_id, hakedis_id, is_kalemi_id, masraf_merkezi_id, kategori_id, karsi_taraf_id`.

## Finansal çekirdek (tek hesap merkezi)

- `money.py` — Money değer nesnesi; **tamsayı kuruş** ve `Decimal`. `0.1 + 0.2` gibi
  kayan nokta hataları finans hesaplarından kaldırılmıştır. TR biçimleme: `₺1.250.000`, `%24,5`.
- `finans_motoru.py` — Merkezi finans motoru. Nakit, alacak, borç, ciro, maliyet,
  brüt kâr/marj, net işletme sermayesi, yatırım kapasitesi, 13 haftalık minimum nakit
  ve şirket/faaliyet alanı/proje/iş kalemi kırılımları **yalnız buradan** gelir.
  UI/AI hiçbir finansal değeri elle hesaplamaz.

## Holding CFO

- `ticari.py` — AR/AP yaşlandırma (0-30 / 31-60 / 61-90 / 90+), gecikmiş işaretleme, yaklaşan ödemeler.
- `butce_forecast.py` — Bütçe / Taahhüt / Gerçekleşen / Tahmin (EAC) / Sapma.
- `nakit_13hafta.py` — 13 haftalık nakit akışı (Opening, Collections, Supplier, Payroll,
  Tax, Opex, Investments, Financing, Closing) + minimum rezerv eşiği ve risk bayrağı.
- Intercompany: mevcut `sirketler_arasi_transfer` çift taraflı kayıt + eliminasyon.

## Senaryo motoru

- `senaryo_motoru.py` — 11 değişkenli senaryo: ek tahsilat, tahsilat/ödeme gecikmesi,
  ek ödeme, yeni yatırım/kredi, faiz maliyeti, kur/gelir değişimi, maliyet artışı, proje gecikmesi.
- Sonuç **4 seviyelidir**: Güvenli / Sınırda / Riskli / Uygulanamaz ve adım adım gerekçe (formül) içerir.
- Senaryo sürümleme: Base / Best / Worst / Management Plan varsayımlarıyla saklanır.

## Doküman otomasyonu

- `belge_pipeline.py` — UPLOAD → PARSE → OCR → FIELD EXTRACTION → CLASSIFICATION →
  DUPLICATE CHECK → MATCHING → USER REVIEW → APPROVAL → POSTING.
- Deterministik sınıflandırma + güven yüzdesi + alan çıkarımı + SHA-256 mükerrer tespiti.
- AI yalnızca **öneri** üretir (ör. "%92 confidence: Göktürk Okulu olabilir"); otomatik kayıt oluşturmaz.
  Belge metni **güvenilmeyen veri** olarak ele alınır (prompt injection'a karşı).

## AI CFO

```
DATABASE → FINANCIAL ENGINE → STRUCTURED CFO CONTEXT → AI → EXPLANATION / RECOMMENDATION
```
- `ai_cfo.py` sayıları **hesaplamaz**; yalnız `finans_motoru` çıktısını yorumlar.
- Cevap her zaman `Data through` tarihini ve kanıtları gösterir; veri yoksa "Bu veri sistemde mevcut değil." der.
- Otomatik içgörüler: type / severity (INFO/WARNING/CRITICAL) / title / description / entity / metric / detectedAt / status.
- Kritik işlemleri otomatik yapmaz; onay zorunludur.

## Roller / yetki

- `yetki.py` — OWNER, CFO, FINANCE_MANAGER, ACCOUNTANT, PROJECT_MANAGER, VIEWER.
- Eski roller (ADMIN/FINANS/ONAYCI/IZLEYICI) uyumluluk için eşlenir.
- **UI gizleme yetkilendirme değildir**; izinler sunucu/API katmanında zorunlu kılınır.

## Audit / onay

- `audit_db.py` — actor / action / entityType / entityId / before / after / timestamp.
  Hassas alanlar (parola, secret, token) **maskelenir**; ayrıca mevcut hash-zincirli JSONL'e yazılır.
- Mevcut onay matrisi (tutar bazlı, DRAFT/PENDING/APPROVED/REJECTED) korunur.

## FX

- `fx_provider.py` — `FxRateProvider` soyutlaması; `ManuelFxRateProvider` (veritabanı) varsayılan,
  `TcmbFxRateProvider` iskelet (ağ/credential gerekir). Geliştirme fallback'i manuel kurdur.

## İleri FP&A

- `fpa_ileri.py` — duyarlılık analizi (maliyet/gelir ±%), başabaş, tahmin doğruluğu (hata % / MAPE),
  Monte Carlo (isteğe bağlı modül; production motorunun bağımlılığı değildir; simülasyon backend'de).

## API (yeni uçlar)

`GET  /api/kpi, /api/kirilim, /api/proje-karlilik, /api/is-kalemleri-karlilik, /api/nakit/13hafta,
/api/ticari/alacak, /api/ticari/borc, /api/ticari/yaklasan, /api/butce/gerceklesen, /api/taahhut,
/api/senaryolar, /api/senaryo/karsilastir, /api/ai-cfo/insights, /api/ai-cfo/baglam, /api/raporlar,
/api/rapor?ad=, /api/audit, /api/yetki/roller, /api/fpa/duyarlilik, /api/fpa/basabas,
/api/fpa/monte-carlo, /api/fpa/dogruluk, /api/fx/cevir, /api/belge/kuyruk`

`POST /api/senaryo/motor, /api/senaryo/kaydet, /api/ai-cfo/sor, /api/belge/pipeline,
/api/fpa/dogruluk-kaydet, /api/demo/hierarchy`

## Arayüz

- `ui/cfo.js`, `ui/cfo.css`, `ui/i18n.js` — mevcut panel korunur; üstte CFO navigasyonu eklenir:
  Holding · Faaliyetler · Projeler · Nakit & Hazine · Alacaklar · Borçlar · Bütçe & Forecast ·
  Senaryolar · Belgeler · AI CFO · Raporlar · Yönetim.
- KPI kartları trend + drill-down; veri tablolarında sorting/filter/search/column-visibility/pagination/export;
  risk badge'leri (renk + metin); loading skeleton, empty/error state; responsive.
- Metinler `ui/i18n.js` içinde merkezileştirilmiştir (Türkçe öncelikli).

## Veritabanı migration

Şema **additive**tir; `veri_katmani.veritabani_hazirla()` çağrısı yeni tabloları (`CREATE TABLE IF NOT EXISTS`)
ve yeni sütunları (`ALTER TABLE ... ADD COLUMN`) idempotent biçimde ekler. Veri kaybına yol açan
drop/rename **yapılmaz**. Demo hiyerarşi yalnızca açık çağrıyla (`POST /api/demo/hierarchy`) yüklenir.

## Testler

```powershell
py -3.12 -m pytest -q
```
Testler yerel `scripts/` klasörünü içeri alır (taşınabilir). Yeni CFO katmanı için
`tests/test_holding_cfo.py` finansal invariantları doğrular (brüt kâr/marj, denge, 13 hafta
kapanış, senaryo seviyeleri, duplicate, yetki, FX, MAPE).

> Not: `test_phase1.py::test_pdf_gorsele_cevrilir` ortamda `pdftoppm` (poppler) yoksa
> hata verir; bu koddan bağımsız bir ortam gereksinimidir.

## Ortam değişkenleri

`.env.example`'a bakın (`.env` olarak kopyalayın). Gerçek secret **commit edilmez**;
`.gitignore` `.env`, `*.xlsx`, `*.db`, `loglar/`, `veri/` gibi tüm gizli/finansal veriyi dışlar.


