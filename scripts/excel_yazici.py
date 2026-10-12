# -*- coding: utf-8 -*-
"""Smart Excel'e AI kaydi yazma (v3).

Bu modul YALNIZ satir/sutun sozlesmesini bilir; dosya guvenligi (tek ANA + tek YEDEK,
gecici dosya, atomik yerlestirme, formul/veri koruma, denetim) ``excel_deposu``
modulundedir. Burada dosyaya dogrudan ``save``/``replace``/kopya YAPILMAZ.
"""

import hashlib
import logging
import unicodedata
from datetime import datetime

import config
from config import FIRMALAR
from excel_deposu import DepoHatasi, guvenli_degistir
from master_data import (
    GURSAR_SISTEM_HESAPLARI,
    SISTEM_ISLEM_ETKILERI,
    SISTEM_ISLEM_TURLERI,
)

log = logging.getLogger(__name__)

AI_GIRIS_SON_SATIR = 500        # 05_AI Belge Inbox tarama siniri
AYARLAR_SAYFASI = "99_Ayarlar"
# Bir ledger satirinin bos/dolu sayilmasini belirleyen kimlik sutunlari.
LEDGER_KIMLIK_ALANLARI = ("Kayit_ID", "Tarih", "Faaliyet", "Cari", "Islem_Turu", "Tutar")


def _norm(value):
    metin = unicodedata.normalize("NFKD", str(value or "").casefold().replace("ı", "i"))
    return "".join(ch for ch in metin if not unicodedata.combining(ch)).strip()


# ============================================================
# YARDIMCILAR
# ============================================================

def _bos_mu(deger):
    return deger is None or (isinstance(deger, str) and not deger.strip())


def _formul_mu(hucre):
    return hucre.data_type == "f"


def _hucre_yaz(ws, adres, deger):
    """Metni her zaman metin olarak yazar (``=``/``+`` ile baslayan metin formul olmaz)."""
    hucre = ws[adres]
    hucre.value = deger
    if isinstance(deger, str):
        hucre.data_type = "s"
    return hucre


def _tarih_yaz(ws, adres, deger):
    hucre = _hucre_yaz(ws, adres, deger)
    if isinstance(deger, datetime) and hucre.number_format == "General":
        hucre.number_format = r"dd\.mm\.yyyy"
    return hucre


