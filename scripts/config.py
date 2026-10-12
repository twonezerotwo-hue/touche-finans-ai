# -*- coding: utf-8 -*-
"""
Touche Holding + Faaliyet Sirketleri icin AI belge isleme sistemi.
Sistem geneli yapilandirma.
"""

from pathlib import Path
import json
import os
from copy import deepcopy
from typing import Any
from faaliyet_sema import firma_tanimi

# === KLASOR YAPISI ===
# Uretim (Windows) varsayilani C:\ai-excel'dir. Baska bir ortamda (gelistirme,
# test, CI) TOUCHE_BASE_DIR ile degistirilir; ayarlanmazsa depo klasoru kullanilir.
# Boylece kod Windows disinda da ice aktarilabilir ve testler uretim dosyasina
# dokunmadan izole bir klasorde calisir.
_DEPO_KOKU = Path(__file__).resolve().parent.parent
_VARSAYILAN_BASE = Path(r"C:\ai-excel") if os.name == "nt" else _DEPO_KOKU
BASE_DIR = Path(os.environ.get("TOUCHE_BASE_DIR") or _VARSAYILAN_BASE)


def _yerel_env_yukle(env_yolu):
    """Git'e girmeyen yerel .env ayarlarını mevcut ortamı ezmeden yükler."""
    if not env_yolu.exists():
        return
    for ham_satir in env_yolu.read_text(encoding="utf-8").splitlines():
        satir = ham_satir.strip()
        if not satir or satir.startswith("#") or "=" not in satir:
            continue
        anahtar, deger = satir.split("=", 1)
        anahtar, deger = anahtar.strip(), deger.strip().strip('"').strip("'")
        if anahtar:
            os.environ.setdefault(anahtar, deger)


_yerel_env_yukle(BASE_DIR / ".env")
SCRIPTS_DIR = BASE_DIR / "scripts"
FIRMALAR_DIR = BASE_DIR / "firmalar"
GELEN_EVRAK = BASE_DIR / "gelen_evrak"
ARSIV = BASE_DIR / "arsiv"
ARSIV_HATA = BASE_DIR / "arsiv" / "hata"
LOGLAR = BASE_DIR / "loglar"
AUDIT_LOG = LOGLAR / "audit.jsonl"
TELEGRAM_STATE = LOGLAR / "telegram_state.json"
TELEGRAM_OWNER_FILE = LOGLAR / 'telegram_owner.json'
TELEGRAM_PAIRING_CODE = LOGLAR / 'telegram_eslestirme.txt'
TEMP_DIR = BASE_DIR / "temp"
VERI_DIR = BASE_DIR / "veri"
FINANS_DB = VERI_DIR / "finans.db"
ONAY_PAROLASI_DOSYASI = VERI_DIR / "onay_parolasi.json"
MUHASEBE_EXPORT_DIR = BASE_DIR / "muhasebe_export"
EXCEL_INDEX_DIR = VERI_DIR / "excel_index"
RECETE_DIR = BASE_DIR / "receteler"
FAALIYET_REGISTRY = FIRMALAR_DIR / 'faaliyetler.json'

# === SMART EXCEL: TEK ANA DOSYA + TEK YEDEK ===
# Calisma zamaninda finansal Excel yalniz iki dosyadan olusur:
#   MAIN_WORKBOOK_PATH   -> canli Touche Holding Smart Excel
#   BACKUP_WORKBOOK_PATH -> bir onceki surum (her onayli yazmadan once MAIN ile yenilenir)
# Tarihli/islem basina yedek, yedek klasoru veya ikinci bir kaynak dosya YOKTUR.
# Tum calisma zamani Excel kodu bu iki yolu ana_excel_yolu()/yedek_excel_yolu()
# uzerinden cozer. Testler TOUCHE_MAIN_WORKBOOK / TOUCHE_BACKUP_WORKBOOK ile
# gecici dosyalara yonlendirir; uretim dosyasina dokunulmaz.
_ANA_EXCEL_ADI = "Touche Holding - AI Ready Finans Yönetim Sistemi.xlsx"
# Uretim (Windows) varsayilani kullanicinin OneDrive masaustundeki mevcut dosyadir; kullanici adi kaynak
# koda yazilmaz (Path.home()). Farkliysa TOUCHE_MAIN_WORKBOOK ile (ortam degiskeni veya .env) belirtilir.
_URETIM_KLASORU = Path.home() / "OneDrive" / "Desktop" / "Touche"


