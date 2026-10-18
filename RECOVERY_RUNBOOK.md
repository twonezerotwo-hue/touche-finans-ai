# Recovery Vault Runbook (PR-04)

## Kapsam

`scripts/recovery_vault.py` bağımsız, şifreli bir kanıt snapshot'ı üretir.
Snapshot Excel master kopyasını, SQLite'ın `Connection.backup()` ile alınmış
online kopyasını ve istenirse audit/journal dosyalarını içerir. İçerik AES-256-GCM
ile şifrelenir; anahtar vault'a yazılmaz.

## Anahtar yönetimi

Üretim çağrısı anahtarı dış bir anahtar yöneticisinden 32 byte olarak almalıdır.
Yerel kontrollü kullanımda `TOUCHE_RECOVERY_VAULT_KEY` 64 karakter hex veya
URL-safe base64 olabilir. Anahtar loglanmaz, manifest'e yazılmaz ve vault içine
konmaz. Anahtar kaybolursa snapshot kurtarılamaz.

## Snapshot doğrulaması

Manifest her dosya için boyut ve SHA-256 tutar. GCM etiketi, başlık ve manifest
hash'i doğrulanmadan hiçbir dosya güvenilir kabul edilmez. Yanlış anahtar,
bozulmuş ciphertext veya değiştirilmiş manifest `VaultError` üretir.

## Güvenli restore

`restore_dry_run()` yalnızca yeni/boş bir izole hedefe açar; mevcut bir klasörü
veya dosyayı ezmez. Üretim Excel/SQLite yerine önce bu çıktı doğrulanmalı,
mutabakat ve audit kontrolü yapılmalıdır. Bu PR otomatik üretim restore'u
uygulamaz; üretim restore'u için ayrıca sahip/onay ve operasyon prosedürü gerekir.

## Retention

`prune_snapshots(directory, keep=N)` yalnız `.vault` dosyalarını siler ve en az
bir snapshot bırakmayı zorunlu kılar. Üretim retention görevi dış yedekleme
politikası ve erişim kontrolüyle birlikte çalıştırılmalıdır.

## Kanıt sınırları

Vault şifreli bütünlük ve geri açma kanıtı sağlar; finansal kayıtların anlamını
tek başına doğrulamaz. Restore sonrası Excel–SQLite mutabakatı, journal durumu,
audit zinciri ve yetkili manuel inceleme yine zorunludur.

PR-04 testleri yalnız sentetik Excel/SQLite/audit dosyalarıyla çalışır; canlı
OneDrive, `C:\ai-excel`, üretim veritabanı ve gerçek kimlik bilgileri kullanılmaz.
