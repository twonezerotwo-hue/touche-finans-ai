# PR-02 — Finansal tutarlılık regresyon sözleşmeleri

Bu PR yalnızca sentetik test ve dokümantasyon ekler. Testler geçici klasörlerde
oluşturulan workbook, SQLite, audit ve senkron kuyruğu kullanır; `C:\ai-excel`,
OneDrive, canlı Excel, üretim veritabanı veya kimlik bilgilerine erişmez.

## Çalıştırma

```powershell
py -3.12 -m pytest tests/test_finance_consistency_contracts.py -q
py -3.12 -m pytest -q
ruff check tests/test_finance_consistency_contracts.py
```

Yeni test modülü için Ruff engelleyicidir. Mypy yapılandırması `tests/` ağacını
kapsam dışı bıraktığından bu PR'da uygulanabilir yeni üretim modülü yoktur;
üretim kodundaki mevcut mypy baseline'ı değiştirilmemiştir.

## Test matrisi

| Kimlik | Senaryo | Test/kanıt | Sonuç |
|---|---|---|---|
| A01 | Excel başarılı, SQLite posting başarısız | `test_a01_excel_once_db_basarisizsa_tamamlandi_denememeli` | **XFAIL (istenen atomik sözleşme)** |
| A02 | SQLite approval öncesi kullanılamıyor | `test_a02_db_on_kontrol_basarisizsa_excel_yazilmaz` | PASS |
| A03 | DB transaction ortasında kesinti/rollback | `test_a03_deneme_sirasinda_sqlite_rollback_yarim_fis_birakmaz` | PASS |
| A04 | Excel-only durum | `test_a04_excel_only_durum_mutabakatta_eksik_defterdir` | PASS |
| A05 | DB-only durum | `test_a05_db_only_durum_mutabakatta_defter_fazlasidir` | PASS |
| A06 | Excel tutarı sonradan değişiyor | `test_a06_excel_tutari_degiserse_tutar_farki_raporlanir` | PASS |
| A07 | Yarım işlem yeniden başlatma/kuyruk kurtarma | `test_a07_kuyruk_kurtarma_ve_yeniden_baslatma` | PASS |
| A08 | Bozuk kuyruk satırı korunuyor | `test_a08_kuyruk_hatasi_kalan_satiri_korur` | PASS |
| A09 | Incomplete transaction audit kanıtı | `test_a09_audit_incomplete_transaction_kanitini_tutar` | PASS |
| A10 | Aynı belge retry idempotency | `test_a10_ayni_belge_retry_idempotenttir` | PASS |
| A11 | Aynı değerli iki gerçek ödeme | `test_a11_ayni_tutar_farkli_belge_ayri_odemedir` | PASS |
| A12 | Kuyruk iki kez işlense de tek posting | `test_a12_kuyruk_iki_kez_islenince_cift_posting_olmaz` | PASS |
| A13 | Döviz kuru eksikliği | `test_a13_doviz_kuru_yoksa_kayit_reddedilir` | PASS |
| A14 | Bilinmeyen işlem sessizce giderleşmiyor | `test_a14_bilinmeyen_islem_turu_sessizce_gider_olmaz` | PASS |
| A15 | Aynı belgeye eşzamanlı approval | `test_a15_es_zamanli_onayda_tek_posting_olur` | PASS |
| A16 | Aynı belge kimliği farklı içerikle retry | `test_a16_ayni_belge_id_farkli_icerik_cakismasi_reddedilir` | PASS |
| FIN-P01-DIAG | Mevcut Excel/DB ayrışmasını canlı akışta kanıtlar | `test_fin_p01_mevcut_excel_db_ayrisini_kanitlar` | PASS diagnostic |
| B01 | Ayrı süreçte Excel sonrası ani kesilme | `test_b01_ayri_surec_cokmesinde_excel_db_ayrisini_kanitlar` | PASS diagnostic |
| B02 | Ani kesilme sonrası kalıcı recovery işareti | `test_b02_ayri_surec_cokmesi_kalici_kurtarma_isareti_uretmelidir` | **XFAIL (PR-03)** |

İstenen A–F sözleşmelerinin tamamı aşağıdaki şekilde yürütülebilir testlere
bağlanmıştır. Aynı davranış zaten kapsanıyorsa mevcut regresyon testi
referanslanmıştır; yeni test eklemek için üretim kodu değiştirilmemiştir.