def varsayilan_yedek_yolu(ana) -> Path:
    """Tek YEDEK Excel: ANA ile ayni klasorde, ``<ana adi>_backup.xlsx``."""
    ana = Path(ana)
    return ana.with_name(f"{ana.stem}_backup{ana.suffix}")


MAIN_WORKBOOK_PATH = Path(
    os.environ.get("TOUCHE_MAIN_WORKBOOK")
    or (_URETIM_KLASORU / _ANA_EXCEL_ADI if os.name == "nt" else VERI_DIR / _ANA_EXCEL_ADI)
)
BACKUP_WORKBOOK_PATH = Path(
    os.environ.get("TOUCHE_BACKUP_WORKBOOK") or varsayilan_yedek_yolu(MAIN_WORKBOOK_PATH)
)
# Geriye uyumluluk: eski kod/testler MASTER_EXCEL adini kullanir. Yeni kod
# ana_excel_yolu() kullanmalidir.
MASTER_EXCEL = MAIN_WORKBOOK_PATH
# Yalniz envanter/operasyon araclari icin: gecmiste kullanilan eski Gursar dosyasi.
# Calisma zamaninda ASLA okunmaz veya yazilmaz.
LEGACY_GURSAR_EXCEL = _URETIM_KLASORU / "Gürsar Touche" / "Gürsar Hesap .xlsx"
INVOICE2DATA_SABLON_DIR = BASE_DIR / "sablonlar" / "invoice2data"

# 03_Kayit Merkezi'nde formul sablonunun hazir oldugu son satir. Bunun otesine
# yazilirsa satirda formuller olmaz; bu yuzden yazma burada durur ve kapasite uyarisi verir.
LEDGER_SON_SATIR = int(os.environ.get("TOUCHE_LEDGER_SON_SATIR", "300"))


def ana_excel_yolu() -> Path:
    """Canli (MAIN) Smart Excel yolu. Calisma zamaninda tek dogru kaynak."""
    return Path(MAIN_WORKBOOK_PATH)


def yedek_excel_yolu() -> Path:
    """Tek yedek (BACKUP) Smart Excel yolu."""
    return Path(BACKUP_WORKBOOK_PATH)


def ana_excel_ayarla(ana, yedek=None):
    """Ana/yedek yolunu calisma zamaninda degistirir (yalniz test ve operasyon araclari).

    FIRMALAR icindeki geriye uyumlu 'excel' alanlari da ayni dosyaya yonlendirilir;
    boylece tek bir yol tanimi korunur.
    """
    global MAIN_WORKBOOK_PATH, BACKUP_WORKBOOK_PATH, MASTER_EXCEL
    MAIN_WORKBOOK_PATH = Path(ana)
    BACKUP_WORKBOOK_PATH = Path(yedek) if yedek else varsayilan_yedek_yolu(MAIN_WORKBOOK_PATH)
    MASTER_EXCEL = MAIN_WORKBOOK_PATH
    for firma in FIRMALAR.values():
        if "excel" in firma:
            firma["excel"] = str(MAIN_WORKBOOK_PATH)


# Guvenli kayit/arsiv politikalari
VALIDATION_PASS_SCORE = 85
VALIDATION_REVIEW_SCORE = 60
TELEGRAM_MAX_DOSYA_MB = 20
PDF_MAX_SAYFA = 3
INVOICE2DATA_ETKIN = True
WITAN_REMOTE_ENABLED = os.environ.get("WITAN_REMOTE_ENABLED", "").strip().lower() in {"1", "true", "evet"}

# === OLLAMA ===
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5vl:7b"
TEXT_MODEL = os.environ.get("OLLAMA_TEXT_MODEL", "qwen3:8b")
TEXT_LLM_PROVIDER = os.environ.get("TEXT_LLM_PROVIDER", "ollama").strip().lower()
EVREN_API_KEY = os.environ.get("EVREN_API_KEY", "").strip()
EVREN_API_BASE = os.environ.get("EVREN_API_BASE", "https://evren-llmapi.ssyz.org.tr/v1").rstrip("/")
EVREN_MODEL = os.environ.get("EVREN_MODEL", "auto").strip() or "auto"
TIMEOUT = 180

# === GORSEL ON ISLEME ===
MAX_IMAGE_SIZE = 1600

# === GENEL AYARLAR ===
MAX_RETRY = 1