def _excel_tarihi(value, zorunlu=False):
    """Metin/ISO tarihini datetime'a cevirir; cevrilemezse ``zorunlu`` ise hata verir."""
    if isinstance(value, datetime):
        return value
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return datetime(value.year, value.month, value.day)
    metin = str(value or "").strip()
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(metin, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(metin)
    except ValueError:
        pass
    if zorunlu:
        raise ValueError(f"Geçersiz tarih: {value!r}")
    return value


def _mukerrer_mi(ws, deger, sutun, ilk_satir, son_satir):
    if not deger:
        return False
    aranan = str(deger).strip()
    for satir in range(ilk_satir, son_satir + 1):
        h = ws[f"{sutun}{satir}"]
        if h.value and str(h.value).strip() == aranan:
            return True
    return False


def _satir_no(koordinat):
    return int("".join(ch for ch in koordinat if ch.isdigit()))


def _bos_satirlar(ws, sutunlar, kimlik_alanlari, yazilacak_alanlar, ilk_satir, son_satir):
    """Yazmak icin GUVENLI satirlari dondurur.

    Bir satir ancak kimlik sutunlarinin (kayit no, tarih, cari, tutar...) hicbirinde
    formul olmayan bir deger yoksa VE yazacagimiz sutunlarin hicbirinde formul yoksa
    uygundur. Kimlik sutunlarindan bazilari dolu ama kayit no bos olan KISMEN DOLU
    satirlar ATLANIR; uzerlerine yazilmaz.
    Donus: (uygun_satirlar, atlanan_kismi_dolu_satirlar)
    """
    uygun, kismi = [], []
    kimlik = [sutunlar[a] for a in kimlik_alanlari if a in sutunlar]
    yazilacak = {sutunlar[a] for a in yazilacak_alanlar if a in sutunlar}
    for satir in range(ilk_satir, son_satir + 1):
        formullu_hedef = any(_formul_mu(ws[f"{c}{satir}"]) for c in yazilacak)
        if formullu_hedef:
            continue
        dolu = [c for c in kimlik
                if not _bos_mu(ws[f"{c}{satir}"].value) and not _formul_mu(ws[f"{c}{satir}"])]
        if not dolu:
            uygun.append(satir)
        elif len(dolu) < len(kimlik):
            kismi.append(satir)
    return uygun, kismi


def _kayit_id(onek, kaynak_id):
    """Ayni kaynak kimligi her zaman ayni Kayit_ID'yi uretir (deterministik)."""
    ozet = hashlib.sha256(str(kaynak_id).encode("utf-8")).hexdigest()[:12].upper()
    return f"{onek}-{ozet}"


# ============================================================
# ANA YAZMA FONKSIYONU
# ============================================================

def _kayit_ozeti(kayit, kayit_id="", kaynak_id=""):
    return {
        "transaction_id": kayit_id, "kaynak_belge_id": kaynak_id,
        "faaliyet": kayit.get("Faaliyet"), "proje": kayit.get("Proje"),
        "islem_turu": kayit.get("Islem_Turu"), "cari": kayit.get("Cari"),
        "hesap": kayit.get("Hesap_Kasa"), "tutar": kayit.get("Tutar"), "pb": kayit.get("PB"),
        "tarih": str(kayit.get("Tarih") or ""), "aciklama": str(kayit.get("Aciklama") or "")[:200],
        "baglanti_id": kayit.get("Baglanti_ID"),
    }


def yaz(firma_key, veri, belge_id, aktor="sistem", baglam=None, kuru_calistir=False):
    """Belirtilen firmaya veri yazar (hepsi tek ANA Excel'e, excel_deposu uzerinden).

    ``baglam``: denetim icin {orijinal_talimat, normalize_komut, onay_kaynagi, taslak_id ...}.
    Donus: {"basari", "yazilan", "hata", ...}; basarisizlikta ANA/YEDEK degismez.
    """
    if firma_key not in FIRMALAR:
        return {"basari": False, "yazilan": 0, "hata": f"Bilinmeyen firma: {firma_key}"}

    # Tek finansal kaynak: bütün faaliyetler Touche Holding master dosyasına yazılır.
    master_firma = FIRMALAR["touche_holding"]
    ledger_sayfa = master_firma["ana_ledger"]["sayfa"]
    inbox_sayfa = master_firma["ai_inbox"]["sayfa"]
    baglam = dict(baglam or {})

    def degistir(wb):
        # Sohbetten gelen standart kayıtlar, faaliyetinden bağımsız olarak tek
        # master ledger şemasına yazılır. Böylece Gürsar gelir/hakedişleri eski
        # yalnız-ödeme adaptöründe yanlışlıkla gider tahakkukuna dönüşmez.
        if veri.get("_hedef") == "ana_ledger":
            _yeni_cari_tanimlarini_yaz(wb, veri)
            _sistem_tanimlarini_yaz(wb, veri)
            sonuc = _yaz_touche_ledger(wb, master_firma, veri, belge_id)
        elif firma_key == "touche_holding":
            sonuc = _yaz_touche(wb, master_firma, veri, belge_id)
        elif firma_key == "gursar_mimarlik":
            sonuc = _yaz_gursar_holding(wb, master_firma, veri, belge_id)
        else:
            faaliyet = FIRMALAR[firma_key].get("faaliyet") or FIRMALAR[firma_key].get("ad")
            master_veri = {
                **veri,
                "kayitlar": [
                    {**kayit, "Faaliyet": kayit.get("Faaliyet") or faaliyet}
                    for kayit in veri.get("kayitlar", [])
                ],
            }
            sonuc = _yaz_touche_ledger(wb, master_firma, master_veri, belge_id)
        if sonuc["basari"] and firma_key != "touche_holding":
            sonuc["holding_yazilan"] = sonuc["yazilan"]
        return sonuc

    def audit_ek(sonuc):
        return {
            "belge_id": belge_id, "firma": firma_key, "yazilan": sonuc.get("yazilan", 0),
            "kayitlar": sonuc.get("kayit_ozetleri", []),
            "orijinal_talimat": baglam.get("orijinal_talimat"),
            "normalize_komut": baglam.get("normalize_komut"),
            "onay_kaynagi": baglam.get("onay_kaynagi"), "taslak_id": baglam.get("taslak_id"),
        }

    def degisim_izni(sonuc):
        # Yalniz bu islemin yazdigi satirlardaki hucreler degisebilir; baska hicbir
        # mevcut hucre (ozellikle formuller ve doldurulmus satirlar) degismemelidir.
        satirlar = {o["satir"] for o in sonuc.get("kayit_ozetleri", []) if "satir" in o}
        return {ledger_sayfa: lambda k: _satir_no(k) in satirlar}

    try:
        sonuc = guvenli_degistir(
            degistir, islem_id=baglam.get("islem_id"), aktor=aktor, kaynak="excel_yazici.yaz",
            beklenen_sayfalar=[ledger_sayfa, inbox_sayfa], audit_ek=audit_ek,
            degisim_izni=degisim_izni, kuru_calistir=kuru_calistir,
        )
    except DepoHatasi as exc:
        log.warning("Excel yazma reddedildi (%s): %s", exc.kod, exc.mesaj)
        return {"basari": False, "yazilan": 0, "hata": exc.mesaj, "hata_kodu": exc.kod}
    except Exception as exc:
        log.exception("Excel yazma hatasi")
        return {"basari": False, "yazilan": 0, "hata": str(exc), "hata_kodu": type(exc).__name__}
    if not sonuc.get("basari"):
        log.info("Excel yazma reddedildi: %s", sonuc.get("hata"))
    return sonuc


# ============================================================
# 05_AI Belge Inbox
# ============================================================

def _yaz_touche(wb, firma, veri, belge_id):
    """Touche Holding 05_AI Belge Inbox'a yazar."""
    if veri.get("_hedef") == "ana_ledger":
        return _yaz_touche_ledger(wb, firma, veri, belge_id)
    inbox = firma["ai_inbox"]
    sayfa_adi = inbox["sayfa"]
    if sayfa_adi not in wb.sheetnames:
        return {"basari": False, "yazilan": 0, "hata": f"Sayfa yok: {sayfa_adi}"}
    ws = wb[sayfa_adi]
    sutunlar = inbox["sutunlar"]
    ilk = inbox["ilk_veri_satiri"]

    # SHA-256 belge ozeti daha once inbox'a yazildiysa ayni belgeyi tekrar alma.
    hash_sutunu = sutunlar.get("Belge_Hash")
    kayitlar = veri.get("kayitlar", [])
    for kayit in kayitlar:
        belge_hash = kayit.get("_hash")
        if hash_sutunu and belge_hash and _mukerrer_mi(ws, belge_hash, hash_sutunu, ilk, AI_GIRIS_SON_SATIR):
            return {"basari": False, "yazilan": 0, "hata": f"Mukerrer SHA-256 belge: {belge_hash}",
                    "hata_kodu": "MUKERRER_BELGE"}

    yazilacak = [a for a in sutunlar if a != "Durum_Kontrol"]
    uygun, _ = _bos_satirlar(ws, {a: c for a, c in sutunlar.items() if a != "Durum_Kontrol"},
                             ("Belge_ID", "AI_Cari", "Tutar", "Belge_Tarihi"),
                             yazilacak, ilk, AI_GIRIS_SON_SATIR)
    if len(uygun) < len(kayitlar):
        return {"basari": False, "yazilan": 0, "hata": "AI Belge Inbox kapasitesi dolu.",
                "hata_kodu": "KAPASITE_DOLU"}
    for i, kayit in enumerate(kayitlar):
        _inbox_satiri_yaz(ws, uygun[i], kayit, belge_id, sutunlar, i)
    return {"basari": True, "yazilan": len(kayitlar), "hata": ""}


def _inbox_satiri_yaz(ws, satir, kayit, belge_id, sutunlar, index):
    belge_id_benzersiz = f"{belge_id}-{index+1:03d}"
    degerler = {
        "Belge_ID":         belge_id_benzersiz,
        "Yukleme_Tarihi":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Kaynak":           "Telegram Bot / AI",
        "Dosya_Metin_Ref":  kayit.get("_dosya_adi", ""),
        "AI_Faaliyet":      kayit.get("Faaliyet", ""),
        "AI_Proje":         kayit.get("Proje", ""),
        "AI_Cari_Tipi":     kayit.get("Cari_Tipi", ""),
        "AI_Cari":          kayit.get("Cari", ""),
        "AI_Islem_Turu":    kayit.get("Islem_Turu", ""),
        "Tutar":            kayit.get("Tutar", 0),
        "PB":               kayit.get("PB", "TL"),
        "Belge_Tarihi":     kayit.get("Tarih", ""),
        "Vade":             kayit.get("Vade", ""),
        "AI_Guven":         kayit.get("_guven", 0.90),
        "Kullanici_Onayi":  kayit.get("_kullanici_onayi", "Onaylandı"),
        "Hedef_Kayit_ID":   "",
        "AI_Aciklama":      str(kayit.get("_dogrulama_ozeti") or kayit.get("Aciklama", ""))[:200],
        "Belge_Hash":       kayit.get("_hash", ""),
    }
    for alan, sutun in sutunlar.items():
        # Q sutunundaki Excel kontrol formulu sablonun parcasidir.
        if alan == "Durum_Kontrol":
            continue
        _hucre_yaz(ws, f"{sutun}{satir}", degerler.get(alan, ""))


# ============================================================
# 99_Ayarlar: yeni cari ve sistem tanimlari (YALNIZ bos hucreler doldurulur)
# ============================================================

def _yeni_cari_tanimlarini_yaz(wb, veri):
    """Onaylanan sohbet taslagindaki acik yeni carileri master veriye ekler.

    Ledger ve tanim ayni gecici workbook'a yazildigi icin islem atomiktir.
    Modelin urettigi her ad degil, yalnizca ``_yeni_cari`` bayrakli ve kullanici
    onayindan gecmis kayitlar burada ele alinir.
    """
    oneriler = [x for x in veri.get("kayitlar", []) if x.get("_yeni_cari")]
    if not oneriler:
        return 0
    if AYARLAR_SAYFASI not in wb.sheetnames:
        raise ValueError("Yeni cari tanımı için 99_Ayarlar sayfası bulunamadı.")
    ws = wb[AYARLAR_SAYFASI]
    mevcut = {_norm(ws[f"AI{satir}"].value) for satir in range(13, 113) if ws[f"AI{satir}"].value}
    eklendi = 0
    for kayit in oneriler:
        cari = str(kayit.get("Cari") or "").strip()
        faaliyet = str(kayit.get("Faaliyet") or "").strip()
        cari_tipi = str(kayit.get("Cari_Tipi") or "Diğer").strip()
        if not cari or not faaliyet:
            raise ValueError("Yeni cari tanımı için faaliyet ve cari adı gerekli.")
        if _norm(cari) in mevcut:
            continue
        bos = next((satir for satir in range(13, 113) if _bos_mu(ws[f"AI{satir}"].value)
                    and _bos_mu(ws[f"AG{satir}"].value) and _bos_mu(ws[f"AH{satir}"].value)), None)
        if bos is None:
            raise ValueError("99_Ayarlar cari tanım alanı dolu; yeni cari eklenemedi.")
        _hucre_yaz(ws, f"AG{bos}", faaliyet)
        _hucre_yaz(ws, f"AH{bos}", cari_tipi)
        _hucre_yaz(ws, f"AI{bos}", cari)
        mevcut.add(_norm(cari))
        eklendi += 1
    return eklendi


def _bos_ise_yaz(ws, adres, deger):
    """Hucre bos ise yazar; dolu hucre (kullanici verisi/formul) ASLA ezilmez."""
    if _bos_mu(ws[adres].value):
        ws[adres] = deger
        return True
    return False


def _sistem_tanimlarini_yaz(wb, veri):
    """Onaylanan kaydın kontrollü işlem türü ve hesaplarını 99_Ayarlar'a ekler."""
    if AYARLAR_SAYFASI not in wb.sheetnames:
        raise ValueError("Sistem tanımları için 99_Ayarlar sayfası bulunamadı.")
    ws = wb[AYARLAR_SAYFASI]
    norm = _norm

    gerekli_turler = {
        str(k.get("Islem_Turu") or "").strip() for k in veri.get("kayitlar", [])
        if norm(k.get("Islem_Turu")) in {norm(x) for x in SISTEM_ISLEM_TURLERI}
    }
    mevcut_turler = {
        norm(ws[f"T{satir}"].value): satir
        for satir in range(6, 31) if ws[f"T{satir}"].value
    }
    for tur in gerekli_turler:
        satir = mevcut_turler.get(norm(tur))
        if satir is None:
            satir = next((aday for aday in range(6, 31) if _bos_mu(ws[f"T{aday}"].value)), None)
            if satir is None:
                raise ValueError("99_Ayarlar işlem türü alanı dolu; sistem tanımı eklenemedi.")
            ws[f"T{satir}"] = tur
            mevcut_turler[norm(tur)] = satir

        etkiler = SISTEM_ISLEM_ETKILERI.get(tur)
        if etkiler is None:
            etkiler = next(
                (deger for ad, deger in SISTEM_ISLEM_ETKILERI.items() if norm(ad) == norm(tur)),
                None,
            )
        if etkiler is not None:
            # Yalniz bos katsayi hucreleri doldurulur; sahibin duzenledigi katsayilar korunur.
            for sutun, deger in zip(("U", "V", "W", "X", "Y", "Z", "AA"), etkiler):
                _bos_ise_yaz(ws, f"{sutun}{satir}", deger)

    sistem_hesaplari = {norm(x) for x in GURSAR_SISTEM_HESAPLARI}
    gerekli_hesaplar = [
        k for k in veri.get("kayitlar", []) if norm(k.get("Hesap_Kasa")) in sistem_hesaplari
    ]
    mevcut_hesaplar = {
        norm(ws[f"M{satir}"].value): satir
        for satir in range(13, 63) if ws[f"M{satir}"].value
    }
    mevcut_hesap_idleri = {
        str(ws[f"K{satir}"].value).strip()
        for satir in range(13, 63) if ws[f"K{satir}"].value
    }

    def yeni_hesap_id():
        sira = 1
        while f"HSP-SYS-{sira:03d}" in mevcut_hesap_idleri:
            sira += 1
        hesap_id = f"HSP-SYS-{sira:03d}"
        mevcut_hesap_idleri.add(hesap_id)
        return hesap_id

    for kayit in gerekli_hesaplar:
        hesap = str(kayit.get("Hesap_Kasa") or "").strip()
        satir = mevcut_hesaplar.get(norm(hesap))
        if satir is None:
            satir = next((aday for aday in range(13, 63) if _bos_mu(ws[f"M{aday}"].value)), None)
            if satir is None:
                raise ValueError("99_Ayarlar hesap alanı dolu; sistem hesabı eklenemedi.")
            ws[f"L{satir}"] = str(kayit.get("Faaliyet") or "")
            ws[f"M{satir}"] = hesap
            mevcut_hesaplar[norm(hesap)] = satir

        # Önceki sürüm yalnız L/M sütunlarını dolduruyordu. Mevcut yarım
        # tanımları da tamamla ki açılış bakiyesi ve kur formülleri çalışsın.
        _bos_ise_yaz(ws, f"K{satir}", yeni_hesap_id())
        _bos_ise_yaz(ws, f"L{satir}", str(kayit.get("Faaliyet") or ""))
        para_birimi = str(kayit.get("PB") or "").strip().upper()
        if not para_birimi:
            para_birimi = "USD" if "USD" in hesap.upper() else "EUR" if "EUR" in hesap.upper() else "TL"
        _bos_ise_yaz(ws, f"N{satir}", para_birimi)
        _bos_ise_yaz(ws, f"O{satir}", 0)
        _bos_ise_yaz(ws, f"P{satir}", f'=IF($K{satir}="","",IFERROR(VLOOKUP($N{satir},$AD$3:$AE$7,2,FALSE),0))')
        _bos_ise_yaz(ws, f"Q{satir}", f'=IF($K{satir}="","",$O{satir}*$P{satir})')


# ============================================================
# 03_Kayit Merkezi
# ============================================================

def _yaz_touche_ledger(wb, firma, veri, belge_id):
    """CFO sohbetinde/belgede acikca girilen ve onaylanan islemi holding ledger'ina yazar."""
    ledger = firma["ana_ledger"]
    if ledger["sayfa"] not in wb.sheetnames:
        return {"basari": False, "yazilan": 0, "hata": f"Sayfa yok: {ledger['sayfa']}"}
    ws = wb[ledger["sayfa"]]
    sutunlar = ledger["sutunlar"]
    ilk, son = ledger["ilk_veri_satiri"], config.LEDGER_SON_SATIR
    kayitlar = veri.get("kayitlar", [])
    gerekli = ("Tarih", "Faaliyet", "Cari_Tipi", "Cari", "Islem_Turu", "Tutar")
    for sira, kayit in enumerate(kayitlar, 1):
        eksik = [alan for alan in gerekli if kayit.get(alan) in (None, "")]
        if eksik:
            return {"basari": False, "yazilan": 0,
                    "hata": f"Kayit {sira} ana ledger icin eksik: {', '.join(eksik)}. Duzelt secenegini kullanin."}

    # Idempotency: ayni belge_id daha once yazildiysa ikinci kez ASLA yazilmaz.
    for index in range(1, len(kayitlar) + 1):
        kaynak_id = f"{belge_id}-{index:03d}"
        if _mukerrer_mi(ws, kaynak_id, sutunlar["Kaynak_Belge_ID"], ilk, son):
            return {"basari": False, "yazilan": 0, "hata": f"Mukerrer belge: {kaynak_id}",
                    "hata_kodu": "MUKERRER_BELGE"}

    yazilacak_alanlar = [
        "Kayit_ID", "Tarih", "Faaliyet", "Proje", "Cari_Tipi", "Cari", "Islem_Turu", "Hesap_Kasa",
        "Tutar", "PB", "Kur", "Baglanti_ID", "Vade", "Durum", "Belge_Referans", "Aciklama",
        "Kaynak_Belge_ID", "Kayit_Kaynak", "AI_Guven", "Onay_Durumu",
    ]
    boslar, kismi = _bos_satirlar(ws, sutunlar, LEDGER_KIMLIK_ALANLARI, yazilacak_alanlar, ilk, son)
    if len(boslar) < len(kayitlar):
        return {"basari": False, "yazilan": 0,
                "hata": f"Holding kayıt merkezi kapasitesi dolu (satır {ilk}-{son}).",
                "hata_kodu": "KAPASITE_DOLU"}

    ozetler, idler = [], []
    for index, kayit in enumerate(kayitlar, 1):
        kaynak_id = f"{belge_id}-{index:03d}"
        kayit_id = _kayit_id("AI-TH", kaynak_id)
        vade = _excel_tarihi(kayit.get("Vade")) if kayit.get("Vade") else ""
        degerler = {
            "Kayit_ID": kayit_id,
            "Tarih": _excel_tarihi(kayit.get("Tarih"), zorunlu=True),
            "Faaliyet": kayit.get("Faaliyet"),
            "Proje": kayit.get("Proje") or "",
            "Cari_Tipi": kayit.get("Cari_Tipi"),
            "Cari": kayit.get("Cari"),
            "Islem_Turu": kayit.get("Islem_Turu"),
            "Hesap_Kasa": kayit.get("Hesap_Kasa") or "",
            "Tutar": kayit.get("Tutar"),
            "PB": kayit.get("PB") or "TL",
            "Kur": kayit.get("Kur") or 1,
            "Baglanti_ID": kayit.get("Baglanti_ID") or "",
            "Vade": vade,
            "Durum": kayit.get("Durum") or "Aktif",
            "Belge_Referans": belge_id,
            "Aciklama": kayit.get("Aciklama") or "CFO Asistanı kaydı",
            "Kaynak_Belge_ID": kaynak_id,
            "Kayit_Kaynak": "CFO Asistanı / AI / Kullanıcı Onaylı",
            "AI_Guven": kayit.get("_guven", 0),
            "Onay_Durumu": "Onaylandı",
        }
        satir = boslar[index - 1]
        for alan, sutun in sutunlar.items():
            if alan not in degerler:
                continue
            if alan in ("Tarih", "Vade"):
                _tarih_yaz(ws, f"{sutun}{satir}", degerler[alan])
            else:
                _hucre_yaz(ws, f"{sutun}{satir}", degerler[alan])
        idler.append(kayit_id)
        ozet = _kayit_ozeti({**kayit, "Baglanti_ID": degerler["Baglanti_ID"]}, kayit_id, kaynak_id)
        ozet["satir"] = satir
        ozetler.append(ozet)
    sonuc = {"basari": True, "yazilan": len(kayitlar), "hata": "",
             "kayit_idleri": idler, "kayit_ozetleri": ozetler}
    if kismi:
        sonuc["atlanan_kismi_dolu_satirlar"] = kismi
    return sonuc


# ============================================================
# Gursar belge adaptoru (dekont -> holding ledger)
# ============================================================

def _yaz_gursar_holding(wb, firma, veri, belge_id):
    """Gursar odemelerini holding ana ledger'ina ayni belge baglantisiyla yansitir."""
    ledger = firma["ana_ledger"]
    sayfa_adi = ledger["sayfa"]
    if sayfa_adi not in wb.sheetnames:
        return {"basari": False, "yazilan": 0, "hata": f"Holding sayfasi yok: {sayfa_adi}"}

    ws = wb[sayfa_adi]
    sutunlar = ledger["sutunlar"]
    kaynak_sutunu = sutunlar["Kaynak_Belge_ID"]
    ilk_satir, son_satir = ledger["ilk_veri_satiri"], config.LEDGER_SON_SATIR
    yazilacaklar = []

    for index, kayit in enumerate(veri.get("kayitlar", []), 1):
        kaynak_id = f"{belge_id}-{index:03d}"
        if _mukerrer_mi(ws, kaynak_id, kaynak_sutunu, ilk_satir, son_satir):
            return {"basari": False, "yazilan": 0, "hata": f"Holding'de mukerrer belge: {kaynak_id}",
                    "hata_kodu": "MUKERRER_BELGE"}
        yazilacaklar.append((kaynak_id, kayit, False))
        try:
            masraf = float(kayit.get("Banka_Masrafi") or 0)
        except (TypeError, ValueError):
            masraf = 0
        if masraf > 0:
            yazilacaklar.append((f"{kaynak_id}-MASRAF", {**kayit, "Tutar": masraf}, True))

    alanlar = ["Kayit_ID", "Tarih", "Faaliyet", "Proje", "Cari_Tipi", "Cari", "Islem_Turu", "Hesap_Kasa",
               "Tutar", "PB", "Kur", "Durum", "Belge_Referans", "Aciklama", "Kaynak_Belge_ID",
               "Kayit_Kaynak", "AI_Guven", "Onay_Durumu"]
    boslar, _ = _bos_satirlar(ws, sutunlar, LEDGER_KIMLIK_ALANLARI, alanlar, ilk_satir, son_satir)
    if len(boslar) < len(yazilacaklar):
        return {"basari": False, "yazilan": 0, "hata": "Holding kayıt merkezi kapasitesi dolu.",
                "hata_kodu": "KAPASITE_DOLU"}
    ozetler = []
    for ofset, (kaynak_id, kayit, banka_masrafi) in enumerate(yazilacaklar):
        satir = boslar[ofset]
        islem_turu = "Banka Masrafı" if banka_masrafi else {
            "Usta ödemesi": "Usta/Tedarikçi Ödemesi",
            "Ortak cari ödemesi": "Ortak Cari Ödemesi",
            "Banka masrafı": "Banka Masrafı",
            "Diğer": "Gider Tahakkuku",
        }.get(kayit.get("Islem_Turu"), "Gider Tahakkuku")
        cari_tipi = "Banka" if banka_masrafi else {
            "Usta ödemesi": "Usta",
            "Ortak cari ödemesi": "Ortak",
            "Banka masrafı": "Banka",
            "Diğer": "Diğer",
        }.get(kayit.get("Islem_Turu"), kayit.get("Cari_Tipi") or "Diğer")
        kayit_id = _kayit_id("AI-GUR", kaynak_id)
        degerler = {
            "Kayit_ID": kayit_id,
            "Tarih": _excel_tarihi(kayit.get("Tarih")) or "",
            "Faaliyet": kayit.get("Faaliyet") or "Gürsar İnşaat",
            "Proje": kayit.get("Proje") or "",
            "Cari_Tipi": cari_tipi,
            "Cari": "Ziraat" if banka_masrafi else (kayit.get("Cari") or kayit.get("Alici") or ""),
            "Islem_Turu": islem_turu,
            "Hesap_Kasa": kayit.get("Hesap_Kasa") or "Gürsar TL Banka",
            "Tutar": kayit.get("Tutar") or kayit.get("Tutar_TL") or 0,
            "PB": kayit.get("PB") or "TL",
            "Kur": 1,
            "Durum": "Gerçekleşti",
            "Belge_Referans": belge_id,
            "Aciklama": kayit.get("Is_Kalemi") or "Gürsar otomatik aktarım",
            "Kaynak_Belge_ID": kaynak_id,
            "Kayit_Kaynak": "Gürsar / Telegram Bot / AI",
            "AI_Guven": kayit.get("_guven", 0),
            "Onay_Durumu": "Onaylandı",
        }
        for alan, sutun in sutunlar.items():
            # Formul sutunlarini ve tanimlanmayan sablon alanlarini koru.
            if alan not in degerler:
                continue
            if alan == "Tarih":
                _tarih_yaz(ws, f"{sutun}{satir}", degerler[alan])
            else:
                _hucre_yaz(ws, f"{sutun}{satir}", degerler[alan])
        ozet = _kayit_ozeti({**kayit, **{k: degerler[k] for k in ("Faaliyet", "Proje", "Islem_Turu", "Cari", "Hesap_Kasa", "Tutar", "PB")}},
                            kayit_id, kaynak_id)
        ozet["satir"] = satir
        ozetler.append(ozet)
    return {"basari": True, "yazilan": len(yazilacaklar), "hata": "", "kayit_ozetleri": ozetler}


# ============================================================
# Iptal
# ============================================================

def belge_kaydini_iptal_et(belge_id, aktor="sistem"):
    """Belgeye bagli kayitlari silmeden Iptal/Reddedildi durumuna getirir."""
    belge_id = str(belge_id or "").strip()
    if not belge_id:
        return {"basari": False, "etkilenen": 0, "hata": "Belge ID bos"}

    master = FIRMALAR["touche_holding"]
    ledger, inbox = master["ana_ledger"], master["ai_inbox"]

    def degistir(wb):
        etkilenen = 0
        ws = wb[ledger["sayfa"]]
        s = ledger["sutunlar"]
        for satir in range(ledger["ilk_veri_satiri"], (ws.max_row or 0) + 1):
            kayit_id = str(ws[f"{s['Kayit_ID']}{satir}"].value or "")
            kaynak = str(ws[f"{s['Kaynak_Belge_ID']}{satir}"].value or "")
            referans = str(ws[f"{s['Belge_Referans']}{satir}"].value or "")
            if (kayit_id == belge_id or kaynak == belge_id or kaynak.startswith(belge_id + "-")
                    or referans == belge_id):
                ws[f"{s['Durum']}{satir}"] = "İptal"
                ws[f"{s['Onay_Durumu']}{satir}"] = "Reddedildi"
                etkilenen += 1

        ws = wb[inbox["sayfa"]]
        s = inbox["sutunlar"]
        for satir in range(inbox["ilk_veri_satiri"], (ws.max_row or 0) + 1):
            kimlik = str(ws[f"{s['Belge_ID']}{satir}"].value or "")
            if kimlik == belge_id or kimlik.startswith(belge_id + "-"):
                ws[f"{s['Kullanici_Onayi']}{satir}"] = "Reddedildi"
                etkilenen += 1
        if not etkilenen:
            return {"basari": False, "etkilenen": 0, "hata": f"Belge ID bulunamadi: {belge_id}"}
        return {"basari": True, "etkilenen": etkilenen, "hata": ""}

    izin = {
        ledger["sayfa"]: {ledger["sutunlar"]["Durum"], ledger["sutunlar"]["Onay_Durumu"]},
        inbox["sayfa"]: {inbox["sutunlar"]["Kullanici_Onayi"]},
    }
    try:
        return guvenli_degistir(
            degistir, aktor=aktor, kaynak="excel_yazici.belge_kaydini_iptal_et",
            beklenen_sayfalar=[ledger["sayfa"], inbox["sayfa"]], degisim_izni=izin,
            audit_ek=lambda sonuc: {"belge_id": belge_id, "etkilenen": sonuc.get("etkilenen", 0)},
        )
    except DepoHatasi as exc:
        return {"basari": False, "etkilenen": 0, "hata": exc.mesaj, "hata_kodu": exc.kod}
    except Exception as exc:
        log.exception("Belge iptali basarisiz")
        return {"basari": False, "etkilenen": 0, "hata": str(exc)}