| Grup | Sözleşme | Test kaynağı |
|---|---|---|
| A01–A06 | Excel/SQLite başarı, başarısızlık, eksik kayıt ve tutar farkı | Bu dosyadaki A01–A06 |
| A07–A10 | Kur/fingerprint, mutabakat, tamamlandı/audit kanıtı | A13; `tests/test_excel_mutabakati.py` mutabakat testleri; A09 |
| B01 | Excel yazılmadan kesinti | `tests/test_excel_deposu.py::test_yerlestirme_hatasinda_ana_degismez_gecici_kalmaz` |
| B02–B07 | Excel sonrası kesinti, rollback, restart/kuyruk/bozuk kuyruk | A03, A07, A08, A12, B01–B02; `tests/test_excel_deposu.py` geçici dosya/geri dönüş testleri |
| C01–C07 | Tekrar approval, retry, eşzamanlılık, gerçek eşit ödemeler | A10–A12, A15; `tests/test_cfo_http.py::test_http_ayni_komut_ikinci_kez_engellenir` |
| D01–D09 | Dış değişiklik, kilit, bozukluk, formül, backup, atomic replace, concurrency | `tests/test_excel_deposu.py` AtomiklikVeKoruma/GeciciKilit/GeriDonus sınıfları |
| E01–E12 | Nakit/gelir/kredi ayrımı, çift taraf, FX, approval ve salt-okuma | `tests/test_holding_cfo.py`, `tests/test_finans_cekirdegi.py`, `tests/test_kurumsal_finans.py`, `tests/test_cfo_http.py` |
| F01–F07 | API durum sözleşmesi, raporlama, yetki ve mutabakat uyarısı | `tests/test_cfo_http.py`, `tests/test_excel_mutabakati.py`, `tests/test_finans_danismani.py` |

Tamamlayıcı Excel güvenlik sözleşmeleri mevcut `tests/test_excel_deposu.py`
içindedir: dış değişiklik (`test_islem_sirasinda_disaridan_degisen_ana_ezilmez`),
kalıcı/geçici kilit, atomic replace, backup/rollback, formül koruması ve
eşzamanlı yazma testleri. Mutabakat ve raporlama sözleşmeleri
`tests/test_excel_mutabakati.py`, API/raporlama sözleşmeleri ise
`tests/test_cfo_http.py`, `tests/test_holding_cfo.py` ve
`tests/test_finans_danismani.py` içindedir.

## Doğrulanmış hata / defect register

`scripts/main.py::hazirlanan_belgeyi_yaz` Excel yazımından sonra SQLite posting
üç kez başarısız olsa bile kaydı kuyruğa alıp `True` ve tamamlandı audit'i
döndürüyor. Böylece kullanıcı işlemi tamamlanmış sanabilir; Excel'de satır
varken merkezi defterde fiş yoktur. A01 bu davranışı zayıflatmadan, istenen
atomik sözleşmeyi bekleyen `xfail(strict=True)` ile gösterir. Bu bir
**confirmed defect**tir; testin XPASS etmesi PR-03'teki düzeltmenin geldiğini
göstermelidir.

Ayrı bir Python sürecinde `os._exit(47)` ile Excel atomic replace tamamlandıktan,
SQLite posting başlamadan hemen önce kesinti enjekte edildi. İkinci, yeni süreçte
gözlenen durum deterministiktir: Excel satırı **1**, SQLite fişi **0**, senkron
kuyruğu **yok**, `belge_tamamlandi` audit'i **yok**. Bu, normal exception kuyruğu
recovery'sinin process-kill aralığını kapsamadığını gösteren ayrı bir
**confirmed defect**tir; üretim koduna dokunulmadan B01 ile kanıtlanır.

