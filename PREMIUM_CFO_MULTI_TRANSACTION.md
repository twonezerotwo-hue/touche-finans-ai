# Premium CFO — güvenli çoklu işlem akışı

Bir kullanıcı mesajında birden fazla ödeme, tahsilat veya tahakkuk bulunduğunda
mesaj `cfo_intelligence` tarafından bağımsız önerilere ayrılır. Öneriler
`cfo_batch` paketi altında saklanır; bu aşamada Excel, SQLite ve merkez defter
değişmez.

## Durumlar

- `PENDING`: satır incelenmeye hazırdır.
- `NEEDS_REVIEW`: mükerrer, belirsiz veya sihirbazın eksik alan sorusu vardır.
- `DRAFT_READY`: satır mevcut parola korumalı CFO taslağına dönüştürülmüştür.
- `REJECTED`: satır kullanıcı tarafından reddedilmiştir.

`DRAFT_READY` finansal kayıt anlamına gelmez. Her satırın son yazımı mevcut
`/api/belge/{token}/onay` rotasındaki parola, Excel doğrulaması ve atomik
finansal commit akışından geçer. Bir satırın onaylanması diğer satırları
otomatik olarak onaylamaz; kısmi başarı açıkça gösterilir.

## HTTP sözleşmesi

- `GET /api/cfo/batch/{batch_id}`: paketi getirir.
- `POST /api/cfo/batch/{batch_id}/{item_id}/hazirla`: tek satırı mevcut CFO
  taslağına hazırlar; finansal yazım yapmaz.
- `POST /api/cfo/batch/{batch_id}/{item_id}/duzelt` (`metin`): satırı yeniden
  deterministik olarak çözer ve mükerrer kontrolünü tekrar çalıştırır.
- `POST /api/cfo/batch/{batch_id}/{item_id}/reddet`: satırı reddeder.

Aynı kullanıcı ve aynı kaynak metin için tekrar gelen istek aynı `batch_id`
ile idempotent döner. Aynı tutar tek başına mükerrer sayılmaz; faaliyet,
proje, cari, işlem türü, para birimi ve tarih birlikte aynıysa `NEEDS_REVIEW`
oluşur.

## Güvenlik sınırı

Bu modül LLM çıktısını finansal yazara doğrudan bağlamaz. LLM veya serbest
metin yalnızca öneri üretir; doğrulanmış öneri mevcut CFO sihirbazına gider,
Excel/SQLite yazımı ise ayrı parola onayında yapılır. Eksik tarih, hesap veya
başka zorunlu alan varsa satır otomatik yazılmaz ve soru `question` alanında
tutulur.