# === FIRMA TANIMLARI ===
# Tek finansal kaynak MASTER_EXCEL'dir. Faaliyetler ayrı dosya değil, master içindeki kayıtlardır.
FIRMALAR: dict[str, dict[str, Any]] = {
    "touche_holding": {
        "ad": "Touche Holding",
        "excel": str(MASTER_EXCEL),
        "ana_ledger": {
            "sayfa": "03_Kayıt Merkezi",
            "baslik_satiri": 5,
            "ilk_veri_satiri": 6,
            "sutunlar": {
                "Kayit_ID":           "A",
                "Tarih":              "B",
                "Faaliyet":           "C",
                "Proje":              "D",
                "Cari_Tipi":          "E",
                "Cari":               "F",
                "Islem_Turu":         "G",
                "Hesap_Kasa":         "H",
                "Tutar":              "I",
                "PB":                 "J",
                "Kur":                "K",
                "Ortak_IC_Cari":      "S",
                "Baglanti_ID":        "T",
                "Vade":               "U",
                "Durum":              "V",
                "Belge_Referans":     "W",
                "Aciklama":           "X",
                "Kaynak_Belge_ID":    "Z",
                "Kayit_Kaynak":       "AA",
                "AI_Guven":           "AB",
                "Onay_Durumu":        "AC",
            },
        },
        "ai_inbox": {
            "sayfa": "05_AI Belge Inbox",
            "baslik_satiri": 6,
            "ilk_veri_satiri": 7,
            "sutunlar": {
                "Belge_ID":           "A",
                "Yukleme_Tarihi":     "B",
                "Kaynak":             "C",
                "Dosya_Metin_Ref":    "D",
                "AI_Faaliyet":        "E",
                "AI_Proje":           "F",
                "AI_Cari_Tipi":       "G",
                "AI_Cari":            "H",
                "AI_Islem_Turu":      "I",
                "Tutar":              "J",
                "PB":                 "K",
                "Belge_Tarihi":       "L",
                "Vade":               "M",
                "AI_Guven":           "N",
                "Kullanici_Onayi":    "O",
                "Hedef_Kayit_ID":     "P",
                "Durum_Kontrol":      "Q",
                "AI_Aciklama":        "R",
                "Belge_Hash":         "S",
            },
        },
        "master_data": {
            "sayfa": "99_Ayarlar",
            "faaliyetler": {"baslik_satiri": 12, "ilk_veri_satiri": 13, "sutun": "A"},
            "projeler":    {"baslik_satiri": 12, "ilk_veri_satiri": 13, "sutun": "I"},
            "hesaplar":    {"baslik_satiri": 12, "ilk_veri_satiri": 13, "sutun": "M"},
            "cari_tipler": {"baslik_satiri": 12, "ilk_veri_satiri": 13, "sutun": "AH"},
            "cari_listesi":{"baslik_satiri": 12, "ilk_veri_satiri": 13, "sutun": "AI"},
            "islem_turleri": {"baslik_satiri": 5, "ilk_veri_satiri": 6, "sutun": "T"},
            "kurlar":      {"baslik_satiri": 2, "ilk_veri_satiri": 3, "sutun": "AD"},
        },
    },

    "gursar_mimarlik": {
        "ad": "Gürsar Mimarlık",
        "faaliyet_aliaslari": ["Gürsar İnşaat"],
        "excel": str(MASTER_EXCEL),
        "ana_ledger": {
            "sayfa": "Ödeme Geçmişi",
            "baslik_satiri": 5,
            "ilk_veri_satiri": 6,
            "sutunlar": {
                "Kayit_ID":           "A",
                "Tarih":              "B",
                "Saat":               "C",
                "Is_Kalemi":          "D",
                "Alici":              "E",
                "Islem_Turu":         "F",
                "Tutar_TL":           "G",
                "Banka_Masrafi":      "H",
                "Islem_Kanali":       "J",
                "FAST_Sorgu_No":      "K",
                "Kaynak_Belge":       "L",
                "Durum":              "M",
                "Proje":              "N",
                "Maliyet_Grubu":      "O",
            },
        },
        "holding_ozeti": {
            "sayfa": "Holding_Ozeti",
            "baslik_satiri": 1,
            "ilk_veri_satiri": 2,
            "sutunlar": {
                "Donem":              "A",
                "Faaliyet":           "B",
                "Ciro":               "C",
                "Gider":              "D",
                "Net_Sonuc":          "E",
                "Holding_Atfedilen":  "F",
                "Gercek_Tahsilat":    "G",
                "Nakit_Cikisi":       "H",
                "Likit_Nakit":        "I",
                "Acik_Alacak":        "J",
                "Cek_Portfoyu":       "K",
                "Toplam_Tahsil":      "L",
                "Operasyonel_Borc":   "M",
                "Kredi_Borcu":        "N",
                "Son_Guncelleme":     "O",
            },
        },
        "master_data": {
            "sayfa": "Dashboard",
            "projeler": {
                "kaynak": "Proje Ciro",
                "baslik_satiri": 9,
                "ilk_veri_satiri": 10,
                "sutun": "A",
            },
            "cariler": {
                "kaynak": "Ödeme Geçmişi",
                "baslik_satiri": 5,
                "ilk_veri_satiri": 6,
                "sutun": "E",
            },
            "islem_turleri": [
                "Usta ödemesi",
                "Ortak cari ödemesi",
                "Banka masrafı",
                "Diğer",
            ],
            "maliyet_gruplari": [
                "Taş işleri",
                "Saha betonu",
                "Ortak cari",
                "Diğer",
            ],
        },
    },
}


