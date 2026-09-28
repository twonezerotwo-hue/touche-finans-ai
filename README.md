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
- Güvenli Excel yedekleme ve atomik kayıt
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

Yerel Excel ve çalışma klasörü yolları `scripts/config.py` içinden yönetilir. Telegram bot anahtarı kaynak koda yazılmaz; Windows ortam değişkeni veya kullanıcı kayıt alanından okunur.

## Güvenlik

Excel dosyaları, finans veritabanı, yüklenen belgeler, loglar, yedekler ve kullanıcı yetki dosyaları `.gitignore` ile Git deposunun dışında tutulur. Bu depo yalnızca kaynak kodu, testleri ve güvenli şablon tanımlarını içerir.

## Test

```powershell
py -3.12 -m unittest discover -s tests -v
```

Finans asistanı para transferi yapmaz. Öneriler kayıtlı verilere dayalı karar desteğidir.