| Defect ID | Önem | Etkilenen fonksiyon | Üretim gözlemi | Doğru davranış | Olası finansal etki | PR-03 düzeltmesi |
|---|---|---|---|---|---|---|
| FIN-P01 | P1 | `scripts/main.py::hazirlanan_belgeyi_yaz` | Excel yazılır, SQLite üç denemede başarısız olur; işlem kuyruğa alındığı halde `True` ve tamamlandı audit'i döner | Commit sözleşmesi sağlanmıyorsa başarı dönmemeli; durum `INCOMPLETE` olarak kalmalı ve Excel/DB eşleştirmesi recovery ile tamamlanmalı | Kullanıcı tamamlandı sanarak aynı ödemeyi tekrar girebilir veya merkezi rapor eksik kalabilir | İşlem kimliği + durable state; DB posting doğrulanmadan tamamlandı/audit yok; idempotent recovery |
| FIN-P02 | P1 | `scripts/main.py::hazirlanan_belgeyi_yaz` + `scripts/excel_deposu.py::guvenli_degistir` sınırı | Ayrı süreç Excel replacement sonrası DB öncesi kesilince Excel kalıyor; queue/state/audit marker yok | Restart yeni işlemi `PENDING_RECOVERY` olarak bulmalı; DB posting veya güvenli rollback sonrası açık sonuç üretmeli | Rapor Excel ve DB arasında kalıcı ayrışabilir; kullanıcı aynı belgeyi yeniden deneyebilir | Durable transaction manifesti, `PREPARED → EXCEL_WRITTEN → DB_POSTED → COMMITTED` state machine; startup recovery ve idempotent replay |

Bu PR'da doğrulanmış finansal bütünlük hataları FIN-P01 ve FIN-P02'dir. A01 ve
B02, istenen gelecek atomik/recovery davranışını strict XFAIL olarak korur;
FIN-P01-DIAG ve B01 ise mevcut üretim davranışını PASS olarak kanıtlar. Diğer
tabloda listelenenler mevcut güvenlik sözleşmelerinin test edilmiş sınırları veya
PR-03 için tasarım riskleridir; testler bunları varsayım olarak değil, sentetik
durumda gözlemlenebilir sonuç olarak raporlar.

Diğer testler mevcut davranışın güvenli sınırlarını doğrular; A04–A06 ve
mutabakat testleri tutarsızlığı sessizce gizlemeyip raporlar. A08'de bozuk
kuyruk satırı silinmez. Bunlar şu an **confirmed pass** sonuçlarıdır.

## PR-03 gereksinimleri

1. `scripts/main.py::hazirlanan_belgeyi_yaz` ile
   `scripts/finans_cekirdegi.py::finans_kaydi_yaz` arasına işlem kimlikli
   recovery state eklenmeli; Excel başarılı/DB başarısız sonucu kullanıcıya
   tamamlandı olarak dönmemeli.
2. Excel ve SQLite posting aynı işlem kimliğiyle bağlanmalı; restart sonrası
   `senkron_kuyrugunu_isle` yalnızca güvenli, idempotent commit yapmalı.
3. Recovery sonrası `belge_tamamlandi` audit'i ancak merkezi fiş doğrulandıktan
   sonra yazılmalı; eksik işlem için açık `incomplete_transaction` kanıtı
   tutulmalı.
4. `scripts/ui_belge_servisi.py::onayla` ve HTTP katmanı recovery durumunu
   kullanıcıya açıkça göstermeli; ikinci approval aynı posting'i üretmemeli.
5. `scripts/excel_defter_mutabakati.py` ve rapor katmanı recovery bekleyen
   kayıtları gelir/gider/nakit toplamlarına yanlışlıkla dahil etmemeli.
6. Bu PR'daki A01 dahil tüm sözleşmeler yeşile dönmeli; gerçek üretim verisiyle
   ayrı bir entegrasyon testi yazılmamalı.
7. İşlem durumları en az `PREPARED`, `EXCEL_WRITTEN`, `DB_POSTED`, `COMMITTED`,
   `PENDING_RECOVERY`, `NEEDS_REVIEW`, `ABORTED` olarak kalıcı tutulmalı; her
   geçişte transaction ID, belge hash'i, workbook fingerprint'i ve audit kanıtı
   bulunmalı.
8. Başlangıçta yarım transaction taranmalı; aynı belge kimliği aynı finansal
   hash ile idempotent devam etmeli, farklı hash kesin çatışma olarak durmalı.
9. Dashboard/raporlar recovery bekleyen işlemleri kesinleşmiş gelir-gider,
   nakit veya borç toplamına katmamalı; kullanıcıya mutabakat uyarısı göstermeli.

