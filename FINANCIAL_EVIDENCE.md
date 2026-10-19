# Finansal Kanıt ve Metrik Açıklama Sözleşmesi

PR-13, dashboard'da görünen sayının hangi defter kayıtlarından oluştuğunu
salt-okunur olarak gösterir. Hesaplama yine `proje_finansi` içindeki
deterministik motorla yapılır; tarayıcı veya LLM SQL çalıştıramaz ve kaynak
kimliği üretemez.

## API

Yetkili bir oturumda şu uç çağrılabilir:

`GET /api/dashboard/provenance?metric=payments&faaliyet=gursar_mimarlik&proje=Göktürk%20Okulu&page=1&page_size=25`

`metric` yalnızca sözlükteki kimliklerden biridir: `revenue`, `expenses`,
`cash_balance`, `receivables`, `payables`, `profit`, `payments`, `collections`,
`net_cash`, `loans`. Faaliyet ve proje kapsamı sunucu tarafında oturum yetkisi
ile sınırlandırılır. `page_size` 1–100 arasına güvenli biçimde kırpılır.

Yanıt; `metric_id`, görünen ad, TL değeri, dönem/kapsam, formül, hesaplama
zamanı, kaynak işlem sayısı, sayfa bilgisi ve her kaynak için defter fişi,
Excel belge kimliği, tarih, cari, proje, para birimi ve TL dönüşüm kurunu
içerir.

## Doğrulama durumları

- `CONSISTENT`: mevcut kanıtlar ve hesaplama birlikte okunabildi.
- `INCONSISTENT`: Excel–SQLite mutabakatında eksik kayıt veya tutar farkı var.
  Fark ayrıntısı gösterilir; otomatik Excel/SQLite düzeltmesi yapılmaz.
- `STALE`: kanıtın son doğrulama zamanı güncel değil.
- `PARTIALLY_VERIFIED`: yalnızca kanıtın bir kısmı okunabildi.
- `NO_EVIDENCE`: güvenilir kaynak işlem yok.

Uyarı durumları metrik değerini sağlıklıymış gibi göstermemek için yanıtın
`warning` alanında görünür. Kaynak listesi sayfalıdır; sayfa dışındaki kayıtlar
özet toplamın parçası olmaya devam eder.

## Arayüz

Holding, faaliyet ve proje metrik kartlarında **Kaynağı gör** düğmesi formülü,
mutabakat durumunu ve kaynak fişlerini açar. Bu etkileşim salt-okunurdur ve
hiçbir onay, Excel yazımı veya finansal kayıt oluşturmaz.

## Sınırlar

Merkezi defterde TL'ye normalize edilen tutarlar TL olarak raporlanır; kaynak
para birimi ve kullanılan kur varsa kanıt satırında korunur. Gerçek OneDrive
senkronizasyon davranışı bu sentetik test kapsamının dışındadır.
