# PR-05 Güvenlik ve RBAC İncelemesi

## Kimlik sınırı

Tailscale kimlik başlıkları yalnızca `TOUCHE_TRUSTED_PROXY_IPS` ile açıkça
tanımlanan güvenilir proxy eşlerinden kabul edilir. Varsayılan güven sınırı
`127.0.0.1/32,::1/128` ile sınırlıdır; doğrudan uzak bir istemci başlık ekleyerek
başka kullanıcı gibi davranamaz. Kimlik başlıklarında satır sonu ve çoklu değer
enjeksiyonu reddedilir.

Yerel yönetici oturumu yalnızca loopback istemci ve
`TOUCHE_LOCAL_ADMIN_HOSTS` allowlist'indeki Host ile oluşturulur. İleri proxy
kurulumunda proxy adresi ve host allowlist'i operasyon ortamında açıkça
belirlenmelidir; kullanıcıdan gelen `X-Forwarded-*` başlıkları kimlik kanıtı
olarak kullanılmaz.

## Rol ve kapsam

`OWNER` ve `CFO` tam master Excel dışa aktarımına yetkilidir. Diğer roller
raporları yalnızca kendi faaliyet/proje kapsamlarıyla alır; tam workbook yerine
scoped rapor uçları kullanılmalıdır. Bekleyen belge/taslak listeleri sahip veya
faaliyet kapsamıyla filtrelenir ve onay/düzeltme/reddetme işleminden önce aynı
kapsam yeniden doğrulanır.

Recovery `NEEDS_REVIEW` kararı ayrı `recovery.authorize` iznidir; yalnız
`OWNER`/`CFO` rolü taşır. Karar gerekçesi ve yönetici kimliği audit zincirine
yazılır; istemcinin gönderdiği kimlik tek başına kanıt kabul edilmez.

## HTTP kontrolleri

- JSON ve multipart POST'lar yetkili oturum ve Origin kontrolünden geçer.
- Güvenlik başlıkları (`nosniff`, `DENY`, `no-referrer`, CSP) korunur.
- Dosya yükleme adı, uzantı ve MIME imzası doğrulanır; belge dosyası dışına
  çıkılamaz.
- Tam workbook export'u audit olayına (`MASTER_WORKBOOK_EXPORTED`) yazılır.
- Yetkisiz kapsam yanıtları finansal satır veya belge içeriği sızdırmaz.

## Bilinen sınırlar

Yerel yönetici güveni işletim sistemi/servis hesabı kimlik doğrulamasını
uygulamaz; HTTP servisi loopback'e bağlanmalı veya güvenilir reverse proxy
arkasında çalışmalıdır. Üretim erişiminde TLS, proxy allowlist'i, OS hesabı ve
ayrı yönetici oturumu birlikte zorunlu tutulmalıdır. Bu PR canlı sistemlere
erişmez ve deployment yapmaz.

## Kanıt

PR-05 testleri yalnız sentetik HTTP istekleri ve geçici dosyalar kullanır:
uzak başlık spoofing'i, explicit trusted proxy, localhost Host allowlist'i,
role recovery sınırı, tam Excel export yetkisi ve belge taslağı faaliyet kapsamı.

## Son doğrulama sınırı

PR-06 ile yetkili kapsam kontrolü, kilitli çalışma kitabında degraded sağlık,
idempotent posting ve şifreli snapshot geri açma sentetik olarak kanıtlandı.
Tarayıcı bağımlılığı bulunmayan yerel ortamda 7 Chromium testi atlandı; bu nedenle
UI kabulü ve gerçek reverse-proxy/OneDrive davranışı üretim için ayrıca doğrulanmalıdır.