def master_firma_tanimi(kayit):
    """Faaliyet tanımını tek master Excel sözleşmesine bağlar."""
    tanim = firma_tanimi(kayit)
    holding = FIRMALAR["touche_holding"]
    tanim["excel"] = holding["excel"]
    tanim["ana_ledger"] = deepcopy(holding["ana_ledger"])
    tanim["ai_inbox"] = deepcopy(holding["ai_inbox"])
    tanim["master_data"] = deepcopy(holding["master_data"])
    tanim["excel_mode"] = "master"
    return tanim


# Gürsar'ın eski dosyası yalnız miras/arşiv kaynağıdır; canlı okuma-yazma master'dadır.
FIRMALAR["gursar_mimarlik"]["legacy_ana_ledger"] = deepcopy(FIRMALAR["gursar_mimarlik"]["ana_ledger"])
FIRMALAR["gursar_mimarlik"]["ana_ledger"] = deepcopy(FIRMALAR["touche_holding"]["ana_ledger"])
FIRMALAR["gursar_mimarlik"]["ai_inbox"] = deepcopy(FIRMALAR["touche_holding"]["ai_inbox"])
FIRMALAR["gursar_mimarlik"]["master_data"] = deepcopy(FIRMALAR["touche_holding"]["master_data"])
FIRMALAR["gursar_mimarlik"]["master_data"]["maliyet_gruplari"] = [
    "Taş işleri", "Saha betonu", "Ortak cari", "Diğer"
]
FIRMALAR["gursar_mimarlik"]["excel_mode"] = "master"

# === VARSAYILAN FIRMA (belge taninamazsa) ===
def faaliyetleri_yukle():
    if FAALIYET_REGISTRY.exists():
        veri = json.loads(FAALIYET_REGISTRY.read_text(encoding='utf-8'))
        if veri.get('surum') != 1:
            raise ValueError('Faaliyet kayit dosyasi surumu desteklenmiyor')
        for key, kayit in veri.get('faaliyetler', {}).items():
            if key in {'touche_holding', 'gursar_mimarlik'}:
                raise ValueError('Mevcut firma tanimi registry ile degistirilemez')
            FIRMALAR[key] = master_firma_tanimi(kayit)

faaliyetleri_yukle()

VARSAYILAN_FIRMA = "touche_holding"

# === FIRMA TANIMA ANAHTAR KELIMELERI ===
# Belge metninde bu kelimeler gecerse ilgili firmaya yonlendirilir
FIRMA_ANAHTARLARI = {
    "gursar_mimarlik": [
        "gürsar", "gursar", "gürsar mimarlık", "gursar mimarlik",
        "gürsar inşaat", "gursar insaat",
        "göktürk okulu", "gokturk okulu",
        "sebahattin çakmak", "sebahattin cakmak",
        "medet i̇me", "medet ime",
        "dinçerler yapı", "dincerler yapi",
    ],
    "touche_holding": [
        "touche holding", "touche",
        "holding",
    ],
}

# === DÜZELTME: Touche Holding master data sütunları ===
# 99_Ayarlar'da doğru yerler:
# - Faaliyetler: A13:A32
# - Projeler: I13:I32 (H faaliyet, I proje)
# - Hesaplar: M13:M62 (K hesap_id, L faaliyet, M hesap)
# - Cari tipleri: AH13:AH112
# - Cari listesi: AI13:AI112
# - İşlem türleri: T6:T22
# - Kurlar: AD3 (PB), AE3 (Kur)
