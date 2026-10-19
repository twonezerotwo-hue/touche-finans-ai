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

## PR-06 işletim kanıtı

Uçtan uca kabul testi, onaylı bir sentetik finans kaydından vault snapshot
oluşturur ve yalnız boş bir hedefe `restore_dry_run()` ile geri açar. SQLite
online backup, manifest SHA-256 ve AES-256-GCM doğrulaması geçmeden restore
başarılı sayılmaz. Bu kanıt üretim restore'u veya ölçülmüş RPO/RTO iddiası değildir.

## PR-09 sentetik disaster-recovery drill

`tests/test_final_release_readiness.py` son on finansal işlem içeren sentetik
Excel + SQLite + audit + journal ortamı oluşturur, AES-256-GCM snapshot alır,
ortamı yeni boş bir klasöre geri açar ve manifest/SHA-256, SQLite
`integrity_check`, işlem ve fiş satırı sayısı, son belge kimliği, Excel sayfa
sözleşmesi, audit/journal içeriği, ikinci restore ve kaynak hash'lerini kontrol
eder. Snapshot son işlemden sonra alındığı için bu drill'deki sentetik veri
kaybı `RPO=0 transaction`; süreler test çıktısında saniye olarak yazdırılır ve
üretime genellenmez.

Yanlış/eksik anahtar, bozuk ciphertext, mevcut hedefi ezme ve başarısız atomic
replace senaryoları fail-closed olarak doğrulanır. `NEEDS_REVIEW` işlemleri
otomatik çalıştırılmaz; OWNER/CFO rolü, faaliyet kapsamı, anlamlı karar nedeni
ve audit kimliği olmadan manuel kurtarma reddedilir. Audit onarımı finansal fişi
tekrar post etmez.

## Önerilen operasyon politikası (onay bekliyor)

Bu varsayılanlar henüz üretimde etkinleştirilmemiştir ve sahibin yazılı onayını
gerektirir:

- Snapshot: her başarılı finansal commit sonrası; ayrıca günde en az bir kez.
- Retention: günlük 30, haftalık 12, aylık 12 recovery noktası.
- Anahtar: üretim dışı KMS/secret manager, iki yetkili escrow ve 90 günde rotasyon.
- Doğrulama: her snapshot manifest/hash kontrolü; aylık sentetik restore drill'i.
- RPO/RTO hedefi: operasyon sahibi onayına kadar öneri olarak `RPO ≤ 1 işlem`,
  `RTO ≤ 30 dakika`; bu test ölçümü garanti değildir.
- Uyarı: snapshot başarısızlığı, audit backlog, `PENDING_RECOVERY` ve
  `NEEDS_REVIEW` için alarm; sorumlu rol CFO + sistem yöneticisi.
- Off-site: gerçek sağlayıcı ve hesap henüz yapılandırılmadı; **NOT VERIFIED**.

Üretim anahtarı etkinleştirme, retention scheduler'ı, off-site hesap ve
restore onayı uygulanmadı; bunlar kontrollü dağıtım ön koşuludur.

## Final operational closure (synthetic, 2026-10-19)

`recovery_offsite.LocalOffsiteStore` yalnız AES-256-GCM ile üretilmiş `.vault`
dosyalarını kabul eden, checksum doğrulayan ve boş hedefe indiren provider-neutral
bir emulator'dur. Gerçek bulut hesabı/credential içermez. Yükleme, indirme,
manifest bozulması, tekrar sürüm, retention ve mevcut hedefi ezmeme testleri
geçmiştir. Gerçek off-site sağlayıcı **NOT VERIFIED**.

Migration provası, eski sentetik `finans_islemleri` tablosundaki kayıtları iki
kez schema hazırlama sonrasında korudu; duplicate `document_id` durumunda unique
index migration'ı fail-closed kaldı ve satırlar silinmedi. Bu, canlı migration
ve rollback onayı değildir.

Üretim öncesi manuel kararlar: KMS/escrow sahibi, retention ve snapshot scheduler
onayı, off-site hesap kurulumu, restore yetkilisi, gerçek Windows/Excel ve
OneDrive test ortamı. Hiçbiri bu çalışmada etkinleştirilmedi.