## Sonuç kaydı

İzole yeni test paketi: **17 passed, 2 strict xfailed**. Yeni B01 testi gerçek
ayrı Python süreci kullanır; çocuk süreç yalnızca sentetik klasörde çalışır ve
exit code `47` ile kesilir. XFAIL'ler A01 (exception sonrası atomiklik) ve B02
(process-kill sonrası durable recovery markerı) olup skip edilmemiş,
assertion gevşetilmemiştir.

Final yerel doğrulama (`py -3.12 -m pytest -q -rs`): **420 passed, 8 skipped,
2 strict xfailed, 352 subtests passed** (193.85 sn). Skip'ler: üretim DB kopyası
ortam değişkeni yok (1) ve yerel Chromium kurulu değil (7). İki XFAIL yalnızca
bilinen/gelecek güvenlik sözleşmeleridir; normal testler yeşildir.

Yerel skip nedenleri: `tests/test_acceptance.py:300` üretim DB kopyası ortam
değişkeni tanımlı değil; `tests/test_ui_tarayici.py` içindeki 7 tarayıcı testi
Chromium başlatılamadığı için atlandı. Bunlar PR-02 test hatası değildir;
Windows doğrulamasında kullanılabilir Chromium kurulumu gerektiğinde çalışır.

İzole ortam güvenliği: yeni test sınıfı her testte `TemporaryDirectory`,
sentetik `tam_akilli_excel`, geçici SQLite, audit ve queue kullanır. Uygulama
modülleri import edilmeden önce test konfigürasyonu geçici yollara yönlendirilir;
gerçek `C:\ai-excel`, OneDrive dosyaları, Telegram/banka/LLM ve üretim
kimlik bilgileri erişilmemiştir. Merge/deploy yapılmayacaktır.

## CI doğrulaması

Final PR-02 koşusu **39135289792** (commit `2bade5483b044c699923c59310da736bdefd721d`) başarılıdır:

- Ubuntu: **425 passed, 3 skipped, 2 xfailed, 352 subtests passed**.
  Skip'ler: `tests/test_acceptance.py:300` üretim DB kopyası ortam değişkeni
  eksik; `tests/test_cfo_borc_eslestirme.py:400` Windows'a özgü Excel paylaşım
  kilidi; `tests/test_phase1.py:230` Poppler/pdftoppm yok.
- Windows: **426 passed, 2 skipped, 2 xfailed, 352 subtests passed**.
  Skip'ler: `tests/test_acceptance.py:300` üretim DB kopyası ortam değişkeni
  eksik; `tests/test_phase1.py:230` Poppler/pdftoppm yok.
- Her iki platformda Ruff baseline ve incremental mypy baseline adımları
  mevcut PR-01 politikasıyla raporlandı; uygulama kodu bu PR'da değiştirilmedi.
- Dependency audit: başarılı; bilinen zafiyetlerde job başarısız olur,
  desteklenmeyen VCS `excel-parser` ayrı raporlanır.
- Secret/production-path scan: başarılı.

CI'da üretim Excel/SQLite erişimi açılmamıştır. Run bağlantısı:
`https://github.com/twonezerotwo-hue/touche-finans-ai/actions/runs/39135289792`.

## PR-03 tamamlanan uygulama

PR-03 branch'inde FIN-P01 ve FIN-P02 artık strict XFAIL değil, geçen
regresyon sözleşmeleridir. Kalıcı `finans_islemleri` journal'ı ve
`finans_islem_olaylari` geçiş kaydı, mevcut tek Excel yazıcısıyla birlikte
çalışır. `PREPARED -> EXCEL_WRITTEN -> DB_POSTED -> COMMITTED` akışı; hata,
kilit, dış değişiklik veya süreç kesintisinde `PENDING_RECOVERY`,
`NEEDS_REVIEW` ya da `ABORTED` durumuna geçer.

PR-03 odak paketi: **27 passed**. Ek senaryolar; SQLite commit sonrası journal
kesintisi, tekrarlı/eşzamanlı recovery, dry-run, dış workbook değişikliği,
bozuk journal, audit onarım kanıtı ve recovery sırasında mutabakat kontrolüdür.
Detaylı protokol ve şema: `FINANCIAL_COMMIT_RECOVERY.md`.
