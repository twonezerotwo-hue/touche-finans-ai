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
6. SQLite fişlerinin idempotent post edilmesi.
7. DB kimlikleri ve Excel/DB hedef tutarlılığının doğrulanması.
8. `DB_POSTED`, ardından `COMMITTED` geçişi ve tamamlanmış audit olayı.

SQLite başarısız olursa işlem `PENDING_RECOVERY` olur; kullanıcıya başarı
mesajı verilmez ve aynı ödemenin tekrar oluşturulmaması söylenir. Excel kilidi
veya dış değişiklik `NEEDS_REVIEW`/yeniden denenebilir pending sonucu üretir.

## Kurtarma

`finans_islem_journali.recover_pending_transactions()` başlangıçta terminal
olmayan işlemleri tarar. `recovery_dry_run()` hiçbir dosya veya DB verisi
değiştirmeden önerilen sonucu döndürür.

Yerel HTTP sunucusu başlarken aynı tarama otomatik çalışır ve yalnızca özet
loglanır. Yönetimsel dry-run için `recovery_dry_run()` doğrudan çağrılabilir;
bu mod hiçbir finansal yazma yapmaz.

- Excel ve SQLite birlikte mevcutsa kanıt doğrulanır ve journal tamamlanır.
- Excel mevcut, SQLite eksikse satırlar tekrar okunur ve SQLite postu idempotent
  biçimde tamamlanır.
- SQLite mevcut, Excel eksikse Excel otomatik yazılmaz; `NEEDS_REVIEW` verilir.
- Workbook satır parmak izi veya journal JSON'u bozuksa işlem kapatılmaz;
  kanıt korunarak `NEEDS_REVIEW` üretilir.
- Kurtarma birden fazla kez veya eşzamanlı çalıştırılsa da benzersiz belge/fiş
  kısıtları nedeniyle ikinci finansal kayıt oluşmaz.

## Kullanıcı/API durumları

- `COMMITTED`: İşlem başarıyla kaydedildi.
- `PENDING_RECOVERY`: İşlem tamamlanmadı; güvenli kurtarma bekliyor.
- `NEEDS_REVIEW`: Dış değişiklik veya belirsizlik var; otomatik işlem yapılmadı.
- `ABORTED`/`FAILED`: Finansal kayıt oluşmadı.

API yanıtları geriye uyumlu alanların yanında `transaction_id`, `status`,
`committed` ve `recovery_required` alanlarını taşır.

## Kapsam ve güvenlik

PR-03 testleri yalnız geçici klasörlerde sentetik Excel/SQLite kullanır. Canlı
`C:\ai-excel`, OneDrive workbook'ları, üretim DB'si, Telegram/banka/LLM ağı ve
üretim kimlik bilgileri kullanılmaz. Bağımsız recovery vault'u ve geniş RBAC
çalışmaları PR-04/PR-05 kapsamındadır.
