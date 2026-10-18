# PR-03 — Kalıcı Finansal Commit ve Kurtarma

## Amaç

Excel master dosyası ile SQLite defteri tek bir ACID transaction değildir. PR-03,
bu iki kaynağı yanlış biçimde atomik ilan etmek yerine kalıcı bir işlem günlüğü
ve güvenli recovery protokolü kullanır.

## Durum makinesi

```text
PREPARED -> EXCEL_WRITTEN -> DB_POSTED -> COMMITTED
    |             |              |
    +----------> PENDING_RECOVERY
    +----------> NEEDS_REVIEW
    +----------> ABORTED
```

`PREPARED`, Excel'e dokunulmadan önce `finans_islemleri` tablosuna yazılır.
Her geçiş `finans_islem_olaylari` tablosuna ve mevcut hash-zincirli audit akışına
eklenir. `COMMITTED` yalnız beklenen Excel satır kimlikleri, SQLite fişleri ve
satır parmak izi doğrulandıktan sonra verilir.

## Journal şeması

`finans_islemleri` şu kanıtları tutar:

- `transaction_id` — belge ve finansal içerikten üretilen sabit kimlik
- `document_id`, kaynak SHA-256 ve normalize finansal hash
- onay kimliği, faaliyet/belge türü ve durum zamanları
- beklenen Excel ve SQLite kimlik listeleri
- onaylanan Excel finansal hash'i ve beklenen SQLite içerik hash'i
- işlem öncesi/sonrası workbook hash'leri ve Excel satır parmak izi
- kurtarma denemesi sayısı, sınırlı hata metni ve audit-onarım bayrağı

Journal aynı belge + aynı finansal içerik tekrarını idempotent kabul eder;
aynı belge + farklı içeriği bütünlük çatışması olarak reddeder. Farklı belge
kimliği aynı tutarda olsa bile ayrı bir işlemdir.

## Yazma protokolü

1. Deterministik doğrulama ve onay kontrolü.
2. Sabit transaction kimliği ve finansal hash üretimi.
3. Kalıcı `PREPARED` kaydı.
4. Mevcut tek Excel yazıcısı ile kontrollü atomik Excel değişikliği.
5. Excel satır kimlikleri ve parmak izi doğrulaması.
6. Persisted Excel satırlarının, onaylanan finansal içerik hash'iyle yeniden
   doğrulanması; hash doğrulanamıyorsa post işlemi yapılmaz.
7. SQLite fişlerinin idempotent post edilmesi.
8. Fiş tutarları, para birimi, hesap ve borç/alacak satırlarının içerik hash'iyle
   doğrulanması; yalnız fiş ID'si eşleşmesi yeterli değildir.
9. `DB_POSTED`, ardından `COMMITTED` geçişi ve tamamlanmış audit olayı.

SQLite başarısız olursa işlem `PENDING_RECOVERY` olur; kullanıcıya başarı
mesajı verilmez ve aynı ödemenin tekrar oluşturulmaması söylenir. Excel kilidi
veya dış değişiklik `NEEDS_REVIEW`/yeniden denenebilir pending sonucu üretir.

## Kurtarma

`finans_islem_journali.recover_pending_transactions()` başlangıçta terminal
olmayan işlemleri tarar. `NEEDS_REVIEW` kayıtları yalnızca raporlanır; otomatik
olarak tekrar post edilmez. Açık bir yönetici kimliği ve gerekçesiyle
`authorize_needs_review()` çağrılmadan kurtarma başlatılamaz. Manuel karar yalnız
doğrulanmış UI oturumundan gelebilir: kimlik oturumdaki `kimlik` alanıyla
eşleşmeli, oturum `yetkili` olmalı, rol hem `finans.duzenle` hem
`audit.goruntule` iznine sahip olmalı ve faaliyet kapsamı transaction'ın
`firma_key` değerini içermelidir (veya `*` olmalıdır). Yetkisiz rol, sahte
kimlik veya kısa/anlamsız gerekçe reddedilir ve kurtarma post edilmez. HTTP
katmanındaki `POST /api/recovery/<transaction_id>/authorize` rotası aynı
kontrolleri uygulayarak oturum bağlamını journal'a geçirir.

`recovery_dry_run()` SQLite'ı salt-okunur URI ile açar; şema oluşturmaz,
INSERT/UPDATE yapmaz, audit eklemez ve Excel/DB dosyalarını değiştirmez.

Yerel HTTP sunucusu başlarken aynı tarama otomatik çalışır ve yalnızca özet
loglanır. Yönetimsel dry-run için `recovery_dry_run()` doğrudan çağrılabilir;
bu mod hiçbir finansal yazma yapmaz.

- Excel ve SQLite birlikte mevcutsa kanıt doğrulanır ve journal tamamlanır.
- Excel mevcut, SQLite eksikse satırlar tekrar okunur ve SQLite postu idempotent
  biçimde tamamlanır.
- SQLite mevcut, Excel eksikse Excel otomatik yazılmaz; `NEEDS_REVIEW` verilir.
- Workbook satır parmak izi veya journal JSON'u bozuksa işlem kapatılmaz;
  kanıt korunarak `NEEDS_REVIEW` üretilir.
- Aynı `document_id` için veritabanı seviyesinde tekil indeks vardır; eşzamanlı
  farklı içerik hazırlama denemesi bütünlük çatışması verir.
- Kurtarma birden fazla kez veya eşzamanlı çalıştırılsa da benzersiz belge/fiş
  kısıtları nedeniyle ikinci finansal kayıt oluşmaz.

Audit yazımı başarısız olan tamamlanmış işlemler
`audit_repair_required=1` ile işaretlenir. `repair_audit()` yalnız eksik audit
kanıtını tamamlar; finansal fişleri yeniden post etmez.

## Kullanıcı/API durumları

- `COMMITTED`: İşlem başarıyla kaydedildi.
- `PENDING_RECOVERY`: İşlem tamamlanmadı; güvenli kurtarma bekliyor.
- `NEEDS_REVIEW`: Dış değişiklik veya belirsizlik var; otomatik işlem yapılmadı.
- `ABORTED`/`FAILED`: Finansal kayıt oluşmadı.

API yanıtları geriye uyumlu alanların yanında `transaction_id`, `status`,
`committed` ve `recovery_required` alanlarını taşır.

Başlangıç recovery taraması çalışmazsa veya pending/review/audit-onarım kayıtları
kalırsa `/api/health` `degraded` döner; endpoint her çağrıda journal'ı salt
okunur sorgulayarak güncel sayımları (`pending_recovery`, `needs_review`,
`audit_repair_required`) yeniler. Sistem bu durumu sağlıklı finansal görünüm gibi
gizlemez.

## Kapsam ve güvenlik

PR-03 testleri yalnız geçici klasörlerde sentetik Excel/SQLite kullanır. Canlı
`C:\ai-excel`, OneDrive workbook'ları, üretim DB'si, Telegram/banka/LLM ağı ve
üretim kimlik bilgileri kullanılmaz. Bağımsız recovery vault'u
`RECOVERY_RUNBOOK.md` ve `scripts/recovery_vault.py` ile uygulanır; geniş RBAC
çalışmaları PR-05 kapsamındadır. Vault üretim restore'u yapmaz, yalnız şifreli
kanıt snapshot'ı ve izole dry-run restore sağlar.
