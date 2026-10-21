# İzole yerel demo

Bu demo yalnızca sentetik Excel ve SQLite oluşturur. `C:\ai-excel`, OneDrive,
gerçek workbook, üretim kimliği ve üretim servisi okunmaz. Demo kökü boş ve
üretim dışı olmak zorundadır.

## Başlatma

PowerShell:

```powershell
cd C:\Temp\touche-pr20-20261021
$root = "C:\Temp\touche-finans-demo-$(Get-Date -Format yyyyMMdd-HHmmss)"
$p = Start-Process py -ArgumentList "-3.12 scripts\demo_local.py --root `"$root`"" -WorkingDirectory (Get-Location) -PassThru -WindowStyle Hidden -RedirectStandardOutput "$root.stdout.log" -RedirectStandardError "$root.stderr.log"
```

Çıktıdaki `DEMO_ROOT`, `DEMO_MAIN`, `DEMO_BACKUP` ve URL yalnızca bu demo
çalışmasına aittir. Başlatma sonrası `http://127.0.0.1:<port>/api/health` ve
`/api/session` ile kontrol edilir. Port, stdout içindeki `Touche Finans
Asistanı:` satırından alınır.

## Durdurma

Yalnızca demo PID'sini durdurun:

```powershell
Stop-Process -Id $p.Id
```

Üretim servisini aramak, durdurmak veya yeniden başlatmak bu prosedürün
kapsamında değildir.

## Sentetik smoke senaryosu

1. `/api/health` `yerel=true` ve `durum=ok`/`degraded` döndürür.
2. `/api/chat` ile iki veya daha fazla bağımsız sentetik kayıt gönderilir.
3. Yanıtta `cfo_batch.batch_id` ve satırların `PENDING` olduğu görülür.
4. Bir satır `/api/cfo/batch/{batch}/{item}/hazirla` ile mevcut şifreli taslağa
   alınır; Excel/SQLite bu adımda değişmez.
5. Son yazım yalnızca mevcut parola onay rotasında yapılır.

Bu ölçüm `LOCAL_DEMO_READY` kanıtıdır; OneDrive senkronizasyonu, gerçek Ollama
üretim trafiği ve gerçek finans dosyaları için üretim doğrulaması değildir.
